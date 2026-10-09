import { element } from "./client.js";
import { decodeContent, meaningful, modelInput, nodeGroups, responseParts, section } from "./trace-content.js";

/** Result-scoped audit views; events remain persisted on the command, never on DOM nodes. */
export class ProcessRecords {
  constructor(api) { this.api = api; this.commands = []; this.events = new Map(); this.views = new Set(); this.entities = new Map(); }
  setStory(story) { this.entities = new Map([...story.characters,...story.scenes,...story.chapters].map(item => [item.id,item.name || item.title])); }
  setCommands(commands) { this.commands = commands; this.update(); }
  reset() { this.commands = []; this.views.clear(); }
  root(command) {
    const payload = command.submission.payload;
    return payload.generation_command_id || payload.proposal_command_id || command.submission.id;
  }
  related(command) {
    const root = this.root(command);
    return [...new Set([root, command.submission.id, ...this.commands.filter(item => this.root(item) === root)
      .map(item => item.submission.id)])];
  }
  scene(parent, scene) {
    const matching = this.commands.filter(item => item.scene_id === scene.id && !item.undone
      && ["generate", "review_draft", "accept_rewrite"].includes(item.submission.payload.kind));
    const command = this.commands.find(item => item.submission.id === scene.source_command_id) || matching.at(-1);
    if (command) this.attach(parent, command);
    else {
      const details = element("details", "", "process-records");
      details.append(element("summary", "过程记录"), element("p", "这段旧正文没有可关联的生成记录。", "hint"));
      parent.append(details);
    }
  }
  attach(parent, command) {
    const index = this.commands.findIndex(item => item.submission.id === command.submission.id);
    if (index < 0) this.commands.push(command); else this.commands[index] = command;
    const details = element("details", "", "process-records");
    details.dataset.command = command.submission.id;
    const summary = element("summary", "过程记录"), body = element("div", "", "process-body");
    details.append(summary, body); parent.append(details);
    const view = {details, summary, body, command}; this.views.add(view);
    details.addEventListener("toggle", () => { if (details.open) this.load(view); });
    this.draw(view);
    return details;
  }
  add(event) {
    if (!event.command_id) return;
    const entries = this.events.get(event.command_id) || new Map();
    entries.set(JSON.stringify(event), event); this.events.set(event.command_id, entries); this.update();
  }
  update() {
    for (const view of this.views) {
      if (!view.details.isConnected) { this.views.delete(view); continue; }
      this.draw(view);
    }
  }
  async load(view) {
    if (view.loading) return;
    view.loading = true; view.error = null; this.draw(view);
    try {
      for (const id of this.related(view.command)) {
        let after = 0, items;
        do {
          items = await this.api.get("/api/commands/" + id + "/events?after=" + after);
          for (const item of items) this.add(item.event);
          if (items.length) after = items.at(-1).id;
        } while (items.length === 100);
      }
    } catch (error) { view.error = error.message; }
    finally { view.loading = false; view.loaded = !view.error; this.draw(view); }
  }
  draw(view) {
    const events = this.related(view.command).flatMap(id => [...(this.events.get(id)?.values() || [])])
      .sort((left, right) => (left.created_at || "").localeCompare(right.created_at || ""));
    const calls = events.filter(event => event.type.startsWith("llm."));
    const inputTokens = calls.reduce((sum, event) => sum + (event.trace?.usage?.input_tokens || 0), 0);
    const outputTokens = calls.reduce((sum, event) => sum + (event.trace?.usage?.output_tokens || 0), 0);
    const cachedTokens = calls.reduce((sum, event) => sum + (event.trace?.usage?.cached_tokens || 0), 0);
    view.summary.textContent = "过程记录" + (events.length
      ? ` · ${calls.length} 次模型调用${inputTokens || outputTokens ? ` · 输入 ${inputTokens} / 输出 ${outputTokens} token${cachedTokens ? `（缓存输入 ${cachedTokens}）` : ""}` : ""}`
      : "");
    view.body.replaceChildren();
    if (view.error) view.body.append(element("p", "记录加载失败：" + view.error + "。收起后再次展开可重试。", "hint"));
    else if (!events.length) view.body.append(element("p", view.loading ? "正在加载记录…" : view.loaded ? "这次操作没有过程记录。" : "展开后加载这次操作的完整过程。", "hint"));
    const groups = nodeGroups(events);
    for (const group of groups) view.body.append(this.row(group));
    if (events.length && !groups.length) view.body.append(element("p", "没有节点或模型调用记录。", "hint"));
  }
  row(group) {
    const trace = group.trace, row = element("details", "", "process-node");
    const names = {discussion:"故事讨论",search_story:"召回故事",update_story_settings:"修改设定",analyze:"分析需求",plan:"规划故事",quick_plan:"快速规划",rewrite_plan:"规划修改",retrieve:"召回故事",skip_retrieval:"跳过召回",consult:"访谈角色",skip_characters:"跳过角色访谈",write:"编写正文",judge:"审阅草稿",extract:"提取角色变化",maintain:"更新设定与进度",draft_review:"等待草稿确认",reuse_extract:"复用角色提取",reuse_maintain:"复用设定更新",reuse_confirmation:"恢复确认步骤",index:"建立索引并提交",Intent:"分析需求",Plan:"写作计划",PlanningDecision:"分析需求与场景规划",Draft:"生成正文",JudgeResult:"审阅结果",Extraction:"角色变化",FinalizationResult:"确认后事实与阶段整理",RewriteSelectionResult:"选区改写",StoryMaintenance:"设定与进度",InterviewResult:"创作访谈",ChatResult:"角色对话",Consultation:"角色访谈",RevisionPlan:"修改计划"};
    row.append(element("summary", (names[trace.node] || trace.node) + " · " + group.status));
    const body = element("div", "", "process-node-body");
    if (trace.summary && trace.summary !== "完成") body.append(element("p", trace.summary, "node-description"));
    const first = group.calls[0], last = group.calls.at(-1);
    const input = meaningful(trace.structured_input) ? (trace.model ? modelInput(trace) : decodeContent(trace.structured_input)) : first ? modelInput(first) : null;
    const nodeOutput = group.output || (!trace.model ? trace.structured_output : null);
    const output = meaningful(nodeOutput) ? decodeContent(nodeOutput)
      : last && !last.error ? responseParts(last).output : null;
    section(body, "节点输入", input, this.entities); section(body, "节点输出", output, this.entities);
    for (const call of group.calls) this.call(body, call);
    if (group.error) body.append(element("p", "操作失败：" + group.error.split("\n")[0], "trace-error"));
    row.append(body); return row;
  }
  call(parent, trace) {
    const parts = responseParts(trace), call = element("details", "", "trace-call");
    section(parent, "模型返回的推理内容" + (trace.attempt > 1 ? " · 第 " + trace.attempt + " 次尝试" : ""), parts.reasoning);
    call.append(element("summary", (parts.calls.length ? "工具调用" : "模型调用") + (trace.attempt > 1 ? " · 第 " + trace.attempt + " 次尝试" : "") + (trace.error ? " · 失败" : "")));
    const usage = trace.usage || {};
    const meta = [trace.model, trace.duration_ms ? trace.duration_ms + " ms" : null,
      usage.input_tokens != null ? "输入 " + usage.input_tokens + " token" : null,
      usage.output_tokens != null ? "输出 " + usage.output_tokens + " token" : null,
      usage.cached_tokens ? "缓存输入 " + usage.cached_tokens + " token（已计入输入）" : null,
      usage.estimated ? "估算值" : null].filter(Boolean);
    if (meta.length) call.append(element("small", meta.join(" · ")));
    section(call, "调用输入", modelInput(trace), this.entities);
    if (!parts.calls.length) section(call, "调用输出", parts.output, this.entities);
    for (const tool of parts.calls) {
      call.append(element("h4", "工具：" + tool.name));
      section(call, "提交参数", tool.arguments, this.entities);
    }
    if (trace.error) call.append(element("p", "调用失败：" + trace.error.split("\n")[0], "trace-error"));
    if (parts.malformed) call.append(element("p", "输出未能解析为结构化结果，未作为有效结果展示。", "trace-error"));
    const diagnostic = element("details", "", "trace-diagnostic");
    diagnostic.append(element("summary", "技术诊断 · 原始数据"));
    for (const [title, raw] of [["原始请求",trace.structured_input],["原始响应",trace.raw_output],["完整错误",trace.error]]) {
      if (!meaningful(raw)) continue;
      const parsed = decodeContent(raw);
      diagnostic.append(element("h4", title), element("pre", typeof parsed === "object" ? JSON.stringify(parsed,null,2) : String(parsed)));
    }
    if (diagnostic.children.length > 1) call.append(diagnostic);
    parent.append(call);
  }
}
