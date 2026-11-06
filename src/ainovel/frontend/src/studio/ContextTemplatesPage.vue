<script setup lang="ts">
import {computed, onBeforeUnmount, reactive, ref, watch} from 'vue';
import {NAlert, NButton, NInput, NSelect} from 'naive-ui';
import {studio, type GmContextSchema, type Novel, type Scene} from '../studio-api';
import ContextVariableTree, {type VariableNode} from './ContextVariableTree.vue';

const props = defineProps<{
  novel:Novel; scenes:Scene[]; sceneId:string; branchId:string; message:string;
  defaultSource:string; schema?:GmContextSchema; busy:boolean;
}>();
const emit = defineEmits<{back:[];updated:[novel:Novel]}>();
const selected = ref<string|null>(null);
const previewScene = ref(props.sceneId || props.scenes.at(-1)?.entity.id || '');
const drafts = reactive<Record<string,{name:string;source:string}>>({});
const preview = ref<{rendered_context:string;input_tokens:number;sample:boolean}|null>(null);
const previewError = ref(''), saveError = ref(''), pending = ref(false), saving = ref(false), notice = ref('');
let timer = 0, generation = 0;

const legacy = computed(() => props.novel.gm_context_source ??
  (props.novel.context_source.trim() === '{{ data | tojson }}' ? undefined : props.novel.context_source));
const active = computed(() => props.novel.active_gm_template_id ?? (legacy.value === undefined ? 'default' : 'legacy'));
const templates = computed(() => [
  {id:'default', name:'内置 GM 模板', source:props.defaultSource},
  ...(legacy.value === undefined ? [] : [{id:'legacy', name:'原有上下文模板', source:legacy.value}]),
  ...(props.novel.gm_templates ?? []),
]);
const options = computed(() => [
  ...templates.value.map(item => ({value:item.id,label:item.name + (item.id === active.value ? ' · 使用中' : '')})),
  ...(drafts.new ? [{value:'new',label:drafts.new.name || '未保存的新模板'}] : []),
]);
const sceneOptions = computed(() => props.scenes.map(scene => ({value:scene.entity.id,label:scene.entity.name})));
const original = computed(() => templates.value.find(item => item.id === selected.value));
const editor = computed(() => selected.value === null ? undefined : drafts[selected.value]);
const readOnly = computed(() => selected.value === 'default' || selected.value === 'legacy');
const dirty = computed(() => !!editor.value &&
  (editor.value.name !== original.value?.name || editor.value.source !== original.value?.source));

function select(id:string|null) {
  selected.value = id;
  saveError.value = '';
  notice.value = '';
  const item = templates.value.find(template => template.id === id);
  if (item && !drafts[item.id]) drafts[item.id] = {name:item.name,source:item.source};
}

function create(copy = false) {
  if (drafts.new) {
    select('new');
    return;
  }
  drafts.new = copy && editor.value
    ? {name:editor.value.name + ' 副本',source:editor.value.source}
    : {name:'',source:props.defaultSource};
  select('new');
}

function variableNodes(schema:GmContextSchema|undefined, prefix = 'data'):VariableNode[] {
  return Object.entries(schema?.properties ?? {}).map(([key, field]) => {
    const path = prefix + '.' + key;
    const shape = field.anyOf?.find(value => value.type !== 'null') ?? field;
    const child = shape.items ?? shape;
    const resolved = child.$ref ? props.schema?.$defs?.[child.$ref.split('/').at(-1) ?? ''] : child;
    const children = variableNodes(resolved, path + (shape.items ? '[]' : ''));
    const kind = shape.items ? '列表' : shape.enum ? '可选值' :
      ({object:'对象',string:'文本',integer:'整数',boolean:'布尔值'} as Record<string,string>)[shape.type ?? '']
      ?? (children.length ? '对象' : '');
    return {
      key:path,label:[key, kind, field.description].filter(Boolean).join(' · '),
      children,
    };
  });
}
const variables = computed(() => variableNodes(props.schema));
const example = '{% for character in data.characters %}\n- {{ character.name }}\n{% endfor %}';

watch(sceneOptions, options => {
  if (!options.some(option => option.value === previewScene.value)) {
    previewScene.value = options.at(-1)?.value ?? '';
  }
});
watch([selected, () => editor.value?.source, previewScene, () => props.branchId, () => props.message], () => {
  window.clearTimeout(timer);
  const request = ++generation;
  const draft = editor.value;
  preview.value = null;
  previewError.value = '';
  pending.value = !!draft;
  if (!draft) return;
  timer = window.setTimeout(async () => {
    try {
      const result = await studio<NonNullable<typeof preview.value>>(
        `/novels/${props.novel.id}/gm-template-preview`,
        {source:selected.value === 'legacy' ? null : draft.source,
          scene_id:previewScene.value, branch_id:props.branchId, message:props.message},
      );
      if (request === generation) preview.value = result;
    } catch (error) {
      if (request === generation) previewError.value = error instanceof Error ? error.message : String(error);
    } finally {
      if (request === generation) pending.value = false;
    }
  }, 300);
});
onBeforeUnmount(() => { window.clearTimeout(timer); ++generation; });

