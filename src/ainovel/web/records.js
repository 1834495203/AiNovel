import { button, element, field } from "./client.js";

export function recordText(records = []) {
  return records.map(item => typeof item === "string" ? item : item.name + (item.value !== undefined && item.value !== null ? "：" + item.value : "") + (item.description ? "：" + item.description : "") + (item.children?.length ? "（" + recordText(item.children).replaceAll("\n","；") + "）" : "")).join("\n");
}
export function textEntries(text, previous = []) {
  return text.split("\n").map(line => line.trim()).filter(Boolean).map(line => {
    const existing = previous.find(item => recordText([item]) === line);
    if (existing) return existing;
    const separator = line.search(/[:：]/);
    return {id:crypto.randomUUID(),name:separator > 0 ? line.slice(0,separator) : line,value:separator > 0 ? line.slice(separator + 1).trim() : null,reason:"用户编辑",children:[]};
  });
}
function openEntries(container, labels) {
  container.entries ||= [];
  for (const [key,label] of labels) {
    const old = container[key];
    if (old) {
      if (Array.isArray(old)) container.entries.push(...old.map(item => typeof item === "string" ? {id:crypto.randomUUID(),name:label || item,value:label ? item : null} : {id:item.id || crypto.randomUUID(),name:(label ? label + " · " : "") + item.name,value:item.value ?? null,description:item.description || "",reason:item.reason || "",children:item.children || item.custom_fields || []}));
      else container.entries.push({id:crypto.randomUUID(),name:label,value:String(old)});
    }
    delete container[key];
  }
}
export function textRecords(text, previous = []) {
  return text.split("\n").map(line => line.trim()).filter(Boolean).map(name =>
    previous.find(item => item.name === name || item.name + (item.description ? "：" + item.description : "") === name) || {id:crypto.randomUUID(),name,description:"",reason:"用户编辑"});
}
export function normalizeProposal(proposal) {
  if (!proposal) return null;
  const result = structuredClone(proposal);
  if (typeof result.world === "string") result.world = {premise:result.world};
  openEntries(result.world,[["rules","规则"],["places","地点"],["factions","组织"],["history","历史"],["custom_fields",""]]);
  if (Array.isArray(result.preferences)) result.preferences = {likes:textRecords(result.preferences.join("\n")),dislikes:textRecords((result.dislikes || []).join("\n"))};
  delete result.dislikes;
  for (const character of result.characters || []) {
    character.profile ||= {};
    openEntries(character.profile,[["background","背景"],["personality","性格"],["values","价值观"],["speech_pattern","表达方式"],["fears","旧档案中的恐惧"],["custom_fields",""]]);
  }
  return result;
}

export class CreationChecklist {
  constructor() { this.panel = document.querySelector("#creation-progress"); }
  hide() { this.panel.hidden = true; }
  render(fields = []) {
    this.panel.hidden = false; this.panel.replaceChildren();
    const completed = fields.filter(item => item.status === "defined").length;
    this.panel.append(element("summary", `初始构思 · 已定义 ${completed} / 4 项（角色、喜好可稍后补充）`));
    const rows = fields.length ? fields : [
      {label:"世界观",required:true,status:"missing"}, {label:"初始角色",status:"missing"},
      {label:"喜欢的文风与情节",status:"missing"}, {label:"不喜欢的写法",status:"missing"},
    ];
    for (const item of rows) {
      const row = element("div", "", "creation-field");
      const labels = {defined:"已定义",missing:item.required ? "还需要定义" : "尚未定义 · 可稍后",deferred:"稍后定义"};
      row.append(element("strong", item.label), element("span", labels[item.status], "coverage " + item.status));
      if (item.summary) row.append(element("small",item.summary));
      this.panel.append(row);
    }
  }
}

