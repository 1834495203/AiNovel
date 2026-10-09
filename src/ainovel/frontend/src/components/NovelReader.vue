<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { NButton, NCollapse, NCollapseItem, NDrawer, NDrawerContent, NInput, NModal, NTag } from "naive-ui";
import { BookOpen } from "@lucide/vue";
import RunSummary from "./RunSummary.vue";
import { api, type CharacterSnapshot, type CommandPayload, type CommandView, type RunOverview, type StoryView } from "../api/client";

type ExecuteCommand = (payload: CommandPayload) => Promise<CommandView>;
type TextTarget = { sceneId: string; chapterId: string; start: number; end: number; text: string };
type RewritePreview = { commandId: string; target: TextTarget; instruction: string; proposal: NonNullable<CommandView["output"]>["rewrite"] };
type RunCache = { loading: boolean; overview: RunOverview | null; error: string };
type StageHover = { characterId: string; characterName: string; sceneId: string; left: number; top: number; state: CharacterSnapshot["state"]["entries"] };

const props = defineProps<{ view: StoryView; commands: CommandView[]; disabled: boolean; executeCommand: ExecuteCommand; selectedScene?: string; embedded?: boolean }>();
const emit = defineEmits<{ "revision-changed": []; "scene-selected": [id: string] }>();

const activeSceneId = ref<string>("");
const directoryOpen = ref(false);
const selection = ref<TextTarget | null>(null);
const rewriteTarget = ref<TextTarget | null>(null);
const rewriteInstruction = ref("");
const preview = ref<RewritePreview | null>(null);
const runDetails = ref<Record<string, RunCache>>({});
const snapshotDetails = ref<Record<string, CharacterSnapshot | null>>({});
const hoveredStage = ref<StageHover | null>(null);
const snapshotRequests = new Set<string>();
const actionBusy = ref(false);
const notice = ref("");
let stageHoverTimer: number | undefined;

const scenesByChapter = computed(() => props.view.story.chapters.map(chapter => ({
  chapter,
  scenes: props.view.story.scenes.filter(scene => scene.chapter_id === chapter.id),
})));
const activeChapterId = computed(() => props.view.story.scenes.find(scene => scene.id === activeSceneId.value)?.chapter_id ?? props.view.story.chapters[0]?.id ?? "");
const activeChapterGroup = computed(() => scenesByChapter.value.find(group => group.chapter.id === activeChapterId.value) ?? scenesByChapter.value[0] ?? null);
const activeProposal = computed(() => [...props.commands].reverse().find(command =>
  !command.undone && command.output.rewrite?.status === "proposed" && command.submission.payload.kind === "rewrite") ?? null);
const proposalStale = computed(() => !!activeProposal.value && activeProposal.value.submission.base_revision_id !== props.view.revision_id);
const activeProposalSelection = computed<TextTarget | null>(() => {
  const command = activeProposal.value;
  if (!command || command.submission.payload.kind !== "rewrite" || !command.output.rewrite) return null;
  const payload = command.submission.payload;
  const proposal = command.output.rewrite;
  const sceneId = payload.scene_id ?? proposal.scene_id ?? "";
  const chapterId = payload.chapter_id ?? proposal.chapter_id ?? "";
  return { sceneId, chapterId, start: proposal.start, end: proposal.end, text: proposal.original };
});

watch(() => props.view.revision_id, () => {
  selection.value = null;
  rewriteTarget.value = null;
  preview.value = null;
  snapshotDetails.value = {};
  hoveredStage.value = null;
});

function clearSelection() { selection.value = null; }
function codePointLength(value: string): number { return Array.from(value).length; }
function textSelectionChanged() {
  const current = window.getSelection();
  if (!current || current.isCollapsed || !current.rangeCount) { selection.value = null; return; }
  const range = current.getRangeAt(0);
  const startElement = range.startContainer instanceof Element ? range.startContainer : range.startContainer.parentElement;
  const endElement = range.endContainer instanceof Element ? range.endContainer : range.endContainer.parentElement;
  const startProse = startElement?.closest<HTMLElement>("[data-prose-scene]");
  const endProse = endElement?.closest<HTMLElement>("[data-prose-scene]");
  if (!startProse || startProse !== endProse) { selection.value = null; return; }
  const sceneId = startProse.dataset.proseScene;
  const scene = props.view.story.scenes.find(item => item.id === sceneId);
  if (!sceneId || !scene?.id) { selection.value = null; return; }
  const prefix = range.cloneRange();
  prefix.selectNodeContents(startProse);
  prefix.setEnd(range.startContainer, range.startOffset);
  const start = codePointLength(prefix.toString());
  const text = range.toString();
  const end = start + codePointLength(text);
  const sceneText = Array.from(scene.text);
  if (!text.trim() || sceneText.slice(start, end).join("") !== text) { selection.value = null; return; }
  selection.value = { sceneId, chapterId: scene.chapter_id, start, end, text };
  activeSceneId.value = sceneId;
}

onMounted(() => window.addEventListener("selectionchange", textSelectionChanged));
onBeforeUnmount(() => { window.removeEventListener("selectionchange", textSelectionChanged); closeCharacterStage(); });

