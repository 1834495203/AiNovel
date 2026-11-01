<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NDrawer, NDrawerContent, NInput, NSelect } from "naive-ui";
import { api, type CommandPayload, type CommandView, type Schemas, type StoryView } from "../api/client";
import { fieldDefinition } from "../ui/fields";

import EntryListEditor from "./EntryListEditor.vue";
import FieldDefinitionEditor from "./FieldDefinitionEditor.vue";
import StoryTemplateSetPanel from "./StoryTemplateSetPanel.vue";

type Scope = Schemas["FieldTarget"]["scope"];
type Template = Schemas["FieldTemplate-Input"];
type Entry = Schemas["Entry-Input"];
type Event = Schemas["FieldChangeEvent"];
const props = defineProps<{ view: StoryView; disabled: boolean; executeCommand: (payload: CommandPayload) => Promise<CommandView> }>();
const emit = defineEmits<{ "revision-changed": [] }>();
const scope = ref<Scope>("world");
const entityId = ref<string | null>(null);
const entries = ref<Schemas["Entry-Output"][]>([]);
const library = ref<Schemas["FieldTemplateLibrary"] | null>(null);
const selectedTemplate = ref<string | null>(null);
const busy = ref(false);
const notice = ref("");
const editorOpen = ref(false);
const templateName = ref("");
const definitions = ref<Schemas["FieldDefinition-Input"][]>([]);
const parentId = ref<string | null>(null);
const events = ref<Event[]>([]);
const showHistory = ref(false);
const selectedTargets = ref<string[]>([]);
const labels: Record<Scope, string> = { story: "小说资料", world: "世界观", character: "角色档案", state: "角色状态", preferences: "创作偏好", writer: "创作笔记与计划", chapter: "章节", scene: "幕" };
const scopeOptions = Object.entries(labels).map(([value, label]) => ({ value, label }));
const entityScope = computed(() => ["character", "state", "chapter", "scene"].includes(scope.value));
const entityOptions = computed(() => (scope.value === "character" || scope.value === "state" ? props.view.story.characters.map(item => ({ value: item.id, label: item.name })) :
  scope.value === "chapter" ? props.view.story.chapters.map(item => ({ value: item.id, label: item.title })) : props.view.story.scenes.map(item => ({ value: item.id, label: item.title }))));
const locked = computed(() => props.view.story.scenes.some(scene => !!scene.text?.trim() && ((scope.value === 'scene' && scene.id === entityId.value) || (scope.value === 'chapter' && scene.chapter_id === entityId.value))));
const templates = computed(() => library.value?.templates.filter(item => item.scope === scope.value) ?? []);
const templateOptions = computed(() => templates.value.map(item => ({ value: item.id, label: item.name })));
const template = computed(() => templates.value.find(item => item.id === selectedTemplate.value));
let generation = 0;

async function refresh() {
  const own = ++generation;
  if (entityScope.value && !entityId.value) { entries.value = []; return; }
  const query = new URLSearchParams({ scope: scope.value });
  if (entityScope.value && entityId.value) query.set("entity_id", entityId.value);
  try {
    const doc = await api.get<Schemas["FieldDocumentView"]>(`/api/stories/${props.view.story.id}/fields?${query}`);
    if (own === generation) entries.value = doc.entries;
  } catch (error) { if (own === generation) notice.value = error instanceof Error ? error.message : "读取失败"; }
}
async function loadLibrary() { library.value = await api.get<Schemas["FieldTemplateLibrary"]>("/api/field-templates"); }
watch(scope, () => { entityId.value = entityScope.value ? entityOptions.value[0]?.value ?? null : null; selectedTemplate.value = null; selectedTargets.value = []; });
watch([scope, entityId, () => props.view.revision_id], () => { void refresh(); if (showHistory.value) void loadHistory(); }, { immediate: true });
void loadLibrary().catch(error => notice.value = error.message);

