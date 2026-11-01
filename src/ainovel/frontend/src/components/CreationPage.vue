<script setup lang="ts">
import ConversationActions from "./ConversationActions.vue";
import ConversationLog from "./ConversationLog.vue";
import { computed, onBeforeUnmount, onMounted, ref, toRaw } from "vue";
import { NButton, NCard, NInput, NSelect, NTag } from "naive-ui";
import { navigate } from "../ui/navigation";
import { api, type CommandPayload, type CommandView, type Schemas, type SetupDraft } from "../api/client";

const props = defineProps<{ draftId?: string }>();
const emit = defineEmits<{ back: []; created: [storyId: string] }>();
type Turn = Schemas["Turn"];
type Proposal = Schemas["CreationProposal-Input"];
const draft = ref<SetupDraft | null>(null);
const turns = ref<Turn[]>([]);
const history = ref<CommandView[]>([]);
const input = ref("");
const busy = ref(false);
const pendingCommand = ref<CommandView | null>(null);
const checklist = ref<Schemas["CreationField"][]>([]);
const saveStatus = ref<"saved" | "saving" | "error">("saved");
const toast = ref("");
const fieldLibrary = ref<Schemas["FieldTemplateLibrary"] | null>(null);
void api.get<Schemas["FieldTemplateLibrary"]>("/api/field-templates").then(value => fieldLibrary.value = value).catch(error => say(error.message));
const title = computed({ get: () => draft.value?.title ?? "新故事草稿", set: value => { if (draft.value) draft.value.title = value; } });
const premise = computed({ get: () => draft.value?.proposal?.world.premise ?? "", set: value => {
  const proposal = draft.value?.proposal;
  if (!proposal) return;
  const definitionId = fieldLibrary.value?.templates.find(item => item.builtin && item.scope === "world")?.fields?.[0]?.id;
  const entry = definitionId ? proposal.world.entries.find(item => item.definition?.id === definitionId) : undefined;
  if (entry) entry.value = value;
  proposal.world = { ...proposal.world, premise: value };
} });
const currentProposal = computed(() => draft.value?.proposal ?? null);
const failedCommands = computed(() => history.value.filter(item => item.status === "failed" && !item.undone && item.submission.payload.kind === "interview"));
let saveTimer = 0;
let loadGeneration = 0;

