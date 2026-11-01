<script setup lang="ts">
import ConversationActions from "./ConversationActions.vue";
import ConversationLog from "./ConversationLog.vue";
import { computed, onMounted, onUnmounted, ref, toRaw, watch } from "vue";
import { api, closeStream, type CommandPayload, type CommandView, type EventData, type RunOverview, type Schemas, type StoryView } from "../api/client";
import { NButton, NDrawer, NDrawerContent, NEmpty, NInput, NSelect } from "naive-ui";
import PromptTemplateSettings from "./PromptTemplateSettings.vue";
import NovelReader from "./NovelReader.vue";
import StoryMaterials from "./StoryMaterials.vue";
import { navigate } from "../ui/navigation";
import { operationLabel, operationInput, operationOutput, statusLabel } from "../ui/runPresentation";

const props = defineProps<{ storyId: string }>();
const emit = defineEmits<{ back: []; openDiscussion: [storyId: string]; openCharacters: [storyId: string] }>();
const view = ref<StoryView | null>(null);
const commands = ref<CommandView[]>([]);
const run = ref<RunOverview | null>(null);
const busy = ref(false);
const currentStep = ref("");
const input = ref("");
const feedback = ref("");
const chatOpen = ref(false);
const novelOpen = ref(true);
const directoryOpen = ref(false);
const selectedScene = ref(localStorage.getItem(`ainovel.readerScene.${props.storyId}`) || "");
const recordFilter = ref("all");
const toast = ref("");
const templatesOpen = ref(false);
const materialsOpen = ref(false);
const selectedTemplates = ref<Schemas["PromptTemplateSelection"][]>([]);
const characterDraft = ref<Schemas["CharacterSpec-Output"] | null>(null);
const monitoring = new Map<string, Promise<CommandView>>();
let overviewRefreshTimer = 0;
const activeRun = computed(() => commands.value.slice().reverse().find(command =>
  !command.undone && command.status === "waiting" && (command.output.writing_draft?.status === "pending" || command.output.need || (command.output.character && command.output.approval_phase === "profile"))) ?? null);
const failedRun = computed(() => commands.value.slice().reverse().find(command => !command.undone && command.status === "failed") ?? null);
const pendingDraft = computed(() => activeRun.value?.output.writing_draft ?? null);
const draftParagraphs = computed(() => pendingDraft.value?.content.text.split(/\n+/).filter(paragraph => paragraph.trim()) ?? []);
const staleDraft = computed(() => !!pendingDraft.value && (run.value?.draft_stale ?? (!!activeRun.value && !!view.value &&
  activeRun.value.submission.base_revision_id !== view.value.revision_id)));
const serverBusy = computed(() => commands.value.some(command => !command.undone && ["queued", "running"].includes(command.status)));
const conversationHistory = computed(() => commands.value.filter(item => !item.undone && !['discussion','start_discussion','character_chat','start_character_chat','undo','recover_command','save_setup_draft'].includes(item.submission.payload.kind)));
async function conversationChanged() { try { await refresh(); } catch { emit('back'); } }

watch(() => activeRun.value?.output.character, character => { characterDraft.value = character ? structuredClone(toRaw(character)) : null; }, { immediate: true });

const notify = (message: string) => { toast.value = message; window.setTimeout(() => { if (toast.value === message) toast.value = ""; }, 6500); };
const phaseLabels: Record<string, string> = {
  analyze: "理解创作要求", planning_decision: "规划本幕", planning: "规划本幕", recall: "查找前文",
  characters: "确认相关人物", writing: "生成正文", review: "检查草稿", draft_review: "检查草稿",
  finalize: "整理故事资料", extraction: "整理故事资料", index: "提交版本并建立索引",
  search_story: "查找相关前文", update_story_settings: "更新故事资料",
};
const event = (item: EventData) => {
  if (item.type === "step.started" && item.trace) {
    currentStep.value = phaseLabels[item.trace.node] || item.trace.summary || phaseLabels[item.trace.node.replaceAll("-", "_")] || "正在处理当前步骤";
  }
  if (item.type === "connection.retrying") currentStep.value = "连接恢复中，任务仍在后台运行…";
  if (overviewRefreshTimer) window.clearTimeout(overviewRefreshTimer);
  overviewRefreshTimer = window.setTimeout(() => {
    api.get<RunOverview>(`/api/runs/${item.command_id}`).then(value => { run.value = value; }).catch(() => undefined);
  }, 350);
};

