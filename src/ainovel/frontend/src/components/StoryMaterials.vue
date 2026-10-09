<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { NButton, NCollapse, NCollapseItem, NDrawer, NDrawerContent, NInput, NSelect, NTab, NTabs, NTag } from "naive-ui";
import { type CommandPayload, type CommandView, type Schemas, type StoryView } from "../api/client";
import EntryListEditor from "./EntryListEditor.vue";

type ExecuteCommand = (payload: CommandPayload) => Promise<CommandView>;
type EntryInput = Schemas["Entry-Input"];
type UpdateStory = Schemas["UpdateStoryRequest-Input"];
type PreferenceOutput = Schemas["Preference-Output"];
type PreferenceInput = Schemas["Preference-Input"];
type RecordGroup = "mainlines" | "subplots" | "future_directions" | "foreshadows" | "outline" | "notes" | "drafts";
type RecordOutput = Schemas["PlotThread-Output"] | Schemas["Foreshadow-Output"] | Schemas["OutlineBeat-Output"] | Schemas["WriterNote-Output"] | Schemas["WorkingDraft-Output"];
type RecordEdit =
  | { group: "mainlines" | "subplots" | "future_directions"; record: Schemas["PlotThread-Input"] }
  | { group: "foreshadows"; record: Schemas["Foreshadow-Input"] }
  | { group: "outline"; record: Schemas["OutlineBeat-Input"] }
  | { group: "notes"; record: Schemas["WriterNote-Input"] }
  | { group: "drafts"; record: Schemas["WorkingDraft-Input"] };
type RecordDraft = { group: RecordGroup; id: string; name: string; description: string; reason: string; status: string; extra: string; aux: string };

const props = defineProps<{ view: StoryView; disabled: boolean; executeCommand: ExecuteCommand }>();
const emit = defineEmits<{ "revision-changed": [] }>();
const section = ref<"world" | "characters" | "plans" | "preferences" | "directions">("world");
const busy = ref(false);
const notice = ref("");
const editingPremise = ref(false);
const premiseDraft = ref("");
const editingRecord = ref<RecordDraft | null>(null);
const preferenceForms = reactive<Record<string, { name: string; description: string; reason: string }>>({});
const addPreference = ref<"likes" | "dislikes" | null>(null);
const newPreference = reactive({ name: "", description: "", reason: "" });
const directionText = ref("");
const directionScope = ref<Schemas["Direction"]["scope"]>("global");
const directionHard = ref(false);
const writerProgress = ref(props.view.story.writer.progress);
const writerIntent = ref(props.view.story.writer.current_user_intent);
const editingProgress = ref(false);
const editingIntent = ref(false);
const addCharacterField = ref<string | null>(null);
const newCharacterField = reactive({ characterId: "", name: "", description: "" });

const story = computed(() => props.view.story);
const materialCount = computed(() => [story.value.world.entries.length, story.value.characters.length,
  story.value.writer.mainlines.length + story.value.writer.subplots.length + story.value.writer.foreshadows.length + story.value.writer.outline.length,
  story.value.preferences.likes.length + story.value.preferences.dislikes.length, story.value.directions.length]);
const sectionCards = computed(() => [
  { id: "world" as const, label: "世界观", note: "故事里成立的规则和背景。", count: materialCount.value[0] },
  { id: "characters" as const, label: "人物", note: "人物档案，以及随剧情变化的状态。", count: materialCount.value[1] },
  { id: "plans" as const, label: "剧情计划", note: "准备发生的事，与已经发生的事实分开。", count: materialCount.value[2] },
  { id: "preferences" as const, label: "创作偏好", note: "影响后续生成，不会自动改写已有正文。", count: materialCount.value[3] },
  { id: "directions" as const, label: "写作要求", note: "按本次范围应用的约束与表达策略。", count: materialCount.value[4] },
]);
watch(() => props.view.revision_id, () => {
  for (const key of Object.keys(preferenceForms)) delete preferenceForms[key];
  premiseDraft.value = story.value.world.premise;
  writerProgress.value = story.value.writer.progress;
  writerIntent.value = story.value.writer.current_user_intent;
  editingPremise.value = false; editingRecord.value = null; notice.value = "";
});

function entryPayload(area: "world" | "preferences" | "writer", entry: EntryInput): UpdateStory {
  const collection = { upsert: [entry], remove: [] as string[] };
  if (area === "world") return { kind: "update_story", patch: { reason: "用户编辑世界观条目", world: { entries: collection } } };
  if (area === "preferences") return { kind: "update_story", patch: { reason: "用户编辑创作偏好资料", preferences: { custom_fields: collection } } };
  return { kind: "update_story", patch: { reason: "用户编辑剧情计划备注", writer: { custom_fields: collection } } };
}
function entryDeletePayload(area: "world" | "preferences" | "writer", id: string): UpdateStory {
  const collection = { upsert: [], remove: [id] };
  if (area === "world") return { kind: "update_story", patch: { reason: "用户删除世界观条目", world: { entries: collection } } };
  if (area === "preferences") return { kind: "update_story", patch: { reason: "用户删除创作偏好资料", preferences: { custom_fields: collection } } };
  return { kind: "update_story", patch: { reason: "用户删除剧情计划备注", writer: { custom_fields: collection } } };
}
async function runCommand(payload: CommandPayload, success: string) {
  if (busy.value || props.disabled) return;
  busy.value = true; notice.value = "已提交，正在保存资料…";
  try {
    const result = await props.executeCommand(payload);
    notice.value = result.status === "failed" ? result.error || "保存失败。" : success;
    if (result.status !== "failed") emit("revision-changed");
  } catch (error) { notice.value = error instanceof Error ? error.message : "保存失败。"; }
  finally { busy.value = false; }
}
async function saveUpdate(payload: UpdateStory, success = "故事资料已保存；它会影响后续生成，不会自动修改已有正文。") {
  await runCommand(payload, success);
}
async function savePremise() {
  const premise = premiseDraft.value.trim();
  if (!premise || premise === story.value.world.premise) { editingPremise.value = false; return; }
  await saveUpdate({ kind: "update_story", patch: { reason: "用户编辑世界观说明", world: { premise } } });
  editingPremise.value = false;
}

