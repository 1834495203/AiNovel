<script setup lang="ts">
import { ref, watch } from "vue";
import { NButton, NCollapse, NCollapseItem, NDrawer, NDrawerContent, NInput } from "naive-ui";
import type { Schemas } from "../api/client";

defineOptions({ name: "EntryTreeNode" });
type EntryOutput = Schemas["Entry-Output"];
type EntryInput = Schemas["Entry-Input"];
const props = defineProps<{ entry: EntryOutput; disabled: boolean; isRoot: boolean }>();
const emit = defineEmits<{ save: [entry: EntryInput]; updated: [entry: EntryInput]; remove: [id: string] }>();
const editing = ref(false);
const addingChild = ref(false);
const name = ref(props.entry.name);
const value = ref(props.entry.value === null ? "" : String(props.entry.value ?? ""));
const description = ref(props.entry.description);
const reason = ref(props.entry.reason);
const childName = ref("");
const childDescription = ref("");
watch(() => props.entry, entry => {
  if (editing.value) return;
  name.value = entry.name; value.value = entry.value === null ? "" : String(entry.value ?? "");
  description.value = entry.description; reason.value = entry.reason;
}, { deep: true });
function toInput(entry: EntryOutput): EntryInput {
  return { id: entry.id, name: entry.name, ...(entry.value === undefined ? {} : { value: entry.value }), description: entry.description,
    reason: entry.reason, source_scene_id: entry.source_scene_id, children: (entry.children ?? []).map(toInput) };
}
function publish(input: EntryInput) {
  if (props.isRoot) emit("save", input);
  else emit("updated", input);
}
function save() {
  const next: EntryInput = { ...toInput(props.entry), name: name.value.trim(), description: description.value.trim(), reason: reason.value.trim() };
  if (!next.name || props.disabled) return;
  if (value.value.trim()) {
    const numeric = Number(value.value);
    next.value = Number.isFinite(numeric) && value.value.trim() !== "" ? numeric : value.value.trim();
  } else delete next.value;
  editing.value = false;
  publish(next);
}
function updateChild(updated: EntryInput) {
  const children = (props.entry.children ?? []).map(child => child.id === updated.id ? updated : toInput(child));
  publish({ ...toInput(props.entry), children });
}
function removeChild(id: string) {
  publish({ ...toInput(props.entry), children: (props.entry.children ?? []).filter(child => child.id !== id).map(toInput) });
}
function addChild() {
  const childNameValue = childName.value.trim();
  if (!childNameValue || props.disabled) return;
  const child: EntryInput = { id: crypto.randomUUID(), name: childNameValue, description: childDescription.value.trim(), reason: "用户补充的子条目" };
  publish({ ...toInput(props.entry), children: [...(props.entry.children ?? []).map(toInput), child] });
  childName.value = ""; childDescription.value = ""; addingChild.value = false;
}
function removeSelf() {
  if (props.disabled || !props.entry.id) return;
  emit("remove", props.entry.id);
}
</script>

<template>
  <article class="entry-node" :class="{ nested: !isRoot }">
    <div v-if="!editing" class="entry-summary"><span class="entry-dot"></span><div class="entry-copy"><strong>{{ entry.name }}</strong><span v-if="entry.value !== undefined && entry.value !== null">{{ entry.value }}</span><p v-if="entry.description">{{ entry.description }}</p><details v-if="entry.reason || entry.source_scene_id"><summary>来源与依据</summary><small v-if="entry.reason">{{ entry.reason }}</small><code v-if="entry.source_scene_id">来源场景 {{ entry.source_scene_id }}</code></details></div>
      <div class="entry-actions"><NButton text size="tiny" :disabled="disabled" @click="editing = true">编辑</NButton><NButton text type="error" size="tiny" :disabled="disabled" @click="removeSelf">删除</NButton></div>
    </div>
    <NDrawer :show="editing" placement="right" :width="'min(440px, 100vw)'" @update:show="visible => editing = visible"><NDrawerContent :title="`编辑资料 · ${entry.name}`" closable>
      <form v-if="editing" class="entry-form" @submit.prevent="save"><label>名称<NInput v-model:value="name" required maxlength="160" /></label><label>值（可选）<NInput v-model:value="value" maxlength="1000" /></label><label>内容<NInput v-model:value="description" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" maxlength="4000" /></label><label>来源说明<NInput v-model:value="reason" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" maxlength="1000" /></label><div class="entry-actions"><NButton secondary size="small" attr-type="button" @click="editing = false">取消</NButton><NButton type="primary" size="small" attr-type="submit" :disabled="disabled || !name.trim()">保存</NButton></div></form>
    </NDrawerContent></NDrawer>
    <NCollapse v-if="entry.children?.length" class="entry-children" :default-expanded-names="isRoot ? ['children'] : []">
      <NCollapseItem name="children" :title="`子条目 · ${entry.children.length}`">
        <EntryTreeNode v-for="child in entry.children" :key="child.id" :entry="child" :disabled="disabled" :is-root="false" @updated="updateChild" @remove="removeChild"/>
      </NCollapseItem>
    </NCollapse>
    <form v-if="addingChild" class="entry-add-child" @submit.prevent="addChild"><NInput v-model:value="childName" required maxlength="160" aria-label="子条目名称" placeholder="子条目名称"/><NInput v-model:value="childDescription" maxlength="1000" aria-label="子条目内容" placeholder="子条目内容（可选）"/><NButton type="primary" size="small" attr-type="submit" :disabled="disabled || !childName.trim()">添加</NButton><NButton secondary size="small" attr-type="button" @click="addingChild = false">取消</NButton></form>
    <NButton v-else type="primary" text size="tiny" class="add-child" :disabled="disabled" @click="addingChild = true">添加子条目</NButton>
  </article>
