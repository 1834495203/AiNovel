<script setup lang="ts">
import ConversationActions from "./ConversationActions.vue";
import ConversationLog from "./ConversationLog.vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { NButton, NCard, NInput, NSelect, NTag, NModal } from "naive-ui";
import { routeQuery, replaceQuery } from "../ui/navigation";
import { api, closeStream, type CharacterSnapshot, type CommandPayload, type CommandView, type EventData, type Story, type StoryView } from "../api/client";

const props = defineProps<{ mode: "characters" | "character" }>();
const query = routeQuery();
const storyId = ref(query.get("story") ?? "");
const revisionId = ref(query.get("revision") ?? "");
const sceneId = ref(query.get("scene") ?? "");
const characterId = ref(query.get("character") ?? "");
const story = ref<Story | null>(null);
const stageId = ref(sceneId.value);
const snapshots = ref<CharacterSnapshot[]>([]);
const detail = ref<CharacterSnapshot | null>(null);
const currentSnapshot = ref<CharacterSnapshot | null>(null);
const history = ref<CommandView[]>([]);
const answer = ref("");
const busy = ref(false);
const status = ref("");
const sessionId = ref("");
const sessionIds = ref<string[]>([]);
const monitoring = new Map<string, Promise<CommandView>>();
const revisionView = ref<StoryView | { id: string; story_id: string; story: Story } | null>(null);
const activeCharacter = computed(() => story.value?.characters.find(character => character.id === characterId.value) ?? null);
const serverBusy = computed(() => history.value.some(command => ["queued", "running"].includes(command.status)));
const visibleScenes = computed(() => story.value?.scenes ?? []);
const stageOptions = computed(() => [
  { label: "最新状态", value: "" },
  ...visibleScenes.value.map(scene => ({ label: `${story.value?.chapters.find(chapter => chapter.id === scene.chapter_id)?.title ?? "章节"} / ${scene.title}`, value: scene.id ?? "" })),
]);

