<script setup lang="ts">
import { computed, ref } from "vue";
import { NButton, NDrawer, NDrawerContent, NInput, NSelect } from "naive-ui";
import { api, type CommandPayload, type CommandView, type Schemas, type StoryView } from "../api/client";
import { completeTemplates, fieldScopeLabels } from "../ui/fields";
import TemplateSetEditor from "./TemplateSetEditor.vue";
type Scope = Schemas["FieldTarget"]["scope"];
type Template = Schemas["FieldTemplate-Input"];
const props = defineProps<{ view: StoryView; library: Schemas["FieldTemplateLibrary"] | null; disabled: boolean; executeCommand: (payload: CommandPayload) => Promise<CommandView> }>();
const emit = defineEmits<{ "revision-changed": []; "library-changed": [] }>();
const selected = ref<string | null>(null);
const editing = ref(false), exporting = ref(false), busy = ref(false);
const templates = ref<Template[]>([]);
const name = ref(""), description = ref(""), notice = ref("");
const sources = ref<Partial<Record<Scope, string | null>>>({});
const setOptions = computed(() => props.library?.template_sets?.map(item => ({ value: item.id!, label: item.name })) ?? []);
const entityScopes: Scope[] = ["character", "state", "chapter", "scene"];
function entityOptions(scope: Scope) {
  const items = scope === "character" || scope === "state" ? props.view.story.characters.map(item => ({ value: item.id, label: item.name }))
    : (scope === "chapter" ? props.view.story.chapters : props.view.story.scenes).map(item => ({ value: item.id, label: item.title }));
  return [{ value: "", label: "本书新增对象的初始化结构" }, ...items];
}
async function apply() {
  if (!selected.value || busy.value || props.disabled) return;
  busy.value = true; notice.value = "";
  try {
    await props.executeCommand({ kind: "initialize_story_template_set", template_set_id: selected.value });
    emit("revision-changed"); notice.value = "已为本书复制独立结构，已有内容保留，后续对象自动初始化。";
  } catch (error) { notice.value = error instanceof Error ? error.message : "初始化失败"; }
  finally { busy.value = false; }
}
async function edit() {
  busy.value = true;
  try {
    const local = props.view.story.field_template_set ?? await api.get<Schemas["FieldTemplateSet-Output"]>(`/api/stories/${props.view.story.id}/field-template-set`);
    templates.value = completeTemplates(JSON.parse(JSON.stringify(local.templates ?? [])));
    editing.value = true;
  } catch (error) { notice.value = error instanceof Error ? error.message : "读取失败"; }
  finally { busy.value = false; }
}
async function saveLocal() {
  if (busy.value || props.disabled) return;
  busy.value = true;
  try {
    await props.executeCommand({ kind: "update_story_field_schema", templates: templates.value });
    editing.value = false; emit("revision-changed"); notice.value = "本书初始化结构已保存，原模板不变。";
  } catch (error) { notice.value = error instanceof Error ? error.message : "保存失败"; }
  finally { busy.value = false; }
}
function openExport() {
  name.value = `${props.view.story.title} · 小说模板`;
  description.value = "";
  sources.value = Object.fromEntries(entityScopes.map(scope => [scope, entityOptions(scope)[1]?.value ?? ""]));
  exporting.value = true;
}
async function exportSet() {
  if (busy.value || !props.library || !name.value.trim()) return;
  busy.value = true;
  try {
    const targets = entityScopes.filter(scope => sources.value[scope]).map(scope => ({ scope, entity_id: sources.value[scope]! }));
    const result = await api.post<Schemas["FieldTemplateSet-Input"]>(`/api/stories/${props.view.story.id}/field-template-set/export`,
      { revision_id: props.view.revision_id, name: name.value.trim(), description: description.value, targets });
    await api.command({ kind: "save_field_template_set", template_set: result }, null, null, props.library.session_id, () => undefined);
    exporting.value = false; emit("library-changed"); notice.value = "已另存为新小说模板套装，可供其他小说初始化使用。";
  } catch (error) { notice.value = error instanceof Error ? error.message : "导出失败"; }
  finally { busy.value = false; }
}
</script>
<template>
  <section class="story-template-set">
    <h3>本书模板套装</h3>
    <p>当前：{{ view.story.field_template_set?.name || '根据本书已有资料建立' }}。模板仅提供初始化结构，本书字段的新增、修改和删除都不回写原模板。</p>
    <div class="toolbar"><NSelect v-model:value="selected" :options="setOptions" placeholder="为这部小说选择一套模板" aria-label="本书小说模板套装" />
      <NButton :disabled="disabled || busy || !selected" @click="apply">应用到本书</NButton></div>
    <p>应用时保留已有字段和内容；以后新增角色、章节和幕使用本书独立保存的结构。</p>
    <div class="toolbar"><NButton :disabled="disabled || busy" @click="edit">编辑本书初始化结构</NButton><NButton :disabled="disabled || busy || !library" @click="openExport">将本书结构导出为新模板</NButton></div>
    <p v-if="notice" role="status">{{ notice }}</p>
    <NDrawer :show="editing" :width="'min(780px, 100vw)'" :mask-closable="!busy" :close-on-esc="!busy" @update:show="value => { if (!busy) editing = value; }"><NDrawerContent title="本书初始化结构" :closable="!busy">
      <div class="form"><p>修改仅用于以后新增对象，不批量改动已有角色、章节或幕；已有资料在字段列表单独编辑。原模板始终保留。</p>
        <fieldset :disabled="busy"><TemplateSetEditor :templates="templates" :library="library?.templates" @change="value => templates = value" /></fieldset>
        <p v-if="notice" role="alert">{{ notice }}</p><NButton type="primary" :disabled="disabled || busy" @click="saveLocal">保存本书初始化结构</NButton></div>
    </NDrawerContent></NDrawer>
    <NDrawer :show="exporting" :width="'min(600px, 100vw)'" :mask-closable="!busy" :close-on-esc="!busy" @update:show="value => { if (!busy) exporting = value; }"><NDrawerContent title="导出为新小说模板" :closable="!busy">
      <form class="form" @submit.prevent="exportSet"><label>模板套装名称<NInput v-model:value="name" :disabled="busy" /></label><label>说明<NInput v-model:value="description" type="textarea" :disabled="busy" /></label>
        <p>世界观、偏好、创作工作区和小说资料导出当前字段结构。角色、章节与幕可选择一个对象作为样本，包含该对象新增、删除及修改后的字段。仅导出定义和填写说明，不带填写值或正文。</p>
        <label v-for="scope in entityScopes" :key="scope">{{ fieldScopeLabels[scope] }}来源<NSelect v-model:value="sources[scope]" :options="entityOptions(scope)" :disabled="busy" :aria-label="`${fieldScopeLabels[scope]}导出来源`" /></label>
        <p v-if="notice" role="alert">{{ notice }}</p><NButton type="primary" attr-type="submit" :disabled="busy || !name.trim()">另存为新模板套装</NButton>
      </form>
    </NDrawerContent></NDrawer>
  </section>
</template>
<style scoped>
.story-template-set,.form{display:grid;gap:14px}.story-template-set{padding:16px;border:1px solid var(--border);border-radius:10px;background:var(--surface-muted)}h3,p{margin:0}p{font-size:13px;line-height:1.6;color:var(--text-muted)}.toolbar{display:flex;gap:9px;flex-wrap:wrap}.toolbar>.n-select{flex:1;min-width:180px}.form label{display:grid;gap:7px;font-size:13px}fieldset{border:0;padding:0;margin:0;min-width:0}fieldset:disabled{pointer-events:none;opacity:.65}
</style>
