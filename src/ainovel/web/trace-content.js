import { element } from "./client.js";

const labels = {
  instruction:"用户要求", feedback:"修改意见", intent:"意图判断", world:"世界观", writer:"写作记录", characters:"角色",
  context:"参考上下文", plan:"写作计划", revision_plan:"修改计划", consultations:"角色访谈", draft:"草稿", judge:"审阅结果",
  extraction:"角色更新", maintenance:"设定与进度更新", draft_status:"草稿状态", draft_version:"草稿版本", approval_phase:"角色审批",
  approved_character:"获批角色", route:"创作路径", reason:"依据", name:"名称", value:"内容", description:"说明", children:"下级条目",
  entries:"记录", text:"正文", title:"标题", summary:"摘要", premise:"基本设定", outline:"大纲", mainlines:"主线", subplots:"支线",
  foreshadows:"伏笔", future_directions:"未来走向", notes:"笔记", drafts:"工作草稿", custom_fields:"自定义记录", progress:"当前进度",
  current_user_intent:"当前用户意图", chapter_plan:"章节计划", scene_plan:"本幕计划", character_names:"涉及角色", new_character:"新角色提议",
  start_new_chapter:"开启新章节", chapter_title:"章节标题", use_retrieval:"需要召回", use_characters:"需要角色访谈", hard_constraints:"必须遵守",
  role:"角色定位", profile:"档案", state:"状态与心理", aliases:"称呼", experiences:"经历", new_experiences:"新经历", event:"事件",
  keywords:"关键词", updates:"更新内容", upsert:"新增或更新", remove:"移除条目", patch:"增量更新", likes:"喜好", dislikes:"厌恶",
  preferences:"读者偏好", decision_summary:"判断摘要", content:"内容", reply:"回复", next_steps:"下一步", payoff:"回收计划", status:"状态",
  recent:"近期内容", recalled:"召回内容", directions:"创作备注", scope:"适用范围", hard:"硬性要求", active:"生效", approved:"通过",
  blocked:"阻止提交", issues:"发现的问题", should_rewrite:"需要重写", rewrite_instruction:"重写要求", goal:"目标", character:"角色",
  source_scene_id:"来源场景", scene_id:"场景", character_id:"角色", character_ids:"角色", selected_text:"选中文字", selection:"选区",
  decision:"处理决定", confidence:"可信度", source:"信息来源", category:"类别", kind:"类型", order:"顺序", message:"说明",
  route_reason:"路径选择依据", constraints:"必须遵守的要求", retrieval_reason:"召回判断依据", characters_reason:"访谈判断依据",
  instructions:"修改要求", preserve_continuity:"保持连续性", user_intent_satisfied:"符合用户意图", direction_satisfied:"符合创作备注",
  overall_score:"综合评分", rewrite_instructions:"重写要求", severity:"严重程度", story:"已有故事", chapters:"章节", scenes:"场景",
  checklist:"构思清单", proposal:"设定提案", required:"必填", defined:"已定义", missing:"缺失", label:"项目", turns:"对话记录",
  history:"会话历史", tool_results:"工具结果", recent_scenes:"近期正文", arguments:"工具参数", excerpts:"召回内容", success:"成功",
};
const internal = new Set(["id","command_id","source_command_id","execution_command_id","graph_checkpoint_id","snapshot_id","revision_id","read_revision_id","updated_revision_id","base_revision_id","story_id","session_id","generation_command_id","tool_call_id","$defs","$ref","parameters","tool_choice","tools","temperature","max_tokens","thinking","prompt_version","created_at","rejected_character","rewrites"]);
const enums = {fast:"快速创作",deliberate:"深入规划",pending:"待确认",confirmed:"已确认",discarded:"已放弃",writing:"编写中",planned:"计划中",active:"进行中",used:"已使用",completed:"已完成",planted:"已埋下",paid_off:"已回收",resolved:"已解决",abandoned:"已放弃",explicit:"用户明确提出",inferred:"推测",consent:"等待引入许可",profile:"等待档案确认",confirm:"确认",revise:"修改",discard:"放弃",global:"全局",future:"后续故事",scene:"场景",chapter:"章节"};