function watchCommand(commandId: string) {
  if (monitoring.has(commandId)) return;
  const watching = api.wait(commandId, event).finally(() => {
    monitoring.delete(commandId);
    refresh().catch(problem => notify(problem.message));
  });
  monitoring.set(commandId, watching);
  watching.catch(problem => notify(problem instanceof Error ? problem.message : "后台任务失败"));
}

async function refresh() {
  const loaded = await api.get<StoryView>(`/api/stories/${props.storyId}`);
  const history = await api.get<CommandView[]>(`/api/stories/${props.storyId}/commands`);
  view.value = loaded;
  if (!loaded.story.scenes.some(scene => scene.id === selectedScene.value)) selectedScene.value = loaded.story.scenes[0]?.id || "";
  commands.value = history;
  const selected = history.slice().reverse().find(command => !command.undone && ["waiting", "running", "queued"].includes(command.status))
    ?? history.slice().reverse().find(command => !command.undone && ["generate", "review_draft", "discussion", "update_story", "direction", "reindex", "rewrite", "accept_rewrite"].includes(command.submission.payload.kind));
  run.value = selected ? await api.get<RunOverview>(`/api/runs/${selected.submission.id}`).catch(() => null) : null;
  for (const command of history.filter(item => ["queued", "running"].includes(item.status))) {
    watchCommand(command.submission.id);
  }
}
onMounted(() => {
  const saved = localStorage.getItem(`ainovel.promptSelection.story.${props.storyId}`);
  if (saved) { try { selectedTemplates.value = JSON.parse(saved) as Schemas["PromptTemplateSelection"][]; } catch { selectedTemplates.value = []; } }
  input.value = localStorage.getItem(`ainovel.composer.${props.storyId}`) || "";
  refresh().catch(error => notify(error.message));
});
onUnmounted(() => { for (const id of monitoring.keys()) closeStream(id); monitoring.clear(); if (overviewRefreshTimer) window.clearTimeout(overviewRefreshTimer); });

