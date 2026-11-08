<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue';
import {NAlert, NButton, NButtonGroup, NInput, NSelect, NTag} from 'naive-ui';
import {studio, type ContextDrafts, type ContextMode, type ContextSchema, type ContextSection, type ContextTemplate, type ContextTemplateDraft, type ContextTemplatePreview, type Novel, type Scene, type StudioContracts, type StudioDocumentation, type StudioMode, type StudioSettings} from '../studio-api';
import ContextSettings from './ContextSettings.vue';
import ContextVariableTree, {type VariableNode} from './ContextVariableTree.vue';

const props = defineProps<{
  novel:Novel; scenes:Scene[]; sceneId:string; branchId:string; message:string;
  contracts?:StudioContracts; busy:boolean; initialSection:ContextSection;
  saveSettings:(payload:StudioSettings)=>Promise<unknown>;
  drafts:ContextDrafts;
}>();
const emit = defineEmits<{back:[];updated:[novel:Novel]}>();
const previewScene = ref(props.sceneId || props.scenes.at(-1)?.entity.id || '');
const mode = ref<ContextMode>('gm');
const settingsMode = ref<StudioMode>(props.novel.created ? 'gm' : 'creation');
const labels = {gm:'GM 演绎', role:'角色扮演', writing:'正文写作'};
const sections = {template:'上下文模板', behavior:'行为提示词', documentation:'工具与变量', compression:'上下文范围'};
const section = ref(props.initialSection);
const draftDocs = ref<StudioDocumentation>();
const showDocumentation = ref(false);
const drafts = computed(() => props.drafts[mode.value]);
const previewMessage = ref(''), characterId = ref('');
const preview = ref<ContextTemplatePreview|null>(null);
const previewError = ref(''), saveError = ref(''), pending = ref(false), saving = ref(false), notice = ref('');
const panel = ref<'editor'|'preview'|'variables'>('editor');
const previewSide = ref<'draft'|'active'>('draft');
let timer = 0, generation = 0;

const builtIn = computed(() => props.contracts?.context_templates[mode.value].default);
const schema = computed(() => props.contracts?.context_templates[mode.value].schema);
const legacy = computed<ContextTemplate|undefined>(() => {
  const gmSource = mode.value === 'gm' ? props.novel.gm_context_source : null;
  const source = gmSource ??
    (props.novel.context_source.trim() === '{{ data | tojson }}' ? undefined : props.novel.context_source);
  if (source === undefined || !builtIn.value) return undefined;
  return {id:'legacy', name:'原有上下文模板', source,
    schema_version:mode.value === 'gm' && gmSource == null ? 'shared-v1' : builtIn.value.schema_version};
});
const library = computed(() => templateSet(props.novel));
function templateSet(novel:Novel) {
  return mode.value === 'gm'
    ? {templates:novel.gm_templates, active_id:novel.active_gm_template_id, active:novel.active_gm_template}
    : novel.context_template_sets?.[mode.value];
}
const templates = computed<ContextTemplate[]>(() => [
  ...(builtIn.value ? [builtIn.value] : []),
  ...(legacy.value ? [legacy.value] : []),
  ...(library.value?.templates ?? []),
]);
const active = computed(() => library.value?.active ??
  templates.value.find(item => item.id === library.value?.active_id) ?? legacy.value ?? builtIn.value);
const sameDraft = (left:ContextTemplateDraft, right:ContextTemplateDraft) =>
  left.name === right.name && left.source === right.source && left.schema_version === right.schema_version;
const hasSavedDraft = computed(() => {
  const saved = templates.value.find(item => item.id === active.value?.id);
  return !!saved && !!active.value && !sameDraft(saved, active.value);
});
const activeOption = computed(() => hasSavedDraft.value ? 'active-version' : active.value?.id ?? 'default');
const selected = ref(activeOption.value);
const options = computed(() => [
  ...(hasSavedDraft.value ? [{value:'active-version',label:active.value?.name + ' · 使用中'}] : []),
  ...templates.value.map(item => ({value:item.id,label:item.name +
    (item.id === active.value?.id ? (hasSavedDraft.value ? ' · 已保存草稿' : ' · 使用中') : '') +
    (drafts.value[item.id] ? ' · 未保存' : '')})),
  ...(drafts.value.new ? [{value:'new',label:drafts.value.new.name || '未保存的新模板'}] : []),
]);
const sceneOptions = computed(() => props.scenes.map(scene => ({value:scene.entity.id,label:scene.entity.name})));
const characterOptions = computed(() => {
  const scene = props.scenes.find(item => item.entity.id === previewScene.value);
  const actors = new Set<string>();
  for (const event of scene?.events ?? []) {
    if (event.kind === 'enter') actors.add(event.actor_id);
    if (event.kind === 'leave') actors.delete(event.actor_id);
  }
  return [...actors].map(id => ({value:id, label:props.novel.characters.find(character => character.id === id)?.name ?? id}));
});
const previewInput = computed(() => ({mode:mode.value, scene_id:previewScene.value, branch_id:props.branchId,
  character_id:mode.value === 'role' ? characterId.value : '', message:mode.value === 'gm' ? props.message : previewMessage.value}));
