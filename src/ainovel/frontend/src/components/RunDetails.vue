<script setup lang="ts">
import type { RunOverview } from "../api/client";
import { readableOutput, statusLabel } from "../ui/runPresentation";
import RunSummary from "./RunSummary.vue";
const props = defineProps<{ run: RunOverview }>();
const stepStatus = (status: string) => status === "started" && ["completed", "failed"].includes(props.run.command.status) ? "结束状态未记录" : statusLabel(status);
</script>
<template>
  <RunSummary :run="run" />
  <ol class="journal-steps">
    <li v-for="step in run.steps" :key="step.event_id">
      <div class="step-heading"><strong>{{ step.label }}</strong><span>{{ stepStatus(step.status) }}</span></div>
      <p v-if="step.explanation">{{ step.explanation }}</p>
      <p v-if="step.error" class="error-text">{{ step.error }}</p>
      <details v-if="readableOutput(step.structured_output).length"><summary>结果与决策</summary><dl class="readable-results"><template v-for="(row, index) in readableOutput(step.structured_output)" :key="index"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></template></dl></details>
      <p v-if="step.tool_name" class="muted">工具：{{ step.tool_name }}</p>
      <details v-if="step.reasoning_content"><summary>模型返回的思考内容</summary><p class="preserve-text">{{ step.reasoning_content }}</p></details>
      <details v-if="step.attempts.length > 1"><summary>{{ step.attempts.length }} 次模型尝试</summary><p v-for="(attempt, index) in step.attempts" :key="index">第 {{ index + 1 }} 次 · {{ attempt.model }} · {{ (attempt.duration_ms / 1000).toFixed(1) }} 秒 · 输入 {{ attempt.input_tokens ?? '未记录' }} / 输出 {{ attempt.output_tokens ?? '未记录' }} tokens<span v-if="attempt.error"> · {{ attempt.error }}</span></p></details>
    </li>
  </ol>
</template>
