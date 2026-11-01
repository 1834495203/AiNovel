const pathLabels: Record<string, string> = {
  template_set: "模板", template: "模板", templates: "资料类别", fields: "字段", children: "子字段", item: "列表项",
  name: "名称", description: "说明", type: "类型", options: "枚举选项", required: "必填设置",
  replace_template_set_id: "模板保存目标", replaces_id: "模板版本", session_id: "会话", revision_id: "小说版本",
};

function location(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const parts: string[] = [];
  for (const item of value) {
    if (typeof item === "number") { if (parts.length) parts[parts.length - 1] += `[${item + 1}]`; }
    else if (typeof item === "string" && !["body", "payload", "query", "path"].includes(item)
      && !/^(save_|edit_|update_|initialize_|create_|apply_)/.test(item)) parts.push(pathLabels[item] ?? item);
  }
  return parts.join(" / ");
}

export function formatApiError(data: unknown, status: number): string {
  const messages: string[] = [];
  function visit(value: unknown, depth = 0) {
    if (depth > 6 || messages.length >= 8 || value == null) return;
    if (typeof value === "string") { if (value.trim()) messages.push(value.trim()); return; }
    if (Array.isArray(value)) { value.forEach(item => visit(item, depth + 1)); return; }
    if (typeof value !== "object") return;
    const error = value as Record<string, unknown>;
    if ("detail" in error) { visit(error.detail, depth + 1); return; }
    if ("errors" in error) { visit(error.errors, depth + 1); return; }
    let message = typeof error.msg === "string" ? error.msg : typeof error.message === "string" ? error.message : "";
    const context = error.ctx && typeof error.ctx === "object" ? error.ctx as Record<string, unknown> : {};
    switch (error.type) {
      case "extra_forbidden":
        message = Array.isArray(error.loc) && error.loc.some(part => ["replace_template_set_id", "replaces_id"].includes(String(part)))
          ? "当前 API 版本过旧，不支持模板直接保存。请重启 API 和 Worker 后刷新页面。" : "不支持这个字段。";
        break;
      case "union_tag_invalid": case "union_tag_not_found":
        message = "当前接口无法识别这个操作，请重启 API 和 Worker 后刷新页面。"; break;
      case "missing": message = "缺少此项。"; break;
      case "string_too_short": message = context.min_length === 1 ? "不能为空。" : `至少需要 ${context.min_length ?? 1} 个字符。`; break;
      case "string_too_long": message = `不能超过 ${context.max_length ?? "规定的"} 个字符。`; break;
      case "string_type": message = "请输入文字。"; break;
      case "list_type": message = "此项必须是列表。"; break;
      case "uuid_parsing": case "uuid_type": message = "对象编号无效。"; break;
      case "value_error": message = message.replace(/^Value error,\s*/, ""); break;
    }
    if (message) {
      const path = location(error.loc);
      messages.push(path ? `${path}：${message}` : message);
    } else if ("error" in error) visit(error.error, depth + 1);
  }
  visit(data);
  return [...new Set(messages)].join("\n") || `请求失败（HTTP ${status}），请查看 API 终端日志。`;
}
