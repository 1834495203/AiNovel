<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue';
import {NAlert, NButton, NFormItem, NInput, NInputNumber, NSelect} from 'naive-ui';
import {studio, type ContextMode, type ContextSection, type ContextTemplateDraft, type DocumentationSettings, type Novel, type Preview, type StudioContracts, type StudioDocumentation, type StudioMode, type StudioSettings} from '../studio-api';

const props = defineProps<{
  section:ContextSection; novel:Novel; sceneId:string; branchId:string; message:string;
  busy:boolean; contracts?:StudioContracts; save:(payload:StudioSettings)=>Promise<unknown>;
  schemaVersion?:ContextTemplateDraft['schema_version'];
}>();
const emit = defineEmits<{documentation:[value:StudioDocumentation|undefined]; preview:[mode:ContextMode]}>();
const mode = defineModel<StudioMode>('mode', {required:true});
const contextMode = computed(() => mode.value === 'gm' || mode.value === 'role' || mode.value === 'writing' ? mode.value : undefined);
const modes = [
  {label:'GM 演绎', value:'gm'}, {label:'角色扮演', value:'role'}, {label:'正文写作', value:'writing'},
  {label:'创建小说', value:'creation'}, {label:'写作对话', value:'writing_chat'}, {label:'章节摘要', value:'summary'},
];
const detailOptions = [{label:'常用字段及自定义项', value:'compact'}, {label:'全部字段', value:'full'}, {label:'关闭变量说明', value:'off'}];
const compression = ref({...props.novel.compression});
const prompts = ref(Object.fromEntries(Object.entries(props.novel.prompts).filter(([key, source])=>source!==props.contracts?.prompts[key])));
const docs = ref<StudioDocumentation>({...props.novel.documentation});
const preview = ref<Preview|null>(null), error = ref(''), estimating = ref(false), saved = ref(false);
const promptSource = computed(() => prompts.value[mode.value] ?? props.contracts?.prompts[mode.value] ?? '');
const customPrompt = computed(() => prompts.value[mode.value] !== undefined);
const settings = computed<StudioSettings>(() => ({
  compression:compression.value, prompts:prompts.value, context_source:props.novel.context_source, documentation:docs.value,
}));
const current = computed<DocumentationSettings>(() => docs.value[mode.value] ?? {
  tool_descriptions:{}, variable_descriptions:{}, variable_detail:'compact',
});
const toolName = ref(''), variablePath = ref('');
const tools = computed(() => props.contracts?.tools[mode.value] ?? []);
const tool = computed(() => tools.value.find(item => item.name === toolName.value));
const fields = computed(() => (mode.value === 'gm' && props.schemaVersion === 'shared-v1'
  ? props.contracts?.shared_gm_variable_docs : props.contracts?.variable_docs[mode.value]) ?? {});
const field = computed(() => fields.value[variablePath.value]);
watch(tools, items => { toolName.value = items[0]?.name ?? ''; }, {immediate:true});
watch(fields, items => { variablePath.value = Object.keys(items)[0] ?? ''; }, {immediate:true});

function updateDocs(change:Partial<DocumentationSettings>) {
  docs.value = {...docs.value, [mode.value]:{...current.value, ...change}};
  emit('documentation', docs.value);
}

function editDescription(kind:'tool_descriptions'|'variable_descriptions', key:string, value:string) {
  const descriptions = {...current.value[kind]};
  if (value.trim()) descriptions[key] = value;
  else delete descriptions[key];
  updateDocs({[kind]:descriptions});
}

let timer = 0, generation = 0;
watch([settings, () => props.section], () => {
  saved.value = false;
  window.clearTimeout(timer);
  const request = ++generation;
  if (props.section !== 'compression') return;
  estimating.value = true;
  timer = window.setTimeout(async () => {
    try {
      const result = await studio<Preview>(`/novels/${props.novel.id}/estimate`, {
        ...settings.value, scene_id:props.sceneId, branch_id:props.branchId, message:props.message,
      });
      if (request === generation) { preview.value = result; error.value = ''; }
    } catch (exception) {
      if (request === generation) error.value = exception instanceof Error ? exception.message : String(exception);
    } finally {
      if (request === generation) estimating.value = false;
    }
  }, 400);
}, {deep:true, immediate:true});
onBeforeUnmount(() => { window.clearTimeout(timer); ++generation; });

async function save() {
  if (await props.save(settings.value)) {
    saved.value = true;
    emit('documentation', undefined);
  }
}
</script>

