<script setup lang="ts">
import { computed } from "vue";
import type { RunOverview } from "../api/client";
import { runMetrics, changeText } from "../ui/runPresentation";
const props = defineProps<{ run: RunOverview | null | undefined }>();
const metrics = computed(() => runMetrics(props.run));
</script>
<template>
  <section class="scene-run-summary" aria-label="本幕运行摘要">
    <div class="metric-line"><span>{{ metrics.recorded ? `${metrics.tokens.toLocaleString()} tokens${metrics.estimated ? '（估算）' : ''}` : '用量未记录' }}</span><span>{{ metrics.recorded ? `${(metrics.milliseconds / 1000).toFixed(1)} 秒模型耗时` : '耗时未记录' }}</span><span>{{ metrics.steps }} 个步骤</span></div>
    <p v-if="run?.steps.length" class="compact-step-list">{{ run.steps.map(s => s.label).filter((s, i, all) => all.indexOf(s) === i).join(' → ') }}</p>
    <p v-if="run?.change_set.text_changed">正文已更新。</p>
    <details v-if="run?.change_set.entries.length" class="run-changes"><summary>{{ run.change_set.entries.length }} 项资料变更 · {{ [...new Set(run.change_set.entries.map(item => item.category))].join('、') }}</summary><div class="change-cards"><details v-for="(change, i) in run.change_set.entries" :key="i" class="change-card"><summary><span>{{ change.category }}</span> {{ change.path }} <small>{{ change.after ? (change.before ? '更新' : '新增') : '移除' }}</small></summary><p class="preserve-text">{{ change.after ? changeText(change.after) : '已移除' }}</p></details></div></details>
    <p v-else-if="run">无额外资料变化。</p>
  </section>
</template>
