<script setup lang="ts">
import {computed,ref,watch} from 'vue';
import {NAlert,NButton,NDrawer,NDrawerContent,NFormItem,NInput,NModal,NSelect,NSkeleton,NTabPane,NTabs,useDialog,useMessage} from 'naive-ui';
import {Plus,Save,Download,Trash2} from '@lucide/vue';
import {studio,type FieldTemplate,type Novel,type Scope,type TemplateFields} from '../studio-api';
import EntityEditor from './EntityEditor.vue';
import {localId} from './localId';
const props=defineProps<{show:boolean;mobile:boolean;novel:Novel|null;busy:boolean}>();
const emit=defineEmits<{'update:show':[value:boolean];apply:[id:string]}>();
const toast=useMessage(),dialog=useDialog();
const templates=ref<FieldTemplate[]>([]),cache=ref<Record<string,FieldTemplate>>({}),selected=ref(''),scope=ref<Scope>('world');
const loading=ref(false),working=ref(false),invalid=ref(false),error=ref(''),exporting=ref(false),exportName=ref('');
const names:Record<Scope,string>={world:'世界观',characters:'角色',creation:'创作',chapters:'章设定'};
const current=computed(()=>cache.value[selected.value]);
const options=computed(()=>Object.entries(cache.value).map(([key,t])=>({value:key,label:t.name+(t.id?'':' · 未保存')})));
const dirty=computed(()=>{const t=current.value;return !!t&&(!t.id||JSON.stringify(t)!==JSON.stringify(templates.value.find(v=>v.id===t.id)))});
function emptyFields():TemplateFields{const entity=(name:string,type:string)=>({name,type,description:[],special_attributes:[]});return {world:entity('世界观','world'),characters:entity('角色','human'),creation:entity('创作','creation'),chapters:entity('章节','chapter')}}
function fresh(){const key=localId('new');cache.value[key]={id:'',version:0,name:'新字段模板',fields:emptyFields()};selected.value=key;scope.value='world'}
async function load(){loading.value=true;error.value='';try{templates.value=await studio<FieldTemplate[]>('/templates');for(const t of templates.value)if(!cache.value[t.id])cache.value[t.id]=JSON.parse(JSON.stringify(t));if(!current.value){if(templates.value.length)selected.value=templates.value[0].id;else fresh()}}catch(e){error.value=String(e)}finally{loading.value=false}}
async function save(){if(!current.value||working.value)return;working.value=true;error.value='';try{const {id,...data}=current.value;const value=await studio<FieldTemplate>('/templates',{...data,id:id||null});const old=selected.value;cache.value[value.id]=JSON.parse(JSON.stringify(value));selected.value=value.id;if(old!==value.id)delete cache.value[old];templates.value=await studio<FieldTemplate[]>('/templates');toast.success('模板已保存，已有小说不受影响')}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{working.value=false}}
function remove(){if(!current.value)return;dialog.warning({title:'删除字段模板？',content:'已初始化的小说会保留自己的字段，不受影响。',positiveText:'删除',negativeText:'取消',onPositiveClick:async()=>{if(!current.value)return;try{if(current.value.id)await studio(`/templates/${current.value.id}?version=${current.value.version}`,undefined,'DELETE');delete cache.value[selected.value];selected.value=Object.keys(cache.value)[0]??'';if(!selected.value)fresh();templates.value=await studio<FieldTemplate[]>('/templates');toast.success('模板已删除')}catch(e){error.value=String(e);return false}}})}
function openExport(){exportName.value=(props.novel?.title??'本书')+'的字段模板';exporting.value=true}
async function exportBook(){if(!props.novel||working.value||!exportName.value.trim())return;working.value=true;try{const t=await studio<FieldTemplate>(`/novels/${props.novel.id}/template/export`,{version:props.novel.version,name:exportName.value.trim()});cache.value[t.id]=JSON.parse(JSON.stringify(t));selected.value=t.id;templates.value=await studio<FieldTemplate[]>('/templates');exporting.value=false;toast.success('已另存为新模板')}catch(e){toast.error(String(e))}finally{working.value=false}}
watch(()=>props.show,value=>{if(value)load()});
</script>
<template><NDrawer :show="show" @update:show="emit('update:show',$event)" :placement="mobile?'bottom':'right'" :width="mobile?undefined:760" :height="mobile?'calc(var(--app-height, 100dvh) - 16px)':undefined" class="workspace-drawer"><NDrawerContent title="字段模板" closable :native-scrollbar="false" :body-content-style="{padding:mobile?'16px':'24px'}">
 <div class="drawer-lead"><p>选择模板，直接编辑。小说保存独立副本，之后互不影响。</p><NButton secondary @click="fresh" :disabled="working"><template #icon><Plus :size="16"/></template>新增模板</NButton></div>
 <NAlert v-if="error" type="error" closable @close="error=''">{{error}}</NAlert>
 <NSkeleton v-if="loading" text :repeat="6"/>
 <template v-else-if="current"><div class="template-picker"><NSelect v-model:value="selected" :options="options" filterable aria-label="切换模板"/><span v-if="dirty" class="unsaved-mark">未保存</span></div>
 <NFormItem label="模板名字"><NInput v-model:value="current.name" :input-props="{'aria-label':'模板名字'}"/></NFormItem>
 <NTabs v-model:value="scope" type="line" animated><NTabPane v-for="(name,key) in names" :key="key" :name="key" :tab="name"><EntityEditor :key="selected+key" :value="current.fields[key]" template :disabled="working" @invalid="invalid=$event"/></NTabPane></NTabs>
 </template>
 <template #footer><div class="drawer-footer-stack"><div class="drawer-footer"><NButton quaternary type="error" @click="remove" :disabled="working||!current"><template #icon><Trash2 :size="16"/></template>删除</NButton><NButton type="primary" @click="save" :loading="working" :disabled="!current||invalid||!current.name.trim()"><template #icon><Save :size="16"/></template>保存模板</NButton></div><div v-if="novel" class="template-book-actions"><NButton v-if="!novel.created" secondary type="primary" :disabled="busy||working||!current?.id||dirty" @click="emit('apply',current.id)">用于当前构思</NButton><NButton secondary :disabled="busy||working" @click="openExport"><template #icon><Download :size="15"/></template>本书另存为模板</NButton></div><small v-if="novel&&!novel.created">初始化仅补充缺少的字段，保留已经讨论好的设定。</small></div></template>
</NDrawerContent></NDrawer>
<NModal v-model:show="exporting" preset="card" title="另存为新模板" class="small-dialog" :style="{width:'min(440px, calc(100vw - 28px))'}"><NFormItem label="新模板名字"><NInput v-model:value="exportName" :input-props="{'aria-label':'新模板名字'}" @keydown.enter="exportBook"/></NFormItem><template #footer><div class="modal-actions"><NButton @click="exporting=false">取消</NButton><NButton type="primary" :loading="working" :disabled="!exportName.trim()" @click="exportBook">另存为新模板</NButton></div></template></NModal>
</template>