function visibleCharactersFor(sceneId: string) {
  const sceneIndex = props.view.story.scenes.findIndex(scene => scene.id === sceneId);
  const visibleSceneIds = new Set(props.view.story.scenes.slice(0, sceneIndex + 1).map(scene => scene.id));
  const stage = props.view.characters_at_scenes.filter(item => item.scene_id === sceneId);
  return props.view.story.characters.filter(character => !character.introduced_scene_id || visibleSceneIds.has(character.introduced_scene_id))
    .flatMap(character => {
      const presentation = stage.find(item => item.character_id === character.id);
      const characterName = presentation?.name || character.name;
      const words = [characterName, ...(presentation?.aliases ?? []).map(alias => alias.name)].filter(word => word.length > 0);
      return words.map(word => ({ word, characterId: character.id ?? "", characterName, state: presentation?.state.entries ?? [] }));
    }).sort((left, right) => right.word.length - left.word.length);
}

function highlightParts(text: string, sceneId: string) {
  const references = visibleCharactersFor(sceneId);
  if (!references.length) return [{ text, characterId: "", characterName: "", state: [], isCharacter: false }];
  const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const expression = new RegExp(references.map(item => escapeRegExp(item.word)).join("|"), "gu");
  const parts: Array<{ text: string; characterId: string; characterName: string; state: CharacterSnapshot["state"]["entries"]; isCharacter: boolean }> = [];
  let cursor = 0;
  for (const match of text.matchAll(expression)) {
    const matched = match[0];
    const index = match.index;
    if (index > cursor) parts.push({ text: text.slice(cursor, index), characterId: "", characterName: "", state: [], isCharacter: false });
    const reference = references.find(item => item.word === matched);
    if (reference) parts.push({ text: matched, characterId: reference.characterId, characterName: reference.characterName, state: reference.state, isCharacter: true });
    cursor = index + matched.length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), characterId: "", characterName: "", state: [], isCharacter: false });
  return parts;
}

