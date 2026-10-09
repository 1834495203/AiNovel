import { ApiClient, button, element, toast } from "./client.js";
import { theme } from "./theme.js";
import { recordText } from "./records.js";
import { failureBubble } from "./recovery.js";
import { ProcessRecords } from "./process-records.js";

class CharacterChat {
  constructor() {
    this.api = new ApiClient();
    this.process = new ProcessRecords(this.api);
    const query = new URLSearchParams(location.search);
    this.revisionId = query.get("revision"); this.characterId = query.get("character"); this.sceneId = query.get("scene");
    this.sessionId = crypto.randomUUID(); this.history = []; this.busy = false;
  }
  async start() {
    theme.bind(document.querySelector("#theme-toggle"));
    const revision = await this.api.get("/api/revisions/" + this.revisionId);
    this.storyId = revision.story_id;
    this.process.setStory(revision.story);
    const character = revision.story.characters.find(character => character.id === this.characterId);
    if (!character) throw new Error("角色不存在");
    const snapshot = await this.api.get("/api/revisions/" + this.revisionId + "/characters/" + this.characterId
      + "/snapshots/" + this.sceneId);
    document.querySelector("#name").textContent = "与 " + character.name + " 聊聊";
    document.querySelector("#meta").textContent = revision.story.title + " · 固定阶段";
    document.querySelector("#chat-meta").textContent = recordText(snapshot.state.entries) || "本阶段暂无记录";
    const card = element("a", "查看完整角色卡");
    card.href = "/characters?revision=" + this.revisionId + "&character=" + this.characterId + "&scene=" + this.sceneId;
    document.querySelector("#chat-meta").append(element("br"), card);
    document.querySelector("#new-session").onclick = () => this.newSession();
    document.querySelector("#form").onsubmit = event => { event.preventDefault(); this.send(); };
    document.querySelector("#undo").onclick = () => this.undo();
    await this.refresh();
  }
  async refresh() {
    const commands = await this.api.get("/api/stories/" + this.storyId + "/commands");
    this.process.setCommands(commands);
    this.undoable = new Set((await this.api.get("/api/undo/" + this.storyId)).command_ids);
    this.history = commands.filter(command => !command.undone
      && ["character_chat", "start_character_chat"].includes(command.submission.payload.kind)
      && command.submission.base_revision_id === this.revisionId
      && command.submission.payload.character_id === this.characterId
      && command.submission.payload.scene_id === this.sceneId && ["completed","failed"].includes(command.status));
    this.render();
  }
  render() {
    const list = document.querySelector("#sessions"); list.replaceChildren();
    const ids = [...new Set(this.history.map(command => command.submission.payload.session_id || command.output.chat_session_id).filter(Boolean))];
    ids.forEach((id, index) => list.append(button("会话 " + (index + 1), () => {
      this.sessionId = id; this.render();
    }, id === this.sessionId ? "active" : "")));
    const messages = document.querySelector("#messages"); messages.replaceChildren();
    for (const command of this.history.filter(command => command.status === "failed" && (command.submission.payload.kind === "start_character_chat" || command.submission.payload.session_id === this.sessionId))) messages.append(failureBubble(command,(view,decision) => this.recover(view,decision)));
    for (const command of this.history.filter(command => command.output.chat
      && command.submission.payload.session_id === this.sessionId)) {
      const reply = element("div", command.output.chat.reply, "bubble");
      const undo = button("撤回到这轮之前", () => this.undo(command.submission.id), "message-undo");
      undo.title = "连续撤回这轮对话及其后的完整操作";
      if (!this.undoable.has(command.submission.id)) { undo.disabled = true; undo.title = "旧记录未保存撤回凭据"; }
      reply.append(undo);
      this.process.attach(reply, command);
      messages.append(element("div", command.submission.payload.message, "bubble user"),reply);
    }
  }
  async send() {
    if (this.busy) return;
    const input = document.querySelector("#message"); const message = input.value.trim();
    if (!message) return;
    this.busy = true; document.querySelector("#form button").disabled = true;
    try {
      await this.api.command({ kind: "character_chat", character_id: this.characterId, scene_id: this.sceneId,
        session_id: this.sessionId, message }, this.storyId, this.revisionId, () => {});
      input.value = ""; await this.refresh();
    } catch (error) { await this.refresh(); toast(error.message); }
    finally { this.busy = false; document.querySelector("#form button").disabled = false; }
  }
  async undo(targetCommandId = null) {
    if (this.busy) return toast("请等待当前操作完成。");
    this.busy = true;
    try {
      const state = await this.api.get("/api/undo/" + this.storyId);
      if (!state.available) return toast(state.message);
      const head = await this.api.get("/api/stories/" + this.storyId);
      const result = await this.api.command({kind: "undo", expected_operation_id: state.operation_id,
        target_command_id:targetCommandId},
        this.storyId, head.revision_id, () => {});
      toast(result.output.message);
      if (result.output.story_deleted) { location.href = "/"; return; }
      await this.refresh();
    } catch (error) { toast(error.message); }
    finally { this.busy = false; }
  }
  async recover(command,decision) {
    if (this.busy) return toast("请等待当前操作完成。");
    this.busy = true;
    try {
      await this.api.recover(command,decision);
      if (decision === "retry") {
        const result = await this.api.wait(command.submission.id,() => {});
        if (result.output.chat_session_id) this.sessionId = result.output.chat_session_id;
      }
    } catch (error) { toast(error.message); }
    finally { await this.refresh(); this.busy = false; }
  }
  async newSession() {
    if (this.busy) return;
    this.busy = true;
    try {
      const result = await this.api.command({kind: "start_character_chat", character_id: this.characterId,
        scene_id: this.sceneId}, this.storyId, this.revisionId, () => {});
      this.sessionId = result.output.chat_session_id; await this.refresh();
    } catch (error) { toast(error.message); }
    finally { this.busy = false; }
  }
}
new CharacterChat().start().catch(error => toast(error.message));
