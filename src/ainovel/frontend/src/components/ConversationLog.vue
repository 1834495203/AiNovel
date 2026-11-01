<script setup lang="ts">
import { ref } from "vue";
import { api, type CommandView, type RunOverview } from "../api/client";
import RunDetails from "./RunDetails.vue";
const props = defineProps<{ command: CommandView }>();
const run = ref<RunOverview | null>(null), error = ref("");
async function open(event: Event) {
  if (!(event.target as HTMLDetailsElement).open || run.value) return;
  try { run.value = await api.get<RunOverview>(`/api/runs/${props.command.submission.id}`); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "读取记录失败"; }
}
</script>
<template><details class="conversation-log" @toggle="open"><summary>本轮执行记录</summary><RunDetails v-if="run" :run="run"/><p v-else>{{ error || '正在读取…' }}</p></details></template>
<style scoped>.conversation-log{margin-top:10px;font-size:12px;color:var(--text-muted)}summary{cursor:pointer}</style>
