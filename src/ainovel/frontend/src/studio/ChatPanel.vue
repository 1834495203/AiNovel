<script setup lang="ts">
import {computed,nextTick,onMounted,ref,watch} from 'vue';
import {NAlert,NButton,NInput,NModal,useDialog} from 'naive-ui';
import {ArrowDown,ArrowUp,Sparkles,UserRound,RotateCcw,SlidersHorizontal} from '@lucide/vue';
import type {Message} from '../studio-api';
const props=defineProps<{messages:Message[];pending:{id:string;content:string}|null;draft:string;busy:boolean;editable:boolean;created:boolean;writing?:boolean;status:string;tokens?:number;failed:string;pollFailed:boolean;mobile:boolean;calls:number;canChangeTurn?:boolean;legacyTurn?:boolean;changeTurn?:(kind:'retry'|'edit'|'withdraw',content?:string)=>Promise<boolean>}>();
const emit=defineEmits<{send:[];'update:draft':[value:string];settings:[];retry:[];refresh:[]}>();
const dialog=useDialog(),editing=ref(false),editText=ref(''),changing=ref(false);
const assistantName=computed(()=>props.writing?'写作助手':props.created?'GM':'构思助手');
const turnState=computed(()=>props.writing?'正文和创作字段':'资料、角色状态和演绎');
const lastUser=computed(()=>[...props.messages].reverse().find(m=>m.role==='user'));
function editLast(){editText.value=lastUser.value?.content??'';editing.value=true}
async function submitEdit(){changing.value=true;try{if(await props.changeTurn?.('edit',editText.value))editing.value=false}finally{changing.value=false}}
function turnAction(kind:'retry'|'withdraw'){
 const gmRetry=kind==='retry'&&props.created&&!props.writing;
 const content=props.legacyTurn
  ? `这条旧消息没有回退记录。当前${turnState.value}将保留。重试会从当前状态重新运行 AI。`
  : `将恢复本轮开始前的${turnState.value}，并移除本轮 AI 回复。之后的剧情修改会阻止此操作。`;
 dialog.warning({title:kind==='retry'?'重新运行这一轮？':'撤回这一轮？',
  content:gmRetry?`${content} 重试会使用当前启用的 GM 模板。`:content,
  positiveText:props.legacyTurn?(kind==='retry'?'保留资料并重试':'仅撤回消息'):(kind==='retry'?'恢复并重试':'撤回对话及操作'),
  negativeText:'取消',onPositiveClick:async()=>{return await props.changeTurn?.(kind)}})
}
const scroller=ref<HTMLElement|null>(null),input=ref<InstanceType<typeof NInput>|null>(null),nearBottom=ref(true),unread=ref(false);
function scroll(){if(scroller.value){scroller.value.scrollTop=scroller.value.scrollHeight;unread.value=false}}
function observe(){const el=scroller.value;if(el)nearBottom.value=el.scrollHeight-el.scrollTop-el.clientHeight<90;if(nearBottom.value)unread.value=false}
function keydown(event:KeyboardEvent){if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!props.mobile){event.preventDefault();if(!props.busy&&props.editable&&props.draft.trim())emit('send')}}
function starter(text:string){emit('update:draft',text);nextTick(()=>input.value?.focus())}
watch(()=>[props.messages.length,props.messages.at(-1)?.content,props.pending?.id,props.busy,props.status],async()=>{const follow=nearBottom.value||!!props.pending;await nextTick();if(follow)scroll();else unread.value=true});
onMounted(()=>nextTick(scroll));
</script>
<template><section class="chat-panel" :aria-label="writing?'写作对话':'与AI对话'">
 <div ref="scroller" class="chat-scroll" @scroll.passive="observe" aria-live="polite" aria-relevant="additions text">
  <div v-if="!messages.length&&!pending" class="chat-welcome"><div class="welcome-symbol"><Sparkles :size="25"/></div><template v-if="writing"><h2>想怎样写这一幕？</h2><p>可以聊写法，也可以指定一句、一段，或在整幕里替换用词。</p></template><template v-else><span class="eyebrow">{{created?'这一幕，从你的想法开始':'先聊构思，再创建小说'}}</span><h2>{{created?'这一幕，你想让什么发生？':'想写一个怎样的故事？'}}</h2><p>{{created?'告诉 GM 场景、人物或你期待的冲突。角色会自己决定行动。':'可以只有一个人物、一种氛围，甚至一句模糊的想法。AI 会边聊边整理设定。'}}</p><div class="starter-list"><button v-for="text in (created?['安排角色在一个意外的地点相遇','让角色自己决定如何应对眼前的局面']:['我有一个人物，想和你一起完善他的故事','我想写一部悬疑小说，先帮我讨论世界观','我还没想好，从几个故事方向聊起吧'])" :key="text" @click="starter(text)" :disabled="!editable||busy">{{text}}<ArrowUp :size="15"/></button></div></template></div>
  <TransitionGroup name="message" tag="div" class="message-list">
   <article v-for="m in messages" :key="m.id" :class="['message',m.role]" :data-message-id="m.id"><div class="message-avatar"><UserRound v-if="m.role==='user'" :size="16"/><Sparkles v-else :size="16"/></div><div class="message-content"><span class="message-author">{{m.role==='user'?'你':assistantName}}</span><div class="message-bubble">{{m.content}}</div><details v-if="m.changes?.length" class="prose-diff"><summary>查看文字改动</summary><div v-for="(change,i) in m.changes" :key="i"><small>{{change.paragraph?'第 '+change.paragraph+' 段':'本幕'}} · {{change.count}} 处</small><del v-if="change.before">{{change.before}}</del><ins v-if="change.after">{{change.after}}</ins></div></details><div v-if="m.id===lastUser?.id" class="turn-actions"><small v-if="legacyTurn&&canChangeTurn" class="legacy-turn-note">旧消息无回退记录，操作将保留当前{{turnState}}。</small><template v-if="canChangeTurn"><NButton size="tiny" text @click="turnAction('retry')">{{legacyTurn?'重试消息':'重试本轮'}}</NButton><NButton size="tiny" text @click="editLast">修改消息</NButton><NButton size="tiny" text @click="turnAction('withdraw')">{{legacyTurn?'撤回消息':'撤回本轮'}}</NButton></template><small v-else-if="editable&&!busy">{{created?'本轮之后资料已变化，或旧消息没有回退记录':'资料已变化，或这条旧消息没有回退记录'}}</small></div></div></article>
   <article v-if="pending" :key="pending.id" class="message user pending"><div class="message-avatar"><UserRound :size="16"/></div><div class="message-content"><span class="message-author">你 · 正在发送</span><div class="message-bubble">{{pending.content}}</div></div></article>
   <article v-if="busy" key="thinking" class="message assistant thinking"><div class="message-avatar"><Sparkles :size="16"/></div><div class="message-content"><span class="message-author">{{assistantName}}</span><div class="thinking-bubble" role="status"><span class="thinking-dots"><i/><i/><i/></span><span>{{status}}</span><small v-if="calls>0">本轮 {{calls}} 次调用</small></div></div></article>
  </TransitionGroup>
  <NAlert v-if="pollFailed" type="warning" :show-icon="false" class="chat-inline-alert">连接暂时中断，任务仍可能在后台进行。<NButton text @click="emit('refresh')">重新连接</NButton></NAlert>
 </div>
 <NButton v-if="unread" round class="new-message-button" @click="scroll"><template #icon><ArrowDown :size="16"/></template>新消息</NButton>
 <div class="composer-zone">
  <NAlert v-if="failed" type="error" :show-icon="false" class="send-error">消息未发送，内容已保留。<NButton size="small" @click="emit('retry')" :disabled="busy">重试</NButton></NAlert>
  <div v-if="!editable&&!busy" class="readonly-hint">{{writing?'当前写作会话只读。':'此幕已冻结。要继续修改，请从这一幕创建分支。'}}</div>
  <div class="composer" :class="{'is-busy':busy}">
   <NInput ref="input" :value="draft" @update:value="emit('update:draft',$event)" type="textarea" :autosize="{minRows:1,maxRows:4}" :readonly="busy||!editable" :placeholder="busy?'AI 正在处理，结束后可继续发言…':!editable?'当前内容只读':writing?'告诉写作助手你想怎么写或改…':created?'和 GM 讨论这一幕…':'和 AI 聊聊你的小说想法…'" :aria-label="writing?'写作消息':'聊天消息'" :input-props="{'aria-label':writing?'写作消息':'聊天消息',enterkeyhint:mobile?'enter':'send',autocapitalize:'sentences'}" @keydown="keydown"/>
   <NButton class="send-button" circle type="primary" :loading="busy" :disabled="busy||!editable||!draft.trim()" aria-label="发送消息" @click="emit('send')"><template #icon><ArrowUp :size="21"/></template></NButton>
  </div>
  <div class="composer-meta"><button v-if="!writing" class="budget-link" @click="emit('settings')"><SlidersHorizontal :size="13"/><span>约 {{tokens==null?'—':tokens>=1000?(tokens/1000).toFixed(1)+'k':tokens}} token</span></button><span>{{busy?'回复会自动更新':mobile?'发送后会立即显示进度':'Enter 发送 · Shift + Enter 换行'}}</span></div>
 </div>