const original = computed(() => selected.value === 'active-version' ? active.value : templates.value.find(item => item.id === selected.value));
const editor = computed(() => drafts.value[selected.value] ?? original.value);
const readOnly = computed(() => ['default', 'legacy', 'active-version'].includes(selected.value));
const dirty = computed(() => !!editor.value && (!original.value || !sameDraft(editor.value, original.value)));
const unsavedDraft = computed(() => Object.keys(drafts.value).find(id => id !== selected.value && (id === 'new' || templates.value.some(item => item.id === id))));
const isActiveVersion = computed(() => selected.value === activeOption.value && !!editor.value && !!active.value && sameDraft(editor.value, active.value));
const visiblePreview = computed(() => previewSide.value === 'active' ? preview.value?.active : preview.value);

function select(id:string) {
  selected.value = id;
  saveError.value = '';
  notice.value = '';
  previewSide.value = 'draft';
}

function chooseMode(value:ContextMode) {
  settingsMode.value = value;
  if (mode.value === value) return;
  mode.value = value;
  previewMessage.value = '';
  panel.value = 'editor';
  select(activeOption.value);
}

watch(settingsMode, value => {
  if (value === 'gm' || value === 'role' || value === 'writing') chooseMode(value);
});

function previewDocumentation(value:ContextMode) {
  chooseMode(value);
  section.value = 'template';
  panel.value = 'preview';
  previewSide.value = 'draft';
  showDocumentation.value = true;
}

watch(() => props.initialSection, value => { section.value = value; });

function edit(update:Partial<ContextTemplateDraft>) {
  if (!editor.value || readOnly.value) return;
  const draft = {...editor.value, ...update};
  if (original.value && sameDraft(draft, original.value)) delete drafts.value[selected.value];
  else drafts.value[selected.value] = draft;
  notice.value = '';
}

function discard() {
  delete drafts.value[selected.value];
  if (selected.value === 'new') select(activeOption.value);
}

function create(copy = false) {
  if (drafts.value.new) {
    select('new');
    return;
  }
  if (!builtIn.value) return;
  drafts.value.new = copy && editor.value
    ? {name:editor.value.name.slice(0, 77) + ' 副本',source:editor.value.source,schema_version:editor.value.schema_version}
    : {name:'',source:builtIn.value.source,schema_version:builtIn.value.schema_version};
  select('new');
  panel.value = 'editor';
}

function tokenChange(value:number, baseline:number) {
  const delta = value - baseline;
  return delta === 0 ? '持平' : `${delta > 0 ? '增加' : '减少'} ${Math.abs(delta).toLocaleString()}`;
}

