<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type StoryView, type CommandView, type CommandPayload } from "../api/client";
import StoryMaterials from "./StoryMaterials.vue";
const props = defineProps<{ storyId: string }>();
const view = ref<StoryView | null>(null);
const busy = ref(false);
const error = ref("");
async function refresh() { view.value = await api.get<StoryView>(`/api/stories/${props.storyId}`); }
async function execute(payload: CommandPayload): Promise<CommandView> {
  if (!view.value || busy.value) throw new Error("请等待资料读取完成。");
  busy.value = true;
  try {
    const result = await api.command(payload, props.storyId, view.value.revision_id, null, () => undefined);
    await refresh();
    return result;
  } finally { busy.value = false; }
}
onMounted(() => refresh().catch(e => error.value = e.message));
</script>
<template>
  <main class="materials-page">
    <header class="materials-page-heading"><h1>故事资料</h1><p>{{ view?.story.title }} · 设定与创作计划，修改后应用于后续创作。</p></header>
    <StoryMaterials v-if="view" :view="view" :disabled="busy" :execute-command="execute" />
    <p v-else>{{ error || '正在读取资料…' }}</p>
  </main>
</template>
