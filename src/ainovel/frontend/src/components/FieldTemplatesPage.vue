<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { NButton, NEmpty, NInput, NSelect } from "naive-ui";
import { Plus, Check, Circle } from "@lucide/vue";
import { api, type Schemas } from "../api/client";
import { completeTemplates } from "../ui/fields";
import TemplateSetEditor from "./TemplateSetEditor.vue";

type Template = Schemas["FieldTemplate-Input"];
type TemplateSet = Schemas["FieldTemplateSet-Output"];
type Draft = { name: string; description: string; templates: Template[]; base: string; sourceId: string | null; parentId: string | null; editorKey?: string };
const storageKey = "ainovel.templateEditor";
const library = ref<Schemas["FieldTemplateLibrary"] | null>(null);
const loading = ref(true), saving = ref(false);
const selected = ref("");
const drafts = ref<Record<string, Draft>>({});
const error = ref("");
const current = computed(() => drafts.value[selected.value]);
const sets = computed<TemplateSet[]>(() => library.value?.template_sets ?? []);
const options = computed(() => [
  ...sets.value.map(item => ({ value: item.id!, label: drafts.value[item.id!]?.name || item.name })),
  ...Object.entries(drafts.value).filter(([, draft]) => !draft.sourceId).map(([key, draft]) => ({ value: key, label: draft.name || "新模板" })),
]);
function contents(draft: Pick<Draft, "name" | "description" | "templates">) { return JSON.stringify([draft.name, draft.description, draft.templates]); }
const dirty = computed(() => !!current.value && contents(current.value) !== current.value.base);
function selectTemplate(id: string) {
  if (saving.value) return;
  if (!drafts.value[id]) {
    const source = sets.value.find(item => item.id === id);
    if (!source) return;
    const draft: Draft = { name: source.name, description: source.description ?? "", templates: completeTemplates(source.templates ?? []), sourceId: source.id!, parentId: source.parent_id ?? null, base: "" };
    draft.base = contents(draft); drafts.value[id] = draft;
  }
  selected.value = id; error.value = "";
}
function addTemplate() {
  if (saving.value || !library.value) return;
  const id = crypto.randomUUID();
  drafts.value[id] = { name: "新模板", description: "", templates: completeTemplates([]), sourceId: null, parentId: null, base: "" };
  selected.value = id; error.value = "";
}
function persist() {
  try { localStorage.setItem(storageKey, JSON.stringify({ selected: selected.value, drafts: drafts.value })); } catch { /* Browser storage is optional. */ }
}
watch([selected, drafts], persist, { deep: true });
async function load() {
  loading.value = true;
  try {
    library.value = await api.get<Schemas["FieldTemplateLibrary"]>("/api/field-templates");
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey) || "null");
      if (stored?.drafts && typeof stored.drafts === "object") {
        for (const [id, draft] of Object.entries(stored.drafts) as [string, Draft][]) {
          if (draft && typeof draft.name === "string" && Array.isArray(draft.templates) && (!draft.sourceId || sets.value.some(item => item.id === draft.sourceId))) drafts.value[id] = draft;
        }
        if (drafts.value[stored.selected]) selected.value = stored.selected;
      }
    } catch { /* Ignore an older editor cache. */ }
    if (!selected.value) { if (sets.value[0]?.id) selectTemplate(sets.value[0].id); else addTemplate(); }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "模板读取失败"; }
  finally { loading.value = false; }
}
async function save() {
  const draft = current.value;
  if (!draft || !library.value || saving.value || !draft.name.trim()) return;
  saving.value = true; error.value = "";
  const oldKey = selected.value;
  try {
    const id = crypto.randomUUID();
    const result = await api.command({ kind: "save_field_template_set", replace_template_set_id: draft.sourceId,
      template_set: { id, name: draft.name.trim(), description: draft.description, templates: draft.templates, builtin: false, parent_id: draft.parentId } }, null, null, library.value.session_id, () => undefined);
    const saved = result.output.field_template_set ?? { id, name: draft.name.trim(), description: draft.description, templates: draft.templates, builtin: false, parent_id: draft.parentId };
    library.value.template_sets = draft.sourceId ? sets.value.map(item => item.id === draft.sourceId ? saved : item) : [...sets.value, saved];
    delete drafts.value[oldKey];
    const clean: Draft = { name: saved.name, description: saved.description, templates: completeTemplates(saved.templates ?? []), base: "", sourceId: saved.id!, parentId: saved.parent_id ?? null, editorKey: draft.editorKey ?? oldKey };
    clean.base = contents(clean); drafts.value[saved.id!] = clean; selected.value = saved.id!;
    persist();
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "保存失败，修改内容仍保留"; }
  finally { saving.value = false; }
}
function beforeUnload(event: BeforeUnloadEvent) { persist(); if (saving.value) { event.preventDefault(); event.returnValue = ""; } }
onMounted(() => { void load(); window.addEventListener("beforeunload", beforeUnload); });
onBeforeUnmount(() => { persist(); window.removeEventListener("beforeunload", beforeUnload); });
</script>