</template>

<style scoped>
.entry-node{padding:.65rem .7rem;border:1px solid var(--line,#e8e5dc);border-radius:.65rem;margin:.55rem 0;background:#fffefa}.entry-node.nested{margin:.45rem 0 0 .7rem;background:#faf9f5}.entry-summary{display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:.6rem;align-items:start}.entry-dot{width:6px;height:6px;border-radius:50%;background:#87947d;margin-top:.45rem}.entry-copy{display:grid;gap:.17rem;min-width:0}.entry-copy strong{font-size:.8rem}.entry-copy>span,.entry-copy p{margin:0;color:#4a4b42;font-size:.78rem;line-height:1.5;white-space:pre-wrap}.entry-copy small{color:var(--muted,#77786e);font-size:.68rem}.entry-actions{display:flex;gap:.3rem;align-items:center}.entry-actions button,.add-child,.entry-add-child button{border:0;background:none;color:var(--accent,#596c57);font:inherit;font-size:.7rem;cursor:pointer;padding:.18rem .32rem}.entry-actions button.remove{color:#9a6258}.entry-form{display:grid;gap:.45rem}.entry-form label{display:grid;gap:.22rem;font-size:.7rem;color:var(--muted,#77786e)}.entry-form input,.entry-form textarea,.entry-add-child input{min-width:0;border:1px solid var(--line,#e8e5dc);border-radius:.4rem;padding:.45rem;font:inherit;font-size:.78rem;color:var(--ink,#28291f);background:white}.entry-form textarea{resize:vertical}.entry-children{margin-left:.25rem;border-left:1px solid #e7e4db;padding-left:.45rem}.add-child{color:var(--muted,#77786e);font-size:.68rem;margin:.25rem 0 0 1rem}.entry-add-child{display:flex;flex-wrap:wrap;gap:.35rem;margin:.45rem 0 0 1rem}.entry-add-child input{flex:1 1 120px}.entry-add-child button{border:1px solid var(--line,#e8e5dc);border-radius:99px;padding:.3rem .55rem}.entry-actions button:disabled,.add-child:disabled{opacity:.45;cursor:not-allowed}
:global(:root[data-theme='dark'] .entry-node){background:var(--surface);border-color:var(--line);color:var(--ink)}
:global(:root[data-theme='dark'] .entry-node.nested){background:color-mix(in srgb,var(--surface) 70%,var(--soft))}
:global(:root[data-theme='dark'] .entry-copy>span),:global(:root[data-theme='dark'] .entry-copy p){color:var(--ink)}
:global(:root[data-theme='dark'] .entry-form input),:global(:root[data-theme='dark'] .entry-form textarea),:global(:root[data-theme='dark'] .entry-add-child input){background:var(--paper);color:var(--ink);border-color:var(--line)}
.entry-node{background:var(--surface);border-color:var(--border);color:var(--text)}
.entry-node.nested{background:var(--surface-muted)}
.entry-copy>span,.entry-copy p{color:var(--text);}
.entry-copy small,.entry-empty{color:var(--text-muted)}
.entry-actions{gap:5px}
.entry-form label{color:var(--text-muted)}
.entry-form .n-input,.entry-add-child .n-input{min-width:0}
.entry-children{margin:.5rem 0 0 .25rem;padding-left:.5rem;border-left:1px solid var(--border)}
.entry-add-child{margin-left:.6rem}
.entry-add-child .n-input{flex:1 1 160px}
.add-child{margin:.3rem 0 0 .5rem}
:global(:root[data-theme='dark'] .entry-node),:global(:root[data-theme='dark'] .entry-node.nested){background:var(--surface);border-color:var(--border);color:var(--text)}
</style>