async function loadStory() {
  if (revisionId.value) {
    const revision = await api.get<{ id: string; story_id: string; story: Story }>(`/api/revisions/${revisionId.value}`);
    revisionView.value = revision; story.value = revision.story; storyId.value = revision.story_id;
  } else if (storyId.value) {
    const view = await api.get<StoryView>(`/api/stories/${storyId.value}`);
    revisionView.value = view; story.value = view.story; revisionId.value = view.revision_id;
  } else throw new Error("请从作品页面打开人物与阶段信息。");
}
async function loadCards() {
  if (!revisionId.value || !story.value) return;
  const queryString = stageId.value ? `?scene_id=${stageId.value}` : "";
  snapshots.value = await api.get<CharacterSnapshot[]>(`/api/revisions/${revisionId.value}/characters${queryString}`);
}
type SummaryEntry = { name: string; value?: string | number | boolean | null; description?: string; reason?: string; children?: SummaryEntry[] };
function itemSummary(items: SummaryEntry[]): string[] {
  return items.flatMap(item => [`${item.name}${item.value != null ? `：${item.value}` : ""}${item.description ? ` · ${item.description}` : ""}${item.reason ? `（依据：${item.reason}）` : ""}`,
    ...itemSummary((item.children || []) as Parameters<typeof itemSummary>[0])]);
}
async function loadCharacterChat() {
  await loadStory();
  if (!story.value || !revisionId.value || !characterId.value || !sceneId.value) throw new Error("角色阶段信息不完整。");
  currentSnapshot.value = await api.get<CharacterSnapshot>(`/api/revisions/${revisionId.value}/characters/${characterId.value}/snapshots/${sceneId.value}`);
  const list = await api.get<CommandView[]>(`/api/stories/${storyId.value}/commands`);
  const allHistory = list.filter(command => !command.undone && command.submission.base_revision_id === revisionId.value
    && ["character_chat", "start_character_chat"].includes(command.submission.payload.kind)
    && "character_id" in command.submission.payload && command.submission.payload.character_id === characterId.value
    && "scene_id" in command.submission.payload && command.submission.payload.scene_id === sceneId.value);
  const key = `ainovel.characterSession.${revisionId.value}.${characterId.value}.${sceneId.value}`;
  sessionIds.value = [...new Set(allHistory.map(command => command.submission.payload.kind === "character_chat"
    ? command.submission.payload.session_id : command.output.chat_session_id).filter((id): id is string => !!id))];
  const requested = routeQuery().get("session");
  const stored = localStorage.getItem(key);
  sessionId.value = [requested, stored, sessionIds.value.at(-1)].find(id => id && sessionIds.value.includes(id)) ?? "";
  if (!sessionId.value) {
    await startSession();
    return;
  }
  localStorage.setItem(key, sessionId.value);
  const query = routeQuery(); query.set("session", sessionId.value); replaceQuery(query);
  history.value = allHistory.filter(command => (command.submission.payload.kind === "character_chat"
    ? command.submission.payload.session_id : command.output.chat_session_id) === sessionId.value);
  answer.value = localStorage.getItem(`ainovel.characterDraft.${sessionId.value}`) ?? "";
  for (const command of history.value.filter(item => ["queued", "running"].includes(item.status))) watchCommand(command.submission.id);
}
async function startSession() {
  if (busy.value || serverBusy.value) return;
  busy.value = true;
  const id = crypto.randomUUID();
  try {
    const started = await api.command({ kind: "start_character_chat", character_id: characterId.value, scene_id: sceneId.value, session_id: id },
      storyId.value, revisionId.value, null, () => undefined);
    sessionId.value = started.output.chat_session_id ?? id;
    sessionIds.value = [...new Set([...sessionIds.value, sessionId.value])];
    localStorage.setItem(`ainovel.characterSession.${revisionId.value}.${characterId.value}.${sceneId.value}`, sessionId.value);
    const query = routeQuery(); query.set("session", sessionId.value); replaceQuery(query);
    await loadCharacterChat();
  } finally { busy.value = false; }
}
onMounted(async () => {
  try {
    if (props.mode === "characters") { await loadStory(); await loadCards(); }
    else await loadCharacterChat();
  } catch (error) { status.value = error instanceof Error ? error.message : "加载失败"; }
});
function watchCommand(id: string) {
  if (monitoring.has(id)) return;
  const pending = api.wait(id, (event: EventData) => { if (event.trace?.summary) status.value = event.trace.summary; })
    .finally(() => { monitoring.delete(id); loadCharacterChat().catch(problem => status.value = problem.message); });
  monitoring.set(id, pending); pending.catch(problem => status.value = problem instanceof Error ? problem.message : "后台任务失败");
}
onUnmounted(() => { for (const id of monitoring.keys()) closeStream(id); monitoring.clear(); });

async function send() {
  const message = answer.value.trim(); if (!message || busy.value || serverBusy.value || !currentSnapshot.value) return;
  busy.value = true;
  try {
    await api.command({ kind: "character_chat", character_id: characterId.value, scene_id: sceneId.value,
      session_id: sessionId.value, message }, storyId.value, revisionId.value, null, event => { if (event.trace?.summary) status.value = event.trace.summary; }, () => { answer.value = ""; localStorage.removeItem(`ainovel.characterDraft.${sessionId.value}`); });
    await loadCharacterChat();
  } catch (error) { answer.value = message; localStorage.setItem(`ainovel.characterDraft.${sessionId.value}`, message); status.value = error instanceof Error ? error.message : "对话失败"; }
  finally { busy.value = false; }
}
function saveAnswerDraft() { localStorage.setItem(`ainovel.characterDraft.${sessionId.value}`, answer.value); }
async function selectSession(id: string) {
  sessionId.value = id;
  const query = routeQuery(); query.set("session", id); replaceQuery(query);
  localStorage.setItem(`ainovel.characterSession.${revisionId.value}.${characterId.value}.${sceneId.value}`, id);
  await loadCharacterChat();
}
</script>