function turnCommand(index: number) {
  const ordinal = turns.value.slice(0, index + 1).filter(turn => turn.role === 'assistant').length - 1;
  return history.value.filter(item => !item.undone && !!item.output.interview)[ordinal];
}
function say(message: string) { toast.value = message; window.setTimeout(() => { if (toast.value === message) toast.value = ""; }, 4500); }
async function persist() {
  if (!draft.value) return;
  const payload: CommandPayload = { kind: "save_setup_draft", draft: structuredClone(toRaw(draft.value)) };
  saveStatus.value = "saving";
  try {
    await api.command(payload, null, null, draft.value.id, () => undefined);
    saveStatus.value = "saved";
  } catch (error) {
    saveStatus.value = "error";
    throw error;
  }
}
function scheduleSave() {
  if (!draft.value) return;
  saveStatus.value = "saving";
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    saveTimer = 0;
    void persist().catch(error => say(`草稿保存失败：${error instanceof Error ? error.message : "请稍后重试"}`));
  }, 450);
}
async function flushSave() {
  if (saveTimer) { window.clearTimeout(saveTimer); saveTimer = 0; }
  await persist();
}
function applyInterview(command: CommandView) {
  const result = command.output.interview;
  if (!result) return;
  const payload = command.submission.payload;
  if (payload.kind !== "interview") return;
  const latestUser = payload.turns.at(-1);
  const previousAssistant = turns.value.at(-1)?.role === "assistant" ? turns.value.at(-1) : null;
  const base = turns.value.length ? [...turns.value] : [...payload.turns.slice(0, -1)];
  if (latestUser?.role === "user" && base.at(-1)?.role !== "user") base.push(latestUser);
  else if (latestUser?.role === "user" && base.at(-1)?.role === "user" && base.at(-1)?.content !== latestUser.content) base.push(latestUser);
  if (previousAssistant && base.at(-1)?.role === "assistant") base.pop();
  turns.value = [...base, { role: "assistant", content: result.reply }];
  checklist.value = result.checklist ?? checklist.value;
  if (result.proposal && draft.value) {
    draft.value.proposal = result.proposal;
    draft.value.title = result.proposal.title;
  }
}
function restoreHistory(commands: CommandView[]) {
  history.value = commands;
  const completed = commands.filter(item => !item.undone && item.status === "completed" && item.output.interview && item.submission.payload.kind === "interview");
  turns.value = [];
  checklist.value = [];
  for (const command of completed) {
    const payload = command.submission.payload;
    if (payload.kind !== "interview" || !command.output.interview) continue;
    const lastUserTurn = payload.turns.at(-1);
    if (lastUserTurn?.role === "user") turns.value.push(lastUserTurn);
    turns.value.push({ role: "assistant", content: command.output.interview.reply });
    checklist.value = command.output.interview.checklist ?? checklist.value;
  }
  const pending = commands.filter(item => !item.undone && item.submission.payload.kind === "interview" && ["queued", "running"].includes(item.status)).at(-1);
  pendingCommand.value = pending ?? null;
  if (pending?.submission.payload.kind === "interview") {
    const lastUserTurn = pending.submission.payload.turns.at(-1);
    if (lastUserTurn?.role === "user" && turns.value.at(-1)?.content !== lastUserTurn.content) turns.value.push(lastUserTurn);
  }
}
async function load() {
  const generation = ++loadGeneration;
  busy.value = false;
  pendingCommand.value = null;
  if (props.draftId) {
    const loadedDraft = await api.get<SetupDraft>(`/api/drafts/${props.draftId}`);
    const commands = await api.get<CommandView[]>(`/api/sessions/${props.draftId}/commands`);
    if (generation !== loadGeneration) return;
    draft.value = loadedDraft;
    restoreHistory(commands);
    const lastUserTurn = commands.filter(item => !item.undone && item.submission.payload.kind === "interview")
      .map(item => item.submission.payload.kind === "interview" ? item.submission.payload.turns.at(-1) : undefined)
      .filter((turn): turn is Turn => !!turn && turn.role === "user").at(-1);
    const pendingAccepted = !!loadedDraft.pending_input && commands.some(item => !item.undone &&
      item.submission.payload.kind === "interview" && ["queued", "running", "completed"].includes(item.status) &&
      item.submission.payload.turns.at(-1)?.role === "user" && item.submission.payload.turns.at(-1)?.content === loadedDraft.pending_input);
    if (loadedDraft.pending_input && pendingAccepted && !pendingCommand.value) {
      loadedDraft.pending_input = "";
      loadedDraft.input_text = "";
      input.value = "";
      scheduleSave();
    } else if (loadedDraft.pending_input && !pendingAccepted) {
      input.value = loadedDraft.input_text || loadedDraft.pending_input;
      loadedDraft.pending_input = "";
      loadedDraft.input_text = input.value;
      scheduleSave();
    } else input.value = loadedDraft.input_text;
    const latestCompleted = commands.filter(item => !item.undone && item.status === "completed" && item.output.interview).at(-1);
    if (latestCompleted?.output.interview) checklist.value = latestCompleted.output.interview.checklist ?? checklist.value;
    if (pendingCommand.value) void monitorPending(pendingCommand.value);
  } else {
    draft.value = { id: crypto.randomUUID(), title: "新故事草稿", input_text: "", pending_input: "", proposal: null,
      editing_setup: false, edits: [], updated_at: new Date().toISOString(), save_event_id: 0, field_template_ids: [], field_template_set_id: null };
    turns.value = [];
    checklist.value = [];
    input.value = "";
    await persist();
    window.history.replaceState(window.history.state, "", `/#draft/${draft.value.id}`);
  }
}
onMounted(() => load().catch(error => say(error.message)));
onBeforeUnmount(() => { loadGeneration++; if (saveTimer) window.clearTimeout(saveTimer); });

