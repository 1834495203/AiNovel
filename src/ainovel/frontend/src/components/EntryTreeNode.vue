<script setup lang="ts">
import { computed, ref } from "vue";
import { NButton, NCheckbox, NCollapse, NCollapseItem, NDrawer, NDrawerContent, NInput, NSelect } from "naive-ui";
import type { Schemas } from "../api/client";
import { fieldDefinition, fieldDescription, instantiateField } from "../ui/fields";
import FieldDefinitionEditor from "./FieldDefinitionEditor.vue";
defineOptions({ name: "EntryTreeNode" });
type Entry = Schemas["Entry-Input"];
type Output = Schemas["Entry-Output"];
const props = defineProps<{ entry: Output; disabled: boolean; isRoot: boolean }>();
const emit = defineEmits<{ save: [entry: Entry]; updated: [entry: Entry]; remove: [id: string] }>();
const editing = ref(false);
const draft = ref<Entry>({ ...props.entry });
const numericValue = ref("");
const error = ref("");
const kind = computed(() => props.entry.definition?.type ?? fieldDefinition(props.entry).type ?? "text");
const draftKind = computed(() => draft.value.definition?.type ?? fieldDefinition(draft.value).type ?? "text");
const definitions = ref<Schemas["FieldDefinition-Input"][]>([]);
const adding = ref(false);
const canAdd = computed(() => kind.value === "list" || (kind.value === "object" && (props.entry.definition?.allow_extra ?? true)));
function publish(entry: Entry) { if (props.disabled) return; if (props.isRoot) emit("save", entry); else emit("updated", entry); }
function edit() {
  draft.value = JSON.parse(JSON.stringify(props.entry)) as Entry;
  numericValue.value = props.entry.value == null ? "" : String(props.entry.value);
  error.value = ""; editing.value = true;
}
function save() {
  if (!draft.value.name.trim()) return;
  if (draftKind.value === "number") {
    const value = numericValue.value.trim();
    if (value && !Number.isFinite(Number(value))) { error.value = "请输入有效数字。"; return; }
    draft.value.value = value ? Number(value) : null;
  }
  if (draft.value.definition && ["object", "list"].includes(draftKind.value)) draft.value.value = null;
  publish(draft.value); editing.value = false;
}
function updateChild(updated: Entry) { publish({ ...props.entry, children: (props.entry.children ?? []).map(child => child.id === updated.id ? updated : child) }); }
function removeChild(id: string) { publish({ ...props.entry, children: (props.entry.children ?? []).filter(child => child.id !== id) }); }
function add() {
  if (props.entry.definition?.type === "list" && props.entry.definition.item) {
    publish({ ...props.entry, children: [...(props.entry.children ?? []), instantiateField(props.entry.definition.item)] });
    return;
  }
  definitions.value = [{ id: crypto.randomUUID(), name: "新字段", type: "text", description: "", required: false, allow_extra: true }]; adding.value = true;
}
function saveChildren() {
  if (!definitions.value.length || definitions.value.some(item => !item.name.trim())) return;
  publish({ ...props.entry, children: [...(props.entry.children ?? []), ...definitions.value.map(instantiateField)] }); adding.value = false;
}
</script>
<template>
  <article class="entry-node" :class="{ nested: !isRoot }">
    <header><div><strong>{{ entry.name }}</strong><small>{{ kind }}{{ entry.definition?.required ? ' · 必填' : '' }}</small>
      <p v-if="fieldDescription(entry.definition?.description, entry.definition?.id)" class="explanation">{{ fieldDescription(entry.definition?.description, entry.definition?.id) }}</p>
      <p v-if="entry.value !== null && entry.value !== undefined">{{ entry.value }}</p><p v-if="entry.description">{{ entry.description }}</p></div>
      <div class="actions"><NButton text size="small" :disabled="disabled" @click="edit">编辑</NButton><NButton text size="small" :disabled="disabled" @click="emit('remove', entry.id!)">删除</NButton></div></header>
    <NCollapse v-if="entry.children?.length" :default-expanded-names="isRoot ? ['children'] : []"><NCollapseItem name="children" :title="`子条目 · ${entry.children.length}`">
      <EntryTreeNode v-for="child in entry.children" :key="child.id" :entry="child" :disabled="disabled" :is-root="false" @updated="updateChild" @remove="removeChild" />
    </NCollapseItem></NCollapse>
    <NButton v-if="canAdd" text size="small" :disabled="disabled" @click="add">{{ kind === 'list' ? '添加列表项' : '添加子字段' }}</NButton>
    <NDrawer :show="editing" :width="'min(520px, 100vw)'" @update:show="value => editing = value"><NDrawerContent :title="`编辑字段 · ${entry.name}`" closable>
      <form class="field-form" @submit.prevent="save">
        <label>名称<NInput v-model:value="draft.name" /></label>
        <label v-if="!['object', 'list'].includes(draftKind)">填写内容
          <NCheckbox v-if="draftKind === 'boolean'" :checked="draft.value === true" @update:checked="value => draft.value = value">是</NCheckbox>
          <NSelect v-else-if="draftKind === 'enum'" :value="draft.value as string | null" clearable :options="draft.definition?.options?.map(value => ({ value, label: value }))" @update:value="value => draft.value = value" />
          <NInput v-else-if="draftKind === 'number'" v-model:value="numericValue" placeholder="数字" />
          <NInput v-else :value="draft.value == null ? '' : String(draft.value)" type="textarea" :autosize="{ minRows: 3 }" @update:value="value => draft.value = value" />
        </label>
        <label v-if="entry.description">旧资料内容<NInput v-model:value="draft.description" type="textarea" /></label>
        <label>字段解释<NInput :value="fieldDescription(draft.definition?.description, draft.definition?.id)" type="textarea" placeholder="解释含义和填写要求，与具体内容分开保存" @update:value="value => draft.definition = { ...fieldDefinition(draft), description: value }" /></label>
        <p v-if="error" role="alert">{{ error }}</p><NButton type="primary" attr-type="submit" :disabled="disabled">保存</NButton>
      </form>
    </NDrawerContent></NDrawer>
    <NDrawer :show="adding" :width="'min(620px, 100vw)'" @update:show="value => adding = value"><NDrawerContent title="新增子字段" closable><FieldDefinitionEditor :fields="definitions" @change="value => definitions = value" /><NButton type="primary" :disabled="disabled" @click="saveChildren">添加</NButton></NDrawerContent></NDrawer>
  </article>
</template>
<style scoped>
.entry-node{padding:12px;border:1px solid var(--border);border-radius:9px;margin:9px 0;background:var(--surface)}.nested{margin-left:12px}header{display:flex;justify-content:space-between;gap:12px}header>div:first-child{min-width:0}strong{font-size:14px}small{margin-left:10px;color:var(--text-muted)}p{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0;font-size:13px}.explanation{color:var(--text-muted)}.actions{display:flex;gap:8px;align-items:start}.field-form{display:grid;gap:14px}.field-form label{display:grid;gap:7px}
</style>
