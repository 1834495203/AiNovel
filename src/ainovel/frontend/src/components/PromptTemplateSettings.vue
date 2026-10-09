<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NAlert, NButton, NCard, NCode, NInput, NSelect, NSpin, NTag } from "naive-ui";
import { historyLink, navigate } from "../ui/navigation";
import { api, type CommandPayload, type PromptTemplateLibrary, type PromptTemplateVersion, type Schemas } from "../api/client";

type Task = Schemas["PromptTemplateSelection"]["task"];
type Selection = Schemas["PromptTemplateSelection"];
type ContextVariable = Schemas["PromptContextVariable"];
type PreviewContext = Schemas["PromptTemplatePreviewContext"];
type PreviewResult = Schemas["PromptTemplatePreview"];

const props = defineProps<{ open: boolean; storyId?: string; revisionId?: string; defaultScope?: "global" | "story"; selectedSelections?: Selection[] }>();
const emit = defineEmits<{ close: []; saved: []; choose: [selection: Selection[]] }>();
const scope = ref<"global" | "story">("global");
const task = ref<Task | null>(null);
const library = ref<PromptTemplateLibrary | null>(null);
const selectedId = ref("");
const name = ref("");
const source = ref("");
const previewContext = ref<Record<string, string | boolean>>({});
const preview = ref<PreviewResult | null>(null);
const selectedVersions = ref<Selection[]>([]);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const libraryUrl = computed(() => props.storyId ? `/api/prompt-templates?story_id=${encodeURIComponent(props.storyId)}` : "/api/prompt-templates");
const taskRow = computed(() => library.value?.tasks.find(row => row.task === task.value) ?? null);
const taskChoices = computed(() => (library.value?.tasks ?? []).map(row => ({ label: row.label, value: row.task })));
const versions = computed(() => {
  if (!taskRow.value) return [];
  const scoped = taskRow.value.versions?.filter(version => version.scope === scope.value) ?? [];
  if (scope.value === "story" && !scoped.some(version => version.id === taskRow.value!.global_active.id)) scoped.unshift(taskRow.value.global_active);
  return scoped;
});
const selectedVersion = computed(() => versions.value.find(version => version.id === selectedId.value) ?? null);
const contextSchema = computed<ContextVariable[]>(() => taskRow.value?.context_schema ?? []);
const currentActive = computed(() => scope.value === "story" ? taskRow.value?.story_active ?? taskRow.value?.global_active : taskRow.value?.global_active);
const editorSource = computed(() => taskRow.value?.editor_source ?? "");

watch(() => [props.open, props.storyId] as const, async ([isOpen]) => {
  if (!isOpen) return;
  scope.value = props.defaultScope ?? (props.storyId ? "story" : "global");
  await load();
}, { immediate: true });
watch([scope, task], () => {
  if (!task.value || !taskRow.value) return;
  const active = currentActive.value;
  selectedId.value = active?.id ?? "";
  name.value = active?.name ?? "自定义模板";
  source.value = active?.editor_source || editorSource.value;
  preview.value = null;
  previewContext.value = {};
}, { flush: "post" });
watch(selectedId, () => {
  const version = selectedVersion.value;
  if (!version) return;
  name.value = version.name;
  source.value = version.editor_source;
  preview.value = null;
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    library.value = await api.get<PromptTemplateLibrary>(libraryUrl.value);
    task.value ??= library.value.tasks[0]?.task ?? null;
    selectedVersions.value = (props.selectedSelections ?? []).filter(selection => library.value!.tasks.some(row => row.task === selection.task));
    const row = library.value.tasks.find(item => item.task === task.value);
    if (row) {
      const active = scope.value === "story" ? row.story_active ?? row.global_active : row.global_active;
      selectedId.value = active.id;
      name.value = active.name;
      source.value = active.editor_source || row.editor_source;
    }
  } catch (problem) {
    error.value = problem instanceof Error ? problem.message : "无法读取提示词模板";
  } finally {
    loading.value = false;
  }
}

function updateContext(variable: ContextVariable, value: string | boolean) {
  previewContext.value = { ...previewContext.value, [variable.name]: value };
}
function variableExpression(name: string) { return `{{ ${name} }}`; }

async function save(action: "save" | "reset") {
  const row = taskRow.value;
  const scopeId = scope.value === "story" ? props.storyId : row?.global_active.scope_id;
  if (!row || !task.value || !scopeId || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const payload: CommandPayload = {
      kind: "save_prompt_template", action, scope: scope.value, task: task.value,
      name: action === "reset" ? "默认模板" : name.value.trim() || "自定义模板",
      guidance: "",
      source: action === "reset" ? undefined : source.value,
    };
    await api.command(payload, scope.value === "story" ? props.storyId! : null,
      scope.value === "story" ? props.revisionId ?? null : null,
      scope.value === "global" ? scopeId : null, () => undefined);
    await load();
    emit("saved");
  } catch (problem) {
    error.value = problem instanceof Error ? problem.message : "保存失败";
  } finally {
    busy.value = false;
  }
}