function onSubmitted(command: CommandView, acceptedText = "") {
  pendingCommand.value = command;
  history.value = [...history.value.filter(item => item.submission.id !== command.submission.id), command];
  if (draft.value) {
    draft.value.pending_input = acceptedText || (command.submission.payload.kind === "interview" ? command.submission.payload.turns.at(-1)?.content ?? "" : "");
    draft.value.input_text = "";
    input.value = "";
    scheduleSave();
  }
}
function restoreInterviewInput(command: CommandView) {
  if (!draft.value || command.submission.payload.kind !== "interview") return;
  const lastTurn = command.submission.payload.turns.at(-1);
  if (lastTurn?.role !== "user") return;
  input.value = lastTurn.content;
  draft.value.input_text = lastTurn.content;
  draft.value.pending_input = "";
  scheduleSave();
}
async function waitForInterview(command: CommandView, generation = loadGeneration) {
  pendingCommand.value = command;
  busy.value = true;
  try {
    const completed = command.status === "completed" ? command : await api.wait(command.submission.id, () => undefined);
    if (generation !== loadGeneration || !draft.value) return;
    history.value = [...history.value.filter(item => item.submission.id !== completed.submission.id), completed];
    applyInterview(completed);
    draft.value.pending_input = "";
    draft.value.input_text = "";
    await flushSave();
    await reloadHistory();
  } catch (error) {
    if (generation === loadGeneration) {
      restoreInterviewInput(command);
      say(error instanceof Error ? error.message : "构思任务失败");
    }
    await reloadHistory().catch(() => undefined);
  } finally {
    if (generation === loadGeneration) { busy.value = false; pendingCommand.value = null; }
  }
}
async function monitorPending(command: CommandView) {
  busy.value = true;
  await waitForInterview(command, loadGeneration);
}
async function reloadHistory() {
  if (!draft.value) return;
  const commands = await api.get<CommandView[]>(`/api/sessions/${draft.value.id}/commands`);
  restoreHistory(commands);
}
async function interview() {
  if (!draft.value || busy.value || !input.value.trim()) return;
  const userText = input.value.trim();
  const nextTurns: Turn[] = [...turns.value, { role: "user", content: userText }];
  busy.value = true;
  draft.value.input_text = userText;
  draft.value.pending_input = userText;
  try {
    await flushSave();
    const command = await api.command({ kind: "interview", turns: nextTurns }, null, null, draft.value.id, () => undefined,
      submitted => onSubmitted(submitted, userText));
    await waitForInterview(command);
  } catch (error) {
    if (draft.value) { draft.value.pending_input = ""; draft.value.input_text = userText; }
    input.value = userText;
    await flushSave().catch(() => undefined);
    say(error instanceof Error ? error.message : "构思失败");
    busy.value = false;
    pendingCommand.value = null;
    await reloadHistory().catch(() => undefined);
  }
}
async function createStory() {
  if (!draft.value?.proposal || !premise.value.trim() || busy.value) return;
  busy.value = true;
  try {
    const worldFields: Schemas["Entry-Input"][] = [...draft.value.proposal.world.entries];
    const definition = fieldLibrary.value?.templates.find(item => item.builtin && item.scope === "world")?.fields?.[0];
    if (definition && !worldFields.some(item => item.definition?.id === definition.id)) {
      worldFields.push({ id: crypto.randomUUID(), name: definition.name, definition, value: premise.value.trim(), description: "", reason: "创建世界观" });
    }
    const proposal: Proposal = { ...draft.value.proposal, title: title.value.trim(), world: { entries: worldFields } };
    const result = await api.command({ kind: "create_story", confirmed: true, proposal, field_template_set_id: draft.value.field_template_set_id ?? null,
      field_template_ids: draft.value.field_template_set_id ? [] : draft.value.field_template_ids ?? [] }, null, null, draft.value.id, () => undefined);
    if (result.output.story_id) emit("created", result.output.story_id);
    else throw new Error("创建结果缺少作品 ID");
  } catch (error) { say(error instanceof Error ? error.message : "创建失败"); }
  finally { busy.value = false; }
}
</script>