async function save(activate = false) {
  const id = selected.value;
  if (!id || !editor.value || saving.value || props.busy) return;
  saving.value = true;
  saveError.value = '';
  notice.value = '';
  try {
    const updated = await studio<Novel>(
      `/novels/${props.novel.id}/${activate ? 'gm-template-selection' : 'gm-templates'}`,
      activate
        ? {version:props.novel.version,id:id === 'legacy' ? null : id}
        : {version:props.novel.version,id:id === 'new' ? '' : id,name:editor.value.name,source:editor.value.source},
    );
    emit('updated', updated);
    if (!activate) {
      const item = id === 'new' ? updated.gm_templates?.at(-1) : updated.gm_templates?.find(item => item.id === id);
      if (item) {
        drafts[item.id] = {name:item.name,source:item.source};
        selected.value = item.id;
        if (id === 'new') delete drafts.new;
      }
    }
    notice.value = activate ? '已设为当前 GM 模板。' : '模板已保存。';
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : String(error);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
 <main class="context-page" aria-label="上下文模板">
  <header class="page-heading">
   <div><h1>上下文模板</h1><p>为这部作品编辑发送给 AI 的上下文。</p></div>
   <NButton quaternary @click="emit('back')">返回作品</NButton>
  </header>
  <div class="mode-list" aria-label="模板类型">
   <strong>GM 演绎</strong><span>角色 AI · 预留</span><span>写作 AI · 预留</span>
  </div>
  <div class="template-toolbar">
   <NSelect :value="selected" :options="options" placeholder="选择模板"
    aria-label="选择上下文模板" :disabled="saving" @update:value="select"/>
   <NButton :disabled="saving || !defaultSource" @click="create()">新建模板</NButton>
   <NButton v-if="editor && selected !== 'new'" :disabled="saving || !!drafts.new" @click="create(true)">复制为新模板</NButton>
  </div>
  <NAlert v-if="saveError" type="error">{{saveError}}</NAlert>
  <p v-if="notice" role="status" class="notice">{{notice}}</p>
  <div v-if="!editor" class="context-empty">选择一个模板开始编辑，或新建一份模板。</div>
  <template v-else>
   <div class="template-toolbar">
    <NInput v-model:value="editor.name" :readonly="readOnly || saving" maxlength="80" aria-label="模板名称" placeholder="模板名称"/>
    <NButton v-if="!readOnly" type="primary" :disabled="busy || saving || pending || !dirty || !editor.name.trim() || !!previewError" @click="save()">保存模板</NButton>
    <NButton v-if="selected === 'new'" :disabled="saving" @click="delete drafts.new; select(null)">放弃草稿</NButton>
    <NButton v-if="selected !== 'new' && selected !== active" :disabled="busy || saving || dirty || pending || !!previewError" @click="save(true)">设为使用中</NButton>
    <span v-else-if="selected === active" class="notice">使用中</span>
   </div>
   <div class="editor-layout">
    <section class="panel">
     <h2>Jinja2 模板</h2>
     <NInput v-model:value="editor.source" :readonly="readOnly || saving" type="textarea"
      :input-props="{'aria-label':'Jinja2 模板源码',spellcheck:false}" class="source-input"/>
     <p v-if="readOnly">复制为新模板后即可修改。</p>
    </section>
    <section class="panel">
     <h2>实时渲染结果</h2>
     <NSelect v-if="sceneOptions.length" v-model:value="previewScene" :options="sceneOptions" aria-label="预览场景"/>
     <p role="status">{{pending ? '正在渲染…' : preview ? (preview.sample ? '使用示例幕 · ' : '') + '预计 ' + preview.input_tokens + ' token' : ''}}</p>
     <NAlert v-if="previewError" type="error">{{previewError}}</NAlert>
     <pre v-else-if="preview" class="rendered">{{preview.rendered_context}}</pre>
    </section>
    <aside class="panel variables">
     <h2>模板变量</h2>
     <p v-if="selected === 'legacy' && novel.gm_context_source == null">这份共享模板使用旧变量结构。下面列出新建 GM 模板可用的变量。</p>
     <ContextVariableTree :nodes="variables"/>
     <details><summary>循环写法</summary><pre>{{example}}</pre></details>
    </aside>
   </div>
  </template>
 </main>
</template>

<style scoped>
.context-page{flex:1;min-height:0;overflow:auto;padding:clamp(14px,3vw,32px);background:#f7f8fb}
.page-heading,.template-toolbar,.mode-list{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.page-heading{justify-content:space-between}
.page-heading h1,.panel h2{margin:0 0 8px}
.page-heading p,.panel p,.notice,.mode-list span{color:#626e87;margin:4px 0}
.mode-list>*{padding:5px 10px;border-radius:6px;background:#edf0f8}
.template-toolbar>.n-select,.template-toolbar>.n-input{width:min(100%,25rem)}
.editor-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(15rem,.55fr);gap:12px;align-items:start}
.panel,.context-empty{min-width:0;padding:14px;background:white;border:1px solid #e4e8f2;border-radius:10px}
.panel h2{font-size:16px}
.source-input{height:55vh;min-height:240px;font:13px/1.6 Consolas,monospace}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 Consolas,monospace}
.rendered{max-height:55vh;overflow:auto}
.variables{overflow-wrap:anywhere}
@media(max-width:1050px){.editor-layout{grid-template-columns:repeat(2,minmax(0,1fr))}.variables{grid-column:1/-1}}
@media(max-width:640px){.editor-layout{grid-template-columns:minmax(0,1fr)}.source-input{height:38vh}}
</style>
