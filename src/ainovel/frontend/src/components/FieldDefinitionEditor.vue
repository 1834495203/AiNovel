<script setup lang="ts">
import { inject, provide, reactive } from "vue";
import { NButton, NCheckbox, NInput, NSelect } from "naive-ui";
import { ChevronRight } from "@lucide/vue";
import type { Schemas } from "../api/client";
import { fieldDisclosureKey } from "../ui/fieldDisclosure";
import { fieldDescription } from "../ui/fields";
defineOptions({ name: "FieldDefinitionEditor" });
type Definition = Schemas["FieldDefinition-Input"];
const props = defineProps<{ fields: Definition[]; single?: boolean; depth?: number }>();
const emit = defineEmits<{ change: [fields: Definition[]] }>();
const inherited = inject(fieldDisclosureKey, null);
const disclosure = inherited ?? reactive(new Map<string, boolean>());
provide(fieldDisclosureKey, disclosure);
function key(field: Definition) { return field.id ?? field.name; }
function expanded(field: Definition) { return disclosure.get(key(field)) ?? false; }
function toggle(field: Definition) { disclosure.set(key(field), !expanded(field)); }
function setAll(open: boolean) {
  function visit(fields: Definition[]) {
    for (const field of fields) {
      disclosure.set(key(field), open);
      visit(field.children ?? []);
      if (field.item) visit([field.item]);
    }
  }
  visit(props.fields);
}
const types = ["text", "number", "boolean", "enum", "object", "list", "reference"] as const;
const labels = ["文本", "数字", "是或否", "枚举", "对象", "列表", "引用 ID"];
const options = types.map((value, index) => ({ value, label: labels[index] }));
function blank(): Definition {
  const field: Definition = { id: crypto.randomUUID(), name: "新字段", description: "", type: "text", required: false, options: [], children: [], item: null, allow_extra: true };
  disclosure.set(key(field), true);
  return field;
}
function update(index: number, patch: Partial<Definition>) { emit("change", props.fields.map((field, i) => i === index ? { ...field, ...patch } : field)); }
function changeType(index: number, type: Definition["type"]) {
  update(index, { type, children: [], item: type === "list" ? blank() : null, options: type === "enum" ? ["选项一", "选项二"] : [] });
}
</script>
<template>
  <div class="definition-list">
    <div v-if="!(depth ?? 0) && fields.length" class="definition-toolbar"><NButton text size="small" @click="setAll(true)">全部展开</NButton><NButton text size="small" @click="setAll(false)">全部收起</NButton></div>
    <article v-for="(field, index) in fields" :key="field.id" class="definition" :class="{ collapsed: !expanded(field) }">
      <div class="definition-row"><button class="definition-toggle" type="button" :aria-expanded="expanded(field)" :aria-label="`${expanded(field) ? '收起' : '展开'}字段 ${field.name}`" @click="toggle(field)"><ChevronRight :size="16" :class="{ expanded: expanded(field) }" /></button>
        <NInput :value="field.name" placeholder="字段名称" :input-props="{ 'aria-label': '字段名称' }" @update:value="name => update(index, { name })" />
        <NSelect class="definition-type" :value="field.type || 'text'" :options="options" aria-label="字段类型" @update:value="type => changeType(index, type)" />
        <NButton class="definition-delete" size="small" @click="emit('change', fields.filter((_, i) => i !== index))">删除</NButton></div>
      <p v-if="!expanded(field) && field.type === 'object'" class="collapsed-summary">{{ field.children?.length ?? 0 }} 个子字段</p>
      <p v-else-if="!expanded(field) && field.type === 'list'" class="collapsed-summary">列表项结构已收起</p>
      <div v-if="expanded(field)" class="definition-body">
      <NInput :value="fieldDescription(field.description, field.id)" placeholder="字段说明（可选）：说明含义或填写要求" :input-props="{ 'aria-label': '字段解释' }" @update:value="description => update(index, { description })" />
      <NCheckbox :checked="field.required" @update:checked="required => update(index, { required })">必填</NCheckbox>
      <NInput v-if="field.type === 'enum'" :value="field.options?.join('\n')" type="textarea" placeholder="每行一个选项" @update:value="value => update(index, { options: value.split('\n').map(item => item.trim()).filter(Boolean) })" />
      <template v-if="field.type === 'object'"><NCheckbox :checked="field.allow_extra ?? true" @update:checked="allow_extra => update(index, { allow_extra })">允许 AI 和用户扩充子字段</NCheckbox>
        <FieldDefinitionEditor :depth="(depth ?? 0) + 1" :fields="field.children || []" @change="children => update(index, { children })" /></template>
      <div v-if="field.type === 'list'"><p>列表项结构</p><FieldDefinitionEditor single :depth="(depth ?? 0) + 1" :fields="field.item ? [field.item] : []" @change="items => update(index, { item: items[0] || null })" /></div>
      </div>
    </article>
    <NButton v-if="!single || !fields.length" size="small" secondary @click="emit('change', [...fields, blank()])">添加字段</NButton>
  </div>
</template>
<style scoped>
.definition-list,.definition-body{display:grid;gap:10px;min-width:0}.definition-toolbar{display:flex;gap:16px;justify-content:flex-end}.definition{display:grid;gap:9px;min-width:0;padding:12px;border:1px solid var(--border);border-radius:8px}.definition-row{display:grid;grid-template-columns:24px minmax(0,1fr) 110px auto;align-items:center;gap:8px}.definition-row>.n-input,.definition-row>.n-select{min-width:0}.definition-toggle{display:flex;align-items:center;justify-content:center;width:24px;height:28px;padding:0;background:transparent;border:0;border-radius:4px;color:var(--text-muted);cursor:pointer}.definition-toggle:hover{background:var(--surface-muted)}.definition-toggle:focus-visible{outline:2px solid var(--primary,#536bce)}.definition-toggle svg{transition:transform .15s}.definition-toggle svg.expanded{transform:rotate(90deg)}.definition p{margin:0;font-size:12px;color:var(--text-muted)}.definition .definition{padding:8px 0 8px 10px;border:0;border-left:2px solid var(--border);border-radius:0}.collapsed-summary{padding-left:32px}.collapsed{gap:5px}
@media(max-width:480px){.definition{padding:10px}.definition-row{grid-template-columns:24px minmax(0,1fr) auto;gap:6px}.definition-type{grid-column:2/-1;grid-row:2}.definition-delete{grid-column:3;grid-row:1}}
</style>