<template>
  <main class="creation-page">
    <header class="work-heading"><div><span class="eyebrow">新作品构思</span><h1>先认识你的故事</h1><p class="muted">通过专门的故事访谈整理设定；确认前这些内容只是草稿。</p></div><div class="creation-header-actions"><NTag class="creation-save-state" size="small" round :bordered="false" :type="saveStatus === 'error' ? 'error' : 'success'" role="status">{{ saveStatus === "saving" ? "正在保存草稿…" : saveStatus === "error" ? "草稿保存失败" : "草稿已保存" }}</NTag><NButton quaternary size="small" @click="emit('back')">返回书架</NButton></div></header>
    <section class="creation-layout">
      <div class="creation-chat">
        <div class="chat-history" aria-live="polite">
          <article v-for="(turn, index) in turns" :key="index" :class="['chat-bubble', turn.role === 'user' ? 'user-bubble' : 'assistant-bubble']"><small>{{ turn.role === 'user' ? '你' : '构思助手' }}</small>{{ turn.content }}<ConversationLog v-if="turn.role === 'assistant' && turnCommand(index)" :command="turnCommand(index)!" /></article>
          <p v-if="pendingCommand" class="creation-pending" role="status">{{ pendingCommand.status === "queued" ? "访谈已排队，等待开始…" : "访谈正在处理中；离开后会自动恢复进度。" }} </p>
          <article v-for="command in failedCommands" :key="command.submission.id" class="creation-failure" role="alert"><strong>这轮访谈没有完成</strong><p>{{ command.error || "任务失败，可以重试或撤回。" }}</p><div></div></article>
          <div v-if="!turns.length && !pendingCommand" class="empty-state"><h2>故事的起点是什么？</h2><p>说说你想写的故事，AI 会协助梳理世界、人物和阅读偏好。</p></div>
        </div>
        <ConversationActions :key="draft?.id" :command="history.filter(item => !item.undone && item.submission.payload.kind === 'interview').at(-1)" :scope-key="`creation-${draft?.id}`" :disabled="busy || !!pendingCommand" @changed="load" />
        <form class="discussion-composer" @submit.prevent="interview"><NInput v-model:value="input" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" :disabled="busy" placeholder="例如：我想写一部关于记忆会被魔法交换的悬疑小说……" @input="draft && (draft.input_text = input); scheduleSave()"/><NButton type="primary" attr-type="submit" :disabled="busy || !input.trim()" :loading="busy">{{ busy ? "正在构思…" : "继续聊" }}</NButton></form>
      </div>
      <NCard class="proposal-card" :bordered="false"><template #header><div><span class="eyebrow">当前故事设定</span><p class="muted">可检查、可修改的提案；确认后才会创建正式作品。</p></div></template>
        <section v-if="checklist.length" class="creation-checklist" aria-label="构思进度"><h2>构思进度</h2><ul><li v-for="field in checklist" :key="field.key" :class="`field-${field.status}`"><span>{{ field.status === "defined" ? "✓" : field.status === "deferred" ? "—" : "○" }}</span><div><strong>{{ field.label }}</strong><small>{{ field.summary || (field.status === "defined" ? "已整理" : field.status === "deferred" ? "暂不设定" : "待讨论") }}</small></div></li></ul></section>
        <template v-if="currentProposal"><label>作品名<NInput v-model:value="title" maxlength="200" @input="scheduleSave"/></label><label>世界观与基本方向<NInput v-model:value="premise" type="textarea" :autosize="{ minRows: 5, maxRows: 12 }" @input="scheduleSave"/></label>
          <label v-if="draft && fieldLibrary">小说模板套装<NSelect v-model:value="draft.field_template_set_id" aria-label="小说模板套装" clearable :options="(fieldLibrary.template_sets ?? []).map(item => ({ value: item.id, label: item.name }))" placeholder="留空使用默认小说模板" @update:value="() => { if (draft) draft.field_template_ids = []; scheduleSave(); }" /></label>
          <p v-if="draft && fieldLibrary" class="notice">每部小说复制独立的字段结构。后续新增角色、章节和幕自动初始化，字段增删改不会修改原模板。</p>
          <p v-if="draft?.field_template_ids?.length && !draft.field_template_set_id" class="notice">此草稿保留了旧版分类模板选择；选择小说模板套装后会替换该选择。</p>
          <article class="proposal-summary"><h3>人物</h3><p>{{ currentProposal.characters.map(item => item.name).join("、") || "暂不设定，之后可以继续补充" }}</p><h3>阅读偏好</h3><p>{{ currentProposal.preferences.likes.map(item => item.name).join("、") || "暂不设定" }}</p><h3>避免的写法</h3><p>{{ currentProposal.preferences.dislikes.map(item => item.name).join("、") || "暂不设定" }}</p></article>
          <NButton type="primary" block :disabled="busy || !premise.trim()" :loading="busy" @click="createStory">确认设定并创建作品</NButton>
        </template><p v-else-if="!checklist.length" class="notice">聊过几轮后，提案会显示在这里。</p>
      </NCard>
    </section>
    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </main>
</template>