<template>
  <main v-if="story && mode === 'characters'" class="characters-page">
    <header class="work-heading"><div><span class="eyebrow">故事资料 · 人物</span><h1>{{ story.title }} · 人物与阶段</h1><p class="muted">档案是人物的长期资料；选择阶段查看当时的状态、心理和经历。</p></div><NButton tag="a" secondary :href="`/#story/${story.id}`">回到正文</NButton></header>
    <label class="stage-picker">查看阶段<NSelect v-model:value="stageId" :options="stageOptions" @update:value="loadCards" /></label>
    <nav class="character-index"><a v-for="snapshot in snapshots" :key="snapshot.character_id" :href="`#character-${snapshot.character_id}`">{{ story.characters.find(character => character.id === snapshot.character_id)?.name }}</a></nav>
    <div v-if="snapshots.length" class="character-grid">
      <NCard v-for="snapshot in snapshots" :id="`character-${snapshot.character_id}`" :key="snapshot.character_id" class="character-card" :bordered="true"><template #header><div class="character-card-heading"><div><span class="eyebrow">{{ stageId ? visibleScenes.find(scene => scene.id === stageId)?.title : "最新状态" }}</span><h2>{{ story.characters.find(character => character.id === snapshot.character_id)?.name }}</h2></div><NButton tag="a" type="primary" size="small" :href="`/#character?story=${storyId}&revision=${revisionId}&character=${snapshot.character_id}&scene=${stageId || visibleScenes.at(-1)?.id || ''}`">聊聊此时的角色</NButton></div></template>
        <section class="character-card-preview"><h3>状态与心理</h3><p v-for="(item, index) in itemSummary(snapshot.state.entries).slice(0, 3)" :key="index">{{ item }}</p><p v-if="!snapshot.state.entries.length" class="muted">暂无状态记录</p></section>
        <div class="character-card-footer"><span>{{ snapshot.profile.entries.length }} 项档案 · {{ snapshot.experiences.length }} 段经历</span><NButton secondary size="small" @click="detail = snapshot">查看完整档案</NButton></div>
      </NCard>
    </div>
    <NModal :show="!!detail" preset="card" class="character-detail-dialog" :title="story.characters.find(c => c.id === detail?.character_id)?.name || '完整档案'" :style="{ width: 'min(760px, calc(100vw - 32px))' }" @update:show="value => { if (!value) detail = null; }"><div v-if="detail" class="character-detail-body">        <section class="character-stage-state"><h3>此时的状态与心理</h3><p v-for="(item, index) in itemSummary(detail.state.entries)" :key="index">{{ item }}</p><p v-if="!detail.state.entries.length" class="muted">本阶段尚无状态记录。</p></section>
        <section class="character-profile"><h3>完整档案</h3><p v-for="(item, index) in itemSummary(detail.profile.entries)" :key="index">{{ item }}</p><p v-if="!detail.profile.entries.length" class="muted">尚无档案条目。</p></section>
        <section><h3>称呼与自定义记录</h3><p v-for="(item, index) in itemSummary([...detail.aliases, ...detail.custom_fields])" :key="index">{{ item }}</p><p v-if="!detail.aliases.length && !detail.custom_fields.length" class="muted">尚无记录。</p></section>
        <details><summary>经历（{{ detail.experiences.length }}）</summary><article v-for="experience in detail.experiences" :key="experience.id" class="experience-row"><strong>{{ experience.event }}</strong><small>{{ visibleScenes.find(scene => scene.id === experience.scene_id)?.title || "旧章节" }}</small><p>{{ experience.keywords.join("、") }}</p><p v-for="(entry, index) in itemSummary(experience.entries)" :key="index">{{ entry }}</p></article></details>
        <p class="muted">经历和状态是这个阶段已经发生的事实；尚未发生的剧情留在故事计划中。</p>
