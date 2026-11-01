<script setup lang="ts">
import { ref } from "vue";
import { NButton, NCheckbox, NInput, NSelect } from "naive-ui";
import type { CommandPayload, CommandView, Schemas, StoryView } from "../api/client";
import CustomFieldsPanel from "./CustomFieldsPanel.vue";
const props = defineProps<{ view: StoryView; disabled: boolean; executeCommand: (payload: CommandPayload) => Promise<CommandView> }>();
const emit = defineEmits<{ "revision-changed": [] }>();
const instruction = ref("");
const scope = ref<Schemas["Direction"]["scope"]>("global");
const hard = ref(false);
const notice = ref("");
async function direction(item?: Schemas["Direction"]) {
  try {
    if (!item && !instruction.value.trim()) return;
    const latest = props.view.story.chapters.at(-1);
    if (!item && scope.value === "chapter" && !latest) throw new Error("尚无章节，请先选择其他范围。");
    await props.executeCommand({ kind: "direction", direction: item ? { ...item, active: !item.active } : {
      id: crypto.randomUUID(), instruction: instruction.value.trim(), scope: scope.value, hard: hard.value, active: true,
      chapter_id: scope.value === "chapter" ? latest?.id : null, scene_id: null,
    } });
    instruction.value = ""; emit("revision-changed"); notice.value = "写作要求已保存";
  } catch (error) { notice.value = error instanceof Error ? error.message : "保存失败"; }
}
</script>
<template>
  <div class="story-materials">
    <CustomFieldsPanel :view="view" :disabled="disabled" :execute-command="executeCommand" @revision-changed="emit('revision-changed')" />
    <details class="directions"><summary>写作要求 · {{ view.story.directions.length }}</summary>
      <p>范围和生效状态由系统管理，资料内容使用上方的自定义字段。</p>
      <form @submit.prevent="direction()"><NInput v-model:value="instruction" type="textarea" placeholder="写作要求" />
        <NSelect v-model:value="scope" :options="[{ value: 'global', label: '全局' }, { value: 'future', label: '后续故事' }, { value: 'chapter', label: '当前章节' }, { value: 'scene', label: '下一幕' }]" />
        <NCheckbox v-model:checked="hard">硬约束</NCheckbox><NButton type="primary" attr-type="submit" :disabled="disabled">添加写作要求</NButton></form>
      <div v-for="item in view.story.directions" :key="item.id" class="direction"><span>{{ item.instruction }} · {{ item.scope }}</span><NButton size="small" :disabled="disabled" @click="direction(item)">{{ item.active ? '停用' : '启用' }}</NButton></div>
      <p v-if="notice" role="status">{{ notice }}</p>
    </details>
  </div>
</template>
<style scoped>
.story-materials{display:grid;gap:24px}.directions{padding:16px;border:1px solid var(--border);border-radius:10px}.directions summary{cursor:pointer}.directions form{display:grid;gap:12px;margin:14px 0}.directions p{color:var(--text-muted);font-size:13px}.direction{display:flex;justify-content:space-between;gap:12px;margin:10px 0}
</style>