async function run(payload: CommandPayload) {
  if (busy.value || props.disabled) return;
  busy.value = true; notice.value = "正在保存…";
  try { const result = await props.executeCommand(payload); notice.value = result.output.message || "已保存"; emit("revision-changed"); await refresh(); }
  catch (error) { notice.value = error instanceof Error ? error.message : "保存失败"; }
  finally { busy.value = false; }
}
function saveMany(items: Entry[]) {
  void run({ kind: "edit_fields", target: { scope: scope.value, entity_id: entityId.value }, patch: { upsert: items }, reason: `用户编辑${labels[scope.value]}字段` });
}
function remove(id: string) { void run({ kind: "edit_fields", target: { scope: scope.value, entity_id: entityId.value }, patch: { remove: [id] }, reason: "用户删除自定义字段" }); }
function editTemplate(source: "blank" | "selected" | "current") {
  parentId.value = source === "selected" ? template.value?.id ?? null : null;
  templateName.value = source === "selected" ? `${template.value?.name ?? ''} · 自定义` : "我的字段模板";
  const fields = source === "selected" ? template.value?.fields ?? [] : source === "current" ? entries.value.map(fieldDefinition) : [];
  definitions.value = JSON.parse(JSON.stringify(fields)); editorOpen.value = true;
}
async function saveTemplate() {
  if (!library.value || !templateName.value.trim() || busy.value) return;
  busy.value = true;
  try {
    const saved: Template = { id: crypto.randomUUID(), name: templateName.value.trim(), description: "用户自定义字段模板", scope: scope.value, fields: definitions.value, parent_id: parentId.value, builtin: false };
    await api.command({ kind: "save_field_template", template: saved }, null, null, library.value.session_id, () => undefined);
    await loadLibrary(); selectedTemplate.value = saved.id ?? null; editorOpen.value = false; notice.value = "模板已保存，可复用于其他小说的同类资料。";
  } catch (error) { notice.value = error instanceof Error ? error.message : "保存模板失败"; }
  finally { busy.value = false; }
}
function applyTemplate() {
  if (!selectedTemplate.value) return;
  const ids = entityScope.value ? selectedTargets.value.length ? selectedTargets.value : entityId.value ? [entityId.value] : [] : [null];
  if (!ids.length) return;
  void run({ kind: "apply_field_template", template_id: selectedTemplate.value, targets: ids.map(entity_id => ({ scope: scope.value, entity_id })) });
}
async function loadHistory(older = false) {
  const cursor = older ? events.value.at(-1)?.parent_id : props.view.revision_id;
  if (!cursor) return;
  try {
    const page = await api.get<Event[]>(`/api/stories/${props.view.story.id}/field-history?revision_id=${cursor}&limit=30`);
    events.value = older ? [...events.value, ...page] : page;
  } catch (error) { notice.value = error instanceof Error ? error.message : "读取历史失败"; }
}
</script>
<template>
  <section class="custom-fields">
    <StoryTemplateSetPanel :view="view" :library="library" :disabled="disabled || busy" :execute-command="executeCommand" @revision-changed="emit('revision-changed')" @library-changed="loadLibrary" />
    <div class="toolbar"><NSelect v-model:value="scope" :options="scopeOptions" aria-label="资料类型" />
      <NSelect v-if="entityScope" v-model:value="entityId" :options="entityOptions" placeholder="选择角色、章节或幕" aria-label="资料对象" />
      <NButton @click="showHistory = !showHistory; showHistory && loadHistory()">字段变更历史</NButton></div>
    <section class="template-bar"><h3>字段模板</h3><p>模板只保存字段定义和填写解释；应用时补充缺失字段，保留已有内容及其定义版本。<a href="/#field-templates">打开全局模板库</a></p>
      <div class="toolbar"><NSelect v-model:value="selectedTemplate" :options="templateOptions" placeholder="选择默认或自定义模板" aria-label="字段模板" />
        <NButton :disabled="disabled || busy || locked || !selectedTemplate || (entityScope && !entityId)" @click="applyTemplate">应用模板</NButton>
        <NButton :disabled="busy || !selectedTemplate" @click="editTemplate('selected')">复制并编辑模板</NButton></div>
      <NSelect v-if="entityScope" v-model:value="selectedTargets" multiple :options="entityOptions" placeholder="可选择多个对象批量应用；留空则应用到当前对象" aria-label="批量应用对象" />
      <div class="toolbar"><NButton :disabled="busy" @click="editTemplate('blank')">新建字段模板</NButton><NButton :disabled="busy || !entries.length" @click="editTemplate('current')">将当前字段保存为模板</NButton></div>
    </section>
    <p v-if="locked">本幕已有正文，设定已锁定；如需调整，请重新生成文本。</p>
    <EntryListEditor v-if="!entityScope || entityId" :title="labels[scope]" :entries="entries" :disabled="disabled || busy || locked" note="字段解释和实际内容分别保存；每次修改均保留可追溯的增量事件。" @save="entry => saveMany([entry])" @save-many="saveMany" @remove="remove" />
    <p v-else>还没有可编辑的对象。</p><p v-if="notice" role="status">{{ notice }}</p>
    <section v-if="showHistory" class="field-history"><h3>当前版本的变更链</h3><p>按父版本读取；旧 patch 在生成快照后仍永久保留。</p>
      <details v-for="event in events" :key="event.revision_id"><summary>{{ event.reason }} · {{ event.patches.length }} 项变化 <small>{{ new Date(event.created_at!).toLocaleString() }}</small></summary>
        <p>版本：{{ event.revision_id }} · 父版本：{{ event.parent_id || '初始' }}</p>
        <ol><li v-for="(patch, index) in event.patches" :key="index"><code>{{ patch.op }} /{{ patch.path?.map(part => part.value).join('/') }}</code><pre v-if="patch.op !== 'remove'">{{ JSON.stringify(patch.value, null, 2) }}</pre></li></ol>
      </details><NButton v-if="events.at(-1)?.parent_id" @click="loadHistory(true)">更早的变更</NButton>
    </section>
    <NDrawer :show="editorOpen" :width="'min(780px, 100vw)'" @update:show="value => editorOpen = value"><NDrawerContent title="字段模板定义" closable>
      <div class="template-editor"><label>模板名称<NInput v-model:value="templateName" /></label><p>适用于{{ labels[scope] }}。保存为独立版本，不自动改变已经使用它的资料。</p>
        <FieldDefinitionEditor :fields="definitions" @change="value => definitions = value" /><NButton type="primary" :disabled="busy || !templateName.trim()" @click="saveTemplate">保存模板</NButton></div>
    </NDrawerContent></NDrawer>
  </section>
</template>
<style scoped>
.custom-fields{display:grid;gap:16px}.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:9px}.toolbar>.n-select{flex:1;min-width:180px}.template-bar{display:grid;gap:12px;padding:16px;background:var(--surface-muted);border:1px solid var(--border);border-radius:10px}h3,p{margin:0}p{font-size:13px;color:var(--text-muted)}.template-editor{display:grid;gap:16px}.template-editor label{display:grid;gap:7px}details{border-bottom:1px solid var(--border);padding:12px 0}summary{cursor:pointer;font-size:13px}small{color:var(--text-muted)}pre{max-height:300px;overflow:auto;font-size:11px;white-space:pre-wrap}code{overflow-wrap:anywhere;font-size:11px}.field-history{display:grid;gap:10px}
</style>