</div></NModal>
    <p v-if="!snapshots.length" class="notice">{{ status || "这个阶段还没有出场角色。" }}</p>
  </main>
  <main v-else-if="mode === 'character' && story && activeCharacter && currentSnapshot" class="character-chat-page">
    <header class="work-heading"><div><span class="eyebrow">固定故事阶段 · 不改变正文或角色资料</span><h1>与 {{ activeCharacter.name }} 聊聊</h1><p class="muted">{{ story.title }} · {{ visibleScenes.find(scene => scene.id === sceneId)?.title || "当前阶段" }}</p></div><div class="actions"><NButton tag="a" secondary :href="`/#characters?story=${storyId}&revision=${revisionId}&character=${characterId}&scene=${sceneId}`">查看人物档案</NButton></div></header>
    <NCard size="small" class="stage-context"><template #header><NTag size="small" type="info" :bordered="false">此时的状态与心理</NTag></template><p v-for="(item, index) in itemSummary(currentSnapshot.state.entries)" :key="index">{{ item }}</p><p v-if="!currentSnapshot.state.entries.length" class="muted">本阶段暂无状态记录。</p></NCard>
    <div class="character-sessions"><NButton type="primary" size="small" :disabled="busy || serverBusy" @click="startSession()">新会话</NButton><NButton v-for="(id, index) in sessionIds" :key="id" :type="id === sessionId ? 'primary' : 'default'" secondary size="small" :disabled="busy" @click="selectSession(id)">会话 {{ index + 1 }}</NButton></div>
    <section class="role-chat-history" aria-live="polite"><article v-for="command in history.filter(item => item.output.chat || item.submission.payload.kind === 'character_chat')" :key="command.submission.id">
      <div v-if="command.submission.payload.kind === 'character_chat'" class="chat-bubble user-bubble"><small>你</small>{{ command.submission.payload.message }}</div>
      <div v-if="command.output.chat" class="chat-bubble assistant-bubble"><small>{{ activeCharacter.name }}</small>{{ command.output.chat.reply }}</div>
      <div v-else-if="command.status === 'failed'" class="notice warning">角色对话失败：{{ command.error }}<div class="actions"></div></div>
      <ConversationLog :command="command" />
    </article><p v-if="!history.some(command => command.output.chat)" class="muted">你可以问问这个阶段的角色如何看待某件事。</p></section>
    <ConversationActions :key="sessionId" :command="history.filter(item => !item.undone && item.submission.payload.kind === 'character_chat').at(-1)" :scope-key="`character-${sessionId}`" :disabled="busy || serverBusy" @changed="loadCharacterChat" />
    <form class="discussion-composer" @submit.prevent="send"><NInput v-model:value="answer" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" :disabled="busy || serverBusy" placeholder="向角色提问……" @input="saveAnswerDraft" /><NButton type="primary" attr-type="submit" :disabled="busy || serverBusy || !answer.trim()" :loading="busy || serverBusy">发送</NButton><small class="muted">角色使用所选阶段的档案与记忆；聊天不会写入小说。</small></form>
    <p v-if="status" class="muted" role="status">{{ status }}</p>
  </main>
  <main v-else class="loading-state"><p>{{ status || "正在读取人物资料…" }}</p><a href="/">返回书架</a></main>
</template>

