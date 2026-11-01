import type { CommandView, RunOverview } from "../api/client";

const labels: Record<string, string> = {
  generate: "创作正文草稿", review_draft: "审阅草稿", resolve_character: "审批新角色",
  rewrite: "提出正文修改", accept_rewrite: "接受正文修改", reject_rewrite: "放弃正文修改",
  discussion: "讨论故事资料", start_discussion: "创建讨论会话", interview: "构思访谈",
  create_story: "创建作品", update_story: "修改故事资料", direction: "更新创作要求",
  character_chat: "角色阶段对话", start_character_chat: "创建角色会话", delete_story: "移入回收站",
  restore_story: "恢复作品", reindex: "重建召回索引", save_prompt_template: "修改提示词",
  save_setup_draft: "保存构思草稿", undo: "撤回最新操作", recover_command: "恢复失败任务", revert: "恢复作品版本",
  save_field_template_set: "另存小说模板套装", initialize_story_template_set: "初始化本书模板套装", update_story_field_schema: "修改本书初始化字段结构",
};
export function operationLabel(command: CommandView): string {
  const p = command.submission.payload;
  if (p.kind === "set_setup_draft_deleted") return p.deleted ? "创作草稿移入回收站" : "恢复创作草稿";
  if (p.kind === "review_draft") return { confirm: "确认正文并保存资料", revise: "按意见修改草稿", discard: "放弃草稿" }[p.decision];
  if (p.kind === "resolve_character") return { consent: "同意引入新角色", accept: "确认角色档案", reject: "拒绝新角色" }[p.decision];
  return labels[p.kind] || "作品操作";
}
export function operationInput(command: CommandView): string {
  const p = command.submission.payload;
  if (p.kind === "generate" || p.kind === "rewrite") return p.instruction;
  if (p.kind === "discussion" || p.kind === "character_chat") return p.message;
  if (p.kind === "review_draft") return p.feedback;
  if (p.kind === "interview") return p.turns?.filter(t => t.role === "user").at(-1)?.content || "";
  return "";
}
export function operationOutput(command: CommandView): string {
  return command.error || command.output.discussion?.reply || command.output.chat?.reply || command.output.interview?.reply
    || command.output.writing_draft?.content.summary || command.output.message || "操作已记录";
}
export function runMetrics(run: RunOverview | null | undefined) {
  const attempts = run?.steps.flatMap(step => step.attempts.length ? step.attempts : step.input_tokens != null || step.output_tokens != null ? [step] : []) ?? [];
  return { tokens: attempts.reduce((n, a) => n + (a.input_tokens ?? 0) + (a.output_tokens ?? 0), 0),
    milliseconds: attempts.reduce((n, a) => n + a.duration_ms, 0), steps: run?.steps.length ?? 0,
    estimated: attempts.some(a => a.estimated_usage), recorded: attempts.length > 0 };
}
export const statusLabel = (value: string) => ({ completed: "已完成", running: "执行中", queued: "排队中", waiting: "等待确认", failed: "失败", skipped: "已跳过", started: "执行中" }[value] || value);

const fieldLabels: Record<string, string> = {
  reply: "回复", summary: "摘要", message: "说明", goal: "目标", route: "路线", reason: "原因", name: "名称", value: "内容", description: "说明",
  title: "标题", text: "正文", scene_plan: "本幕安排", chapter_plan: "章节安排", character_names: "参与人物", feedback: "审阅意见",
  use_retrieval: "召回前文", use_characters: "人物访谈", new_chapter: "新建章节", intent: "创作意图", plan: "计划", notes: "笔记",
  writer_patch: "写作计划变化", world_patch: "世界观变化", preferences_patch: "写作偏好变化", character_updates: "人物变化",
  upsert: "新增或更新", remove: "移除", entries: "资料条目", mainlines: "主线", subplots: "支线", foreshadows: "伏笔",
  extraction: "事实提取", maintenance: "长期资料整理", actions: "行动", thoughts: "心理状态", profile: "人物档案", state: "人物状态",
};
export type ReadableRow = { label: string; value: string };
export function changeText(raw: string): string {
  if (!raw.trim().startsWith('{') && !raw.trim().startsWith('[')) return raw;
  return readableOutput(raw).map(row => `${row.label}：${row.value}`).join('；');
}
export function readableOutput(raw: string): ReadableRow[] {
  if (!raw.trim() || ["{}", "[]", "null"].includes(raw.trim())) return [];
  let data: unknown;
  try { data = JSON.parse(raw); } catch { return [{ label: "结果", value: raw }]; }
  const rows: ReadableRow[] = [];
  function visit(value: unknown, path: string) {
    if (value === null || value === undefined || value === "") return;
    if (typeof value === "object") {
      if (Array.isArray(value)) value.forEach((item, i) => visit(item, `${path} ${i + 1}`));
      else Object.entries(value).forEach(([key, child]) => {
        if (["id", "command_id", "story_id", "scene_id", "chapter_id", "source_scene_id", "prompt_templates", "messages", "tools", "schema", "parameters", "system", "structured_input"].includes(key)) return;
        visit(child, [path, fieldLabels[key] || key.replaceAll("_", " ")].filter(Boolean).join(" · "));
      });
    } else rows.push({ label: path || "结果", value: typeof value === "boolean" ? value ? "是" : "否" : String(value) });
  }
  visit(data, "");
  return rows;
}