function variableNodes(shapeSchema:ContextSchema|undefined, prefix = 'data'):VariableNode[] {
  return Object.entries(shapeSchema?.properties ?? {}).map(([key, field]) => {
    const path = prefix + '.' + key;
    const shape = field.anyOf?.find(value => value.type !== 'null') ?? field;
    const child = shape.items ?? shape;
    const resolved = child.$ref ? schema.value?.$defs?.[child.$ref.split('/').at(-1) ?? ''] : child;
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
const variables = computed(() => variableNodes(schema.value));
const example = computed(() => mode.value === 'role'
  ? '当前角色：{{ data.self.name }}\n{% for character in data.present %}\n- {{ character.name }}\n{% endfor %}'
  : '{% for character in data.characters %}\n- {{ character.name }}\n{% endfor %}');

watch(sceneOptions, options => {
  if (!options.some(option => option.value === previewScene.value)) {
    previewScene.value = options.at(-1)?.value ?? '';
  }
});
watch(activeOption, id => {
  if ((selected.value === 'active-version' && id !== 'active-version') || !editor.value) select(id);
});
watch(characterOptions, options => {
  if (!options.some(option => option.value === characterId.value)) characterId.value = options[0]?.value ?? '';
}, {immediate:true});
watch([selected, () => editor.value?.source, () => editor.value?.schema_version,
  previewInput, draftDocs, section, () => props.novel.version], () => {
  window.clearTimeout(timer);
  const request = ++generation;
  const draft = editor.value;
  preview.value = null;
  previewError.value = '';
  pending.value = !!draft && section.value === 'template';
  if (!draft || !pending.value) return;
  timer = window.setTimeout(async () => {
    try {
      const result = await studio<ContextTemplatePreview>(
        `/novels/${props.novel.id}/context-template-preview`,
        {...previewInput.value, source:draft.source, schema_version:draft.schema_version, documentation:draftDocs.value},
      );
      if (request === generation) preview.value = result;
    } catch (error) {
      if (request === generation) previewError.value = error instanceof Error ? error.message : String(error);
    } finally {
      if (request === generation) pending.value = false;
    }
  }, 300);
}, {immediate:true});
onBeforeUnmount(() => { window.clearTimeout(timer); ++generation; });

async function save(activate = false, id = selected.value) {
  if (!id || !editor.value || saving.value || props.busy) return;
  saving.value = true;
  saveError.value = '';
  notice.value = '';
  try {
    const updated = await studio<Novel>(
      `/novels/${props.novel.id}/${activate ? 'context-template-selection' : 'context-templates'}`,
      activate
        ? {...previewInput.value, version:props.novel.version,id:id === 'legacy' ? null : id}
        : {version:props.novel.version,id:id === 'new' ? '' : id,name:editor.value.name,
          mode:mode.value,source:editor.value.source,schema_version:editor.value.schema_version},
    );
    emit('updated', updated);
    if (!activate) {
      const saved = templateSet(updated)?.templates;
      const item = id === 'new' ? saved?.at(-1) : saved?.find(item => item.id === id);
      if (item) {
        delete drafts.value[id];
        selected.value = item.id;
      }
    } else if (id === 'default') select('default');
    notice.value = activate ? `已启用，后续${labels[mode.value]}会使用此版本。` : '草稿已保存，启用后才会生效。';
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : String(error);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
 <main class="context-page" aria-label="上下文与提示词">
  <header class="page-heading">
   <div><h1>上下文与提示词</h1><p>管理本书的模型输入、行为提示词、工具说明和变量 Schema。</p></div>
   <NButton quaternary :disabled="saving" @click="emit('back')">返回作品</NButton>
  </header>
  <nav class="page-tabs" aria-label="上下文设置分类">
   <NButton v-for="(label, value) in sections" :key="value" :aria-pressed="section === value" :disabled="saving" @click="section=value">{{label}}</NButton>
  </nav>
  <ContextSettings v-model:mode="settingsMode" :section="section" :novel="novel" :scene-id="sceneId" :branch-id="branchId" :message="message" :contracts="contracts" :schema-version="editor?.schema_version"
   :busy="busy" :save="saveSettings" @documentation="draftDocs=$event" @preview="previewDocumentation"/>
  <template v-if="section === 'template'">
  <div class="mode-list" aria-label="模板类型">
   <NButtonGroup aria-label="模板模式">
    <NButton v-for="(label, value) in labels" :key="value" :aria-pressed="mode === value" :disabled="saving" @click="chooseMode(value)">{{label}}</NButton>
   </NButtonGroup>
   <span v-if="active">当前使用 · {{active.name}}</span>
   <NButton v-if="active && active.id !== 'default'" text type="primary" :disabled="busy || saving" @click="save(true, 'default')">切回内置模板</NButton>
  </div>
  <div class="template-toolbar">
   <NSelect :value="selected" :options="options" placeholder="选择模板"
    aria-label="选择上下文模板" :disabled="saving" @update:value="select"/>
   <NButton :disabled="saving || !builtIn" @click="create()">新建模板</NButton>
   <NButton v-if="editor && selected !== 'new'" :disabled="saving || !!drafts.new" @click="create(true)">复制为新模板</NButton>
   <NButton v-if="unsavedDraft && !dirty" text type="primary" :disabled="saving" @click="select(unsavedDraft)">继续未保存的草稿</NButton>
  </div>
  <NAlert v-if="saveError" type="error">{{saveError}}</NAlert>
  <p v-if="notice" role="status" class="notice">{{notice}}</p>
  <div v-if="!editor" class="context-empty">选择一个模板开始编辑，或新建一份模板。</div>
  <template v-else>
   <div class="template-toolbar">
    <NInput :value="editor.name" :readonly="readOnly || saving" maxlength="80" aria-label="模板名称" placeholder="模板名称" @update:value="edit({name:$event})"/>
    <NButton v-if="!readOnly" :disabled="busy || saving || !dirty || !editor.name.trim()" @click="save()">保存草稿</NButton>
    <NButton v-if="dirty" :disabled="saving" quaternary @click="discard">放弃更改</NButton>
    <NButton v-if="selected !== 'new' && !isActiveVersion" type="primary" :disabled="busy || saving || dirty || pending || !preview" @click="save(true)">启用此版本</NButton>
    <NTag v-if="isActiveVersion" type="success" :bordered="false">使用中</NTag>
    <span v-else-if="dirty" class="notice">未保存 · 返回作品会保留编辑</span>
    <span v-else-if="!readOnly" class="notice">已保存 · 待启用</span>
   </div>
   <NButtonGroup class="panel-switch" aria-label="编辑区域">
    <NButton :aria-pressed="panel === 'editor'" @click="panel='editor'">编辑</NButton>
    <NButton :aria-pressed="panel === 'preview'" @click="panel='preview'">预览</NButton>
    <NButton :aria-pressed="panel === 'variables'" @click="panel='variables'">变量</NButton>
   </NButtonGroup>
   <div class="editor-layout">
    <section class="panel" :class="{'mobile-active':panel === 'editor'}">
     <h2>Jinja2 模板</h2>
     <NInput :value="editor.source" :readonly="readOnly || saving" type="textarea"
      :input-props="{'aria-label':'Jinja2 模板源码',spellcheck:false}" class="source-input" @update:value="edit({source:$event})"/>
     <p v-if="readOnly">此版本只读，复制为新模板后即可修改。</p>
    </section>
    <section class="panel" :class="{'mobile-active':panel === 'preview'}">
     <h2>渲染对比</h2>
     <NSelect v-if="sceneOptions.length" v-model:value="previewScene" :options="sceneOptions" aria-label="预览场景"/>
     <NSelect v-if="mode === 'role' && characterOptions.length" v-model:value="characterId" :options="characterOptions" aria-label="预览角色" class="preview-control"/>
     <NInput v-if="mode !== 'gm'" v-model:value="previewMessage" :placeholder="mode === 'role' ? '示例调度提示（可选）' : '示例写作要求（可选）'"
      :input-props="{'aria-label':mode === 'role' ? '示例调度提示' : '示例写作要求'}" class="preview-control"/>
     <p v-if="pending || preview?.sample" role="status">{{pending ? '正在渲染…' : mode === 'role' ? '当前幕没有在场角色，正在预览示例角色' : '正在预览示例幕'}}</p>
     <NAlert v-if="previewError" type="error">{{previewError}}</NAlert>
     <template v-else-if="preview">
      <table class="token-compare" aria-label="预计 token 对比">
       <thead><tr><th scope="col">预计 token</th><th scope="col">使用中</th><th scope="col">所选版本</th><th scope="col">差值</th></tr></thead>
       <tbody>
        <tr><th scope="row">模板文本</th><td>{{preview.active?.context_tokens.toLocaleString() ?? '—'}}</td><td>{{preview.context_tokens.toLocaleString()}}</td><td>{{preview.active ? tokenChange(preview.context_tokens, preview.active.context_tokens) : '—'}}</td></tr>
        <tr><th scope="row">整次输入</th><td>{{preview.active?.input_tokens.toLocaleString() ?? '—'}}</td><td>{{preview.input_tokens.toLocaleString()}}</td><td>{{preview.active ? tokenChange(preview.input_tokens, preview.active.input_tokens) : '—'}}</td></tr>
       </tbody>
      </table>
      <NAlert v-if="preview.active_error" type="warning">使用中模板渲染失败。{{preview.active_error}}</NAlert>
      <NButtonGroup v-if="preview.active" aria-label="选择渲染版本">
       <NButton size="small" :aria-pressed="previewSide === 'draft'" @click="previewSide='draft'">所选版本</NButton>
       <NButton size="small" :aria-pressed="previewSide === 'active'" @click="previewSide='active'">使用中</NButton>
      </NButtonGroup>
      <details class="sent-documentation" :open="showDocumentation" @toggle="showDocumentation=($event.currentTarget as HTMLDetailsElement).open">
       <summary>随请求发送 · 工具说明与变量 Schema</summary>
       <h3>工具说明</h3>
       <pre v-if="visiblePreview?.documentation.tool_descriptions">{{JSON.stringify(visiblePreview.documentation.tool_descriptions,null,2)}}</pre>
       <p v-else>此模式没有可调用工具。</p>
       <h3>变量 Schema</h3>
       <pre v-if="visiblePreview?.documentation.variable_schema">{{JSON.stringify(visiblePreview.documentation.variable_schema,null,2)}}</pre>
       <p v-else>当前版本关闭了变量说明。</p>
       <details><summary>工具返回 Schema</summary><pre>{{JSON.stringify(visiblePreview?.documentation.tool_result_schema,null,2)}}</pre></details>
      </details>
      <pre class="rendered">{{(visiblePreview ?? preview).rendered_context || '模板输出为空'}}</pre>
     </template>
    </section>
    <aside class="panel variables" :class="{'mobile-active':panel === 'variables'}">
     <h2>模板变量</h2>
     <p v-if="editor.schema_version === 'shared-v1'">这份模板沿用旧版变量，例如 <code>data.title</code> 和 <code>data.current_scene.scene.name</code>。</p>
     <template v-else>
      <ContextVariableTree :nodes="variables"/>
      <details><summary>循环写法</summary><pre>{{example}}</pre></details>
     </template>
    </aside>
   </div>
  </template>
  </template>
 </main>
</template>

<style scoped>
.context-page{flex:1;min-height:0;overflow:auto;padding:clamp(14px,3vw,32px);background:var(--theme-sunken,#f7f8fb)}
.page-heading,.template-toolbar,.mode-list{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.page-heading{justify-content:space-between}
.page-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.sent-documentation{margin-top:12px;padding:8px 10px;border-radius:6px;background:var(--theme-accent-surface,#f3f5fc)}
.sent-documentation summary{cursor:pointer;color:var(--theme-accent-text,#445585)}
.sent-documentation h3{font-size:14px;margin-bottom:0}
.sent-documentation pre{max-height:35vh;overflow:auto}
.page-heading h1,.panel h2{margin:0 0 8px}
.page-heading p,.panel p,.notice,.mode-list span{color:var(--theme-muted,#626e87);margin:4px 0}
.mode-list>span{padding:5px 10px;border-radius:6px;background:var(--theme-accent-surface,#edf0f8)}
.preview-control{margin-top:8px}
.template-toolbar>.n-select,.template-toolbar>.n-input{width:min(100%,25rem)}
.editor-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(15rem,.55fr);gap:12px;align-items:start}
.panel,.context-empty{min-width:0;padding:14px;background:var(--surface);border:1px solid var(--theme-border,#e4e8f2);border-radius:10px}
.panel h2{font-size:16px}
.source-input{height:55vh;min-height:240px;font:13px/1.6 Consolas,monospace}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 Consolas,monospace}
.rendered{max-height:55vh;overflow:auto}
.variables{overflow-wrap:anywhere}
.panel-switch{display:none;margin-bottom:10px}
.token-compare{width:100%;margin:10px 0;border-collapse:collapse;text-align:left;font-size:13px;font-variant-numeric:tabular-nums}
.token-compare th,.token-compare td{padding:5px 4px;border-bottom:1px solid var(--theme-border,#e4e8f2)}
button[aria-pressed="true"]{background:var(--theme-accent-surface,#edf0ff);color:var(--theme-accent-text,#444da4)}
@media(max-width:1050px){.editor-layout{grid-template-columns:repeat(2,minmax(0,1fr))}.variables{grid-column:1/-1}}
@media(max-width:640px){.panel-switch{display:inline-flex}.editor-layout{grid-template-columns:minmax(0,1fr)}.editor-layout>.panel:not(.mobile-active){display:none}.source-input{height:45vh}}
</style>
