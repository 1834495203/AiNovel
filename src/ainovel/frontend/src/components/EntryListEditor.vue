<script setup lang="ts">
import { ref } from "vue";
import { NButton, NDrawer, NDrawerContent, NInput, NTag } from "naive-ui";
import { type Schemas } from "../api/client";
import EntryTreeNode from "./EntryTreeNode.vue";
import FieldDefinitionEditor from "./FieldDefinitionEditor.vue";
import { instantiateField } from "../ui/fields";

type EntryOutput = Schemas["Entry-Output"];
type EntryInput = Schemas["Entry-Input"];
const props = defineProps<{ title: string; entries: EntryOutput[]; disabled: boolean; note?: string }>();
const emit = defineEmits<{ save: [entry: EntryInput]; saveMany: [entries: EntryInput[]]; remove: [id: string] }>();
const adding = ref(false);
const definitions = ref<Schemas["FieldDefinition-Input"][]>([]);
function add() {
  if (props.disabled || !definitions.value.length || definitions.value.some(field => !field.name.trim())) return;
  // Save all new fields in one event through the batch callback when available.
  emit("saveMany", definitions.value.map(instantiateField));
  definitions.value = []; adding.value = false;
}

</script>

<template>
  <section class="entry-collection">
    <header class="entry-collection-heading"><div><h4>{{ title }} <NTag size="small" round :bordered="false">{{ entries.length }}</NTag></h4><p v-if="note">{{ note }}</p></div><NButton size="small" secondary :disabled="disabled" @click="adding = !adding">{{ adding ? "取消" : "添加条目" }}</NButton></header>
    <NDrawer :show="adding" placement="right" :width="'min(420px, 100vw)'" @update:show="visible => adding = visible"><NDrawerContent title="新增资料条目" closable>
      <form v-if="adding" class="entry-add-form" @submit.prevent="add"><FieldDefinitionEditor :fields="definitions" @change="value => definitions = value" /><NButton attr-type="submit" type="primary" :disabled="disabled || !definitions.length">保存字段</NButton></form>
    </NDrawerContent></NDrawer>
    <EntryTreeNode v-for="entry in entries" :key="entry.id" :entry="entry" :disabled="disabled" :is-root="true" @save="emit('save',$event)" @remove="emit('remove',$event)"/>
  <p v-if="!entries.length && !adding" class="entry-empty">还没有记录。可以先写下一个具体条目，之后再继续补充。</p>
  </section>
</template>

<style scoped>
.entry-collection{border-top:1px solid var(--line,#e8e5dc);padding-top:.85rem;margin-top:.9rem}.entry-collection-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:.8rem}.entry-collection-heading h4{font-size:.86rem;margin:0}.entry-collection-heading h4 span{font-size:.7rem;color:var(--muted,#77786e);font-weight:400}.entry-collection-heading p,.entry-empty{color:var(--muted,#77786e);font-size:.76rem;line-height:1.5;margin:.35rem 0}.entry-collection-heading button,.entry-add-form button{border:1px solid var(--line,#e8e5dc);background:#fffefa;border-radius:99px;padding:.35rem .65rem;font:inherit;font-size:.75rem;cursor:pointer}.entry-add-form{display:grid;gap:.45rem;margin:.7rem 0;padding:.7rem;background:#f7f5ef;border-radius:.65rem}.entry-add-form input,.entry-add-form textarea{min-width:0;border:1px solid var(--line,#e8e5dc);border-radius:.45rem;padding:.55rem;font:inherit;font-size:.8rem;background:#fffefa}.entry-add-form textarea{resize:vertical}
:global(:root[data-theme='dark'] .entry-collection-heading button),:global(:root[data-theme='dark'] .entry-add-form button){background:var(--surface);color:var(--ink);border-color:var(--line)}
:global(:root[data-theme='dark'] .entry-add-form){background:var(--soft)}
:global(:root[data-theme='dark'] .entry-add-form input),:global(:root[data-theme='dark'] .entry-add-form textarea){background:var(--paper);color:var(--ink);border-color:var(--line)}
.entry-collection{border-color:var(--border);color:var(--text)}
.entry-collection-heading{align-items:center}
.entry-collection-heading h4{display:flex;align-items:center;gap:8px;font-size:.85rem;font-weight:620}
.entry-collection-heading p,.entry-empty{color:var(--text-muted)}
.entry-add-form{display:grid;gap:12px;padding:12px 0;background:transparent;border:0}
.entry-add-form label{display:grid;gap:6px;color:var(--text-muted);font-size:12px}
.entry-add-actions{display:flex;justify-content:flex-end;gap:7px}
.entry-add-form .n-input{min-width:0}
</style>