function sceneCommand(sceneId: string): CommandView | null {
  const scene = props.view.story.scenes.find(item => item.id === sceneId);
  if (!scene) return null;
  const history = [...props.commands].reverse().filter(command => !command.undone);
  return history.find(command => command.submission.id === scene.source_command_id)
    ?? history.find(command => command.scene_id === sceneId && command.status === 'completed') ?? null;
}
function commandLabel(command: CommandView | null): string {
  if (!command) return "暂无本次运行记录";
  const payload = command.submission.payload;
  return payload.kind === "generate" ? payload.instruction : command.output.message || "本幕来源运行";
}
function cacheFor(commandId: string): RunCache {
  return runDetails.value[commandId] ?? { loading: false, overview: null, error: "" };
}
async function toggleRun(command: CommandView | null) {
  if (!command) return;
  const existing = cacheFor(command.submission.id);
  if (existing.loading || existing.overview) return;
  runDetails.value = { ...runDetails.value, [command.submission.id]: { loading: true, overview: null, error: "" } };
  try {
    const overview = await api.get<RunOverview>(`/api/runs/${command.submission.id}`);
    runDetails.value = { ...runDetails.value, [command.submission.id]: { loading: false, overview, error: "" } };
  } catch (error) {
    runDetails.value = { ...runDetails.value, [command.submission.id]: { loading: false, overview: null,
      error: error instanceof Error ? error.message : "读取运行过程失败" } };
  }
}
function targetForScene(sceneId: string): TextTarget | null {
  const scene = props.view.story.scenes.find(item => item.id === sceneId);
  return scene?.id ? { sceneId, chapterId: scene.chapter_id, start: 0, end: codePointLength(scene.text), text: scene.text } : null;
}
function targetForChapter(chapterId: string): TextTarget | null {
  const scenes = props.view.story.scenes.filter(scene => scene.chapter_id === chapterId);
  if (!scenes.length) return null;
  const text = scenes.map(scene => scene.text).join("\n\n");
  return { sceneId: "", chapterId, start: 0, end: codePointLength(text), text };
}
function beginRewrite(target: TextTarget | null) {
  if (!target || props.disabled || actionBusy.value || !target.text) return;
  rewriteTarget.value = target;
  rewriteInstruction.value = "";
  notice.value = "";
}
async function createRewrite(target: TextTarget, instruction: string) {
  if (!instruction.trim() || actionBusy.value) return;
  actionBusy.value = true;
  notice.value = "正在生成修改建议…";
  try {
    const answer = await props.executeCommand({ kind: "rewrite", ...(target.sceneId ? { scene_id: target.sceneId } : { chapter_id: target.chapterId }),
      start: target.start, end: target.end, selected_text: target.text, instruction: instruction.trim() });
    const proposal = answer.output.rewrite;
    if (!proposal) throw new Error(answer.output.message || "没有收到修改建议。原文尚未变化。");
    preview.value = { commandId: answer.submission.id, target, instruction: instruction.trim(), proposal };
    rewriteTarget.value = null;
    selection.value = null;
    notice.value = "建议已生成，确认前不会改动正文。";
  } catch (error) { notice.value = error instanceof Error ? error.message : "生成修改建议失败。"; }
  finally { actionBusy.value = false; }
}
async function resolveRewrite(decision: "accept" | "reject" | "regenerate") {
  const command = activeProposal.value;
  const proposal = command?.output.rewrite;
  const selectionFromHistory = activeProposalSelection.value;
  const current = preview.value ?? (command && proposal && selectionFromHistory && command.submission.payload.kind === "rewrite"
    ? { commandId: command.submission.id, proposal, target: selectionFromHistory, instruction: command.submission.payload.instruction }
    : null);
  if (!current || actionBusy.value) return;
  actionBusy.value = true;
  notice.value = decision === "accept" ? "正在提交修改…" : decision === "reject" ? "正在放弃建议…" : "正在重新生成建议…";
  try {
    if (decision === "regenerate") {
      await props.executeCommand({ kind: "reject_rewrite", proposal_command_id: current.commandId });
      preview.value = null;
      actionBusy.value = false;
      await createRewrite(current.target, current.instruction);
      return;
    }
    await props.executeCommand(decision === "accept"
      ? { kind: "accept_rewrite", proposal_command_id: current.commandId }
      : { kind: "reject_rewrite", proposal_command_id: current.commandId });
    preview.value = null;
    notice.value = decision === "accept" ? "修改已接受，新版本正在显示。" : "已放弃修改建议，原文保留。";
    emit("revision-changed");
  } catch (error) { notice.value = error instanceof Error ? error.message : "处理修改建议失败。"; }
  finally { actionBusy.value = false; }
}
async function openCharacterStage(characterId: string, sceneId: string) {
  const revisionId = props.view.revision_id;
  const key = `${revisionId}:${characterId}:${sceneId}`;
  if (Object.hasOwn(snapshotDetails.value, key)) return;
  if (snapshotRequests.has(key)) return;
  snapshotRequests.add(key);
  try {
    const snapshot = await api.get<CharacterSnapshot>(`/api/revisions/${revisionId}/characters/${characterId}/snapshots/${sceneId}`);
    if (revisionId === props.view.revision_id) snapshotDetails.value = { ...snapshotDetails.value, [key]: snapshot };
  } catch { if (revisionId === props.view.revision_id) snapshotDetails.value = { ...snapshotDetails.value, [key]: null }; }
  finally { snapshotRequests.delete(key); }
}
function showCharacterStage(event: MouseEvent | FocusEvent, characterId: string, characterName: string, sceneId: string,
  state: CharacterSnapshot["state"]["entries"]) {
  if (stageHoverTimer !== undefined) window.clearTimeout(stageHoverTimer);
  const anchor = event.currentTarget;
  if (!(anchor instanceof HTMLElement)) return;
  const rect = anchor.getBoundingClientRect();
  hoveredStage.value = { characterId, characterName, sceneId, left: Math.max(160, Math.min(window.innerWidth - 160, rect.left + rect.width / 2)),
    top: Math.max(12, Math.min(window.innerHeight - 180, rect.bottom + 8)), state };
  void openCharacterStage(characterId, sceneId);
}
function scheduleHideCharacterStage() {
  if (stageHoverTimer !== undefined) window.clearTimeout(stageHoverTimer);
  stageHoverTimer = window.setTimeout(() => { hoveredStage.value = null; stageHoverTimer = undefined; }, 180);
}
function keepCharacterStage() { if (stageHoverTimer !== undefined) window.clearTimeout(stageHoverTimer); stageHoverTimer = undefined; }
function closeCharacterStage() { hoveredStage.value = null; if (stageHoverTimer !== undefined) window.clearTimeout(stageHoverTimer); stageHoverTimer = undefined; }
function stageLink(characterId: string, sceneId: string) {
  return `/#character?story=${props.view.story.id}&revision=${encodeURIComponent(props.view.revision_id)}&scene=${encodeURIComponent(sceneId)}&character=${encodeURIComponent(characterId)}`;
}
function characterState(characterId: string, sceneId: string, fallback: CharacterSnapshot["state"]["entries"]) {
  const snapshot = snapshotDetails.value[`${props.view.revision_id}:${characterId}:${sceneId}`];
  return snapshot?.state.entries ?? fallback;
}
function chapterTitle(chapterId: string) { return props.view.story.chapters.find(chapter => chapter.id === chapterId)?.title ?? "当前章节"; }
async function navigateTo(targetId: string) {
  if (targetId.startsWith("scene-")) {
    const sceneId = targetId.slice("scene-".length);
    if (props.view.story.scenes.some(scene => scene.id === sceneId)) activeSceneId.value = sceneId;
  } else if (targetId.startsWith("chapter-")) {
    const chapterId = targetId.slice("chapter-".length);
    const firstScene = props.view.story.scenes.find(scene => scene.chapter_id === chapterId);
    if (firstScene?.id) activeSceneId.value = firstScene.id;
  }
  directoryOpen.value = false;
  await nextTick();
  document.getElementById(targetId)?.scrollIntoView({ behavior: "smooth", block: "start" });
}
function restoreSceneSelection(preferRemembered = false) {
  const sceneIds = new Set(props.view.story.scenes.map(scene => scene.id).filter((id): id is string => Boolean(id)));
  let remembered = "";
  try { remembered = localStorage.getItem(`ainovel.readerScene.${props.view.story.id}`) ?? ""; }
  catch { /* Storage may be unavailable in private browsing; the first scene is still a stable fallback. */ }
  const selected = preferRemembered && sceneIds.has(remembered) ? remembered
    : sceneIds.has(activeSceneId.value) ? activeSceneId.value
      : sceneIds.has(remembered) ? remembered : props.view.story.scenes[0]?.id ?? "";
  activeSceneId.value = selected;
}
watch(() => props.view.story.id, () => { activeSceneId.value = ""; restoreSceneSelection(true); }, { immediate: true });
watch(() => props.view.story.scenes.map(scene => scene.id), () => restoreSceneSelection(), { deep: true });
watch(() => props.selectedScene, id => { if (id && id !== activeSceneId.value && props.view.story.scenes.some(s => s.id === id)) { activeSceneId.value = id; void nextTick(() => document.getElementById(`scene-${id}`)?.scrollIntoView({ block: "start" })); } }, { immediate: true });
watch(activeSceneId, sceneId => {
  emit("scene-selected", sceneId);
  if (!sceneId) return;
  try { localStorage.setItem(`ainovel.readerScene.${props.view.story.id}`, sceneId); }
  catch { /* Navigation remains available when browser storage is disabled. */ }
});
watch(() => props.view.revision_id, () => { snapshotDetails.value = {}; restoreSceneSelection(); });
watch(() => [props.view.revision_id, activeChapterId.value, props.commands.length], () => {
  for (const scene of activeChapterGroup.value?.scenes ?? []) {
    const command = sceneCommand(scene.id);
    if (command) void toggleRun(command);
  }
}, { immediate: true });
</script>