<template>
 <section v-show="section !== 'template'" class="studio-config">
  <div v-if="section === 'compression'" class="settings-section">
   <div class="context-stat"><span>预计下一次输入</span><strong>{{preview?.input_tokens.toLocaleString() ?? '—'}} <small>token</small></strong><p>{{estimating ? '正在更新估算…' : '按当前幕估算。保存后应用到作品。'}}</p></div>
   <NAlert v-if="error" type="error">{{error}}</NAlert>
   <h3>章节保留范围</h3><p>先使用完整章节，再使用幕级内容，更早的章节使用摘要。缺少有效摘要时保留全文。</p>
   <div class="form-grid">
    <NFormItem label="最近完整章节数"><NInputNumber v-model:value="compression.full_chapters" :min="0" :max="100"/></NFormItem>
    <NFormItem label="再往前的幕级章节数"><NInputNumber v-model:value="compression.scene_chapters" :min="0" :max="100"/></NFormItem>
   </div>
   <h3>演绎与状态</h3>
   <div class="form-grid">
    <NFormItem label="每轮模型调用预算"><NInputNumber v-model:value="compression.max_calls" :min="1" :max="100"/></NFormItem>
    <NFormItem label="角色快照间隔（幕）"><NInputNumber v-model:value="compression.snapshot_interval" :min="1" :max="100"/></NFormItem>
   </div>
  </div>
  <template v-else-if="section !== 'template'">
   <NFormItem label="AI 模式"><NSelect v-model:value="mode" :options="modes" aria-label="AI 模式"/></NFormItem>
   <div v-if="section === 'behavior'" class="settings-section">
    <p>行为提示词决定 AI 怎么做。内置版本只读，可复制后自定义。</p>
    <NFormItem label="行为 Jinja 模板">
     <NInput :value="promptSource" :readonly="!customPrompt || busy" type="textarea" :autosize="{minRows:12,maxRows:24}"
      :input-props="{'aria-label':'行为Jinja模板',spellcheck:false}" @update:value="prompts[mode]=$event"/>
    </NFormItem>
    <NButton v-if="customPrompt" :disabled="busy" @click="delete prompts[mode]">恢复内置提示词</NButton>
    <NButton v-else :disabled="busy || !promptSource" @click="prompts[mode]=promptSource">复制并自定义</NButton>
   </div>
   <div v-else class="documentation-grid">
    <section class="settings-section">
     <h3>工具说明</h3>
     <p>说明会随模型请求发送，也用于原生工具定义。参数结构由程序维护。</p>
     <template v-if="tool">
      <NFormItem label="工具"><NSelect v-model:value="toolName" :options="tools.map(item=>({label:item.name,value:item.name}))" aria-label="选择工具"/></NFormItem>
      <p>内置说明 · {{tool.description}}</p>
      <NFormItem label="自定义工具说明"><NInput type="textarea" :value="current.tool_descriptions[tool.name] ?? ''" placeholder="留空使用内置说明"
       :input-props="{'aria-label':'自定义工具说明'}" @update:value="editDescription('tool_descriptions',toolName,$event)"/></NFormItem>
      <details><summary>工具参数 Schema</summary><pre class="schema-block">{{JSON.stringify(tool.parameters,null,2)}}</pre></details>
     </template>
     <p v-else>此模式直接返回文本，当前没有可调用工具。</p>
    </section>
    <section class="settings-section">
     <h3>变量 Schema</h3>
     <p>变量路径和类型来自实际资料。可自定义字段含义，帮助 AI 理解数据。</p>
     <NFormItem label="发送范围"><NSelect :value="current.variable_detail" :options="detailOptions" @update:value="updateDocs({variable_detail:$event})"/></NFormItem>
     <NFormItem label="变量路径"><NSelect v-model:value="variablePath" filterable :options="Object.keys(fields).map(path=>({label:path,value:path}))" aria-label="选择变量路径"/></NFormItem>
     <p v-if="field">{{field.type}}<template v-if="field.description"> · {{field.description}}</template></p>
     <NFormItem v-if="field" label="自定义变量说明"><NInput type="textarea" :value="current.variable_descriptions[variablePath] ?? ''" placeholder="留空使用内置说明"
      :input-props="{'aria-label':'自定义变量说明'}" @update:value="editDescription('variable_descriptions',variablePath,$event)"/></NFormItem>
    </section>
   </div>
  </template>
  <div class="settings-actions">
   <NButton v-if="section === 'documentation' && contextMode" @click="emit('preview',contextMode)">查看随请求发送的说明</NButton>
   <NButton type="primary" :disabled="busy" :loading="busy" @click="save">保存设置</NButton>
   <span v-if="saved" role="status">设置已保存</span>
  </div>
 </section>
</template>

<style scoped>
.studio-config{max-width:1040px}
.documentation-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.settings-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:16px}
@media(max-width:640px){.documentation-grid{grid-template-columns:minmax(0,1fr)}}
</style>
