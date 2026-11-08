<script lang="ts">
export type VariableNode = {key:string;label:string;children:VariableNode[]};
</script>

<script setup lang="ts">
defineProps<{nodes:VariableNode[]}>();
</script>

<template>
 <ul class="variable-tree">
  <li v-for="node in nodes" :key="node.key">
   <details v-if="node.children.length">
    <summary>{{node.label}}</summary>
    <ContextVariableTree :nodes="node.children"/>
   </details>
   <span v-else>{{node.label}}</span>
  </li>
 </ul>
</template>

<style scoped>
.variable-tree{list-style:none;padding:0 0 0 13px;margin:4px 0;border-left:1px solid var(--theme-border,#e5eaf3)}
.variable-tree li{padding:3px 0;line-height:1.5}
.variable-tree summary{cursor:pointer;color:var(--theme-accent-text,#445585)}
</style>