<template>
  <section class="novel-reader" aria-label="小说正文">
    <header v-if="!embedded" class="reader-heading">
      <div class="reader-heading-copy"><span class="eyebrow">正文 · {{ view.story.title }}</span><p>选择一段文字提出修改建议；资料和已写正文会保持分开。</p></div>
      <div class="reader-heading-actions"><NTag round :bordered="false" type="info">{{ activeChapterGroup?.chapter.title ?? "尚无章节" }}</NTag><NButton secondary size="small" aria-label="打开章节目录" @click="directoryOpen = true"><template #icon><BookOpen :size="16" /></template>目录</NButton></div>
    </header>

    <div class="reader-layout">
      <div class="reader-body">
        <div v-if="!view.story.scenes.length" class="reader-empty-state"><span>✦</span><h3>正文从第一幕开始</h3><p>确认草稿后，故事正文会显示在这里。</p></div>
        <section v-for="group in (activeChapterGroup ? [activeChapterGroup] : [])" :id="`chapter-${group.chapter.id}`" :data-chapter="group.chapter.id" :key="group.chapter.id" class="reader-chapter">
          <header class="reader-chapter-heading"><div><span class="eyebrow">本章 · {{ group.scenes.length }} 幕</span><h3>{{ group.chapter.title }}</h3></div>
            <NButton v-if="group.scenes.length" secondary size="small" :disabled="disabled || actionBusy" @click="beginRewrite(targetForChapter(group.chapter.id))">修改整章</NButton>
          </header>
          <article v-for="scene in group.scenes" :id="`scene-${scene.id}`" :key="scene.id" class="reader-scene"
            :class="{ 'is-active': activeSceneId === scene.id }" @mouseenter="activeSceneId = scene.id">
            <header class="reader-scene-heading"><h4>{{ scene.title }}</h4><NButton quaternary size="small" :disabled="disabled || actionBusy" @click="beginRewrite(targetForScene(scene.id))">修改本幕</NButton></header>
            <p :data-prose-scene="scene.id" class="reader-prose" @mouseup="textSelectionChanged">
              <template v-for="(part, index) in highlightParts(scene.text, scene.id)" :key="`${index}:${part.text}`">
                <a v-if="part.isCharacter" :href="stageLink(part.characterId, scene.id)" class="reader-character-mention"
                  @mouseenter="showCharacterStage($event, part.characterId, part.characterName, scene.id, part.state)"
                  @mouseleave="scheduleHideCharacterStage" @focus="showCharacterStage($event, part.characterId, part.characterName, scene.id, part.state)"
                  @blur="scheduleHideCharacterStage" :aria-label="`${part.characterName}：查看本幕状态`">
                  {{ part.text }}
                </a><template v-else>{{ part.text }}</template>
              </template>
            </p>
            <div class="reader-selection-tools" v-if="selection?.sceneId === scene.id"><span>已选 {{ codePointLength(selection.text) }} 个字符</span><NButton text type="primary" size="tiny" :disabled="disabled || actionBusy" @click="beginRewrite(selection)">讨论修改</NButton><NButton quaternary circle size="tiny" aria-label="清除选区" @click="clearSelection">×</NButton></div>
            <RunSummary :run="sceneCommand(scene.id) ? cacheFor(sceneCommand(scene.id)!.submission.id).overview : null" />
          </article>
        </section>
      </div>
    </div>

    <NDrawer v-model:show="directoryOpen" placement="left" :width="'min(320px, 100vw)'" class="reader-directory-drawer">
      <NDrawerContent title="章节与场景" closable>
        <p class="reader-drawer-intro">选择一章或一幕，正文会切换到对应位置。</p>
        <NCollapse :default-expanded-names="activeChapterId ? [activeChapterId] : []" arrow-placement="right">
          <NCollapseItem v-for="group in scenesByChapter" :key="group.chapter.id" :name="group.chapter.id">
            <template #header><span class="reader-chapter-label">{{ group.chapter.title }}</span><span class="reader-chapter-count">{{ group.scenes.length }} 幕</span></template>
            <div class="reader-nav-scenes">
              <NButton v-for="scene in group.scenes" :key="scene.id" quaternary block size="small"
                :type="activeSceneId === scene.id ? 'primary' : 'default'" :class="['reader-scene-link', { active: activeSceneId === scene.id }]"
                @click="navigateTo('scene-' + scene.id)">{{ scene.title }}</NButton>
              <p v-if="!group.scenes.length" class="reader-empty-note">本章还没有正文幕。</p>
            </div>
          </NCollapseItem>
        </NCollapse>
        <p v-if="!view.story.chapters.length" class="reader-empty-note">确认设定并写下第一幕后，正文目录会出现在这里。</p>
      </NDrawerContent>
    </NDrawer>

    <div v-if="hoveredStage" class="reader-stage-card" role="tooltip"
      :style="{ left: `${hoveredStage.left}px`, top: `${hoveredStage.top}px` }"
      @mouseenter="keepCharacterStage" @mouseleave="closeCharacterStage">
      <strong>{{ hoveredStage.characterName }} · 本幕状态</strong>
      <span v-for="item in characterState(hoveredStage.characterId, hoveredStage.sceneId, hoveredStage.state)" :key="item.id">{{ item.name }}<template v-if="item.value !== null">：{{ item.value }}</template><template v-if="item.description"> · {{ item.description }}</template></span>
      <span v-if="!characterState(hoveredStage.characterId, hoveredStage.sceneId, hoveredStage.state).length">本幕暂无状态记录。</span>
      <span v-if="snapshotDetails[`${view.revision_id}:${hoveredStage.characterId}:${hoveredStage.sceneId}`] === null" class="reader-stage-error">阶段详情暂时不可用。</span>
    </div>

    <NModal :show="!!rewriteTarget" preset="card" class="reader-dialog" :style="{ width: 'min(620px, calc(100vw - 32px))' }" aria-label="提出正文修改建议" :mask-closable="!actionBusy" @update:show="visible => !visible && (rewriteTarget = null)">
      <form v-if="rewriteTarget" class="reader-dialog-form" aria-label="提出正文修改建议" @submit.prevent="createRewrite(rewriteTarget, rewriteInstruction)">
        <span class="eyebrow">讨论修改 · {{ rewriteTarget.sceneId ? "场景" : "章节" }}</span>
        <h3>你希望怎样修改？</h3><p class="reader-muted">{{ chapterTitle(rewriteTarget.chapterId) }} · 这次只生成建议，不会直接改动正文。</p>
        <details class="reader-quoted-text"><summary>查看本次选中的文字</summary><pre>{{ rewriteTarget.text }}</pre></details>
        <label>修改方向<NInput v-model:value="rewriteInstruction" type="textarea" :rows="4" :maxlength="4000" autofocus placeholder="例如：保留情节信息，把解释性独白改成动作和对白。" /></label>
        <div class="reader-dialog-actions"><NButton secondary attr-type="button" @click="rewriteTarget = null">取消</NButton><NButton type="primary" attr-type="submit" :disabled="actionBusy || disabled || !rewriteInstruction.trim()" :loading="actionBusy">生成修改建议</NButton></div>
      </form>
    </NModal>

    <section v-if="preview || activeProposal" class="reader-proposal" aria-live="polite">
      <header><div><span class="eyebrow">正文修改建议</span><h3>确认前不会改动正式正文</h3></div><button class="reader-clear-button" aria-label="关闭修改建议" :disabled="actionBusy" @click="preview = null">×</button></header>
      <p v-if="notice" class="reader-notice">{{ notice }}</p>
      <p v-if="proposalStale && !preview" class="reader-notice">这条建议依据的是旧正文版本。你可以放弃它；请选中当前正文后重新提出修改，才能应用到最新版本。</p>
      <div class="reader-diff"><article><h4>原文</h4><pre>{{ preview?.proposal?.original ?? activeProposal?.output.rewrite?.original }}</pre></article><article class="reader-after"><h4>建议文本</h4><pre>{{ preview?.proposal?.replacement ?? activeProposal?.output.rewrite?.replacement }}</pre></article></div>
      <p class="reader-muted">接受建议会从这里建立新版本；之后的内容仍可在版本历史中找回。</p>
      <div class="reader-dialog-actions"><NButton secondary :disabled="actionBusy || disabled" @click="resolveRewrite('reject')">放弃建议</NButton><NButton secondary :disabled="actionBusy || disabled || proposalStale || !(preview || activeProposal)" @click="resolveRewrite('regenerate')">重新生成</NButton><NButton type="primary" :disabled="actionBusy || disabled || proposalStale || !(preview || activeProposal)" :loading="actionBusy" @click="resolveRewrite('accept')">接受修改</NButton></div>
    </section>
    <p v-if="notice && !preview && !activeProposal" class="reader-notice" role="status">{{ notice }}</p>
  </section>
