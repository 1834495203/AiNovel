import { ApiClient, button, element, toast } from "./client.js";
import { ProcessRecords } from "./process-records.js";
import { failureBubble } from "./recovery.js";
import { chatText } from "./chat-text.js";
import { theme } from "./theme.js";

class StoryDiscussion {
  constructor() {
    this.api=new ApiClient();this.process=new ProcessRecords(this.api);this.busy=false;this.monitoring=new Map();
    const query=new URLSearchParams(location.search);this.storyId=query.get("story");this.sessionId=query.get("session");
    this.messages=document.querySelector("#discussion-messages");this.input=document.querySelector("#discussion-input");this.status=document.querySelector("#discussion-status");
  }
  draftKey() { return "ainovel.discussionDraft."+this.storyId+"."+(this.sessionId || "new"); }
  saveInput() { localStorage.setItem(this.draftKey(),this.input.value); }
  setBusy(value) { this.busy=value || this.monitoring.size>0;this.input.disabled=this.busy;document.querySelector("#discussion-form button").disabled=this.busy;document.querySelector("#new-discussion").disabled=this.busy;document.querySelector("#discussion-undo").disabled=!this.undoAvailable || this.busy; }
  async start() {
    theme.bind(document.querySelector("#theme-toggle"));
    if (!this.storyId) throw new Error("请从小说页面打开故事讨论。");
    document.querySelector("#return-story").href="/#story/"+this.storyId;
    document.querySelector("#new-discussion").onclick=() => this.newSession();
    document.querySelector("#discussion-form").onsubmit=event => { event.preventDefault();this.send(); };
    document.querySelector("#discussion-undo").onclick=() => this.undo();
    this.input.oninput=() => this.saveInput();
    this.input.onkeydown=event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault();this.send(); } };
    this.sessionId ||= localStorage.getItem("ainovel.discussionSession."+this.storyId);
    await this.refresh();
  }
  async refresh() {
    const [view,sessions,undo]=await Promise.all([this.api.get("/api/stories/"+this.storyId),this.api.get("/api/stories/"+this.storyId+"/discussions"),this.api.get("/api/undo/"+this.storyId)]);
    this.view=view;this.sessions=sessions;this.undoable=new Set(undo.command_ids);this.undoAvailable=undo.available;this.process.setStory(view.story);
    document.querySelector("#discussion-title").textContent=view.story.title;
    document.querySelector("#discussion-undo").disabled=!undo.available || this.busy;
    if (!sessions.some(item => item.id === this.sessionId)) this.sessionId=sessions[0]?.id || null;
    this.renderSessions();await this.open(this.sessionId,false);
  }
  renderSessions() {
    const nav=document.querySelector("#discussion-sessions");nav.replaceChildren();
    for (const session of this.sessions) {
      const row=button(session.title,() => this.busy ? toast("请等待本轮讨论完成。") : this.open(session.id),session.id === this.sessionId ? "active" : "");
      row.dataset.session=session.id;row.append(element("small",session.message_count+" 轮讨论"));nav.append(row);
    }
  }
  async open(id,save=true) {
    if (save) this.saveInput();this.sessionId=id;
    const url=new URL(location.href);if (id) url.searchParams.set("session",id);else url.searchParams.delete("session");history.replaceState(null,"",url);
    if (id) localStorage.setItem("ainovel.discussionSession."+this.storyId,id);else localStorage.removeItem("ainovel.discussionSession."+this.storyId);
    this.input.value=localStorage.getItem(this.draftKey()) || "";this.renderSessions();this.messages.replaceChildren();
    document.querySelector("#discussion-subtitle").textContent=this.sessions.find(item => item.id === id)?.title || "讨论剧情走向，推敲人物与世界。";
    if (!id) return this.empty();
    const commands=await this.api.get("/api/stories/"+this.storyId+"/discussions/"+id+"/commands");
    this.process.setCommands(commands);
    for (const command of commands) {
      this.message(command.submission.payload.message,"user");
      if (command.status === "failed") {
        const failure=failureBubble(command,(view,decision) => this.recover(view,decision));failure.className="discussion-message failure";
        this.process.attach(failure,command);this.messages.append(failure);
      } else {
        const node=this.message(command.output.discussion?.reply || "正在讨论…","assistant");this.process.attach(node,command);
        if (command.output.discussion?.settings_changed) node.append(element("p","已更新设定 · 正文保持原样","settings-changed"));
        if (command.status === "completed") { const undo=button("撤回到这轮之前",() => this.undo(command.submission.id),"message-undo");undo.disabled=!this.undoable.has(command.submission.id);undo.title="连续撤回这轮及其后的小说操作";node.append(undo); }
        if (["queued","running"].includes(command.status)) this.watch(command).catch(error => toast(error.message));
      }
    }
    if (!commands.length) this.empty();this.messages.scrollTop=this.messages.scrollHeight;
  }
  empty() {
    const node=element("div","","discussion-empty");node.append(element("h2","下一步，故事可以怎样发展？"),element("p","聊聊你的想法。需要时，AI 会查找前文，也可以按你的要求调整设定。","hint"));
    for (const text of ["下一幕有哪些可能的走向？","梳理当前主线与尚未回收的伏笔。","看看角色的发展是否需要调整。"]) node.append(button(text,() => {this.input.value=text;this.saveInput();this.input.focus();}));
    this.messages.append(node);
  }
  message(text,role) { const node=element("article","","discussion-message "+role);node.append(element("small",role === "user" ? "你" : "故事讨论助手"),chatText(text));this.messages.append(node);return node; }
  async createSession() {
    const head=await this.api.get("/api/stories/"+this.storyId), id=crypto.randomUUID(), draft=this.input.value;
    await this.api.command({kind:"start_discussion",session_id:id,title:"新讨论"},this.storyId,head.revision_id,event => this.event(event));
    this.sessionId=id;this.input.value=draft;this.saveInput();return id;
  }
  async newSession() {
    if (this.busy) return;this.saveInput();this.setBusy(true);
    try { this.input.value="";await this.createSession();await this.refresh();this.input.focus(); }
    catch (error) { toast(error.message); }
    finally { this.setBusy(false); }
  }
  async send() {
    const text=this.input.value.trim();if (this.busy || !text) return;
    this.saveInput();this.setBusy(true);this.status.textContent="正在读取最新故事…";
    try {
      if (!this.sessionId) await this.createSession();
      const head=await this.api.get("/api/stories/"+this.storyId);
      const command=await this.api.submit({kind:"discussion",session_id:this.sessionId,message:text},this.storyId,head.revision_id);
      this.input.value="";this.saveInput();await this.refresh();await this.watch(command);
    } catch (error) { toast(error.message);this.status.textContent="讨论未完成，可在本轮结果中重试。";await this.refresh().catch(() => {}); }
    finally { this.setBusy(false); }
  }
  event(event) { this.process.add(event);if (event.trace?.summary) this.status.textContent=event.trace.summary; }
  watch(command) {
    const id=command.submission.id;if (this.monitoring.has(id)) return this.monitoring.get(id);
    const promise=this.waitFor(command);this.monitoring.set(id,promise);return promise;
  }
  async waitFor(command) {
    const id=command.submission.id;this.setBusy(true);this.status.textContent="正在讨论，按需查询或调整设定…";
    try { await this.api.wait(id,event => this.event(event));this.status.textContent="本轮讨论已保存";await this.refresh(); }
    catch (error) { toast(error.message);this.status.textContent="本轮失败，设定未保存，可重试或撤回。";await this.refresh(); }
    finally { this.monitoring.delete(id);this.setBusy(false); }
  }
  async recover(command,decision) {
    if (this.busy) return;this.setBusy(true);
    try { await this.api.recover(command,decision,event => this.event(event));if (decision === "retry") await this.watch(command);else await this.refresh(); }
    catch (error) { toast(error.message); }
    finally { this.setBusy(false); }
  }
  async undo(target=null) {
    if (this.busy) return;this.saveInput();this.setBusy(true);
    try { const state=await this.api.get("/api/undo/"+this.storyId);if (!state.available) return toast(state.message);const head=await this.api.get("/api/stories/"+this.storyId);await this.api.command({kind:"undo",expected_operation_id:state.operation_id,target_command_id:target},this.storyId,head.revision_id,event => this.event(event));await this.refresh(); }
    catch (error) { toast(error.message); }
    finally { this.setBusy(false); }
  }
}

new StoryDiscussion().start().catch(error => toast(error.message));
