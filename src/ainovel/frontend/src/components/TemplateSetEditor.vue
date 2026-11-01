<script setup lang="ts">
import { computed, provide, reactive, ref } from "vue";
import { NSelect } from "naive-ui";
import type { Schemas } from "../api/client";
import { fieldScopeLabels } from "../ui/fields";
import FieldDefinitionEditor from "./FieldDefinitionEditor.vue";
import { fieldDisclosureKey } from "../ui/fieldDisclosure";
type Template = Schemas["FieldTemplate-Input"];
type Scope = Schemas["FieldTarget"]["scope"];
const props = defineProps<{ templates: Template[]; library?: Template[] }>();
const emit = defineEmits<{ change: [templates: Template[]] }>();
provide(fieldDisclosureKey, reactive(new Map<string, boolean>()));
const scope = ref<Scope>("world");
const options = (["world", "character", "state", "writer", "chapter", "scene", "preferences", "story"] as Scope[]).map(value => ({ value, label: fieldScopeLabels[value] }));
const active = computed(() => props.templates.find(item => item.scope === scope.value));
const available = computed(() => props.library?.filter(item => item.scope === scope.value).map(item => ({ label: item.name, value: item.id! })) ?? []);
function change(fields: Schemas["FieldDefinition-Input"][]) {
  emit("change", props.templates.map(item => item.scope === scope.value ? { ...item, fields } : item));
}
function useTemplate(id: string) {
  const source = props.library?.find(item => item.id === id);
  if (source) change(JSON.parse(JSON.stringify(source.fields ?? [])));
}
</script>
<template>
  <div class="template-set-editor">
    <nav class="scope-tabs" role="tablist" aria-label="资料类别"><button v-for="item in options" :key="item.value" type="button" role="tab" :aria-selected="scope === item.value" :class="{ active: scope === item.value }" @click="scope = item.value">{{ item.label }}<span>{{ templates.find(template => template.scope === item.value)?.fields?.length ?? 0 }}</span></button></nav>
    <div role="tabpanel" :aria-label="fieldScopeLabels[scope]"><p v-if="!active?.fields?.length" class="empty-fields">还没有字段，点击下方添加。</p><FieldDefinitionEditor :key="scope" :fields="active?.fields ?? []" @change="change" /></div>
    <details v-if="available.length" class="import-fields"><summary>导入已有字段模板</summary><NSelect :value="null" :options="available" placeholder="选择要导入的字段结构" aria-label="导入字段模板" @update:value="useTemplate" /></details>
  </div>
</template>
<style scoped>
.template-set-editor{display:grid;gap:18px;min-width:0}.scope-tabs{position:sticky;top:0;z-index:2;display:flex;gap:4px;overflow:auto;padding:14px 0 10px;background:var(--surface,var(--paper));border-bottom:1px solid var(--border);scrollbar-width:thin}.scope-tabs button{display:flex;align-items:center;gap:5px;white-space:nowrap;flex-shrink:0;border:0;border-radius:6px;background:transparent;color:var(--text-muted);padding:8px 10px;font:inherit;font-size:13px;cursor:pointer}.scope-tabs button.active{background:var(--surface-muted);color:var(--text);font-weight:600}.scope-tabs button span{font-size:10px;opacity:.65}.empty-fields{font-size:13px;color:var(--text-muted);margin:4px 0 18px}.import-fields{font-size:12px;color:var(--text-muted)}.import-fields summary{cursor:pointer;margin-bottom:10px}
</style>
