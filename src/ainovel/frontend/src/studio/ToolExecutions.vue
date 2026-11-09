<script setup lang="ts">
import {ref} from 'vue';
import type {ToolExecution} from '../studio-api';

defineProps<{entries?:ToolExecution[];modelReply?:boolean;showRecord?:boolean}>();
const expanded = ref(false);

function display(value:unknown){
 return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
</script>

<template>
 <details v-if="entries?.length || modelReply || showRecord" class="tool-executions" @toggle="expanded=($event.currentTarget as HTMLDetailsElement).open">
  <summary>调用过程<template v-if="entries?.length"> · {{entries.length}} 次工具调用</template></summary>
  <ol v-if="expanded && entries?.length">
   <li v-for="(entry, index) in entries" :key="index">
    <div class="execution-heading">
     <code>{{entry.name}}</code>
     <span v-if="entry.actor">{{entry.actor}}</span>
     <span :class="entry.outcome.success ? 'succeeded' : 'failed'">{{entry.outcome.success ? '成功' : '未成功'}}</span>
    </div>
    <div v-if="entry.outcome.message" class="execution-message">{{entry.outcome.message}}</div>
    <pre v-if="entry.outcome.data != null" aria-label="工具返回数据">{{display(entry.outcome.data)}}</pre>
    <details v-if="entry.outcome.variable_schema != null">
     <summary>更新后的变量 Schema</summary>
     <pre>{{display(entry.outcome.variable_schema)}}</pre>
    </details>
   </li>
  </ol>
  <div v-else-if="expanded" class="execution-message">
   {{modelReply ? '模型返回了文字回复，工具调用 0 次。' : '这条回复暂无已保存的调用记录。'}}
  </div>
 </details>
</template>

<style scoped>
.tool-executions{min-width:0;max-width:100%;margin-top:6px;font-size:12px;color:var(--theme-text,#454c63)}
summary{cursor:pointer;width:fit-content;min-height:24px;line-height:24px;color:var(--theme-accent-text,#52628d)}
ol{display:grid;gap:8px;list-style:none;padding:0;margin:4px 0 0}
li{min-width:0;padding:8px 10px;border:1px solid var(--theme-border,#e4e8f2);border-radius:8px;background:var(--surface)}
.execution-heading{display:flex;align-items:center;gap:6px 10px;flex-wrap:wrap}
code,.execution-message{overflow-wrap:anywhere}
.execution-message{margin-top:4px;white-space:pre-wrap}
.succeeded{color:var(--theme-green-text,#37664c)}
.failed{color:var(--theme-red-text,#973c45)}
pre{max-height:15rem;overflow:auto;margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
</style>