<template>
  <main class="field-templates-page">
    <header class="page-heading"><div><h1>字段模板</h1><p>选择模板，直接修改字段后保存。</p></div>
      <NButton :disabled="loading || saving || !library" @click="addTemplate"><template #icon><Plus :size="16" /></template>新增模板</NButton>
    </header>
    <div v-if="library" class="template-workspace">
      <aside class="template-sidebar" aria-label="模板列表">
        <p class="list-label">我的模板</p>
        <button v-for="option in options" :key="option.value" type="button" :disabled="saving" :class="['template-item', { active: selected === option.value }]" :aria-pressed="selected === option.value" @click="selectTemplate(option.value)"><span>{{ option.label }}</span><Circle v-if="drafts[option.value] && contents(drafts[option.value]) !== drafts[option.value].base" :size="7" fill="currentColor" aria-label="有未保存修改" /></button>
        <NSelect class="mobile-template-switch" :value="selected" :options="options" filterable :disabled="saving" aria-label="切换模板" @update:value="selectTemplate" />
      </aside>
      <section v-if="current" class="template-editor-pane" aria-label="模板编辑区">
        <div class="editor-heading"><label class="name-field">模板名称<NInput v-model:value="current.name" :input-props="{ 'aria-label': '模板名称' }" placeholder="给模板起个名字" maxlength="160" :disabled="saving" /></label>
          <div class="save-area"><span class="save-status" role="status"><Circle v-if="dirty" :size="7" fill="currentColor" /><Check v-else :size="14" />{{ saving ? '保存中…' : dirty ? '未保存' : '已保存' }}</span><NButton type="primary" :loading="saving" :disabled="!dirty || !current.name.trim() || loading" @click="save">保存</NButton></div>
        </div>
        <p v-if="error" class="editor-message error" role="alert">{{ error }}</p>
        <div class="editor-body">
          <fieldset :disabled="saving"><TemplateSetEditor :key="current.editorKey ?? selected" :templates="current.templates" :library="library.templates" @change="value => current && (current.templates = value)" /></fieldset>
          <details class="template-description"><summary>模板说明</summary><NInput v-model:value="current.description" type="textarea" aria-label="模板说明" placeholder="可选：这个模板适合写什么故事" :disabled="saving" /></details>
          <p class="template-footnote">模板只用于初始化。已创建小说的字段保持独立。</p>
        </div>
      </section>
      <NEmpty v-else description="点击新增模板开始编辑" />
    </div>
    <p v-else class="page-feedback" :role="error ? 'alert' : 'status'">{{ error || '正在读取模板…' }}</p>
  </main>
</template>

<style scoped>
.field-templates-page{display:flex;flex-direction:column;gap:20px;width:100%;height:100%;min-height:0;padding:24px;overflow:hidden}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-shrink:0}.page-heading h1{font-size:23px;margin:0 0 5px}.page-heading p{margin:0;color:var(--text-muted);font-size:13px}.template-workspace{display:grid;grid-template-columns:200px minmax(0,1fr);gap:24px;flex:1;min-height:0}.template-sidebar{min-height:0;overflow:auto;border-right:1px solid var(--border);padding-right:16px}.list-label{font-size:11px;color:var(--text-muted);margin:0 0 10px;padding:0 10px}.template-item{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;text-align:left;border:0;border-radius:7px;background:transparent;color:var(--text-muted);padding:11px 12px;margin-bottom:4px;cursor:pointer;font:inherit;font-size:13px;overflow-wrap:anywhere}.template-item.active{background:var(--surface-muted);color:var(--text);font-weight:600}.template-item:hover{background:var(--surface-muted)}.mobile-template-switch{display:none}.template-editor-pane{display:flex;flex-direction:column;min-width:0;min-height:0}.editor-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding-bottom:18px;border-bottom:1px solid var(--border);flex-shrink:0}.name-field{display:grid;gap:7px;max-width:420px;flex:1;font-size:12px;color:var(--text-muted)}.save-area{display:flex;align-items:center;gap:14px}.save-status{display:flex;align-items:center;gap:5px;white-space:nowrap;color:var(--text-muted);font-size:12px}.editor-body{overflow:auto;min-height:0;padding:0 12px 24px 0;overscroll-behavior:contain}.editor-body fieldset{border:0;min-width:0;margin:0;padding:0}.editor-body fieldset:disabled{pointer-events:none;opacity:.65}.template-description{margin-top:24px;font-size:13px;color:var(--text-muted)}.template-description summary{cursor:pointer;margin-bottom:10px}.template-footnote{font-size:12px;color:var(--text-muted);margin:22px 0 0}.editor-message{font-size:13px;color:var(--text-muted);margin:10px 0}.editor-message.error{color:var(--error,#c05050)}.page-feedback{color:var(--text-muted)}
@media(max-width:700px){.field-templates-page{padding:16px 12px;gap:14px}.page-heading h1{font-size:20px}.page-heading p{font-size:12px}.template-workspace{display:flex;flex-direction:column;gap:14px}.template-sidebar{overflow:visible;border:0;padding:0;flex-shrink:0}.template-item,.list-label{display:none}.mobile-template-switch{display:block}.template-editor-pane{flex:1}.editor-heading{gap:12px;padding-bottom:12px}.save-area{gap:8px}.save-status{font-size:11px}.editor-body{padding-right:0}}
</style>