/** Human-readable editors submit typed metadata commands; they never write directly to storage. */
export class StoryRecords {
  constructor(dialog, save) {
    this.dialog = dialog; this.save = save;
    this.panel = document.querySelector("#story-records"); this.view = null;
    document.querySelector("#record-category").onchange = () => this.render(this.view);
  }
  reset() { this.view = null; this.panel.replaceChildren(element("p","确认创建后，可以在这里维护世界观、角色与长期计划。","hint")); }
  render(view) {
    if (!view) return this.reset();
    this.view = view; this.panel.replaceChildren();
    const story = view.story;
    const kind = document.querySelector("#record-category").value;
    if (kind === "world") {
      this.scalar("作品名",story.title,value => ({title:value}));
      this.scalar("世界观方向",story.world.premise,value => ({world:{premise:value}}));
      this.group("世界观条目",story.world.entries || [],"entries",patch => ({world:{entries:patch}}));
      this.group("小说自定义字段",story.custom_fields || [],"custom_fields",patch => ({custom_fields:patch}));
    } else if (kind === "preferences") {
      for (const [key,label] of [["likes","喜欢"],["dislikes","不喜欢"]]) this.group(label,story.preferences[key],"preference",patch => ({preferences:{[key]:patch}}));
      this.group("偏好自定义字段",story.preferences.custom_fields,"custom_fields",patch => ({preferences:{custom_fields:patch}}));
    } else if (kind === "writer") {
      this.scalar("当前进度",story.writer.progress,value => ({writer:{progress:value}}));
      this.scalar("目前用户想要什么",story.writer.current_user_intent,value => ({writer:{current_user_intent:value}}));
      for (const [key,label] of [["mainlines","主线"],["subplots","支线"],["future_directions","未来走向"],["foreshadows","伏笔"],["outline","大纲"],["notes","作家笔记"],["drafts","工作草稿"],["custom_fields","作家自定义字段"]]) this.group(label,story.writer[key],key,patch => ({writer:{[key]:patch}}));
    } else {
      for (const character of story.characters) {
        const snapshots = view.characters_at_scenes.filter(item => item.character_id === character.id);
        const latest = snapshots.at(-1) || story.initial_snapshots.find(item => item.character_id === character.id) || character;
        const heading = element("h3",character.name); this.panel.append(heading);
        const saveEdit = async changes => { await this.save({kind:"update_story",patch:{reason:"用户修改角色资料"},character_edits:[{character_id:character.id,...changes,reason:"用户修改角色资料"}]}); this.dialog.close(); };
        this.group("档案条目",latest.profile.entries || [],"entries",patch => ({profile:{entries:patch}}),saveEdit);
        this.group("绰号与来源",latest.aliases || [],"alias",patch => ({aliases:patch}),saveEdit);
        this.group("角色自定义字段",latest.custom_fields || character.custom_fields || [],"custom_fields",patch => ({custom_fields:patch}),saveEdit);
        if (latest.state?.entries?.length) this.panel.append(element("p","本阶段记录：\n" + recordText(latest.state.entries)));
      }
    }
  }
  async metadata(patch) { await this.save({kind:"update_story",patch:{...patch,reason:"用户更新设定或作家记录"}}); this.dialog.close(); }
  scalar(label,value,patch,save = changes => this.metadata(changes)) {
    const row = element("div","","record-row"); row.append(element("strong",label),element("p",value || "尚未记录"));
    row.append(button("编辑",() => { const body=this.dialog.open(label); const input=field(body,label,value || "",true); body.append(button("保存",() => save(patch(input.value)),"primary")); })); this.panel.append(row);
  }
  group(label,items,kind,patch,save = changes => this.metadata(changes)) {
    const section = element("section","","record-group");
    const head = element("div","","record-heading"); head.append(element("h4",label),button("＋",() => this.edit(label,null,kind,patch,save)));
    section.append(head);
    if (!items.length) section.append(element("p","暂无条目","hint"));
    for (const item of items) {
      const row = element("div","","record-row"); row.append(element("strong",item.name));
      if (item.description) row.append(element("p",item.description));
      if (item.value !== undefined && item.value !== null) row.append(element("p",String(item.value)));
      if (item.status) row.append(element("small","状态：" + ({planned:"计划中",active:"进行中",resolved:"已解决",completed:"已完成",abandoned:"已放弃",planted:"已埋下",paid_off:"已回收",dropped:"已舍弃",draft:"草稿",used:"已使用",discarded:"已丢弃"}[item.status] || item.status)));
      if (item.reason) row.append(element("small","依据：" + item.reason));
      if (item.progress) row.append(element("p",item.progress));
      if (item.payoff) row.append(element("p","预期回收：" + item.payoff));
      if (item.origin) row.append(element("small",(item.origin === "explicit" ? "用户明确表达" : "AI 推测") + " · 置信度 " + item.confidence));
      if (item.content) { const details=element("details"); details.append(element("summary","查看草稿"),element("pre",item.content)); row.append(details); }
      if (item.children?.length) row.append(element("small",`包含 ${item.children.length} 个子字段`));
      row.append(button("编辑",() => this.edit(label,item,kind,patch,save)),button("删除",() => save(patch({remove:[item.id]})))); section.append(row);
    }
    this.panel.append(section);
  }
  edit(label,item,kind,patch,save) {
    const body = this.dialog.open("编辑" + label);
    const name = field(body,"名称",item?.name || ""); const description = field(body,"说明",item?.description || "",true);
    const reason = field(body,"原因 / 依据",item?.reason || "用户编辑",true);
    const controls=[];
    const children=[];
    let childrenPanel;
    const text = (key,label,multiline=true) => { const input=field(body,label,item?.[key] ?? "",multiline); controls.push({key,input}); };
    if (["custom_fields","entries"].includes(kind)) {
      text("value","值");
      childrenPanel=element("section"); childrenPanel.append(element("h4","子字段（可选）")); body.append(childrenPanel);
      const addChild = child => {
        const row=element("div","","custom-child");
        const entry={base:child || {},name:field(row,"名称",child?.name || ""),value:field(row,"值",String(child?.value ?? "")),description:field(row,"说明",child?.description || "")};
        row.append(button("移除此项",() => { children.splice(children.indexOf(entry),1); row.remove(); }));
        children.push(entry); childrenPanel.append(row);
      };
      for (const child of item?.children || []) addChild(child);
      body.append(button("＋ 子字段",() => addChild(null)));
    }
    if (["mainlines","subplots","future_directions"].includes(kind)) { text("progress","当前进展"); text("next_steps","下一步（每行一项）"); }
    if (kind === "foreshadows") text("payoff","准备怎样回收");
    if (kind === "drafts") text("content","草稿内容");
    let status;
    const statuses = ["mainlines","subplots","future_directions"].includes(kind) ? ["planned","active","resolved","abandoned"] : kind === "foreshadows" ? ["planned","planted","paid_off","dropped"] : kind === "outline" ? ["planned","active","completed","abandoned"] : kind === "drafts" ? ["draft","used","discarded"] : [];
    if (statuses.length) { status=element("select"); for (const value of statuses) { const option=element("option",{planned:"计划中",active:"进行中",resolved:"已解决",completed:"已完成",abandoned:"已放弃",planted:"已埋下",paid_off:"已回收",dropped:"已舍弃",draft:"草稿",used:"已使用",discarded:"已丢弃"}[value]); option.value=value; status.append(option); } status.value=item?.status || statuses[0]; body.append(status); }
    body.append(button("保存条目",async () => {
      if (!name.value.trim()) throw new Error("请填写名称");
      const record={...(item || {}),id:item?.id || crypto.randomUUID(),name:name.value.trim(),description:description.value,reason:reason.value};
      for (const control of controls) record[control.key]=control.key === "next_steps" ? control.input.value.split("\n").filter(Boolean) : control.input.value;
      if (["custom_fields","entries"].includes(kind)) {
        // Preserve typed values if unchanged; newly entered values remain user-authored text.
        const valueControl=controls.find(control => control.key === "value");
        if (item && valueControl.input.value === String(item.value ?? "")) record.value=item.value;
        record.children=children.filter(child => child.name.value.trim()).map(child => ({...child.base,id:child.base.id || crypto.randomUUID(),name:child.name.value.trim(),value:child.value.value === String(child.base.value ?? "") && child.base.value !== undefined ? child.base.value : child.value.value,description:child.description.value}));
      }
      if (status) record.status=status.value;
      if (kind === "preference") { record.origin="explicit"; record.confidence=1; record.active=true; }
      await save(patch({upsert:[record]}));
    },"primary"));
  }
}
