<script setup lang="ts">
import { computed, onMounted, ref, toRaw } from "vue";
import { NButton, NInput } from "naive-ui";
import { api, latestEventId, type CommandPayload, type CommandView, type StoryView, type UndoState } from "../api/client";
import { operationInput } from "../ui/runPresentation";
const props = defineProps<{ command?: CommandView | null; scopeKey: string; disabled?: boolean; committed?: boolean }>();
const emit = defineEmits<{ changed: [] }>();
const busy = ref(false), editing = ref(false), message = ref(""), input = ref("");
const pending = ref<CommandView | null>(null), undone = ref(false);
const target = computed(() => pending.value ?? props.command);
const locked = computed(() => !!props.committed || target.value?.output.writing_draft?.status === 'confirmed');
const storage = computed(() => `ainovel.conversationAction.${props.scopeKey}`);
const regenerable = computed(() => !!target.value && ['generate','review_draft','rewrite','discussion','character_chat','interview'].includes(target.value.submission.payload.kind));
function persist() { if (pending.value) localStorage.setItem(storage.value, JSON.stringify({ command: pending.value, input: input.value, undone: undone.value })); }
onMounted(() => { try { const saved = JSON.parse(localStorage.getItem(storage.value) || 'null'); if (saved?.command) { pending.value = saved.command; input.value = saved.input; undone.value = saved.undone; editing.value = true; } } catch {} });
async function rollback(command: CommandView) {
  const submission = command.submission;
  const payload = submission.payload;
  const chat = payload.kind === 'discussion' || payload.kind === 'start_discussion' || payload.kind === 'character_chat' || payload.kind === 'start_character_chat';
  const scope = chat ? payload.session_id : submission.story_id ?? submission.session_id;
  if (!scope) throw new Error("会话作用域缺失");
  if (command.status === 'failed' && !chat) {
    const head = submission.story_id ? await api.get<StoryView>(`/api/stories/${submission.story_id}`) : null;
    await api.command({ kind: 'recover_command', target_command_id: submission.id, expected_event_id: await latestEventId(submission.id), decision: 'withdraw' }, submission.story_id, head?.revision_id ?? null, submission.session_id, () => undefined);
    return;
  }
  let undoScope = scope;
  let state = await api.get<UndoState>(`/api/undo/${scope}`);
  if (chat && !state.available && submission.story_id) {
    state = await api.get<UndoState>(`/api/undo/${submission.story_id}`);
    undoScope = submission.story_id;
  }
  if (!state.available || state.command_id !== submission.id) throw new Error("其他会话或资料有后续操作，请先回退后续操作，避免覆盖其修改。");
  const head = submission.story_id ? await api.get<StoryView>(`/api/stories/${submission.story_id}`) : null;
  await api.command({ kind: 'undo', expected_operation_id: state.operation_id!, target_command_id: submission.id, scope_id: undoScope }, submission.story_id, head?.revision_id ?? null, submission.session_id, () => undefined);
}
async function remove() {
  if (!target.value || busy.value || props.disabled) return;
  busy.value = true;
  try { await rollback(target.value); emit('changed'); message.value = "已回退该轮对话及其关联操作"; }
  catch (cause) { message.value = cause instanceof Error ? cause.message : '回退失败'; }
  finally { busy.value = false; }
}
function cancel() {
  pending.value = null; editing.value = false; undone.value = false; localStorage.removeItem(storage.value); emit('changed');
}
function edit() {
  if (!target.value || locked.value) return;
  pending.value = target.value; input.value = operationInput(target.value); editing.value = true; persist();
}
async function regenerate(modified = false) {
  if (!target.value || busy.value || props.disabled) return;
  busy.value = true;
  const original = target.value;
  try {
    let payload = JSON.parse(JSON.stringify(toRaw(original.submission.payload))) as CommandPayload;
    if (!pending.value) { pending.value = original; input.value = operationInput(original); }
    if (modified && locked.value) throw new Error("本幕已有正文，不能修改本轮设定，只能重新生成文本。");
    if (modified) {
      if (payload.kind === 'generate' || payload.kind === 'rewrite') payload.instruction = input.value;
      else if (payload.kind === 'discussion' || payload.kind === 'character_chat') payload.message = input.value;
      else if (payload.kind === 'interview') { const last = payload.turns?.filter(turn => turn.role === 'user').at(-1); if (last) last.content = input.value; }
    }
    if (!undone.value) { await rollback(original); undone.value = true; persist(); }
    const storyId = original.submission.story_id;
    const head = storyId ? await api.get<StoryView>(`/api/stories/${storyId}`) : null;
    if (payload.kind === 'review_draft') {
      const generation = await api.get<CommandView>(`/api/commands/${payload.generation_command_id}`);
      const draft = generation.output.writing_draft;
      if (!draft) throw new Error("待确认草稿不存在");
      payload = { ...payload, text_only: locked.value, expected_draft_version: draft.version, decision: 'revise', feedback: modified ? input.value : '仅重新生成正文，保留原有设定、创作要求和规划。', selection: null };
    }
    await api.command(payload, storyId, payload.kind === 'character_chat' ? original.submission.base_revision_id : head?.revision_id ?? null, original.submission.session_id, () => undefined, () => {
      if (payload.kind === 'discussion') localStorage.removeItem(`ainovel.discussionDraft.${storyId}.${payload.session_id}`);
      if (payload.kind === 'character_chat') localStorage.removeItem(`ainovel.characterDraft.${payload.session_id}`);
      pending.value = null; undone.value = false; editing.value = false; localStorage.removeItem(storage.value);
    });
    emit('changed'); message.value = "已重新生成，正文仍需确认";
  } catch (cause) { editing.value = true; persist(); message.value = cause instanceof Error ? cause.message : '重新生成失败'; emit('changed'); }
  finally { busy.value = false; }
}
async function retry() {
  if (!target.value || busy.value) return;
  if (['discussion', 'character_chat'].includes(target.value.submission.payload.kind)) { await regenerate(); return; }
  busy.value = true;
  try {
    const command = target.value;
    const head = command.submission.story_id ? await api.get<StoryView>(`/api/stories/${command.submission.story_id}`) : null;
    await api.command({ kind:'recover_command', target_command_id:command.submission.id, expected_event_id:await latestEventId(command.submission.id), decision:'retry' }, command.submission.story_id, head?.revision_id ?? null, command.submission.session_id, () => undefined);
    emit('changed');
  } catch(cause) { message.value = cause instanceof Error ? cause.message : '重试失败'; }
  finally { busy.value = false; }
}
</script>
<template><div v-if="target" class="conversation-actions">
  <div class="buttons"><NButton size="small" :disabled="disabled || busy || undone" @click="remove">回退</NButton><NButton v-if="regenerable" size="small" :disabled="disabled || busy" @click="regenerate()">{{ locked ? '重新生成文本' : '重新生成' }}</NButton><NButton v-if="regenerable && !locked" size="small" :disabled="disabled || busy || undone" @click="edit">修改</NButton><NButton size="small" :disabled="disabled || busy || undone" @click="remove">删除</NButton><NButton v-if="target.status === 'failed'" size="small" :disabled="disabled || busy" @click="retry">重试</NButton></div>
  <p v-if="locked">本幕已有正文，原轮次设定已锁定。</p><div v-if="editing && !locked" class="editor"><NInput v-model:value="input" type="textarea" aria-label="修改最后一条消息" @update:value="persist"/><NButton :disabled="disabled || busy || !input.trim()" @click="regenerate(true)">{{ undone ? '继续提交' : '保存修改并重新生成' }}</NButton><NButton :disabled="busy" @click="cancel">取消修改</NButton></div><p v-if="message" role="status">{{ message }}</p>
</div></template>
<style scoped>.conversation-actions{margin-top:12px;display:grid;gap:10px}.buttons{display:flex;gap:7px;flex-wrap:wrap}.editor{display:grid;gap:8px}p{font-size:12px;color:var(--text-muted);margin:0;white-space:pre-wrap}</style>
