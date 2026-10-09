<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, toRaw, watch } from "vue";
import { NButton, NInput, NSelect } from "naive-ui";
import { api, closeStream, latestEventId, type CommandPayload, type CommandView, type RunOverview, type ProjectHistory, type StorySummary, type StoryView, type DiscussionSession } from "../api/client";
import { historyContext } from "../ui/historyContext";
import { routeQuery, navigate, historyLink } from "../ui/navigation";
import { operationInput, operationLabel, operationOutput, statusLabel } from "../ui/runPresentation";
import RunDetails from "./RunDetails.vue";

type Journal = ProjectHistory;
const props = defineProps<{ scopeId?: string }>();
const journal = ref<Journal | null>(null);
const books = ref<StorySummary[]>([]);
const scope = ref(props.scopeId || "");
const contextView = ref<StoryView | null>(null);
const contextCommands = ref<CommandView[]>([]);
const sessions = ref<DiscussionSession[]>([]);
const contextFor = (command: CommandView) => historyContext(command, contextCommands.value, contextView.value, sessions.value);
const selected = ref<CommandView | null>(null);
const selectedRun = ref<RunOverview | null>(null);
const latest = ref<CommandView | null>(null);
const editedInput = ref("");
const editing = ref(false);
const replayCommand = ref<CommandView | null>(null);
const regenerationUndone = ref(false);
const regenerationStorage = computed(() => `ainovel.regeneration.history.${scope.value}`);
const busy = ref(false);
const message = ref("");
const offset = ref(0);
const monitored = new Set<string>();
const latestVisible = computed(() => selected.value?.submission.id === journal.value?.latest?.command_id);
const latestFailed = computed(() => selected.value?.status === 'failed' && !selected.value.undone && selected.value.submission.id === journal.value?.latest_command_id);
const running = computed(() => journal.value?.items?.some(i => !i.command.undone && ['queued', 'running'].includes(i.command.status)) ?? false);
const canRegenerate = computed(() => !!latest.value && ['generate', 'discussion', 'character_chat', 'interview', 'rewrite', 'review_draft'].includes(latest.value.submission.payload.kind));
const options = computed(() => [...books.value.map(b => ({ label: b.title, value: b.id })), ...(props.scopeId && !books.value.some(b => b.id === props.scopeId) ? [{ label: journal.value?.items[0]?.story_title || "当前构思 / 设置", value: props.scopeId }] : [])]);