function preferenceForm(kind: "likes" | "dislikes", item: PreferenceOutput) {
  const key = `${kind}:${item.id}`;
  preferenceForms[key] ??= { name: item.name, description: item.description, reason: item.reason };
  return preferenceForms[key];
}
function preferencePayload(kind: "likes" | "dislikes", item: PreferenceInput): UpdateStory {
  const patch = kind === "likes" ? { likes: { upsert: [item], remove: [] as string[] } } : { dislikes: { upsert: [item], remove: [] as string[] } };
  return { kind: "update_story", patch: { reason: "用户编辑创作偏好", preferences: patch } };
}
async function savePreference(kind: "likes" | "dislikes", item: PreferenceOutput) {
  const form = preferenceForm(kind, item);
  const input: PreferenceInput = { id: item.id, name: form.name.trim(), description: form.description.trim(), reason: form.reason.trim(),
    source_scene_id: item.source_scene_id, custom_fields: item.custom_fields, origin: "explicit", confidence: 1, active: item.active };
  if (!input.name) return;
  await saveUpdate(preferencePayload(kind, input));
}
async function removePreference(kind: "likes" | "dislikes", item: PreferenceOutput) {
  if (!item.id) return;
  const patch = kind === "likes" ? { likes: { upsert: [], remove: [item.id] } } : { dislikes: { upsert: [], remove: [item.id] } };
  await saveUpdate({ kind: "update_story", patch: { reason: "用户删除创作偏好", preferences: patch } });
}
async function createPreference() {
  const kind = addPreference.value;
  if (!kind || !newPreference.name.trim()) return;
  const item: PreferenceInput = { id: crypto.randomUUID(), name: newPreference.name.trim(), description: newPreference.description.trim(),
    reason: newPreference.reason.trim(), origin: "explicit", confidence: 1, active: true };
  await saveUpdate(preferencePayload(kind, item));
  newPreference.name = ""; newPreference.description = ""; newPreference.reason = ""; addPreference.value = null;
}

function recordList(group: RecordGroup): RecordOutput[] {
  const writer = story.value.writer;
  switch (group) {
    case "mainlines": return writer.mainlines;
    case "subplots": return writer.subplots;
    case "future_directions": return writer.future_directions;
    case "foreshadows": return writer.foreshadows;
    case "outline": return writer.outline;
    case "notes": return writer.notes;
    case "drafts": return writer.drafts;
  }
}
function recordGroupLabel(group: RecordGroup) {
  return ({ mainlines: "主线", subplots: "支线", future_directions: "近期计划", foreshadows: "伏笔", outline: "大纲节点", notes: "工作笔记", drafts: "工作草稿" } satisfies Record<RecordGroup, string>)[group];
}
function beginRecord(group: RecordGroup, record?: RecordOutput) {
  const base = record;
  const id = base?.id ?? crypto.randomUUID();
  const defaults: RecordDraft = { group, id, name: base?.name ?? "", description: base?.description ?? "", reason: base?.reason ?? "",
    status: "planned", extra: "", aux: "" };
  if (base) {
    if (group === "mainlines" || group === "subplots" || group === "future_directions") {
      if ("status" in base) defaults.status = base.status;
      if ("progress" in base) defaults.extra = base.progress;
      if ("next_steps" in base) defaults.aux = (base.next_steps ?? []).join("\n");
    } else if (group === "foreshadows") {
      if ("status" in base) defaults.status = base.status;
      if ("payoff" in base) defaults.extra = base.payoff;
    } else if (group === "outline") {
      if ("status" in base) defaults.status = base.status;
      if ("order" in base) defaults.extra = String(base.order);
    } else if (group === "notes") {
      if ("category" in base) defaults.extra = base.category;
    } else if (group === "drafts") {
      if ("status" in base) defaults.status = base.status;
      if ("content" in base) defaults.extra = base.content;
      if ("kind" in base) defaults.aux = base.kind;
    }
  }
  editingRecord.value = defaults;
}
function saveRecord() {
  const draft = editingRecord.value;
  if (!draft || !draft.name.trim()) return;
  const old = recordList(draft.group).find(item => item.id === draft.id);
  const base = { id: draft.id, name: draft.name.trim(), description: draft.description.trim(), reason: draft.reason.trim(),
    source_scene_id: old?.source_scene_id ?? null };
  let edit: RecordEdit;
  if (["mainlines", "subplots", "future_directions"].includes(draft.group)) {
    const source = old && "status" in old && "progress" in old ? old : null;
    const statuses = ["planned", "active", "resolved", "abandoned"] as const;
    const status = statuses.find(value => value === draft.status) ?? "planned";
    const record: Schemas["PlotThread-Input"] = { ...base, status, progress: draft.extra.trim(), next_steps: draft.aux.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
      character_ids: source?.character_ids ?? [], depends_on: source?.depends_on ?? [], custom_fields: source?.custom_fields ?? [] };
    const group = draft.group;
    if (group === "mainlines" || group === "subplots" || group === "future_directions") edit = { group, record };
    else return;
  } else if (draft.group === "foreshadows") {
    const source = old && "payoff" in old ? old : null;
    const statuses = ["planned", "planted", "paid_off", "dropped"] as const;
    const status = statuses.find(value => value === draft.status) ?? "planned";
    edit = { group: "foreshadows", record: { ...base, status, payoff: draft.extra.trim(), planted_scene_id: source?.planted_scene_id ?? null,
      paid_off_scene_id: source?.paid_off_scene_id ?? null, custom_fields: source?.custom_fields ?? [] } };
  } else if (draft.group === "outline") {
    const source = old && "order" in old ? old : null;
    const statuses = ["planned", "active", "completed", "abandoned"] as const;
    const status = statuses.find(value => value === draft.status) ?? "planned";
    const numericOrder = Number(draft.extra);
    edit = { group: "outline", record: { ...base, status, order: Number.isFinite(numericOrder) && numericOrder >= 0 ? Math.floor(numericOrder) : 0,
      thread_id: source?.thread_id ?? null, custom_fields: source?.custom_fields ?? [] } };
  } else if (draft.group === "notes") {
    const source = old && "category" in old ? old : null;
    edit = { group: "notes", record: { ...base, category: draft.extra.trim(), custom_fields: source?.custom_fields ?? [] } };
  } else {
    const source = old && "content" in old ? old : null;
    const kinds = ["scene", "outline", "idea"] as const;
    const kind = kinds.find(value => value === draft.aux) ?? "idea";
    const statuses = ["draft", "used", "discarded"] as const;
    const status = statuses.find(value => value === draft.status) ?? "draft";
    edit = { group: "drafts", record: { ...base, kind, content: draft.extra, status, custom_fields: source?.custom_fields ?? [] } };
  }
  const record = edit.record;
  const patch = { upsert: [record], remove: [] as string[] };
  const writer = { [edit.group]: patch };
  editingRecord.value = null;
  void saveUpdate({ kind: "update_story", patch: { reason: `用户编辑${recordGroupLabel(edit.group)}`, writer } });
}
function removeRecord(group: RecordGroup, record: RecordOutput) {
  if (!record.id) return;
  const writer = { [group]: { upsert: [], remove: [record.id] } };
  void saveUpdate({ kind: "update_story", patch: { reason: `用户删除${recordGroupLabel(group)}`, writer } });
}
function recordStatusOptions(group: RecordGroup): string[] {
  if (group === "foreshadows") return ["planned", "planted", "paid_off", "dropped"];
  if (group === "outline") return ["planned", "active", "completed", "abandoned"];
  if (group === "drafts") return ["draft", "used", "discarded"];
  return ["planned", "active", "resolved", "abandoned"];
}
function statusLabel(status: string) {
  return ({ planned: "计划中", active: "进行中", resolved: "已解决", abandoned: "已放弃", planted: "已埋下", paid_off: "已回收", dropped: "已舍弃", completed: "已完成", draft: "草稿", used: "已使用", discarded: "已搁置" } as Record<string, string>)[status] ?? status;
}