<style scoped>
.creation-header-actions{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap}.creation-save-state{color:var(--muted);font-size:.72rem}.creation-pending,.creation-failure{padding:.7rem .8rem;border:1px solid var(--line);border-radius:.65rem;background:var(--surface);color:var(--ink)}.creation-pending button,.creation-failure button{margin-left:.45rem}.creation-failure{border-color:color-mix(in srgb,var(--amber) 40%,var(--line))}.creation-failure p{font-size:.82rem;white-space:pre-wrap}.creation-failure>div{display:flex;gap:.4rem}.creation-checklist{padding:.8rem 0;border-bottom:1px solid var(--line);margin-bottom:.8rem}.creation-checklist h2{font:600 .92rem Georgia,serif;margin:0 0 .5rem}.creation-checklist ul{list-style:none;padding:0;margin:0;display:grid;gap:.4rem}.creation-checklist li{display:flex;gap:.5rem;align-items:flex-start}.creation-checklist li>span{font-weight:700;color:var(--muted)}.creation-checklist li.field-defined>span{color:var(--green)}.creation-checklist li div{display:grid;gap:.1rem}.creation-checklist li strong{font-size:.75rem}.creation-checklist li small{color:var(--muted);font-size:.69rem;line-height:1.4}
:global(:root[data-theme='dark'] .creation-pending),:global(:root[data-theme='dark'] .creation-failure){background:var(--surface);color:var(--ink);border-color:var(--line)}
.creation-page{width:min(1220px,calc(100% - 40px));margin:0 auto;padding:28px 0 48px;color:var(--text);}
.creation-page .work-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:0 0 18px;margin:0 0 18px;border-bottom:1px solid var(--border)}
.creation-page h1{margin:4px 0;font-size:clamp(24px,3vw,32px);font-weight:620;letter-spacing:-.035em}
.creation-page .work-heading p{margin:4px 0;color:var(--text-muted);font-size:13px}
.creation-header-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.creation-save-state{color:var(--text-muted);font-size:.72rem}
.creation-layout{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);gap:18px;align-items:start}
.creation-chat{min-width:0;display:flex;flex-direction:column;min-height:min(72vh,780px);border:1px solid var(--border);border-radius:12px;background:var(--surface);overflow:hidden}
.creation-chat .chat-history{flex:1;min-height:320px;max-height:none;overflow:visible;padding:18px}
.creation-chat .chat-bubble{max-width:85%;padding:12px 14px;border:1px solid var(--border);border-radius:12px;margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.creation-chat .chat-bubble small{display:block;margin-bottom:4px;color:var(--text-muted);font-size:11px}
.creation-chat .user-bubble{margin-left:auto;background:var(--accent-soft);border-color:color-mix(in srgb,var(--accent) 18%,var(--border))}
.creation-chat .assistant-bubble{background:var(--surface-muted)}
.creation-chat .empty-state{display:grid;place-content:center;min-height:250px;color:var(--text-muted);text-align:center}
.creation-chat .empty-state h2{margin:0 0 5px;color:var(--text);font-size:18px;font-weight:600}
.creation-chat .discussion-composer{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:9px;align-items:end;padding:12px;border-top:1px solid var(--border);background:var(--surface)}
.proposal-card{position:sticky;top:16px;align-self:start;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
.proposal-card :deep(.n-card-header){padding:15px 16px 9px}
.proposal-card :deep(.n-card__content){padding:8px 16px 16px}
.proposal-card :deep(.n-card-header__main){width:100%}
.proposal-card label{display:grid;gap:6px;margin:12px 0;color:var(--text-muted);font-size:12px}
.proposal-card .muted{margin:4px 0 0;color:var(--text-muted);font-size:12px}
.proposal-summary{border-top:1px solid var(--border);margin:15px 0;padding-top:6px}
.proposal-summary h3{margin:12px 0 3px;color:var(--accent);font-size:12px}
.proposal-summary p{margin:0;color:var(--text);font-size:13px}
.creation-pending,.creation-failure{padding:10px 12px;border:1px solid var(--border);border-radius:9px;background:var(--surface-muted);color:var(--text)}
.creation-pending{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12px}
.creation-failure{border-color:color-mix(in srgb,var(--danger) 35%,var(--border));background:color-mix(in srgb,var(--danger) 5%,var(--surface))}
.creation-failure p{font-size:12px;white-space:pre-wrap}
.creation-failure>div{display:flex;gap:6px;flex-wrap:wrap}
.creation-checklist{padding:8px 0 14px;border-bottom:1px solid var(--border);margin-bottom:10px}
.creation-checklist h2{margin:0 0 8px;font-size:13px;font-weight:620}
.creation-checklist ul{list-style:none;padding:0;margin:0;display:grid;gap:7px}
.creation-checklist li{display:flex;gap:9px;align-items:flex-start}
.creation-checklist li>span{width:18px;flex:none;text-align:center;color:var(--text-muted)}
.creation-checklist li.field-defined>span{color:var(--success)}
.creation-checklist li div{display:grid;gap:1px;min-width:0}
.creation-checklist li strong{font-size:12px}
.creation-checklist li small{color:var(--text-muted);font-size:11px;line-height:1.45}
@media(max-width:760px){.creation-page{width:calc(100% - 24px);padding-top:18px}.creation-page .work-heading{flex-direction:column;align-items:stretch}.creation-layout{grid-template-columns:1fr}.creation-chat{min-height:60vh}.creation-chat .chat-history{max-height:none}.proposal-card{position:static}.creation-chat .discussion-composer{grid-template-columns:1fr}.creation-chat .discussion-composer .n-button{justify-self:end}}
</style>
