<script setup lang="ts">
import {nextTick,onMounted,ref,watch} from 'vue';
import {NButton,NEmpty,NModal,NSelect,NTag} from 'naive-ui';
import {Plus,Users,LogIn,LogOut,Drama} from '@lucide/vue';
import type {Entity,Scene,StageEvent} from '../studio-api';
const props=defineProps<{scene:Scene;characters:Entity[];present:Entity[];offstage:Entity[];busy:boolean;editable:boolean;status:string}>();
const emit=defineEmits<{enter:[id:string];leave:[id:string];create:[]}>();
const joining=ref(false),choice=ref<string|null>(null),scroll=ref<HTMLElement|null>(null),follow=ref(true),history=ref(false);
function actor(id:string){return props.characters.find(c=>c.id===id)?.name??'GM'}
function author(event:StageEvent){
  if(event.kind==='gm_direction')return `GM → ${actor(event.actor_id)}`;
  return event.kind==='gm'?'GM':actor(event.actor_id);
}
const eventLabels:Record<string,string>={
  gm:'场景 / 客观结果',gm_direction:'调度提示词',role:'发言 / 行动意图',self_edit:'修改自身字段',
};
watch(()=>props.scene.events.length,async()=>{if(follow.value){await nextTick();if(scroll.value)scroll.value.scrollTop=scroll.value.scrollHeight}});
onMounted(()=>nextTick(()=>{if(scroll.value)scroll.value.scrollTop=scroll.value.scrollHeight}));
</script>
<template><section class="stage-panel"><div class="presence-bar"><span><Users :size="15"/>在场 <b>{{present.length}}</b></span><div class="presence-tags"><NTag v-for="c in present" :key="c.id" round :closable="editable&&!busy" @close="emit('leave',c.id)">{{c.name}}</NTag><small v-if="!present.length">暂无角色</small></div><NButton circle secondary :disabled="busy||!editable" aria-label="安排角色入场" @click="joining=true"><template #icon><Plus :size="17"/></template></NButton></div>
 <div ref="scroll" class="stage-scroll" aria-live="polite" @scroll.passive="follow=!!scroll&&scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90"><div v-if="!scene.events.length" class="stage-empty"><Drama :size="35"/><h3>舞台已准备好</h3><p>和 GM 商量场景与出场角色，演绎会在这里逐步展开。</p><NButton v-if="editable" secondary :disabled="busy" @click="joining=true">安排角色入场</NButton></div><TransitionGroup name="message" tag="div"><article v-for="(e,index) in scene.events" :key="e.id" :data-event-id="e.id" :class="['stage-event',e.kind]"><template v-if="e.kind==='enter'||e.kind==='leave'"><span class="stage-event-line"/><div class="stage-system-event"><span class="stage-position">#{{index+1}}</span><LogIn v-if="e.kind==='enter'" :size="13"/><LogOut v-else-if="e.kind==='leave'" :size="13"/><span>{{e.text}}</span></div></template><template v-else><div class="stage-avatar" :class="{gm:e.kind==='gm'||e.kind==='gm_direction'}">{{e.kind==='gm'||e.kind==='gm_direction'?'GM':actor(e.actor_id).slice(0,1)}}</div><div><header><span class="stage-position">#{{index+1}}</span><strong>{{author(e)}}</strong><small>{{eventLabels[e.kind]}}</small></header><p>{{e.text}}</p></div></template></article></TransitionGroup><div v-if="busy" class="stage-running"><span class="live-dot"/>{{status}}</div><NButton v-if="scene.abandoned.length" quaternary size="small" @click="history=!history">{{history?'收起':'查看'}}废弃演绎 · {{scene.abandoned.length}} 次</NButton><div v-if="history" class="abandoned-runs"><details v-for="(run,index) in scene.abandoned" :key="index"><summary>尝试 {{index+1}} · 不进入上下文</summary><p v-for="e in run" :key="e.id">{{author(e)}}<template v-if="e.kind==='gm_direction'||e.kind==='self_edit'"> · {{eventLabels[e.kind]}}</template>：{{e.text}}</p></details></div></div>
 <NModal v-model:show="joining" preset="card" title="安排角色入场" class="small-dialog" :style="{width:'min(440px, calc(100vw - 28px))'}"><p class="drawer-copy">选择已有角色。新建角色后，也需要单独安排入场。</p><NSelect v-model:value="choice" :options="offstage.map(c=>({label:c.name,value:c.id}))" filterable placeholder="选择角色" aria-label="选择入场角色"/><NButton text class="create-character-link" @click="joining=false;emit('create')"><template #icon><Plus :size="15"/></template>创建一个新角色</NButton><template #footer><div class="modal-actions"><NButton @click="joining=false">取消</NButton><NButton type="primary" :disabled="!choice||busy" @click="choice&&emit('enter',choice);joining=false;choice=null">入场</NButton></div></template></NModal>
</section></template>

<style scoped>
.stage-event.gm_direction p,.stage-event.self_edit p{white-space:pre-wrap;overflow-wrap:anywhere}
.stage-event.gm_direction header small{color:#65519a}
.stage-event.self_edit header small{color:#496957}
</style>