<style scoped>
.characters-page,.character-chat-page{width:100%;margin:0;padding:28px 32px 48px;color:var(--text)}
.work-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin:0 0 18px;padding:0 0 17px;border-bottom:1px solid var(--border)}
.work-heading h1{margin:4px 0;font-size:clamp(23px,3vw,30px);font-weight:620;letter-spacing:-.03em}
.work-heading p{margin:4px 0;color:var(--text-muted);font-size:13px}
.stage-picker{display:grid;grid-template-columns:auto minmax(220px,460px);align-items:center;gap:12px;margin:15px 0;color:var(--text-muted);font-size:12px}
.character-index{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}
.character-index a{padding:5px 10px;border:1px solid var(--border);border-radius:99px;text-decoration:none;color:var(--accent);background:var(--surface);font-size:12px}
.character-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr));gap:13px;align-items:start}
.character-card{min-width:0;border-color:var(--border);border-radius:12px;background:var(--surface)}
.character-card :deep(.n-card-header){padding:15px 16px 10px}
.character-card :deep(.n-card__content){padding:0 16px 16px}
.character-card-heading{display:flex;width:100%;align-items:center;justify-content:space-between;gap:12px}
.character-card-heading h2{margin:3px 0 0;font-size:18px;font-weight:620}
.character-card section{padding:11px 0;border-top:1px solid var(--border)}
.character-card h3{margin:0 0 6px;color:var(--accent);font-size:12px;font-weight:650}
.character-card p{margin:4px 0;color:var(--text);font-size:12px;line-height:1.6;white-space:pre-wrap}
.character-card .muted,.muted{color:var(--text-muted)}
.character-card details{padding-top:10px;border-top:1px solid var(--border)}
.character-card summary{cursor:pointer;color:var(--accent);font-size:12px}
.experience-row{padding:9px 0;border-top:1px solid var(--border)}
.experience-row small{display:block;color:var(--text-muted)}
.stage-context{margin:0 0 14px;border-color:var(--border);background:var(--surface-muted)}
.stage-context :deep(.n-card-header){padding:9px 12px 0}
.stage-context :deep(.n-card__content){padding:3px 12px 10px}
.stage-context p{margin:3px 0;color:var(--text);font-size:12px}
.character-sessions{display:flex;align-items:center;gap:6px;overflow-x:auto;padding:7px 0;border-bottom:1px solid var(--border)}
.character-sessions .n-button{flex:none}
.role-chat-history{min-height:220px;max-height:calc(100vh - 360px);overflow:auto;padding:10px 2px 18px}
.role-chat-history>article{margin:10px 0}
.chat-bubble{max-width:86%;margin:8px 0;padding:12px 14px;border:1px solid var(--border);border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.chat-bubble small{display:block;margin-bottom:4px;color:var(--text-muted);font-size:11px}
.user-bubble{margin-left:auto;background:var(--accent-soft);border-color:color-mix(in srgb,var(--accent) 20%,var(--border))}
.assistant-bubble{background:var(--surface)}
.assistant-bubble .n-button{display:flex;margin-top:6px}
.discussion-composer{position:sticky;bottom:0;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:8px;padding:11px 0;background:var(--app-bg);border-top:1px solid var(--border)}
.discussion-composer small{grid-column:1/-1;color:var(--text-muted);font-size:11px}
.character-chat-page .actions{display:flex;gap:6px;flex-wrap:wrap}
.notice{padding:12px;border:1px solid var(--border);border-radius:9px;background:var(--surface);color:var(--text)}
.notice.warning{border-color:color-mix(in srgb,var(--warning) 40%,var(--border));background:color-mix(in srgb,var(--warning) 7%,var(--surface))}
.actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
@media(max-width:640px){.characters-page,.character-chat-page{width:calc(100% - 24px);padding-top:18px}.work-heading{flex-direction:column;align-items:stretch}.stage-picker{grid-template-columns:1fr;gap:5px}.character-grid{grid-template-columns:1fr}.character-card-heading{align-items:flex-start;flex-direction:column}.role-chat-history{max-height:none}.discussion-composer{grid-template-columns:1fr}.discussion-composer .n-button{justify-self:end}}
</style>

<style scoped>
.character-card-preview p { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.character-card-preview { min-height:150px; }
.character-card-footer { display:flex; align-items:center; justify-content:space-between; gap:8px; color:var(--text-muted); font-size:11px; }
.character-detail-body { max-height:70dvh; overflow:auto; padding-right:12px; line-height:1.8; }
.character-detail-body section { border-bottom:1px solid var(--border); padding:12px 0; }
.character-detail-body h3 { font-size:14px; color:var(--accent); }
.character-detail-body p { font-size:13px; white-space:pre-wrap; }
</style>