</section><NModal v-model:show="editing" preset="card" title="修改最后一条消息" :mask-closable="!changing" :style="{width:'min(600px, calc(100vw - 24px))'}"><p class="drawer-copy">{{legacyTurn?`此旧消息没有回退记录。保存后保留当前${turnState}，替换本轮聊天，并按新内容重新运行 AI。`:`保存后会恢复本轮开始前的${turnState}，再按新内容运行。`}}</p><NInput v-model:value="editText" type="textarea" :autosize="{minRows:4,maxRows:10}" :readonly="changing" :input-props="{'aria-label':'修改消息内容'}"/><template #footer><div class="modal-actions"><NButton :disabled="changing" @click="editing=false">取消</NButton><NButton type="primary" :loading="changing" :disabled="!editText.trim()||changing" @click="submitEdit">保存并重新运行</NButton></div></template></NModal></template>

<style scoped>
.prose-diff{margin-top:8px;font-size:13px}
.prose-diff summary{cursor:pointer;color:var(--theme-accent-text,#52628d)}
.prose-diff div{display:grid;gap:4px;margin-top:8px}
.prose-diff del,.prose-diff ins{padding:4px 8px;border-radius:4px;white-space:pre-wrap;overflow-wrap:anywhere}
.prose-diff del{background:var(--theme-red-surface,#fcebea);color:var(--theme-red-text,#832e31)}
.prose-diff ins{background:var(--theme-green-surface,#e9f6ee);color:var(--theme-green-text,#245a38);text-decoration:none}
</style>