async function load() {
  const query = new URLSearchParams({ offset: String(offset.value), limit: "50" });
  if (!scope.value) return;
  query.set("scope_id", scope.value);
  if (books.value.some(book => book.id === scope.value)) {
    [contextView.value, contextCommands.value, sessions.value] = await Promise.all([api.get<StoryView>(`/api/stories/${scope.value}`).catch(() => null), api.get<CommandView[]>(`/api/stories/${scope.value}/commands`).catch(() => []), api.get<DiscussionSession[]>(`/api/stories/${scope.value}/discussions`).catch(() => [])]);
  }
  journal.value = await api.get<Journal>(`/api/history?${query}`);
  latest.value = journal.value.latest ? await api.get<CommandView>(`/api/commands/${journal.value.latest.command_id}`) : null;
  if (selected.value) {
    const retained = journal.value.items?.find(i => i.command.submission.id === selected.value!.submission.id)?.command;
    await select(retained as CommandView || latest.value);
  } else {
    const requested = routeQuery().get("command");
    const item = journal.value.items?.find(i => i.command.submission.id === requested)?.command;
    await select(item as CommandView || latest.value || journal.value.items?.[0]?.command as CommandView || null);
  }
  for (const item of journal.value.items ?? []) {
    const command = item.command;
    if (['queued', 'running'].includes(command.status) && !command.undone && !monitored.has(command.submission.id)) {
      monitored.add(command.submission.id);
      api.wait(command.submission.id, () => undefined).catch(e => message.value = e.message).finally(() => { monitored.delete(command.submission.id); load().catch(e => message.value = e.message); });
    }
  }
}
async function select(command: CommandView | null) {
  selected.value = command;
  selectedRun.value = null;
  const overview = command ? await api.get<RunOverview>(`/api/runs/${command.submission.id}`).catch(() => null) : null;
  if (selected.value?.submission.id === command?.submission.id) selectedRun.value = overview;
}
async function undoLatest() {
  const target = journal.value?.latest;
  if (!target || busy.value || running.value) return;
  busy.value = true;
  try {
    await api.command({ kind: "undo", expected_operation_id: target.operation_id, target_command_id: target.command_id }, target.story_id ?? null, target.revision_id ?? null, target.story_id ? null : target.scope_id, () => undefined);
    selected.value = null;
    await load();
    message.value = "已撤回最新操作，现在可继续撤回上一条。";
  } catch (error) { message.value = error instanceof Error ? error.message : "撤回失败"; }
  finally { busy.value = false; }
}
async function editRegeneration() {
  if (!latest.value) return;
  replayCommand.value = latest.value;
  regenerationUndone.value = false;
  editedInput.value = operationInput(latest.value);
  const payload = latest.value.submission.payload;
  if (payload.kind === "review_draft") {
    const origin = await api.get<CommandView>(`/api/commands/${payload.generation_command_id}`);
    editedInput.value = payload.feedback || operationInput(origin);
  }
  editing.value = true;
}
async function regenerate() {
  const target = journal.value?.latest, original = replayCommand.value ?? latest.value;
  if (!original || (!regenerationUndone.value && (!target || target.command_id !== original.submission.id)) || busy.value || running.value) return;
  // Undo is durably recorded first. A rejected resubmission remains editable rather than silently restoring old output.
  busy.value = true;
  let undone = regenerationUndone.value;
  try {
    let payload = structuredClone(toRaw(original.submission.payload)) as CommandPayload;
    const input = editedInput.value.trim();
    if (payload.kind === "generate") { payload.instruction = input; payload.new_chapter = false; }
    else if (payload.kind === "rewrite") payload.instruction = input;
    else if (payload.kind === "discussion" || payload.kind === "character_chat") payload.message = input;
    else if (payload.kind === "interview") {
      const user = payload.turns?.slice().reverse().find(t => t.role === "user");
      if (user) user.content = input;
      else if (input) payload.turns = [...(payload.turns ?? []), { role: 'user', content: input }];
    } else if (payload.kind === "review_draft") payload = { ...payload, decision: "revise", feedback: input || "重新生成一份草稿，保留原始创作要求。", selection: null };
    if (!undone && target) {
      await api.command({ kind: "undo", expected_operation_id: target.operation_id, target_command_id: target.command_id }, target.story_id ?? null, target.revision_id ?? null, target.story_id ? null : target.scope_id, () => undefined);
      undone = true;
      regenerationUndone.value = true;
      persistRegeneration();
    }
    const storyId = original.submission.story_id;
    const head = storyId ? await api.get<StoryView>(`/api/stories/${storyId}`) : null;
    const result = await api.command(payload, storyId,
      payload.kind === 'character_chat' ? original.submission.base_revision_id : head?.revision_id ?? null,
      original.submission.session_id ?? null, () => undefined, () => {
        // Accepted commands now own their own durable input and recovery history.
        replayCommand.value = null; editing.value = false; regenerationUndone.value = false;
        localStorage.removeItem(regenerationStorage.value);
      });
    editing.value = false;
    selected.value = result;
    await load();
    message.value = "已保存新的生成结果；原结果与撤回记录仍保留在历史中。";
  } catch (error) {
    message.value = `${undone ? '原操作已撤回；新任务未完成，输入仍保留。' : ''}${error instanceof Error ? error.message : '重新生成失败'}`;
    await load().catch(() => undefined);
  } finally { busy.value = false; }
}
async function retryLatest(decision: 'retry' | 'withdraw' = 'retry') {
  if (!selected.value || !latestFailed.value || busy.value) return;
  busy.value = true;
  try {
    const original = selected.value;
    const head = original.submission.story_id ? await api.get<StoryView>(`/api/stories/${original.submission.story_id}`) : null;
    await api.command({ kind: 'recover_command', target_command_id: original.submission.id, expected_event_id: await latestEventId(original.submission.id), decision }, original.submission.story_id, head?.revision_id ?? original.submission.base_revision_id, original.submission.session_id, () => undefined);
    await load();
  } catch (error) { message.value = error instanceof Error ? error.message : '重试失败'; }
  finally { busy.value = false; }
}
function persistRegeneration() {
  if (editing.value && replayCommand.value) localStorage.setItem(regenerationStorage.value, JSON.stringify({ command_id: replayCommand.value.submission.id, input: editedInput.value, undone: regenerationUndone.value }));
}
watch([editedInput, editing], persistRegeneration);
function cancelRegeneration() { editing.value = false; replayCommand.value = null; regenerationUndone.value = false; localStorage.removeItem(regenerationStorage.value); }
onMounted(async () => {
  try {
    books.value = await api.get<StorySummary[]>('/api/stories');
    scope.value ||= books.value[0]?.id || ''; await load();
    const saved = localStorage.getItem(regenerationStorage.value);
    if (saved) {
      const draft = JSON.parse(saved) as { command_id: string; input: string; undone: boolean };
      replayCommand.value = await api.get<CommandView>(`/api/commands/${draft.command_id}`);
      editedInput.value = draft.input; regenerationUndone.value = draft.undone; editing.value = true;
    }
  } catch (error) { message.value = error instanceof Error ? error.message : '无法读取历史'; }
});
onUnmounted(() => { for (const id of monitored) closeStream(id); });
</script>