async function command(payload: CommandPayload, sessionId: string | null = null, revisionId = view.value?.revision_id ?? null) {
  if (!view.value) throw new Error("请先打开作品。");
  if (serverBusy.value && !["review_draft", "resolve_character", "undo"].includes(payload.kind)) throw new Error("已有任务在后台运行，请等待完成后再提交。");
  const answer = await api.command(payload, props.storyId, revisionId, sessionId, event, () => {
    if (payload.kind === "generate") {
      input.value = ""; localStorage.removeItem(`ainovel.composer.${props.storyId}`);
      selectedTemplates.value = []; localStorage.removeItem(`ainovel.promptSelection.story.${props.storyId}`);
    }
  });
  await refresh();
  if (answer.status === "waiting") requestAnimationFrame(showDecision);
  return answer;
}
async function write() {
  const instruction = input.value.trim();
  if (!instruction || busy.value || serverBusy.value || activeRun.value) return;
  busy.value = true; currentStep.value = "任务已提交，等待后台开始…";
  const original = instruction;
  localStorage.setItem(`ainovel.composer.${props.storyId}`, instruction);
  try {
    const result = await command({ kind: "generate", instruction: original, new_chapter: false,
      ...(selectedTemplates.value.length ? { prompt_templates: selectedTemplates.value } : {}) });
    if (result.status === "waiting" && result.output.need) notify("需要确认新角色，故事尚未写入。");
    else if (result.output.writing_draft?.status === "pending") notify("草稿已就绪，请检查后确认。");
    else notify("本轮已完成并保存。");
  } catch (error) { input.value = original; notify(error instanceof Error ? error.message : "创作失败"); }
  finally { busy.value = false; currentStep.value = ""; }
}
async function newLatestDraft() {
  const old = activeRun.value;
  if (!old || !view.value || busy.value || serverBusy.value) return;
  const priorInstruction = old.submission.payload.kind === "generate" ? old.submission.payload.instruction : "";
  const instruction = `请依据当前最新故事资料重新创作。旧草稿仅供参考，不要沿用与新资料冲突的内容。原始写作要求：${priorInstruction}\n\n旧草稿摘要：${pendingDraft.value?.content.summary || pendingDraft.value?.content.text.slice(0, 500) || "无"}`;
  busy.value = true;
  try {
    const result = await command({ kind: "generate", instruction, new_chapter: false,
      ...(selectedTemplates.value.length ? { prompt_templates: selectedTemplates.value } : {}) });
    notify(result.output.writing_draft?.status === "pending" ? "已基于最新资料另建草稿；旧草稿仍保留为历史候选。" : "新任务已提交。旧草稿仍保留为历史候选。");
  } catch (problem) { notify(problem instanceof Error ? problem.message : "新草稿创建失败"); }
  finally { busy.value = false; }
}
async function review(decision: "confirm" | "discard" | "revise", text = "") {
  if (!activeRun.value || !pendingDraft.value || busy.value) return;
  if (decision === "confirm" && staleDraft.value) return;
  busy.value = true;
  try {
    await command({ kind: "review_draft", text_only: false, generation_command_id: pendingDraft.value.generation_command_id,
      expected_draft_version: pendingDraft.value.version, decision, feedback: text, selection: null }, null,
    staleDraft.value ? activeRun.value!.submission.base_revision_id ?? null : view.value!.revision_id);
    feedback.value = "";
    notify(decision === "confirm" ? "已确认；正文与事实变化正在保存。" : decision === "discard" ? "旧草稿已放弃，正式正文未变化。" : "已提交草稿修改。查看运行过程。");
  } catch (error) { notify(error instanceof Error ? error.message : "草稿操作失败"); }
  finally { busy.value = false; }
}
async function approveCharacter(decision: "consent" | "accept" | "reject") {
  if (!activeRun.value || busy.value) return;
  busy.value = true;
  try {
    const generationId = activeRun.value.submission.payload.kind === "resolve_character" ? activeRun.value.submission.payload.generation_command_id : activeRun.value.submission.id;
    const editedCharacter = characterDraft.value ? structuredClone(toRaw(characterDraft.value)) : activeRun.value.output.character;
    const payload = { kind: "resolve_character", generation_command_id: generationId, decision,
      ...(decision === "accept" ? { character: editedCharacter } : {}) } as CommandPayload;
    await command(payload);
    notify(decision === "reject" ? "已拒绝新角色，写作流程会按此决定继续。" : "角色审批已更新。");
  } catch (error) { notify(error instanceof Error ? error.message : "审批失败"); }
  finally { busy.value = false; }
}
async function reindex() {
  if (busy.value || serverBusy.value) return;
  busy.value = true;
  try { await command({ kind: "reindex" }); notify("已提交重建索引任务。"); }
  catch (problem) { notify(problem instanceof Error ? problem.message : "重建索引失败"); }
  finally { busy.value = false; }
}
async function deleteStory() {
  if (busy.value || serverBusy.value || !window.confirm("将作品移入回收站？正文、角色与历史版本可之后恢复。")) return;
  busy.value = true;
  try {
    await api.command({ kind: "delete_story" }, props.storyId, view.value?.revision_id ?? null, null, event);
    notify("作品已移入回收站。"); emit("back");
  } catch (problem) { notify(problem instanceof Error ? problem.message : "删除失败"); }
  finally { busy.value = false; }
}

function chooseTemplates(selection: Schemas["PromptTemplateSelection"][]) {
  selectedTemplates.value = selection;
  localStorage.setItem(`ainovel.promptSelection.story.${props.storyId}`, JSON.stringify(selection));
  notify("本次采用的模板版本已保存到此浏览器；运行记录会固定绑定版本。");
}
function persistComposer() { localStorage.setItem(`ainovel.composer.${props.storyId}`, input.value); }
function showRun() { chatOpen.value = true; }
function showDecision() { document.getElementById("decision-card")?.scrollIntoView({ behavior: "smooth", block: "center" }); }