/** Decode transport strings, including historical JSON encoded inside message content. */
export function decodeContent(value) {
  for (let depth=0; depth<4 && typeof value === "string"; depth++) {
    const text=value.trim(); if (!text) return null;
    if (!/^[\[{"]/.test(text)) return value;
    try { value=JSON.parse(text); } catch { return value; }
  }
  return value;
}
export function meaningful(value) {
  value=decodeContent(value);
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length>0;
  if (Array.isArray(value)) return value.some(meaningful);
  if (typeof value === "object") return Object.entries(value).some(([key,item]) => !internal.has(key) && meaningful(item));
  return true; // False and zero are legitimate decisions, not empty results.
}
function scalar(value) { return typeof value === "boolean" ? (value ? "是" : "否") : enums[value] || String(value); }
export function renderContent(parent, value, entities) {
  value=decodeContent(value); if (!meaningful(value)) return false;
  if (Array.isArray(value)) {
    for (const item of value.filter(meaningful)) {
      const row=element("div","","trace-item"); renderContent(row,item,entities); parent.append(row);
    }
  } else if (typeof value === "object") {
    for (const [key,item] of Object.entries(value)) {
      if (internal.has(key) || !meaningful(item)) continue;
      const row=element("div","","trace-field"); row.append(element("strong",labels[key] || key));
      const body=element("div","","trace-value");
      if (key === "remove" && Array.isArray(item)) body.append(element("p",`移除 ${item.length} 个条目`));
      else renderContent(body,item,entities);
      row.append(body); parent.append(row);
    }
  } else parent.append(element("p",entities?.get(value) || scalar(value),"trace-text"));
  return true;
}
export function section(parent, title, value, entities) {
  if (!meaningful(value)) return false;
  const block=element("section","","trace-section"); block.append(element("h4",title));
  renderContent(block,value,entities); parent.append(block); return true;
}
export function modelInput(trace) {
  const input=decodeContent(trace.structured_input);
  if (trace.node === "embedding" && input?.input) return {"待编码文本":input.input};
  if (input?.messages) return input.messages.filter(message => meaningful(message.content)).map(message => ({
    [message.role === "system" ? "任务说明" : message.role === "assistant" ? "先前回复" : "输入上下文"]:decodeContent(message.content),
  }));
  return input;
}
export function responseParts(trace) {
  const raw=decodeContent(trace.raw_output);
  const message=raw?.choices?.[0]?.message;
  const calls=message?.tool_calls || [];
  let output=decodeContent(trace.structured_output);
  if (trace.node === "embedding" && output?.data) output={"向量数量":output.data.length,"向量维度":output.data[0]?.embedding?.length || 0};
  let malformed=typeof output === "string" && /^[\[{]/.test(output.trim());
  const parsedCalls=calls.map(call => {
    const argumentsRaw=call.function?.arguments || call.arguments;
    let argumentsValue=null;
    try { argumentsValue=typeof argumentsRaw === "string" ? decodeContent(JSON.parse(argumentsRaw)) : argumentsRaw; }
    catch { malformed=true; }
    return {name:call.function?.name || call.name,arguments:argumentsValue};
  });
  return {
    output:malformed ? null : meaningful(output) ? output : decodeContent(message?.content),
    reasoning:trace.reasoning_content || message?.reasoning_content || message?.reasoning || "",
    calls:calls.length ? parsedCalls
      : trace.tool_name ? [{name:trace.tool_name,arguments:malformed ? null : output}] : [],
    malformed,
  };
}

/** Pair lifecycle records while retaining each failed/retried model call. */
export function nodeGroups(events) {
  const groups=[], active=new Map();
  for (const event of events) {
    const trace=event.trace, id=event.command_id;
    if (event.type === "step.started") {
      const group={trace,calls:[],status:["draft_review","approval"].includes(trace.node) ? "等待确认" : "运行中",commandId:id};
      groups.push(group); active.set(id,group);
    } else if (event.type === "step.completed") {
      let group=active.get(id);
      if (!group || group.trace.node !== trace.node) { group={trace,calls:[],commandId:id}; groups.push(group); }
      group.output=trace.structured_output; group.status="已完成"; active.delete(id);
    } else if (event.type.startsWith("llm.") && trace) {
      let group=active.get(id);
      if (!group) { group={trace,calls:[],status:"已完成",commandId:id}; groups.push(group); }
      group.calls.push(trace); if (event.type === "llm.failed") group.status="调用失败";
      else if (group.status === "调用失败") group.status="已完成";
    } else if (event.type === "run.failed") {
      let group=active.get(id);
      if (!group) { group={trace:{node:"失败",summary:"操作未完成"},calls:[],commandId:id}; groups.push(group); }
      group.status="失败"; group.error=event.output?.message;
    } else if (event.type === "run.completed" || event.type === "story.committed") {
      const group=active.get(id);
      if (group && group.status !== "等待确认") {
        group.status="已完成";
        if (event.output?.message) group.output=JSON.stringify({message:event.output.message});
        active.delete(id);
      }
    } else if (event.type === "draft.updated") {
      const group=active.get(id);
      if (group?.status === "等待确认") { group.status=event.output?.writing_draft?.status === "discarded" ? "已放弃" : "已修改"; active.delete(id); }
    } else if (event.type === "draft.confirmed") {
      const group=groups.findLast(item => item.status === "等待确认");
      if (group) group.status="已确认";
    } else if (event.type === "command.retry_requested" || event.type === "command.withdrawn") {
      groups.push({trace:{node:"操作",summary:event.type === "command.retry_requested" ? "从失败处重试" : "已撤回操作"},calls:[],status:"已记录",commandId:id});
    }
  }
  return groups;
}