async function saveWriterText(field: "progress" | "current_user_intent") {
  const value = field === "progress" ? writerProgress.value : writerIntent.value;
  const old = field === "progress" ? story.value.writer.progress : story.value.writer.current_user_intent;
  if (value === old) { editingProgress.value = false; editingIntent.value = false; return; }
  await saveUpdate({ kind: "update_story", patch: { reason: field === "progress" ? "用户更新写作进展" : "用户更新当前创作意图", writer: { [field]: value } } });
  editingProgress.value = false; editingIntent.value = false;
}

async function saveCharacterEntry(characterId: string, entry: EntryInput) {
  const payload: UpdateStory = { kind: "update_story", patch: { reason: "用户编辑人物档案" }, character_edits: [{ character_id: characterId,
    profile: { entries: { upsert: [entry], remove: [] } }, reason: "用户编辑人物档案" }] };
  await saveUpdate(payload);
}
async function removeCharacterEntry(characterId: string, id: string) {
  const payload: UpdateStory = { kind: "update_story", patch: { reason: "用户删除人物档案条目" }, character_edits: [{ character_id: characterId,
    profile: { entries: { upsert: [], remove: [id] } }, reason: "用户删除人物档案条目" }] };
  await saveUpdate(payload);
}
function createCharacterField(characterId: string) { addCharacterField.value = characterId; newCharacterField.characterId = characterId; newCharacterField.name = ""; newCharacterField.description = ""; }
async function saveNewCharacterField() {
  const character = story.value.characters.find(item => item.id === newCharacterField.characterId);
  if (!character?.id || !newCharacterField.name.trim()) return;
  await saveCharacterEntry(character.id, { id: crypto.randomUUID(), name: newCharacterField.name.trim(), description: newCharacterField.description.trim(), reason: "用户补充人物档案" });
  addCharacterField.value = null;
}

async function createDirection() {
  const instruction = directionText.value.trim();
  if (!instruction) return;
  const latestScene = story.value.scenes.at(-1);
  const latestChapter = story.value.chapters.at(-1);
  await runCommand({ kind: "direction", direction: { id: crypto.randomUUID(), instruction, scope: directionScope.value,
    hard: directionHard.value, active: true, chapter_id: directionScope.value === "chapter" ? latestChapter?.id ?? null : null,
    scene_id: directionScope.value === "scene" ? latestScene?.id ?? null : null } }, "写作要求已保存，会应用于后续生成。");
  directionText.value = "";
}
async function toggleDirection(direction: Schemas["Direction"]) {
  await runCommand({ kind: "direction", direction: { ...direction, active: !direction.active } }, direction.active ? "已停用这条要求。" : "已重新启用这条要求。");
}
function scopeLabel(scope: Schemas["Direction"]["scope"]) { return ({ global: "全局", future: "后续", chapter: "章节", scene: "场景" })[scope]; }

const descriptions: Record<typeof section.value, string> = {
  world: "这里记录故事中成立的规则与背景。确认保存后会成为后续生成的依据；已有正文不会自动改写。",
  characters: "档案描述人物较稳定的资料。随剧情变化的阶段状态由正文确认后的运行记录维护。",
  plans: "大纲、主线、伏笔和下一步计划都是未来安排，不能代替已经发生的事实。",
  preferences: "明确写下喜欢和不喜欢的表达方式。它们会影响后续生成，不会自动改动现有正文。",
  directions: "写作要求可按全局、后续、章节或场景生效，并区分必须遵守与尽量采用。",
};
</script>