function chooseForRun() {
  if (!task.value || !selectedId.value) return;
  const next = selectedVersions.value.filter(selection => selection.task !== task.value);
  next.push({ task: task.value, version_id: selectedId.value });
  selectedVersions.value = next;
  emit("choose", next);
}

async function runPreview() {
  if (!task.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const context = {} as PreviewContext;
    for (const variable of contextSchema.value) {
      const value = previewContext.value[variable.name];
      if (variable.type === "boolean") (context as Record<string, unknown>)[variable.name] = Boolean(value);
      else (context as Record<string, unknown>)[variable.name] = typeof value === "string" ? value : "";
    }
    const request: Schemas["PromptTemplatePreviewRequest"] = { task: task.value, source: source.value, guidance: "", context };
    const response = await fetch("/api/prompt-templates/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
    const data = await response.json() as PreviewResult & { detail?: string };
    if (!response.ok) throw new Error(data.detail || "预览失败");
    preview.value = data;
  } catch (problem) {
    error.value = problem instanceof Error ? problem.message : "预览失败";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="template-backdrop" @click.self="emit('close')">
      <section class="template-dialog" role="dialog" aria-modal="true" aria-labelledby="template-title">
        <header class="template-header">
          <div><span class="eyebrow">高级设置</span><h2 id="template-title">提示词模板</h2><p>编辑完整的 Jinja 系统角色与任务说明。版本不可变，运行记录会固定保存实际采用的版本。</p></div>
          <NButton quaternary circle aria-label="关闭" @click="emit('close')">×</NButton>
        </header>
        <NAlert v-if="error" type="error" closable class="template-alert" @close="error = ''">{{ error }}</NAlert>
        <NSpin :show="loading">
          <div v-if="library && taskRow" class="template-content">
            <div class="template-toolbar">
              <label>模板范围<NSelect v-model:value="scope" aria-label="模板范围" :options="[{label:'所有作品默认',value:'global'},{label:'当前作品',value:'story',disabled:!storyId}]" /></label>
              <label>任务<NSelect v-model:value="task" aria-label="任务" :options="taskChoices" /></label>
              <div class="template-current"><span>当前生效</span><strong>{{ currentActive?.name ?? '默认模板' }}</strong><small>v{{ currentActive?.version ?? 1 }} · {{ taskRow.template_path }}</small></div>
            </div>
            <p class="template-description">{{ taskRow.description }}</p>
            <div class="template-grid">
              <aside class="template-history" aria-label="模板版本">
                <div class="template-subhead"><h3>版本记录</h3><NTag size="small" :bordered="false">{{ versions.length }}</NTag></div>
                <button v-for="version in versions" :key="version.id" type="button" class="template-version-row" :class="{ selected: version.id === selectedId }" @click="selectedId = version.id">
                  <strong>{{ version.name }}</strong><span>v{{ version.version }}{{ version.builtin ? ' · 内置' : '' }}</span>
                </button>
                <p v-if="!versions.length" class="template-empty">当前范围沿用默认版本。</p>
                <NButton block secondary class="template-history-link" @click="emit('close'); navigate(historyLink(scope === 'global' ? library?.tasks[0]?.global_active.scope_id : storyId))">查看运行历史</NButton>
              </aside>
              <main class="template-editor">
                <div class="template-editor-heading"><div><label class="template-name-label">版本名称<NInput v-model:value="name" maxlength="100" placeholder="自定义模板" /></label></div><NTag size="small" type="info">完整源码</NTag></div>
                <label class="template-source-label">Jinja 源码<NInput v-model:value="source" type="textarea" class="template-source" :autosize="{ minRows: 16, maxRows: 28 }" spellcheck="false" placeholder="用 Jinja2 编写系统角色、任务目标和可选上下文变量。" /></label>
                <details class="template-help"><summary>可用变量与类型</summary><p>这些变量由运行流程提供；未提供的值为空。条件语句可用 {% if variable %}。</p><div v-for="variable in contextSchema" :key="variable.name" class="context-variable"><code>{{ variableExpression(variable.name) }}</code><span>{{ variable.description }}</span><NTag size="tiny" :bordered="false">{{ variable.type }}</NTag></div><p v-if="!contextSchema.length" class="template-empty">此任务没有额外上下文变量。</p></details>
                <details class="template-preview-inputs"><summary>预览输入（默认留空）</summary><div class="preview-context-grid"><label v-for="variable in contextSchema" :key="variable.name">{{ variable.name }}<NInput v-if="variable.type === 'string'" :aria-label="variable.name" :value="String(previewContext[variable.name] ?? '')" size="small" @update:value="value => updateContext(variable, value)" /><input v-else :aria-label="variable.name" type="checkbox" :checked="Boolean(previewContext[variable.name])" @change="event => updateContext(variable, (event.target as HTMLInputElement).checked)" /></label></div></details>
                <div class="template-actions"><NButton secondary :disabled="busy" @click="runPreview">{{ busy ? '处理中…' : '渲染预览' }}</NButton><NButton secondary :disabled="busy" @click="save('reset')">恢复默认</NButton><NButton type="primary" :disabled="busy || !source.trim()" @click="save('save')">保存新版本</NButton><NButton :disabled="busy || !selectedId" @click="chooseForRun">本轮采用此版本</NButton></div>
                <p class="template-footnote">本轮选版只暂存到下一次创作请求；保存或切换版本不会改变其它任务的选版。</p>
                <section v-if="preview" class="rendered-preview" aria-label="渲染结果"><div class="template-subhead"><h3>完整渲染结果</h3><NTag size="small">{{ preview.source_hash.slice(0, 12) }}</NTag></div><p>{{ preview.note }}</p><p class="template-path">{{ preview.template_path }}</p><pre>{{ preview.rendered_prompt }}</pre><details v-if="preview.rendered_messages?.length"><summary>消息结构</summary><pre>{{ JSON.stringify(preview.rendered_messages, null, 2) }}</pre></details></section>
              </main>
            </div>
          </div>
        </NSpin>
        <footer><NButton @click="emit('close')">完成</NButton></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.template-backdrop{position:fixed;inset:0;z-index:1000;background:rgb(15 23 42 / 38%);display:grid;place-items:center;padding:20px}.template-dialog{width:min(1120px,100%);max-height:min(92dvh,980px);overflow:auto;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:16px;padding:22px;box-shadow:0 22px 70px rgb(15 23 42 / 22%)}.template-header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:18px}.template-header h2{margin:5px 0;font-size:22px}.template-header p,.template-description,.template-footnote,.template-help>p,.rendered-preview>p{color:var(--text-muted);font-size:13px;line-height:1.6;margin:6px 0}.eyebrow{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em}.template-alert{margin-bottom:14px}.template-toolbar{display:grid;grid-template-columns:minmax(180px,240px) minmax(240px,1fr) minmax(190px,260px);gap:14px;align-items:end}.template-toolbar>label,.template-name-label,.template-source-label{display:grid;gap:6px;font-size:12px;color:var(--text-muted)}.template-current{display:grid;gap:4px;border:1px solid var(--border);background:var(--surface-muted);padding:9px 12px;border-radius:9px;min-width:0}.template-current span,.template-current small{font-size:11px;color:var(--text-muted)}.template-current strong{font-size:13px}.template-current small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.template-description{padding:8px 0 12px;border-bottom:1px solid var(--border)}.template-grid{display:grid;grid-template-columns:220px minmax(0,1fr);gap:18px}.template-history{border:1px solid var(--border);background:var(--surface-muted);border-radius:10px;padding:12px;align-self:start}.template-subhead{display:flex;align-items:center;justify-content:space-between;gap:8px}.template-subhead h3{font-size:13px;margin:0}.template-version-row{width:100%;border:1px solid transparent;background:transparent;color:var(--text);text-align:left;border-radius:7px;padding:9px;margin-top:5px;display:grid;gap:4px;cursor:pointer}.template-version-row:hover,.template-version-row.selected{background:var(--surface);border-color:var(--border)}.template-version-row strong{font-size:12px}.template-version-row span,.template-empty{font-size:11px;color:var(--text-muted)}.template-undo{margin-top:14px}.template-editor{min-width:0}.template-editor-heading{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:10px}.template-name-label{width:min(360px,100%)}.template-source :deep(textarea){font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace!important;background:var(--surface-muted)!important;color:var(--text)!important}.template-help,.template-preview-inputs{margin-top:12px;border:1px solid var(--border);border-radius:9px;padding:10px 12px}.template-help summary,.template-preview-inputs summary,.rendered-preview summary{cursor:pointer;font-size:12px;font-weight:600}.context-variable{display:grid;grid-template-columns:minmax(145px,210px) minmax(0,1fr) auto;gap:12px;align-items:center;padding:7px 0;border-top:1px solid var(--border);font-size:12px}.context-variable code,.template-path{color:var(--text-muted);font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.preview-context-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding-top:10px}.preview-context-grid label{display:grid;gap:5px;font-size:11px;color:var(--text-muted)}.preview-context-grid input[type=checkbox]{justify-self:start}.template-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.template-footnote{font-size:11px}.rendered-preview{margin-top:16px;padding-top:14px;border-top:1px solid var(--border)}.rendered-preview>pre,.rendered-preview details pre{max-height:340px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid var(--border);background:var(--surface-muted);padding:12px;border-radius:9px;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}.rendered-preview details{margin-top:12px}.template-dialog>footer{display:flex;justify-content:flex-end;margin-top:16px;padding-top:12px;border-top:1px solid var(--border)}
@media(max-width:760px){.template-backdrop{padding:0;place-items:stretch}.template-dialog{width:100%;height:100dvh;max-height:none;border-radius:0;padding:16px;box-sizing:border-box}.template-toolbar{grid-template-columns:1fr}.template-grid{grid-template-columns:1fr}.template-history{max-height:170px;overflow:auto}.context-variable{grid-template-columns:1fr auto}.context-variable span{grid-column:1/-1;grid-row:2}.preview-context-grid{grid-template-columns:1fr}.template-actions>*{flex:1 1 145px}}
</style>