function togglePane(pane: "directory" | "novel" | "records") {
  const target = pane === "directory" ? directoryOpen : pane === "novel" ? novelOpen : chatOpen;
  const count = Number(directoryOpen.value) + Number(novelOpen.value) + Number(chatOpen.value);
  if (target.value && count === 1) return;
  target.value = !target.value;
  localStorage.setItem(`ainovel.panes.${props.storyId}`, JSON.stringify({ directory: directoryOpen.value, novel: novelOpen.value, records: chatOpen.value }));
}
function selectScene(id: string) { selectedScene.value = id; recordFilter.value = `scene:${id}`; }
function rootId(item: CommandView): string {
  const p = item.submission.payload;
  if (p.kind === 'review_draft' || p.kind === 'resolve_character') return p.generation_command_id;
  if (p.kind === 'accept_rewrite' || p.kind === 'reject_rewrite') return p.proposal_command_id;
  return item.submission.id;
}
function recordScene(item: CommandView) {
  const id = rootId(item);
  const source = commands.value.find(c => c.submission.id === id);
  const p = source?.submission.payload;
  return view.value?.story.scenes.find(scene => {
    const sourceCommand = commands.value.find(command => command.submission.id === scene.source_command_id);
    return scene.source_command_id === id || (sourceCommand && rootId(sourceCommand) === id) || source?.scene_id === scene.id || item.scene_id === scene.id || (p?.kind === 'rewrite' && p.scene_id === scene.id);
  });
}
function recordLocation(item: CommandView) {
  const scene = recordScene(item);
  if (!scene) return item.undone ? '已撤回 / 历史版本' : item.status === 'waiting' && item.output.writing_draft ? '待确认草稿' : '尚未归档到正文';
  const chapter = view.value?.story.chapters.find(c => c.id === scene.chapter_id);
  return `${chapter?.title || '章节'} / ${scene.title}`;
}
const recordOptions = computed(() => [{ label: '全部写作对话', value: 'all' }, ...(view.value?.story.chapters.flatMap(chapter => [{ label: chapter.title, value: `chapter:${chapter.id}` }, ...view.value!.story.scenes.filter(s => s.chapter_id === chapter.id).map(scene => ({ label: `${chapter.title} / ${scene.title}`, value: `scene:${scene.id}` }))]) ?? [])]);
const visibleRecords = computed(() => conversationHistory.value.filter(item => {
  if (recordFilter.value === 'all') return true;
  const scene = recordScene(item);
  if (!scene) return !item.undone && ['waiting','running','queued'].includes(item.status);
  return recordFilter.value === `scene:${scene.id}` || recordFilter.value === `chapter:${scene.chapter_id}`;
}));
async function sidebarAction(event: Event) {
  const action = (event as CustomEvent<string>).detail;
  if (!view.value) { try { await refresh(); } catch (error) { notify(error instanceof Error ? error.message : '无法读取作品'); return; } }
  if (action === 'materials') materialsOpen.value = true;
  else if (action === 'prompts') templatesOpen.value = true;
  else if (action === 'delete') void deleteStory();
  else if (action === 'reindex') void reindex();
}
onMounted(() => {
  const saved = localStorage.getItem(`ainovel.panes.${props.storyId}`);
  if (saved) { try { const value = JSON.parse(saved); directoryOpen.value = !!value.directory; novelOpen.value = !!value.novel; chatOpen.value = !!value.records; if (!directoryOpen.value && !novelOpen.value && !chatOpen.value) novelOpen.value = true; } catch {} }
  window.addEventListener('ainovel:work-action', sidebarAction);
});
onUnmounted(() => window.removeEventListener('ainovel:work-action', sidebarAction));
</script>