<template>
  <section class="story-materials" aria-label="故事资料">
    <header class="materials-heading"><div><span class="eyebrow">AI 长期依据什么写</span><h2>故事资料</h2><p>资料修改后影响后续生成；要改已有正文，请在正文中提出修改建议。</p></div><span class="materials-revision">当前版本 · {{ view.revision_id.slice(0, 8) }}</span></header>
    <NTabs v-model:value="section" type="line" animated class="materials-tabs" aria-label="资料类别">
      <NTab v-for="card in sectionCards" :key="card.id" :name="card.id" :tab="`${card.label} · ${card.count}`" />
    </NTabs>
    <p class="materials-explainer">{{ descriptions[section] }}</p>
    <p v-if="notice" class="materials-notice" role="status">{{ notice }}</p>

    <section v-if="section === 'world'" class="materials-section">
      <article class="material-card"><header><div><span class="eyebrow">已发生事实的背景</span><h3>世界设定</h3></div><button class="material-action" :disabled="disabled || busy" @click="editingPremise = !editingPremise; premiseDraft = story.world.premise">{{ editingPremise ? "取消" : "编辑说明" }}</button></header>
        <p v-if="!editingPremise" class="material-premise">{{ story.world.premise || "还没有写下世界的基本规则。" }}</p>
        <form v-else class="material-form" @submit.prevent="savePremise"><textarea v-model="premiseDraft" rows="4" maxlength="8000" placeholder="这个故事世界中有哪些成立的规则？"/><div><button type="button" @click="editingPremise = false">取消</button><button class="material-primary" :disabled="disabled || busy || !premiseDraft.trim()">保存背景</button></div></form>
      </article>
      <article class="material-card"><header><div><span class="eyebrow">可自由补充</span><h3>世界观条目</h3></div></header><EntryListEditor title="已记录的规则和背景" :entries="story.world.entries" :disabled="disabled || busy" note="可用子条目整理一项规则下的细节。" @save="entry => saveUpdate(entryPayload('world', entry))" @remove="id => saveUpdate(entryDeletePayload('world', id))"/></article>
    </section>

    <section v-else-if="section === 'characters'" class="materials-section">
      <p v-if="!story.characters.length" class="materials-empty">正文确认新角色后，人物档案会出现在这里。</p>
      <article v-for="character in story.characters" :key="character.id" class="material-card character-material">
        <header><div><span class="eyebrow">人物档案</span><h3>{{ character.name }}</h3></div><a class="material-link" :href="`/characters?revision=${view.revision_id}#character-${character.id}`">查看人物与阶段</a></header>
        <p class="materials-muted">人物资料介绍相对稳定的信息；阶段状态与心理请在对应场景的正文标记上查看。</p>
        <EntryListEditor title="档案条目" :entries="character.profile.entries" :disabled="disabled || busy" @save="entry => saveCharacterEntry(character.id!, entry)" @remove="id => removeCharacterEntry(character.id!, id)"/>
        <form v-if="addCharacterField === character.id" class="material-inline-form" @submit.prevent="saveNewCharacterField"><input v-model="newCharacterField.name" required maxlength="160" placeholder="档案项目名称"/><textarea v-model="newCharacterField.description" rows="2" placeholder="人物资料"/><button class="material-primary" :disabled="disabled || busy || !newCharacterField.name.trim()">添加档案</button><button type="button" @click="addCharacterField = null">取消</button></form>
        <button v-else class="material-add" :disabled="disabled || busy" @click="createCharacterField(character.id!)">＋ 补充人物档案</button>
      </article>
    </section>

    <section v-else-if="section === 'plans'" class="materials-section">
      <article class="material-card"><header><div><span class="eyebrow">写作进展</span><h3>当前进展说明</h3></div><button class="material-action" :disabled="disabled || busy" @click="editingProgress = !editingProgress; writerProgress = story.writer.progress">{{ editingProgress ? "取消" : "编辑" }}</button></header>
        <p v-if="!editingProgress" class="material-premise">{{ story.writer.progress || "暂时没有记录当前进展。" }}</p><form v-else class="material-form" @submit.prevent="saveWriterText('progress')"><textarea v-model="writerProgress" rows="3" maxlength="4000"/><div><button type="button" @click="editingProgress = false">取消</button><button class="material-primary" :disabled="disabled || busy">保存进展</button></div></form>
      </article>
      <article class="material-card"><header><div><span class="eyebrow">下一步目标</span><h3>当前创作意图</h3></div><button class="material-action" :disabled="disabled || busy" @click="editingIntent = !editingIntent; writerIntent = story.writer.current_user_intent">{{ editingIntent ? "取消" : "编辑" }}</button></header>
        <p v-if="!editingIntent" class="material-premise">{{ story.writer.current_user_intent || "还没有当前创作意图。" }}</p><form v-else class="material-form" @submit.prevent="saveWriterText('current_user_intent')"><textarea v-model="writerIntent" rows="3" maxlength="4000"/><div><button type="button" @click="editingIntent = false">取消</button><button class="material-primary" :disabled="disabled || busy">保存意图</button></div></form>
      </article>
      <article class="material-card"><header><div><span class="eyebrow">未来安排</span><h3>剧情计划</h3></div></header>
        <div class="record-groups">
          <NCollapse :default-expanded-names="['mainlines']" arrow-placement="right">
          <NCollapseItem v-for="group in (['mainlines','subplots','future_directions','foreshadows','outline','notes','drafts'] as const)" :key="group" :name="group">
            <template #header><span class="record-group-title">{{ recordGroupLabel(group) }}</span><NTag size="small" round :bordered="false">{{ recordList(group).length }}</NTag></template>
            <div class="record-group">
            <header><p>{{ group === 'foreshadows' ? '记录已经埋下但尚未回收的线索。' : group === 'drafts' ? '工作草稿默认不代表正式正文。' : group === 'notes' ? '保留尚未归入正式设定的观察。' : '记录准备发生的事与当前进展。' }}</p><NButton size="tiny" secondary :disabled="disabled || busy" @click="beginRecord(group)">添加记录</NButton></header>
            <article v-for="record in recordList(group)" :key="record.id" class="story-record">
              <div class="story-record-copy"><strong>{{ record.name }}</strong><span>{{ 'status' in record ? statusLabel(record.status) : '记录' }}</span><p v-if="record.description">{{ record.description }}</p><small v-if="'progress' in record && record.progress">进展：{{ record.progress }}</small><small v-if="'payoff' in record && record.payoff">回收方向：{{ record.payoff }}</small><small v-if="'content' in record && record.content">草稿内容：{{ record.content }}</small><details v-if="record.reason || record.source_scene_id"><summary>来源与说明</summary><p>{{ record.reason || '未填写说明' }}</p><code v-if="record.source_scene_id">来源场景 {{ record.source_scene_id }}</code></details></div>
              <div class="story-record-actions"><button :disabled="disabled || busy" @click="beginRecord(group, record)">编辑</button><button class="material-danger" :disabled="disabled || busy" @click="removeRecord(group, record)">删除</button></div>
            </article><p v-if="!recordList(group).length" class="materials-empty small">暂无{{ recordGroupLabel(group) }}。可以从下一件准备发生的事开始。</p>
            </div>
          </NCollapseItem>
          </NCollapse>
        </div>
      </article>
      <NDrawer :show="!!editingRecord" placement="right" :width="'min(520px, 100vw)'" @update:show="visible => !visible && (editingRecord = null)"><NDrawerContent :title="editingRecord ? `${recordGroupLabel(editingRecord.group)} · 编辑` : '剧情计划'" closable>
        <form v-if="editingRecord" class="record-editor" @submit.prevent="saveRecord">
          <label>名称<NInput v-model:value="editingRecord.name" required maxlength="200"/></label><label>内容<NInput v-model:value="editingRecord.description" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" maxlength="8000"/></label><label>来源或决策依据<NInput v-model:value="editingRecord.reason" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" maxlength="2000" placeholder="说明这条计划的依据或相关线索"/></label>
          <template v-if="['mainlines','subplots','future_directions','foreshadows','outline','drafts'].includes(editingRecord.group)"><label>状态<select v-model="editingRecord.status"><option v-for="option in recordStatusOptions(editingRecord.group)" :key="option" :value="option">{{ statusLabel(option) }}</option></select></label></template>
          <label v-if="['mainlines','subplots','future_directions'].includes(editingRecord.group)">当前进展<NInput v-model:value="editingRecord.extra" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }"/></label>
          <label v-if="['mainlines','subplots','future_directions'].includes(editingRecord.group)">下一步（每行一项）<NInput v-model:value="editingRecord.aux" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }"/></label>
          <label v-if="editingRecord.group === 'foreshadows'">回收方向<NInput v-model:value="editingRecord.extra" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }"/></label>
          <label v-if="editingRecord.group === 'outline'">顺序<NInput v-model:value="editingRecord.extra" inputmode="numeric"/></label>
          <label v-if="editingRecord.group === 'notes'">笔记类别<NInput v-model:value="editingRecord.extra" maxlength="100" placeholder="例如：待验证的问题"/></label>
          <template v-if="editingRecord.group === 'drafts'"><label>草稿类型<select v-model="editingRecord.aux"><option value="idea">想法</option><option value="scene">场景</option><option value="outline">大纲</option></select></label><label>草稿内容<NInput v-model:value="editingRecord.extra" type="textarea" :autosize="{ minRows: 5, maxRows: 12 }" maxlength="12000"/></label></template>
          <div class="material-form-actions"><NButton secondary attr-type="button" @click="editingRecord = null">取消</NButton><NButton type="primary" attr-type="submit" :disabled="disabled || busy || !editingRecord.name.trim()" :loading="busy">保存这条记录</NButton></div>
        </form>
      </NDrawerContent></NDrawer>
      <article class="material-card"><header><div><span class="eyebrow">自由资料</span><h3>剧情计划备注</h3></div></header><EntryListEditor title="自定义记录" :entries="story.writer.custom_fields" :disabled="disabled || busy" note="适合暂时无法归入主线、大纲或笔记类别的内容。" @save="entry => saveUpdate(entryPayload('writer', entry))" @remove="id => saveUpdate(entryDeletePayload('writer', id))"/></article>
    </section>

    <section v-else-if="section === 'preferences'" class="materials-section">
      <article class="material-card"><header><div><span class="eyebrow">表达方式与限制</span><h3>你喜欢怎样的叙述</h3></div><button class="material-add" :disabled="disabled || busy" @click="addPreference = 'likes'">＋ 添加偏好</button></header>
        <div v-for="item in story.preferences.likes" :key="item.id" class="preference-row"><label>偏好<input v-model="preferenceForm('likes', item).name" maxlength="200"/></label><label>补充<textarea v-model="preferenceForm('likes', item).description" rows="2" maxlength="2000"/></label><label>原因 / 使用场景<textarea v-model="preferenceForm('likes', item).reason" rows="2" maxlength="1000"/></label><div class="story-record-actions"><small>{{ item.origin === 'inferred' ? 'AI 推测 · 请确认后使用' : '明确偏好' }}</small><button :disabled="disabled || busy" @click="savePreference('likes', item)">保存</button><button class="material-danger" :disabled="disabled || busy" @click="removePreference('likes', item)">删除</button></div></div>
        <p v-if="!story.preferences.likes.length" class="materials-empty">还没有明确的表达偏好。</p>
      </article>
      <article class="material-card"><header><div><span class="eyebrow">避免的写法</span><h3>你不喜欢什么</h3></div><button class="material-add" :disabled="disabled || busy" @click="addPreference = 'dislikes'">＋ 添加限制</button></header>
        <div v-for="item in story.preferences.dislikes" :key="item.id" class="preference-row"><label>限制<input v-model="preferenceForm('dislikes', item).name" maxlength="200"/></label><label>补充<textarea v-model="preferenceForm('dislikes', item).description" rows="2" maxlength="2000"/></label><label>原因 / 使用场景<textarea v-model="preferenceForm('dislikes', item).reason" rows="2" maxlength="1000"/></label><div class="story-record-actions"><small>{{ item.origin === 'inferred' ? 'AI 推测 · 请确认后使用' : '明确要求' }}</small><button :disabled="disabled || busy" @click="savePreference('dislikes', item)">保存</button><button class="material-danger" :disabled="disabled || busy" @click="removePreference('dislikes', item)">删除</button></div></div>
        <p v-if="!story.preferences.dislikes.length" class="materials-empty">还没有明确的写作限制。</p>
      </article>
      <form v-if="addPreference" class="material-card preference-editor" @submit.prevent="createPreference"><header><div><span class="eyebrow">{{ addPreference === 'likes' ? '新增表达偏好' : '新增写作限制' }}</span><h3>从具体要求开始</h3></div><button type="button" class="material-action" @click="addPreference = null">关闭</button></header><label>要求<input v-model="newPreference.name" required maxlength="200" placeholder="例如：用动作表现情绪"/></label><label>补充<textarea v-model="newPreference.description" rows="2" maxlength="2000"/></label><label>原因<textarea v-model="newPreference.reason" rows="2" maxlength="1000"/></label><div class="material-form-actions"><button class="material-primary" :disabled="disabled || busy || !newPreference.name.trim()">保存偏好</button></div></form>
      <article class="material-card"><header><div><span class="eyebrow">其他资料</span><h3>创作偏好备注</h3></div></header><EntryListEditor title="自由条目" :entries="story.preferences.custom_fields" :disabled="disabled || busy" @save="entry => saveUpdate(entryPayload('preferences', entry))" @remove="id => saveUpdate(entryDeletePayload('preferences', id))"/></article>
    </section>

    <section v-else class="materials-section">
      <article class="material-card"><header><div><span class="eyebrow">本作持续生效</span><h3>写作要求</h3></div></header><form class="direction-form" @submit.prevent="createDirection"><label>要求<NInput v-model:value="directionText" type="textarea" :autosize="{ minRows: 3, maxRows: 7 }" :maxlength="4000" placeholder="写下希望 AI 遵守的表达方式、视角或边界。"/></label><div class="direction-options"><label>生效范围<NSelect v-model:value="directionScope" :options="[{ label: '全局', value: 'global' }, { label: '后续内容', value: 'future' }, { label: '当前最后一章', value: 'chapter', disabled: !story.chapters.length }, { label: '当前最后一幕', value: 'scene', disabled: !story.scenes.length }]"/></label><label class="checkbox-row"><input v-model="directionHard" type="checkbox"/> 必须遵守（硬约束）</label><NButton type="primary" attr-type="submit" :disabled="disabled || busy || !directionText.trim()" :loading="busy">添加要求</NButton></div><p class="materials-muted">范围选择“章节”或“场景”时，要求会关联当前最后一章或最后一幕。全局与后续要求应用在之后的生成任务中。</p></form></article>
      <article class="material-card"><header><div><span class="eyebrow">生效状态</span><h3>已保存的要求 <small>{{ story.directions.length }}</small></h3></div></header>
        <article v-for="direction in story.directions" :key="direction.id" class="direction-row"><div class="direction-copy"><p>{{ direction.instruction }}</p><div class="direction-meta"><span>{{ scopeLabel(direction.scope) }}<template v-if="direction.chapter_id"> · {{ story.chapters.find(chapter => chapter.id === direction.chapter_id)?.title ?? '已归档章节' }}</template><template v-if="direction.scene_id"> · {{ story.scenes.find(scene => scene.id === direction.scene_id)?.title ?? '已归档场景' }}</template></span><span>{{ direction.hard ? '必须遵守' : '写作偏好' }}</span><span>{{ direction.active ? '生效中' : '已停用' }}</span></div><details v-if="direction.id"><summary>查看记录来源</summary><code>{{ direction.id }}</code></details></div><button class="material-action" :disabled="disabled || busy" @click="toggleDirection(direction)">{{ direction.active ? '停用' : '重新启用' }}</button></article>
        <p v-if="!story.directions.length" class="materials-empty">还没有按范围设置的写作要求。</p>
      </article>
    </section>
  </section>