</template>

<style scoped>
.novel-reader{min-width:0;color:var(--ink,#28291f)}.reader-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;padding:0 0 1.25rem;border-bottom:1px solid var(--line,#e8e5dc)}.reader-heading h2{margin:.35rem 0;font:600 clamp(1.55rem,3vw,2.15rem)/1.2 Georgia,serif;letter-spacing:-.025em}.reader-heading p,.reader-muted{margin:.35rem 0;color:var(--muted,#77786e);font-size:.88rem;line-height:1.55}.reader-revision{color:var(--muted,#77786e);font-size:.75rem;border:1px solid var(--line,#e8e5dc);border-radius:99px;padding:.35rem .65rem}.reader-layout{display:grid;grid-template-columns:minmax(145px,190px) minmax(0,1fr);gap:clamp(1.3rem,4vw,3.4rem);align-items:start}.reader-outline{position:sticky;top:1rem;padding:1.2rem .25rem 1rem 0;border-right:1px solid var(--line,#e8e5dc);max-height:calc(100vh - 2rem);overflow:auto}.reader-nav-title{display:flex;justify-content:space-between;color:var(--muted,#77786e);font-size:.72rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin:0 0 .9rem}.reader-nav-title span{font-weight:400;letter-spacing:0;text-transform:none}.reader-nav-chapter{display:grid;gap:.18rem;margin:.6rem 0 1rem}.reader-chapter-link,.reader-scene-link{display:block;text-decoration:none;color:inherit;border-radius:.45rem;padding:.38rem .5rem;font-size:.83rem}.reader-chapter-link{font-weight:650}.reader-scene-link{margin-left:.5rem;color:var(--muted,#77786e);line-height:1.4}.reader-scene-link:hover,.reader-scene-link.active{background:#f1efe8;color:var(--accent,#596c57)}.reader-empty-note,.reader-empty-state{color:var(--muted,#77786e);font-size:.88rem;line-height:1.65}.reader-body{min-width:0;padding-bottom:2rem}.reader-empty-state{max-width:32rem;margin:4rem auto;text-align:center;padding:2rem}.reader-empty-state>span{font-size:2rem;color:var(--accent,#596c57)}.reader-empty-state h3{font:600 1.4rem Georgia,serif;color:var(--ink,#28291f)}.reader-chapter{scroll-margin-top:1rem}.reader-chapter-heading{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:2.25rem 0 .8rem;border-bottom:1px solid var(--line,#e8e5dc)}.reader-chapter-heading h3{margin:.25rem 0;font:600 1.55rem Georgia,serif}.eyebrow{color:var(--accent,#596c57);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;font-weight:700}.reader-subtle-button,.reader-tool-button,.reader-clear-button,.reader-primary-button{font:inherit;cursor:pointer}.reader-subtle-button{border:1px solid var(--line,#e8e5dc);background:transparent;color:var(--muted,#77786e);border-radius:99px;padding:.43rem .75rem;font-size:.77rem}.reader-subtle-button:hover{border-color:#a9b4a1;color:var(--accent,#596c57)}.reader-subtle-button:disabled,.reader-primary-button:disabled{opacity:.48;cursor:not-allowed}.reader-scene{padding:1.45rem 0 1rem;border-bottom:1px solid var(--line,#eeeae2);scroll-margin-top:1rem}.reader-scene-heading{display:flex;align-items:center;justify-content:space-between;gap:1rem}.reader-scene-heading h4{margin:0;font:600 1.06rem Georgia,serif}.reader-prose{white-space:pre-wrap;overflow-wrap:anywhere;margin:1rem 0;font:400 1.025rem/2 Georgia,'Noto Serif SC',serif;color:#393a32;user-select:text}.reader-character-mention{position:relative;color:#425c46;text-decoration:underline;text-decoration-color:#b3c3af;text-decoration-thickness:1px;text-underline-offset:3px}.reader-character-mention:hover{color:#243b28}.reader-stage-card{display:none;position:absolute;z-index:4;left:50%;bottom:calc(100% + .5rem);transform:translateX(-50%);width:min(280px,70vw);padding:.75rem .85rem;border-radius:.7rem;background:#303a31;color:#fff;box-shadow:0 10px 28px #24281d38;font:400 .78rem/1.5 system-ui,sans-serif;white-space:normal}.reader-stage-card strong,.reader-stage-card span{display:block;margin:.16rem 0}.reader-character-mention:hover .reader-stage-card,.reader-character-mention:focus .reader-stage-card{display:block}.reader-stage-error{opacity:.75}.reader-selection-tools{display:flex;align-items:center;gap:.45rem;margin:-.2rem 0 .8rem;padding:.4rem .55rem;background:#f1efe8;border-radius:.5rem;color:var(--muted,#77786e);font-size:.75rem;width:max-content}.reader-tool-button{border:0;background:none;color:var(--accent,#596c57);font-weight:700}.reader-clear-button,.reader-dialog-close{border:0;background:none;color:var(--muted,#77786e);font-size:1.3rem;line-height:1}.reader-process{margin:.9rem 0 0;border:1px solid var(--line,#e8e5dc);border-radius:.65rem;background:#fbfaf7}.reader-process>summary{cursor:pointer;display:flex;gap:.7rem;align-items:baseline;padding:.65rem .8rem;list-style:none}.reader-process>summary::-webkit-details-marker{display:none}.reader-process>summary span{font-size:.76rem;font-weight:650;white-space:nowrap}.reader-process>summary small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted,#77786e);font-size:.72rem}.reader-run-content{padding:0 .8rem .8rem}.reader-run-steps{list-style:none;padding:0;margin:.2rem 0}.reader-run-steps li{padding:.65rem 0;border-top:1px solid var(--line,#e8e5dc)}.reader-run-steps li>div{display:flex;justify-content:space-between;gap:1rem}.reader-run-steps strong{font-size:.8rem}.reader-run-steps small,.reader-run-meta{color:var(--muted,#77786e);font-size:.72rem}.reader-run-steps p{font-size:.78rem;line-height:1.55;margin:.35rem 0}.reader-run-steps pre,.reader-quoted-text pre,.reader-diff pre{white-space:pre-wrap;overflow-wrap:anywhere;font:400 .78rem/1.55 ui-monospace,Consolas,monospace;background:#f2f0ea;border-radius:.45rem;padding:.7rem;margin:.35rem 0}.reader-run-steps details summary,.reader-quoted-text summary{cursor:pointer;color:var(--accent,#596c57);font-size:.76rem}.reader-run-steps h5{margin:.55rem 0 .2rem;font-size:.72rem}.reader-error,.reader-notice{color:#805d33;background:#f8f1e3;border-radius:.55rem;padding:.65rem .8rem;font-size:.82rem}.reader-proposal{margin:1.3rem 0;padding:1.15rem;border:1px solid #d8dfd4;border-radius:.8rem;background:#f8faf6}.reader-proposal>header{display:flex;justify-content:space-between;align-items:flex-start}.reader-proposal h3{font:600 1.15rem Georgia,serif;margin:.3rem 0}.reader-diff{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}.reader-diff article{min-width:0}.reader-diff h4{margin:.5rem 0;font-size:.78rem;color:var(--muted,#77786e)}.reader-diff pre{min-height:4rem;font-family:Georgia,'Noto Serif SC',serif;font-size:.9rem;background:#f0eee9}.reader-diff .reader-after pre{background:#edf3eb;color:#324a34}.reader-dialog-backdrop{position:fixed;inset:0;z-index:20;background:#1c201b88;display:grid;place-items:center;padding:1rem}.reader-dialog{position:relative;width:min(620px,100%);max-height:90vh;overflow:auto;background:#fffefa;border-radius:1rem;padding:1.5rem;box-shadow:0 20px 70px #13181245}.reader-dialog-close{position:absolute;right:1rem;top:1rem;font-size:1.5rem}.reader-dialog h3{font:600 1.45rem Georgia,serif;margin:.4rem 0}.reader-dialog label{display:grid;gap:.4rem;font-weight:650;font-size:.83rem}.reader-dialog textarea{resize:vertical;min-height:6rem;padding:.75rem;border:1px solid var(--line,#e8e5dc);border-radius:.6rem;font:inherit;line-height:1.55}.reader-quoted-text{margin:.8rem 0}.reader-dialog-actions{display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem}.reader-primary-button{border:1px solid #536b54;background:#536b54;color:white;border-radius:99px;padding:.55rem .9rem;font-size:.82rem;font-weight:650}.reader-primary-button:hover{background:#415643}.reader-notice{margin:.7rem 0}.reader-muted{color:var(--muted,#77786e)}
:global(:root[data-theme='dark'] .reader-prose),:global(:root[data-theme='dark'] .reader-dialog),:global(:root[data-theme='dark'] .reader-chapter-heading),:global(:root[data-theme='dark'] .reader-scene-heading),:global(:root[data-theme='dark'] .reader-run-content){color:var(--ink)}
:global(:root[data-theme='dark'] .reader-character-mention),:global(:root[data-theme='dark'] .reader-tool-button),:global(:root[data-theme='dark'] .reader-run-steps details summary){color:var(--green)}
:global(:root[data-theme='dark'] .reader-scene-link:hover),:global(:root[data-theme='dark'] .reader-scene-link.active),:global(:root[data-theme='dark'] .reader-selection-tools),:global(:root[data-theme='dark'] .reader-run-steps pre),:global(:root[data-theme='dark'] .reader-quoted-text pre),:global(:root[data-theme='dark'] .reader-diff pre),:global(:root[data-theme='dark'] .reader-dialog textarea){background:var(--soft);color:var(--ink)}
:global(:root[data-theme='dark'] .reader-process),:global(:root[data-theme='dark'] .reader-dialog){background:var(--surface);border-color:var(--line)}
:global(:root[data-theme='dark'] .reader-proposal){background:color-mix(in srgb,var(--surface) 70%,var(--soft));border-color:var(--line)}
:global(:root[data-theme='dark'] .reader-diff .reader-after pre){background:var(--soft);color:var(--ink)}
:global(:root[data-theme='dark'] .reader-error),:global(:root[data-theme='dark'] .reader-notice){background:color-mix(in srgb,var(--amber) 14%,var(--surface));color:var(--amber)}
.reader-chapter-link,.reader-scene-link{width:100%;border:0;background:transparent;text-align:left;font:inherit;cursor:pointer}
.reader-stage-card{display:block;position:fixed;z-index:40;left:auto;top:auto;bottom:auto;transform:translate(-50%,-100%);width:min(300px,calc(100vw - 24px));max-height:min(45vh,320px);overflow:auto;pointer-events:auto}
.reader-prompt-message{min-width:0}.reader-prompt-message pre{margin:.35rem 0;max-height:240px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--soft,#f1efe8);color:var(--ink);border-radius:.45rem;padding:.7rem;font:400 .78rem/1.55 ui-monospace,Consolas,monospace}.reader-tool-call{overflow-wrap:anywhere;color:var(--accent,#596c57);font-size:.72rem}.reader-attempts{border-top:1px solid var(--line,#e8e5dc);margin-top:.7rem}.reader-attempts>article{display:grid;gap:.25rem;padding:.5rem 0;border-top:1px solid var(--line,#e8e5dc)}.reader-attempts>article>strong{font-size:.76rem}.reader-attempts>article>span{font-size:.69rem;color:var(--muted,#77786e)}.reader-attempts details{margin:.25rem 0}.reader-attempts details summary{cursor:pointer;font-size:.71rem;color:var(--accent,#596c57)}.reader-attempts pre{max-height:220px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--soft,#f1efe8);color:var(--ink);border-radius:.45rem;padding:.65rem;font:400 .72rem/1.55 ui-monospace,Consolas,monospace}
@media(max-width:760px){.reader-layout{grid-template-columns:1fr}.reader-outline{position:static;border-right:0;border-bottom:1px solid var(--line,#e8e5dc);max-height:9rem;padding:.6rem 0;display:flex;gap:.6rem;overflow:auto;align-items:flex-start}.reader-nav-title{display:none}.reader-nav-chapter{display:flex;margin:0;flex:0 0 auto}.reader-chapter-link,.reader-scene-link{white-space:nowrap;margin:0}.reader-diff{grid-template-columns:1fr}.reader-heading{align-items:flex-start}.reader-revision{display:none}}
.novel-reader{color:var(--text,var(--ink));}
.reader-heading{align-items:center;border-color:var(--border,var(--line));padding-bottom:1rem;margin-bottom:1rem}
.reader-heading-copy p{margin:.3rem 0 0;color:var(--text-muted,var(--muted));font-size:.8rem}
.reader-heading-actions{display:flex;align-items:center;gap:.5rem}
.reader-layout{display:block;max-width:860px;margin:0 auto}
.reader-body{padding:0 0 3rem}
.reader-chapter-heading{padding:1.1rem 0 .75rem;border-color:var(--border,var(--line))}
.reader-chapter-heading h3{font:600 1.2rem/1.4 Inter,"Microsoft YaHei",system-ui,sans-serif;letter-spacing:-.015em}
.reader-scene{padding:1.15rem 0 .8rem;border-color:var(--border,var(--line))}
.reader-scene-heading h4{font:600 .9rem/1.4 Inter,"Microsoft YaHei",system-ui,sans-serif;color:var(--text,var(--ink))}
.reader-prose{max-width:72ch;margin:1rem auto 1.1rem;color:var(--text,var(--ink));font:400 1.05rem/2.08 var(--reading-font,var(--serif,Georgia)),"Noto Serif SC","Songti SC",serif}
.reader-character-mention{color:var(--accent,#536bce);text-decoration-color:color-mix(in srgb,var(--accent,#536bce) 36%,transparent)}
.reader-character-mention:hover{color:var(--accent,#536bce)}
.reader-selection-tools{background:var(--accent-soft,var(--soft));color:var(--text-muted,var(--muted));border:1px solid var(--border,var(--line));padding:.35rem .5rem}
.reader-process{background:var(--surface-muted,var(--surface));border-color:var(--border,var(--line))}
.reader-run-steps li{border-color:var(--border,var(--line))}
.reader-run-steps pre,.reader-quoted-text pre,.reader-diff pre{background:var(--surface-muted,var(--soft));color:var(--text,var(--ink));border:1px solid var(--border,var(--line))}
.reader-proposal{background:var(--surface-muted,var(--surface));border-color:var(--border,var(--line))}
.reader-diff .reader-after pre{background:color-mix(in srgb,var(--success,#33836c) 10%,var(--surface));color:var(--text,var(--ink))}
.reader-dialog-form{display:grid;gap:.65rem}
.reader-dialog{padding:0;max-height:90dvh;overflow:auto;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:12px}
.reader-dialog-form h3{font:600 1.2rem/1.35 Inter,"Microsoft YaHei",system-ui,sans-serif;margin:0}
.reader-dialog-form label{display:grid;gap:.4rem;font-size:.8rem;font-weight:600}
.reader-dialog-actions{display:flex;justify-content:flex-end;gap:.45rem;flex-wrap:wrap;margin-top:.5rem}
.reader-directory-drawer{color:var(--text,var(--ink))}
.reader-drawer-intro,.reader-empty-note{font-size:.8rem;line-height:1.55;color:var(--text-muted,var(--muted))}
.reader-chapter-label{font-weight:600}
.reader-chapter-count{margin-left:auto;color:var(--text-muted,var(--muted));font-size:.72rem}
.reader-nav-scenes{display:grid;gap:.25rem;padding:.15rem 0 .55rem}
.reader-nav-scenes .reader-scene-link{justify-content:flex-start;margin:0;text-align:left;height:auto;min-height:34px}
.reader-stage-card{position:fixed;z-index:40;left:auto;top:auto;bottom:auto;transform:translateX(-50%);width:min(320px,calc(100vw - 24px));background:var(--text,#242625);color:var(--surface,#fff);}
@media(max-width:640px){.reader-heading{align-items:flex-start}.reader-heading-actions{flex-direction:column;align-items:flex-end}.reader-layout{width:100%}.reader-chapter-heading{align-items:flex-end}.reader-prose{font-size:1rem;line-height:1.95}.reader-diff{grid-template-columns:1fr}.reader-selection-tools{max-width:100%;flex-wrap:wrap}}
:global(:root[data-theme='dark'] .reader-prose),:global(:root[data-theme='dark'] .reader-dialog),:global(:root[data-theme='dark'] .reader-chapter-heading),:global(:root[data-theme='dark'] .reader-scene-heading),:global(:root[data-theme='dark'] .reader-run-content){color:var(--text,var(--ink))}
:global(:root[data-theme='dark'] .reader-dialog-form),:global(:root[data-theme='dark'] .reader-proposal),:global(:root[data-theme='dark'] .reader-process){background:var(--surface);color:var(--text);border-color:var(--border)}
:global(:root[data-theme='dark'] .reader-dialog){background:var(--surface);color:var(--text);border-color:var(--border)}
:global(:root[data-theme='dark'] .reader-run-steps pre),:global(:root[data-theme='dark'] .reader-quoted-text pre),:global(:root[data-theme='dark'] .reader-diff pre){background:var(--surface-muted);color:var(--text);border-color:var(--border)}
</style>