<template>
  <main class="history-page">
    <header class="history-heading"><div><h1>作品运行历史</h1><p>创作、讨论与资料修改，按发生顺序记录。仅最新操作可撤回。</p></div><NSelect aria-label="选择小说" :disabled="busy || editing" :value="scope" :options="options" @update:value="value => navigate(historyLink(value))"/><NButton :disabled="busy" @click="load">刷新</NButton></header>
    <div class="journal-layout">
      <aside class="journal-list"><button v-for="item in journal?.items" :key="item.command.submission.id" :data-command-id="item.command.submission.id" :class="{ selected: selected?.submission.id === item.command.submission.id }" @click="select(item.command as CommandView)"><span class="history-category">{{ contextFor(item.command as CommandView).category }}</span><strong>{{ operationLabel(item.command as CommandView) }}</strong><span>{{ contextFor(item.command as CommandView).location }}</span><small>{{ new Date(item.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }} · {{ item.command.undone ? '已撤回' : statusLabel(item.command.status) }}{{ item.command.submission.id === journal?.latest?.command_id ? ' · 最新可撤回操作' : '' }}</small></button><div class="journal-pagination"><NButton size="small" :disabled="offset === 0" @click="offset -= 50; load()">上一页</NButton><span>{{ journal?.total ?? 0 }} 条</span><NButton size="small" :disabled="offset + 50 >= (journal?.total ?? 0)" @click="offset += 50; load()">下一页</NButton></div></aside>
      <section class="journal-detail" v-if="selected"><div class="history-breadcrumb"><span class="history-category">{{ contextFor(selected).category }}</span><span>{{ contextFor(selected).location }}</span></div><header><h2>{{ operationLabel(selected) }}</h2><span>{{ selected.undone ? '已撤回' : statusLabel(selected.status) }}</span></header><section v-if="operationInput(selected)" class="history-content-card"><h3>你的要求</h3><p class="history-preview">{{ operationInput(selected) }}</p><details v-if="operationInput(selected).length > 180"><summary>展开完整要求</summary><p class="preserve-text">{{ operationInput(selected) }}</p></details></section>
        <section class="history-content-card"><h3>操作结果</h3><p class="history-preview">{{ operationOutput(selected) }}</p><details v-if="operationOutput(selected).length > 180"><summary>展开完整回复</summary><p class="preserve-text">{{ operationOutput(selected) }}</p></details></section>
        <div v-if="latestVisible && !latestFailed" class="journal-controls"><NButton :disabled="busy || running || regenerationUndone" @click="undoLatest">撤回最新操作</NButton><NButton v-if="canRegenerate" :disabled="busy || running || regenerationUndone" @click="editRegeneration">修改后重新生成</NButton><NButton v-if="canRegenerate" :disabled="busy || running || regenerationUndone" @click="editRegeneration().then(regenerate)">重新生成</NButton></div>
        <div v-if="latestFailed" class="journal-controls"><NButton :disabled="busy || running" @click="retryLatest('retry')">从检查点重试</NButton><NButton :disabled="busy || running" @click="retryLatest('withdraw')">撤回最新失败任务</NButton></div>
        <section v-if="editing && (latestVisible || regenerationUndone)" class="regenerate-editor"><label>本次生成要求<NInput aria-label="重新生成要求" v-model:value="editedInput" type="textarea" :autosize="{ minRows: 3, maxRows: 10 }" /></label><p class="muted">{{ regenerationUndone ? '原操作已撤回。此请求尚未提交，继续提交不会再次撤回上一条。' : '重新生成会先撤回当前最新操作，再按修改后的要求运行；新正文仍需确认草稿。' }}</p><NButton type="primary" :loading="busy" @click="regenerate">{{ regenerationUndone ? '继续提交重新生成' : '撤回并重新生成' }}</NButton><NButton @click="cancelRegeneration">取消</NButton></section>
        <RunDetails v-if="selectedRun" :run="selectedRun" />
      </section><section v-else class="journal-detail"><p>暂无操作记录。</p></section>
    </div><p v-if="message" class="journal-notice" role="status">{{ message }}</p>
  </main>
</template>