</template>

<style scoped>
.story-materials{min-width:0;color:var(--ink,#28291f)}.materials-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;padding-bottom:1.1rem;border-bottom:1px solid var(--line,#e8e5dc)}.materials-heading h2{margin:.35rem 0;font:600 clamp(1.55rem,3vw,2.1rem)/1.2 Georgia,serif}.materials-heading p,.materials-muted{color:var(--muted,#77786e);font-size:.84rem;line-height:1.6;margin:.4rem 0}.materials-revision{border:1px solid var(--line,#e8e5dc);border-radius:99px;padding:.35rem .6rem;color:var(--muted,#77786e);white-space:nowrap;font-size:.72rem}.eyebrow{color:var(--accent,#596c57);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;font-weight:700}.materials-tabs{display:flex;gap:.35rem;overflow:auto;padding:.9rem 0 .2rem}.materials-tabs button{display:flex;gap:.45rem;align-items:center;white-space:nowrap;border:1px solid transparent;border-radius:99px;background:transparent;color:var(--muted,#77786e);padding:.48rem .7rem;font:inherit;font-size:.79rem;cursor:pointer}.materials-tabs button.selected{background:#eef1e9;border-color:#dfe5d9;color:#435a45}.materials-tabs small{font-size:.68rem;opacity:.75}.materials-explainer{margin:.7rem 0 1rem;color:var(--muted,#77786e);font-size:.82rem;line-height:1.55}.materials-notice{background:#edf3eb;border-radius:.55rem;padding:.65rem .8rem;color:#3d5b3d;font-size:.8rem}.materials-section{display:grid;gap:.8rem}.material-card{padding:1rem 1.1rem;border:1px solid var(--line,#e8e5dc);border-radius:.85rem;background:#fffefa}.material-card>header{display:flex;justify-content:space-between;align-items:flex-start;gap:.8rem}.material-card h3{font:600 1.13rem Georgia,serif;margin:.25rem 0}.material-card h3 small{font:400 .72rem system-ui,sans-serif;color:var(--muted,#77786e)}.material-premise{white-space:pre-wrap;line-height:1.7;color:#414239;font:400 .94rem/1.75 Georgia,'Noto Serif SC',serif;margin:.6rem 0 0}.material-action,.material-add,.material-primary,.material-form button,.story-record-actions button{cursor:pointer;font:inherit}.material-action,.material-add{border:1px solid var(--line,#e8e5dc);border-radius:99px;background:transparent;padding:.38rem .68rem;color:var(--accent,#596c57);font-size:.75rem;white-space:nowrap}.material-action:disabled,.material-add:disabled,.material-primary:disabled{opacity:.45;cursor:not-allowed}.material-form,.direction-form{display:grid;gap:.65rem;margin-top:.65rem}.material-form textarea,.direction-form textarea,.preference-row textarea,.preference-row input,.preference-editor input,.preference-editor textarea,.record-editor input,.record-editor textarea,.record-editor select,.material-inline-form input,.material-inline-form textarea,.direction-form select{width:100%;box-sizing:border-box;border:1px solid var(--line,#e8e5dc);border-radius:.5rem;padding:.58rem .65rem;font:inherit;font-size:.81rem;line-height:1.5;color:var(--ink,#28291f);background:white}.material-form textarea,.direction-form textarea,.preference-row textarea,.preference-editor textarea,.record-editor textarea,.material-inline-form textarea{resize:vertical}.material-form>div,.material-form-actions{display:flex;justify-content:flex-end;gap:.45rem}.material-form button,.material-form-actions button{border:1px solid var(--line,#e8e5dc);border-radius:99px;background:transparent;padding:.42rem .7rem;font-size:.75rem}.material-primary{border:1px solid #536b54!important;background:#536b54!important;color:white!important;border-radius:99px;padding:.48rem .8rem;font-size:.77rem;font-weight:650}.material-link{color:var(--accent,#596c57);font-size:.75rem;text-decoration:none;border-bottom:1px solid #a6b4a0;padding:.3rem 0}.materials-empty{color:var(--muted,#77786e);font-size:.83rem;padding:.4rem 0}.materials-empty.small{font-size:.74rem}.record-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem;margin-top:.6rem}.record-group{border:1px solid var(--line,#e8e5dc);border-radius:.7rem;padding:.75rem;min-width:0}.record-group>header{display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem}.record-group>header h4{margin:0;font-size:.82rem}.record-group>header h4 small{color:var(--muted,#77786e);font-weight:400}.record-group>header p{margin:.28rem 0;color:var(--muted,#77786e);font-size:.68rem;line-height:1.45}.story-record{display:flex;justify-content:space-between;align-items:flex-start;gap:.55rem;border-top:1px solid #eeeae2;padding:.6rem 0}.story-record-copy{display:grid;gap:.18rem;min-width:0}.story-record-copy>strong{font-size:.79rem;overflow-wrap:anywhere}.story-record-copy>span{color:var(--accent,#596c57);font-size:.67rem}.story-record-copy p,.story-record-copy small{margin:0;color:#56574f;font-size:.73rem;line-height:1.5;white-space:pre-wrap}.story-record-copy details{color:var(--muted,#77786e);font-size:.68rem;margin-top:.15rem}.story-record-copy details summary{cursor:pointer}.story-record-copy code,.direction-row code{font-size:.65rem;overflow-wrap:anywhere}.story-record-actions{display:flex;align-items:center;justify-content:flex-end;gap:.25rem;flex-wrap:wrap}.story-record-actions button{border:0;background:transparent;color:var(--accent,#596c57);font-size:.68rem;padding:.2rem}.story-record-actions button.material-danger{color:#9a6258}.record-editor{display:grid;gap:.65rem}.record-editor>label,.preference-editor>label,.preference-row>label{display:grid;gap:.25rem;color:var(--muted,#77786e);font-size:.72rem}.record-editor input,.record-editor textarea,.record-editor select{font-size:.8rem}.record-editor .material-form-actions{margin-top:.3rem}.preference-row{display:grid;gap:.5rem;padding:.7rem 0;border-top:1px solid #eeeae2}.preference-row:first-of-type{margin-top:.6rem}.preference-row .story-record-actions{justify-content:space-between}.preference-row small{margin-right:auto;color:var(--muted,#77786e);font-size:.69rem}.preference-editor{display:grid;gap:.6rem}.material-inline-form{display:grid;gap:.4rem;margin:.6rem 0}.material-inline-form button:not(.material-primary){border:0;background:none;color:var(--muted,#77786e);justify-self:end}.direction-options{display:flex;align-items:flex-end;gap:.7rem;flex-wrap:wrap}.direction-options>label:first-child{display:grid;gap:.25rem;font-size:.72rem;color:var(--muted,#77786e);min-width:150px}.checkbox-row{display:flex;align-items:center;gap:.35rem;font-size:.76rem;color:var(--muted,#77786e);padding:.55rem 0}.checkbox-row input{accent-color:#536b54}.direction-row{display:flex;justify-content:space-between;align-items:flex-start;gap:.8rem;padding:.75rem 0;border-top:1px solid #eeeae2}.direction-copy{min-width:0}.direction-copy>p{margin:.1rem 0 .4rem;white-space:pre-wrap;line-height:1.55;font-size:.85rem}.direction-meta{display:flex;gap:.4rem;flex-wrap:wrap;color:var(--muted,#77786e);font-size:.68rem}.direction-meta span+span:before{content:"·";margin-right:.4rem}.direction-copy details{margin-top:.3rem;color:var(--muted,#77786e);font-size:.68rem}.direction-copy details summary{cursor:pointer}
:global(:root[data-theme='dark'] .story-materials),:global(:root[data-theme='dark'] .material-card),:global(:root[data-theme='dark'] .record-group),:global(:root[data-theme='dark'] .story-record-copy),:global(:root[data-theme='dark'] .preference-row),:global(:root[data-theme='dark'] .direction-row){color:var(--ink)}
:global(:root[data-theme='dark'] .material-card),:global(:root[data-theme='dark'] .record-group){background:var(--surface);border-color:var(--line)}
:global(:root[data-theme='dark'] .material-premise),:global(:root[data-theme='dark'] .story-record-copy p),:global(:root[data-theme='dark'] .story-record-copy small){color:var(--ink)}
:global(:root[data-theme='dark'] .materials-tabs button.selected),:global(:root[data-theme='dark'] .materials-notice){background:var(--soft);border-color:var(--line);color:var(--ink)}
:global(:root[data-theme='dark'] .material-form textarea),:global(:root[data-theme='dark'] .direction-form textarea),:global(:root[data-theme='dark'] .preference-row textarea),:global(:root[data-theme='dark'] .preference-row input),:global(:root[data-theme='dark'] .preference-editor input),:global(:root[data-theme='dark'] .preference-editor textarea),:global(:root[data-theme='dark'] .record-editor input),:global(:root[data-theme='dark'] .record-editor textarea),:global(:root[data-theme='dark'] .record-editor select),:global(:root[data-theme='dark'] .material-inline-form input),:global(:root[data-theme='dark'] .material-inline-form textarea),:global(:root[data-theme='dark'] .direction-form select){background:var(--paper);color:var(--ink);border-color:var(--line)}
:global(:root[data-theme='dark'] .story-record),:global(:root[data-theme='dark'] .preference-row),:global(:root[data-theme='dark'] .direction-row){border-color:var(--line)}
:global(:root[data-theme='dark'] .materials-notice){color:var(--green)}
@media(max-width:720px){.materials-heading{align-items:flex-start}.materials-revision{display:none}.record-groups{grid-template-columns:1fr}.materials-tabs button{padding:.45rem .58rem}}
.story-materials{color:var(--text,var(--ink))}
.materials-heading{align-items:center;border-color:var(--border,var(--line));margin-bottom:.25rem}
.materials-heading h2{font:600 1.4rem/1.25 Inter,"Microsoft YaHei",system-ui,sans-serif;letter-spacing:-.02em}
.materials-heading p,.materials-muted{color:var(--text-muted,var(--muted))}
.materials-heading>div{min-width:0}
.materials-tabs{margin:.4rem 0 .8rem}
.materials-explainer{padding:.65rem .8rem;margin:.25rem 0 1rem;border:1px solid var(--border,var(--line));border-radius:.65rem;background:var(--surface-muted,var(--surface));color:var(--text-muted,var(--muted))}
.materials-section{gap:.75rem}
.material-card{background:var(--surface);border-color:var(--border,var(--line));border-radius:.8rem;padding:1rem}
.material-card h3{font:600 1rem/1.35 Inter,"Microsoft YaHei",system-ui,sans-serif}
.material-premise{font:400 .92rem/1.75 Inter,"Microsoft YaHei",system-ui,sans-serif;color:var(--text,var(--ink))}
.materials-notice{background:var(--accent-soft,var(--soft));color:var(--text,var(--ink));border:1px solid var(--border,var(--line))}
.record-groups{grid-template-columns:1fr;gap:.45rem}
.record-group{border-color:var(--border,var(--line));padding:.65rem .8rem}
.record-group>header h4{font:600 .85rem/1.35 Inter,"Microsoft YaHei",system-ui,sans-serif}
.story-record{border-color:var(--border,var(--line))}
.story-record-copy p,.story-record-copy small{color:var(--text-muted,var(--muted))}
.material-form textarea,.direction-form textarea,.preference-row textarea,.preference-row input,.preference-editor input,.preference-editor textarea,.record-editor input,.record-editor textarea,.record-editor select,.material-inline-form input,.material-inline-form textarea,.direction-form select{background:var(--surface);color:var(--text,var(--ink));border-color:var(--border,var(--line))}
.material-primary{background:var(--accent,#536bce)!important;border-color:var(--accent,#536bce)!important}
.material-link{color:var(--accent,#536bce);border-color:color-mix(in srgb,var(--accent,#536bce) 45%,transparent)}
.direction-row,.preference-row{border-color:var(--border,var(--line))}
@media(max-width:640px){.materials-heading{align-items:flex-start}.materials-revision{white-space:normal}.material-card{padding:.85rem}.record-group>header{align-items:center}}
:global(:root[data-theme='dark'] .material-premise),:global(:root[data-theme='dark'] .story-record-copy p),:global(:root[data-theme='dark'] .story-record-copy small){color:var(--text)}
:global(:root[data-theme='dark'] .material-card),:global(:root[data-theme='dark'] .record-group),:global(:root[data-theme='dark'] .materials-explainer){background:var(--surface);border-color:var(--border)}
:global(:root[data-theme='dark'] .materials-explainer){background:var(--surface-muted)}
</style>