<template>
  <main v-if="view" class="workbench-shell pane-workbench">
    <Teleport to="#workspace-header-controls"><div class="pane-toolbar"><div class="pane-switches">
      <n-button size="small" :type="directoryOpen ? 'primary' : 'default'" :secondary="directoryOpen" @click="togglePane('directory')">目录</n-button>
      <n-button size="small" :type="novelOpen ? 'primary' : 'default'" :secondary="novelOpen" @click="togglePane('novel')">小说</n-button>
      <n-button size="small" :type="chatOpen ? 'primary' : 'default'" :secondary="chatOpen" @click="togglePane('records')">写作会话</n-button>
    </div></div></Teleport>
    <div class="work-panes" :class="{ 'has-directory': directoryOpen, 'has-novel': novelOpen, 'has-records': chatOpen }">
      <aside v-if="directoryOpen" class="workspace-pane directory-pane" aria-label="章节目录"><header class="pane-heading"><h2>目录</h2><n-button quaternary circle aria-label="关闭目录" @click="togglePane('directory')">×</n-button></header><div class="pane-scroll directory-items">
        <section v-for="chapter in view.story.chapters" :key="chapter.id"><button class="directory-chapter" @click="selectedScene = view.story.scenes.find(s => s.chapter_id === chapter.id)?.id || ''; recordFilter = `chapter:${chapter.id}`">{{ chapter.title }}</button><button v-for="scene in view.story.scenes.filter(s => s.chapter_id === chapter.id)" :key="scene.id" :class="['directory-scene', { active: selectedScene === scene.id }]" @click="selectScene(scene.id)">{{ scene.title }}</button></section><p v-if="!view.story.chapters.length" class="muted">暂无章节，确认草稿后会显示在这里。</p>
      </div></aside>
      <section v-if="novelOpen" class="workspace-pane novel-pane" aria-label="小说面板"><header class="pane-heading"><h2>小说</h2><n-button quaternary circle aria-label="关闭小说" @click="togglePane('novel')">×</n-button></header><div class="pane-scroll">
      <section v-if="activeRun && (pendingDraft || activeRun.output.need || (activeRun.output.approval_phase === 'profile' && characterDraft))" id="decision-card" class="decision-card">
        <span class="eyebrow">{{ activeRun.output.need ? "等待角色审批" : activeRun.output.approval_phase === 'profile' ? '确认角色档案' : "草稿待确认" }}</span>
        <h2>{{ activeRun.output.need ? `本幕涉及新角色：${activeRun.output.need.name}` : characterDraft ? `确认新角色档案：${characterDraft.name}` : pendingDraft?.content.title }}</h2>
        <p v-if="activeRun.output.need">{{ activeRun.output.need.reason || activeRun.output.need.role }}。批准前不会将角色加入故事。</p>
        <div v-else-if="pendingDraft" class="draft-preview"><p class="draft-version">第 {{ pendingDraft.version }} 版 · 确认后保存为正式正文</p><article class="draft-text" aria-label="草稿正文"><p v-for="(paragraph, index) in draftParagraphs" :key="index">{{ paragraph }}</p></article><details v-if="pendingDraft.content.summary" class="draft-synopsis"><summary>本幕摘要</summary><p>{{ pendingDraft.content.summary }}</p></details></div>
        <div v-else-if="activeRun.output.approval_phase === 'profile' && characterDraft" class="profile-approval-form">
          <label>角色姓名 <n-input v-model:value="characterDraft.name" /></label>
          <label v-for="(entry, index) in characterDraft.profile?.entries || []" :key="entry.id || index">{{ entry.name }} <n-input v-model:value="entry.description" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" /></label>
          <p class="muted">可在确认前编辑姓名与档案内容；拒绝不会将角色加入故事。</p>
        </div>
        <aside v-if="staleDraft" class="notice warning"><strong>草稿依据的是旧资料版本。</strong><br>故事资料已更新，旧草稿不能直接确认。你可以保留它作参考，或按最新资料另写一份新草稿。</aside>
        <form v-if="pendingDraft && !staleDraft" class="feedback-form" @submit.prevent="review('revise', feedback)"><label>修改意见 <n-input v-model:value="feedback" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" placeholder="可先修改措辞、节奏或人物表现；确认后再正式保存正文。" /></label><n-button attr-type="submit" type="primary" :disabled="busy || !feedback.trim()">按意见修改</n-button></form>
        <div class="actions">
          <template v-if="activeRun.output.need || activeRun.output.approval_phase">
            <n-button v-if="activeRun.output.approval_phase === 'consent'" :disabled="busy" @click="approveCharacter('reject')">不引入角色</n-button>
            <n-button v-if="activeRun.output.approval_phase === 'consent'" type="primary" :disabled="busy" @click="approveCharacter('consent')">同意，查看角色档案</n-button>
            <n-button v-if="activeRun.output.approval_phase === 'profile'" :disabled="busy" @click="approveCharacter('reject')">拒绝并继续</n-button>
            <n-button v-if="activeRun.output.approval_phase === 'profile'" type="primary" :disabled="busy || !characterDraft?.name.trim()" @click="approveCharacter('accept')">确认角色档案</n-button>
          </template>
          <template v-else-if="staleDraft"><n-button :disabled="busy" @click="review('discard')">放弃旧草稿</n-button><n-button type="primary" :disabled="busy || serverBusy" @click="newLatestDraft">按最新资料另写一份</n-button></template>
          <template v-else><n-button :disabled="busy" @click="review('discard')">放弃草稿</n-button><n-button type="primary" :disabled="busy" @click="review('confirm')">确认正文并保存变化</n-button></template>
        </div>
      </section>

        <NovelReader v-if="!pendingDraft || view.story.scenes.length" :view="view" :commands="commands" :disabled="busy || serverBusy" :execute-command="command" :selected-scene="selectedScene" :embedded="true" @scene-selected="selectedScene = $event" @revision-changed="refresh" />
      </div></section>
      <section v-if="chatOpen" class="workspace-pane records-pane" aria-label="创作记录面板"><header class="pane-heading"><h2>写作会话</h2><n-button quaternary circle aria-label="关闭创作记录" @click="togglePane('records')">×</n-button></header><n-select class="record-filter" aria-label="创作记录章节和幕" v-model:value="recordFilter" :options="recordOptions"/><div class="pane-scroll records-scroll">
        <n-empty v-if="!visibleRecords.length" description="此章节或幕还没有创作记录" />
        <article v-for="item in visibleRecords" :key="item.submission.id" class="creation-record" :data-command-id="item.submission.id"><div class="record-location">{{ recordLocation(item) }} · {{ item.undone ? '已撤回' : statusLabel(item.status) }}</div><h3>{{ operationLabel(item) }}</h3><p v-if="operationInput(item)" class="record-request">{{ operationInput(item) }}</p><p class="preserve-text">{{ operationOutput(item) }}</p><ConversationLog :command="item" /></article>
        <ConversationActions v-if="!visibleRecords.length || visibleRecords.at(-1)?.submission.id === conversationHistory.at(-1)?.submission.id" :command="visibleRecords.at(-1)" :scope-key="`writing-${storyId}`" :disabled="busy || serverBusy" :committed="!!(visibleRecords.at(-1) && recordScene(visibleRecords.at(-1)!)?.text)" @changed="conversationChanged" />
      </div></section>
    </div>
    <footer class="studio-composer"><div class="studio-composer-inner"><div v-if="pendingDraft" class="draft-pending-bar"><div><strong>草稿待确认</strong><span>阅读正文后，可以提出修改意见、确认或放弃草稿。</span></div><n-button secondary @click="novelOpen = true; showDecision()">查看草稿与确认操作</n-button></div><form v-else class="composer-form" @submit.prevent="write"><div class="composer-controls"><span role="status">{{ busy || serverBusy ? currentStep || '任务处理中' : activeRun ? '先确认草稿或审批角色，再继续创作' : '说明你希望接下来发生什么' }}</span><n-button v-if="activeRun" text size="small" @click="novelOpen = true; showDecision()">处理待确认内容</n-button><n-button v-if="failedRun" text size="small" @click="showRun">任务失败，查看会话末尾</n-button></div><n-input id="instruction" aria-label="创作要求" v-model:value="input" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :disabled="busy || serverBusy || !!activeRun" @input="persistComposer" placeholder="告诉 AI 你希望接下来发生什么…"/><div class="composer-actions"><span class="muted">草稿确认后进入正文</span><n-button type="primary" attr-type="submit" :loading="busy || serverBusy" :disabled="!!activeRun || !input.trim()">开始创作</n-button></div></form></div></footer>
    <n-drawer v-model:show="materialsOpen" placement="right" width="min(900px, 95vw)" :trap-focus="false">
      <n-drawer-content title="故事资料" closable>
        <p class="muted">已发生事实、未来剧情计划和创作策略各自保存。修改资料后，会在本次运行中展示具体变化。</p>
        <StoryMaterials :view="view" :disabled="busy || serverBusy" :execute-command="command" @revision-changed="refresh" />
      </n-drawer-content>
    </n-drawer>
    <PromptTemplateSettings :open="templatesOpen" :story-id="storyId" :revision-id="view.revision_id" :selected-selections="selectedTemplates" default-scope="story" @close="templatesOpen = false" @choose="chooseTemplates" @saved="refresh" />

    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </main><main v-else class="loading-state">正在打开作品…</main>
</template>
