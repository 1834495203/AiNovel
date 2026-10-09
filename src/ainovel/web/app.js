import { StoryRecords, CreationChecklist, recordText, textRecords, textEntries } from "./records.js";
import { DraftReviewPanel } from "./draft-review.js";
import { ApiClient, DialogView, button, element, field, toast } from "./client.js";
import { theme } from "./theme.js";
import { ConversationPanel } from "./conversation.js";
import { SetupDraftController } from "./drafts.js";
import { PageNavigation, StoryContents } from "./navigation.js";
import { failureBubble } from "./recovery.js";
import { ProcessRecords } from "./process-records.js";

/** Workspace controller; transport, dialogs and the character interview page are separate modules. */
class Workspace {
  constructor() {
    this.api = new ApiClient();
    this.dialog = new DialogView();
    this.view = null;
    this.turns = [];
    this.interview = false;
    this.selection = null;
    this.busy = false;
    this.sessionId = localStorage.getItem("ainovel.setupSession") || crypto.randomUUID();
    this.undoSource = null;
    this.drafts = new SetupDraftController(this.api);
    this.undoable = new Set();
    this.conversation = new ConversationPanel();
    this.navigation = new PageNavigation();
    this.contents = new StoryContents(this.conversation);
    this.records = new StoryRecords(this.dialog, async payload => this.result(await this.command(payload)));
    this.checklist = new CreationChecklist();
    this.draftReview = new DraftReviewPanel(this.dialog, (...args) => this.reviewDraft(...args));
    this.messages = document.querySelector("#messages");
    this.process = new ProcessRecords(this.api);
    this.status = document.querySelector("#status");
  }
  async start() {
    theme.bind(document.querySelector("#theme-toggle"));
    document.querySelector("#new-story").onclick = () => this.begin();
    document.querySelector("#back-shelf").onclick = () => this.showShelf();
    document.querySelector(".brand").onclick = event => { event.preventDefault(); this.showShelf(); };
    window.addEventListener("hashchange", () => this.openRoute().catch(error => toast(error.message)));
    document.querySelector("#begin").onclick = () => this.begin();
    document.querySelector("#versions").onclick = () => this.versions();
    document.querySelector("#story-discussion").onclick = () => this.view ? window.open("/discussion?story=" + this.view.story.id,"_blank","noopener") : toast("请先打开小说。");
    document.querySelector("#character-cards").onclick = () => { if (this.view) window.open("/characters?story=" + this.view.story.id, "_blank", "noopener"); };
    document.querySelector("#delete-story").onclick = () => this.deleteStory();
    document.querySelector("#trash").onclick = () => this.trash();
    document.querySelector("#undo").onclick = () => this.undo();
    document.querySelector("#toggle-tools").onclick = () => this.tools(
      !document.querySelector("#studio").classList.contains("tools-open"));
    document.querySelector("#close-tools").onclick = () => this.tools(false);
    for (const tab of ["direction", "records"]) {
      document.querySelector("#tab-" + tab).onclick = () => this.tab(tab);
    }
    document.querySelector("#chat-form").onsubmit = event => { event.preventDefault(); this.send(); };
    document.querySelector("#chat-input").oninput = event => {
      if (this.interview) this.drafts.update({input_text:event.target.value});
    };
    this.dialog.dialog.addEventListener("close", () => {
      if (this.interview && this.drafts.current?.editing_setup) this.drafts.update({editing_setup:false});
    });
    document.querySelector("#direction-form").onsubmit = event => { event.preventDefault(); this.direction(); };
    document.querySelector("#novel").addEventListener("mouseup", () => this.select());
    document.querySelector("#novel").addEventListener("touchend", () => this.select());
    this.draftReview.panel.addEventListener("mouseup", () => this.select());
    this.draftReview.panel.addEventListener("touchend", () => this.select());
    document.querySelector("#rewrite-selection").onclick = () => this.rewrite(this.selection);
    document.querySelector("#clear-selection").onclick = () => this.clearSelection();
    const health = await this.api.get("/health");
    document.querySelector("#health").textContent = health.database && health.redis
      ? (health.worker ? (health.provider === "mock" ? "演示模式 · 已连接" : health.model + " · 已连接")
        : "请启动 worker") : "请检查数据库与 Redis";
    document.querySelector("#health").title = health.embedding_provider === "hash"
      ? "写作使用当前聊天模型；检索暂用本地哈希向量，可独立配置语义 embedding 服务。"
      : "已配置独立语义 embedding 服务。";
    await this.library();
    await this.openRoute();
  }
  async openRoute() {
    const route = this.navigation.current();
    if (this.busy) {
      this.navigation.show(this.interview ? "draft" : "story", this.interview ? this.sessionId : this.view?.story.id, true);
      return toast("请等待当前操作完成。");
    }
    try {
      if (route.kind === "story") await this.load(route.id);
      else if (route.kind === "draft") {
        const saved = await this.api.get("/api/drafts/" + route.id).catch(error => {
          const cached = this.drafts.cached(route.id);
          if (cached) return cached;
          throw error;
        });
        await this.openDraft(saved);
      } else await this.showShelf();
    } catch (error) { await this.showShelf(); toast(error.message); }
  }
  async showShelf() {
    if (this.busy) return toast("请等待当前操作完成。");
    if (this.interview) await this.drafts.flush();
    this.dialog.close(); this.conversation.close(); this.tools(false); this.contents.toggle(false);
    this.navigation.show("shelf");
    await this.library();
  }
  tab(name) {
    for (const tab of ["direction", "records"]) {
      document.querySelector("#" + tab + "-panel").hidden = name !== tab;
      document.querySelector("#tab-" + tab).classList.toggle("active", name === tab);
    }
  }
  tools(open) {
    document.querySelector("#studio").classList.toggle("tools-open", open);
    document.querySelector("#toggle-tools").setAttribute("aria-expanded", String(open));
  }
  bubble(text, role = "assistant") {
    const node = element("div", "", "bubble " + role);
    node.append(element("small", role === "user" ? "你" : "叙事助手"), element("div", text));
    this.messages.append(node); this.messages.scrollTop = this.messages.scrollHeight; return node;
  }
  attachUndo(node, command) {
    if (!command) return;
    this.process.attach(node, command);
    if (!["completed", "waiting"].includes(command.status)) return;
    const action = button("撤回到这轮之前", () => this.undo(this.view || this.undoSource, command.submission.id), "message-undo");
    action.title = "连续撤回这轮对话及其后的完整操作";
    if (!this.undoable.has(command.submission.id)) {
      action.disabled = true; action.title = "旧记录未保存撤回凭据，可通过版本历史恢复正文";
    }
    node.append(action);
  }
  async refreshUndo(scope) {
    const state = await this.api.get("/api/undo/" + scope);
    this.undoable = new Set(state.command_ids);
  }
  event(event) {
    if (event.type === "connection.retrying") { this.status.textContent = "连接恢复中，任务仍在后台运行…"; return; }
    if (event.type === "step.started" && event.trace) this.status.textContent = event.trace.summary + "…";
    this.process.add(event);
  }

  async command(payload, source = this.view) {
    try { return await this.api.command(payload, source?.story.id ?? null, source?.revision_id ?? null,
      event => this.event(event), source ? null : this.sessionId); }
    catch (error) { if (error.view) this.showFailure(error.view); throw error; }
  }
  showFailure(command) {
    if (command.undone || ["undo","recover_command","save_setup_draft"].includes(command.submission.payload.kind)) return;
    const previous = this.messages.querySelector(`[data-failed-command="${command.submission.id}"]`);
    if (previous) previous.remove();
    const failure = failureBubble(command,(view,decision) => this.recoverFailed(view,decision));
    this.process.attach(failure, command); this.messages.append(failure);
    if (command.submission.payload.kind === "review_draft" && command.submission.payload.decision === "confirm")
      this.status.textContent = "确认失败 · 草稿保留，再次确认会重试已失败的步骤";
    this.refreshUndo(command.submission.story_id || command.submission.session_id || command.submission.id).catch(error => toast(error.message));
  }
  async recoverFailed(command, decision) {
    if (this.busy) return toast("请等待当前操作完成。");
    this.busy = true; document.querySelector("#chat-form button").disabled = true;
    try {
      await this.api.recover(command,decision,event => this.event(event));
      if (decision === "retry") {
        this.status.textContent = "正在重试失败任务…";
        const result = await this.api.wait(command.submission.id,event => this.event(event));
        if (this.interview && result.output.interview) await this.restoreInterview();
        else await this.result(result);
      }
      if (this.interview) await this.restoreInterview();
      else if (this.view) await this.load(this.view.story.id);
    } catch (error) { if (error.view) this.showFailure(error.view); toast(error.message); }
    finally { this.busy = false; document.querySelector("#chat-form button").disabled = false; }
  }
  async library() {
    const nav = document.querySelector("#stories"); nav.replaceChildren();
    for (const story of await this.api.get("/api/stories")) {
      const node = button(story.title, () => this.busy ? toast("请等待当前操作完成。") : this.load(story.id), "book-card");
      node.append(element("small", story.scene_count + " 个场景")); nav.append(node);
    }
    if (!nav.children.length) nav.append(element("p", "书架还是空的，点击「＋ 新故事」开始创作。", "hint"));
    const drafts = document.querySelector("#drafts"); drafts.replaceChildren();
    const saved = await this.api.get("/api/drafts");
    const cachedDrafts = [];
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (key?.startsWith("ainovel.draft.")) {
        const cached = this.drafts.cached(key.slice("ainovel.draft.".length));
        if (cached?.id) cachedDrafts.push(cached);
      }
    }
    for (const cached of cachedDrafts) {
      const index = saved.findIndex(item => item.id === cached.id);
      if (index < 0) saved.unshift(cached);
      else if (cached.updated_at > saved[index].updated_at) saved[index] = cached;
    }
    for (const draft of saved) {
      const node = button(draft.title || "新故事草稿", () => this.openDraft(draft), "book-card draft-card");
      node.append(element("small", "草稿 · 点击继续构思")); drafts.append(node);
    }
    if (!drafts.children.length) drafts.append(element("p", "暂无创作草稿。构思中的故事会自动保存在这里。", "hint"));
  }
  async load(storyId, hydrate = true) {
    const changedStory = this.interview || this.view?.story.id !== storyId;
    if (this.interview) await this.drafts.flush();
    this.view = await this.api.get("/api/stories/" + storyId);
    this.navigation.show("story", storyId);
    this.undoSource = this.view;
    this.interview = false; this.checklist.hide();
    localStorage.setItem("ainovel.story", storyId);
    localStorage.setItem("ainovel.mode", "story");
    if (changedStory) document.querySelector("#chat-input").value = "";
    document.querySelector("#title").textContent = this.view.story.title;
    this.clearSelection();
    const commands = await this.api.get("/api/stories/" + storyId + "/commands");
    this.process.setCommands(commands); this.render();
    if (hydrate || changedStory) this.draftReview.clear();
    document.querySelector("#delete-story").disabled = false;
    if (hydrate) {
      await this.refreshUndo(storyId);
      this.messages.replaceChildren();
      for (const command of commands) {
        const payload = command.submission.payload;
        if (command.status === "failed" && !command.undone) { this.showFailure(command); continue; }
        if (payload.kind === "generate" && !command.undone) {
          this.bubble(payload.instruction, "user");
          if (command.status === "waiting") {
            if (command.output.writing_draft) this.showDraft(command);
            else this.approval(command);
          }
          else this.attachUndo(this.bubble(command.status === "completed" ? "已完成场景创作。" : (command.error || "任务进行中")), command);
        }
        if (payload.kind === "review_draft" && !command.undone) {
          if (payload.feedback) this.bubble(payload.feedback,"user");
          if (command.status === "waiting" && command.output.approval_phase) { this.draftReview.pauseForCharacter(); this.approval(command); }
          else if (command.status === "completed") this.attachUndo(this.bubble(command.output.message),command);
        }
        if (command.status === "queued" || command.status === "running") {
          this.api.wait(command.submission.id, event => this.event(event)).then(
            result => this.result(result)).catch(error => { if (error.view) this.showFailure(error.view); toast(error.message); });
        }
      }
      const failedConfirmation = commands.some(command => !command.undone && command.status === "failed"
        && command.submission.payload.kind === "review_draft" && command.submission.payload.decision === "confirm"
        && commands.some(parent => parent.status === "waiting" && !parent.undone
          && parent.submission.id === command.submission.payload.generation_command_id
          && parent.output.writing_draft?.version === command.submission.payload.expected_draft_version));
      this.status.textContent = commands.some(command => command.status === "queued" || command.status === "running")
        ? "任务正在后台运行" : failedConfirmation ? "上次确认失败 · 草稿保留，再次确认会继续重试" : commands.some(command => command.status === "waiting")
          ? (this.draftReview.command && !this.draftReview.awaitingCharacter ? "草稿已保存 · 等待你修改或确认" : "等待你确认新角色") : "已加载当前版本 · 所有操作已保存";
    }
    await this.library();
  }
  render() {
    const novel = document.querySelector("#novel"); novel.replaceChildren();
    const story = this.view.story;
    this.process.setStory(story);
    if (!story.scenes.length) novel.append(element("div", "设定已就绪。在下方告诉 AI，你想如何开始第一幕。", "empty"));
    for (const chapter of story.chapters) {
      const section = element("section", "", "chapter");
      section.id = "chapter-" + chapter.id; section.tabIndex = -1;
      const heading = element("div", "", "chapter-heading");
      heading.append(element("h2", chapter.title));
      const chapterScenes = story.scenes.filter(scene => scene.chapter_id === chapter.id);
      const chapterText = chapterScenes.map(scene => scene.text).join("\n\n");
      heading.append(button("修改整章", () => this.rewrite({ chapter_id: chapter.id, start: 0,
        end: Array.from(chapterText).length, selected_text: chapterText })));
      section.append(heading);
      for (const scene of chapterScenes) {
        const article = element("article", "", "scene"); article.dataset.scene = scene.id;
        article.id = "scene-" + scene.id; article.tabIndex = -1;
        const title = element("div", "", "chapter-heading");
        title.append(element("h3", scene.title), button("修改场景", () => this.rewrite({
          scene_id: scene.id, start: 0, end: Array.from(scene.text).length, selected_text: scene.text,
        })));
        article.append(title);
        const prose = element("div", "", "prose"); prose.dataset.scene = scene.id;
        this.highlight(prose, scene); article.append(prose); this.process.scene(article, scene); section.append(article);
      }
      novel.append(section);
    }
    this.contents.render(story); this.records.render(this.view);
    const directions = document.querySelector("#directions"); directions.replaceChildren();
    const labels = { global: "全局", future: "后续故事", scene: "场景", chapter: "章节" };
    for (const direction of story.directions) {
      const row = element("div", direction.instruction, "direction");
      row.append(element("small", labels[direction.scope] + (direction.hard ? " · 硬约束" : " · 偏好")
        + (direction.active ? "" : " · 已完成")));
      if (direction.active) row.append(button("停用", async () => {
        await this.result(await this.command({ kind: "direction", direction: { ...direction, active: false } }));
      }));
      directions.append(row);
    }
  }
  highlight(parent, scene) {
    const story = this.view.story;
    const index = story.scenes.findIndex(item => item.id === scene.id);
    const visibleIds = new Set(story.scenes.slice(0, index + 1).map(item => item.id));
    const characters = story.characters.filter(character => !character.introduced_scene_id
      || visibleIds.has(character.introduced_scene_id));
    const presentations = this.view.characters_at_scenes.filter(item => item.scene_id === scene.id);
    const references = characters.flatMap(character => [character.name, ...(presentations.find(item => item.character_id === character.id)?.aliases || []).map(alias => alias.name)]
      .filter(Boolean).map(name => ({ name, character }))).sort((a, b) => b.name.length - a.name.length);
    if (!references.length) { parent.textContent = scene.text; return; }
    const escape = value => value.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    const pattern = new RegExp(references.map(ref => escape(ref.name)).join("|"), "g");
    let offset = 0;
    for (const match of scene.text.matchAll(pattern)) {
      parent.append(document.createTextNode(scene.text.slice(offset, match.index)));
      const character = references.find(ref => ref.name === match[0]).character;
      const snapshot = presentations.find(item => item.character_id === character.id);
      const reference = element("span", match[0], "character");
      reference.tabIndex = 0; reference.setAttribute("role", "link");
      const state = snapshot?.state;
      const changed = scene.character_patches.filter(patch => patch.character_id === character.id)
        .flatMap(patch => patch.state.entries.upsert);
      reference.title = "此时的状态与心理：\n" + (recordText(state?.entries || []) || "本阶段尚无状态记录")
        + (changed.length ? "\n\n本幕变化：\n" + recordText(changed) : "")
        + "\n点击采访此时的角色（不影响正文）";
      const open = () => window.open("/character?revision=" + this.view.revision_id + "&character="
        + character.id + "&scene=" + scene.id, "_blank", "noopener");
      reference.onclick = () => { if (!window.getSelection()?.toString()) open(); };
      reference.onkeydown = event => { if (event.key === "Enter") open(); };
      parent.append(reference); offset = match.index + match[0].length;
    }
    parent.append(document.createTextNode(scene.text.slice(offset)));
  }
  select() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const startElement = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    const endElement = range.endContainer.nodeType === 1 ? range.endContainer : range.endContainer.parentElement;
    const prose = startElement.closest(".prose");
    if (!prose || prose !== endElement.closest(".prose")) { this.clearSelection(); return; }
    const prefix = range.cloneRange(); prefix.selectNodeContents(prose); prefix.setEnd(range.startContainer, range.startOffset);
    const start = Array.from(prefix.toString()).length;
    const text = range.toString();
    this.selection = { ...(prose.dataset.draft ? {generation_command_id:prose.dataset.draft} : {scene_id:prose.dataset.scene}), start, end: start + Array.from(text).length, selected_text: text };
    document.querySelector("#selection-tools").hidden = false;
  }
  clearSelection() { this.selection = null; document.querySelector("#selection-tools").hidden = true; }
  async begin() {
    if (this.busy) return toast("请等待当前任务完成。");
    await this.drafts.flush();
    this.checklist.render(); this.records.reset();
    this.draftReview.clear();
    this.interview = true; this.view = null; this.turns = []; this.messages.replaceChildren(); this.conversation.open();
    this.undoSource = null;
    this.sessionId = crypto.randomUUID(); localStorage.setItem("ainovel.setupSession", this.sessionId);
    this.navigation.show("draft", this.sessionId); this.contents.reset();
    this.drafts.open({id:this.sessionId,title:"新故事草稿",input_text:"",proposal:null,editing_setup:false,edits:[]});
    document.querySelector("#chat-input").value = "";
    await this.drafts.flush(); await this.library();
    document.querySelector("#delete-story").disabled = true;
    this.clearSelection();
    document.querySelector("#novel").replaceChildren(element("div", "先聊聊世界与人物，再确认故事设定。", "empty"));
    document.querySelector("#title").textContent = "一起构思一个新故事";
    this.busy = true;
    try {
      const result = await this.command({ kind: "interview", turns: [] }, null);
      await this.refreshUndo(this.sessionId); this.interviewResult(result.output.interview, result);
    } catch (error) { toast(error.message); }
    finally { this.busy = false; }
  }
  interviewResult(result, command = null, updateDraft = true) {
    this.turns.push({ role: "assistant", content: result.reply });
    const bubble = this.bubble(result.reply);
    this.attachUndo(bubble, command);
    this.checklist.render(result.checklist);
    if (result.proposal) bubble.append(button("查看当前设定", () => this.reviewProposal(this.drafts.current?.proposal || result.proposal)));
    if (command && updateDraft) this.drafts.update({proposal:result.proposal,edits:[],editing_setup:false,
      title:result.proposal?.title || this.drafts.current?.title || "新故事草稿"});
    this.messages.scrollTop = this.messages.scrollHeight;
  }
  async send() {
    if (this.busy) return;
    if (this.draftReview.awaitingCharacter) return toast("请先确认本次修改需要的新角色。");
    const input = document.querySelector("#chat-input"); const message = input.value.trim();
    if (!message) return;
    if (!this.interview && !this.view) await this.begin();
    this.conversation.open();
    this.bubble(message, "user"); input.value = ""; this.busy = true;
    if (this.interview) this.drafts.update({input_text:"",pending_input:message});
    document.querySelector("#chat-form button").disabled = true;
    try {
      if (this.interview) {
        this.turns.push({ role: "user", content: message });
        const view = await this.command({ kind: "interview", turns: this.turns }, null);
        this.drafts.update({pending_input:""});
        await this.refreshUndo(this.sessionId); this.interviewResult(view.output.interview, view);
      } else {
        const pending = this.draftReview.command;
        const view = pending ? await this.command({kind:"review_draft",generation_command_id:pending.output.writing_draft.generation_command_id,expected_draft_version:pending.output.writing_draft.version,decision:"revise",feedback:message}, {story:{id:pending.submission.story_id},revision_id:pending.submission.base_revision_id}) : await this.command({ kind: "generate", instruction: message });
        await this.result(view);
      }
    } catch (error) { if (!error.view) this.bubble("任务未完成：" + error.message); toast(error.message); }
    finally { this.busy = false; document.querySelector("#chat-form button").disabled = false; }
  }
  reviewProposal(proposal) {
    const body = this.dialog.open("确认故事设定");
    const title = field(body, "作品名", proposal.title);
    const world = field(body, "世界观", proposal.world.premise, true);
    const preferences = field(body, "喜欢的文风 / 情节（每行一项）", recordText(proposal.preferences.likes), true);
    const dislikes = field(body, "不喜欢的写法（每行一项）", recordText(proposal.preferences.dislikes), true);
    const characters = proposal.characters.map(character => {
      const name = field(body, "角色姓名", character.name);
      const profile = field(body, "角色档案（每行一项，可写名称：内容）", recordText(character.profile.entries), true);
      return { character, name, profile };
    });
    const fields = [
      {key:"title",index:0,input:title}, {key:"world",index:0,input:world},
      {key:"preferences",index:0,input:preferences}, {key:"dislikes",index:0,input:dislikes},
      ...characters.flatMap((item,index) => [
        {key:"name",index,input:item.name}, {key:"profile",index,input:item.profile},
      ]),
    ];
    for (const edit of this.drafts.current?.edits || []) {
      const field = fields.find(item => item.key === edit.key && item.index === edit.index);
      if (field) field.input.value = edit.value;
      else if (["background","personality"].includes(edit.key) && characters[edit.index]) {
        const input = characters[edit.index].profile;
        const label = edit.key === "background" ? "背景" : "性格";
        input.value = [...input.value.split("\n").filter(line => !line.startsWith(label + "：") && !line.startsWith(label + " · ")), ...edit.value.split("\n").filter(Boolean).map(line => label + "：" + line)].join("\n");
      }
    }
    this.drafts.update({editing_setup:true});
    body.addEventListener("input", () => this.drafts.update({
      title:title.value.slice(0,200),
      edits:fields.map(item => ({key:item.key,index:item.index,value:item.input.value})),
    }));
    body.append(element("p", "世界观需要一个基本方向；初始角色、喜好与厌恶可以暂时留空，创建后继续补充。", "hint"));
    body.append(button("确认创建", async () => {
      const payload = { kind: "create_story", confirmed: true, proposal: { ...proposal, title: title.value,
        world: {...proposal.world,premise:world.value}, preferences: {...proposal.preferences,likes:textRecords(preferences.value,proposal.preferences.likes),dislikes:textRecords(dislikes.value,proposal.preferences.dislikes)},
        characters: characters.map(item => ({ ...item.character, name: item.name.value,
          profile: { entries: textEntries(item.profile.value,item.character.profile.entries) } })),
      } };
      const result = await this.command(payload, null);
      const firstInstruction = document.querySelector("#chat-input").value;
      this.drafts.complete(this.sessionId);
      this.dialog.close(); await this.load(result.output.story_id);
      document.querySelector("#chat-input").value = firstInstruction;
      this.attachUndo(this.bubble("小说已创建。你希望第一幕发生什么？"), result);
    }, "primary"));
  }
  async result(view) {
    if (view.output.undone_command_id) {
      this.dialog.close();
      if (view.output.story_deleted) { this.resetWorkspace(); await this.library(); }
      else if (view.output.story_id) await this.load(view.output.story_id);
      else await this.restoreInterview(true);
      this.status.textContent = view.output.message; toast(view.output.message); return;
    }
    if (view.output.story_deleted) {
      if (this.view?.story.id === view.output.story_id) this.resetWorkspace();
      await this.library(); toast(view.output.message); return;
    }
    if (view.output.story_restored) {
      await this.load(view.output.story_id); toast(view.output.message); return;
    }
    if (view.output.writing_draft && !view.output.revision_id) {
      await this.refreshUndo(view.submission.story_id);
      if (view.output.writing_draft.status === "pending") this.showDraft(view);
      else { this.draftReview.clear(); this.attachUndo(this.bubble(view.output.message),view); }
      this.status.textContent = view.output.message; return;
    }
    if (view.status === "waiting") {
      if (view.submission.payload.kind === "review_draft") this.draftReview.pauseForCharacter();
      await this.refreshUndo(view.submission.story_id); this.approval(view);
      this.status.textContent = "等待你确认新角色"; return;
    }
    if (view.output.revision_id) {
      await this.load(view.output.story_id, false); await this.refreshUndo(view.output.story_id);
      if (view.output.writing_draft?.status === "confirmed") this.draftReview.clear();
      this.attachUndo(this.bubble(view.output.message), view);
    }
    this.status.textContent = "已完成 · 所有操作已保存";
  }
  resetWorkspace() {
    this.draftReview.clear();
    this.view = null; this.interview = false; this.turns = []; this.clearSelection();
    localStorage.removeItem("ainovel.story");
    document.querySelector("#title").textContent = "故事，从一个想法开始。";
    document.querySelector("#delete-story").disabled = true;
    this.messages.replaceChildren();
    document.querySelector("#directions").replaceChildren();
    document.querySelector("#novel").replaceChildren(element("div", "从书架打开小说，或创建一个新的故事。", "empty"));
    this.status.textContent = "小说已移入回收站";
    this.contents.reset(); this.navigation.show("shelf"); this.conversation.close();
  }
  async openDraft(draft) {
    if (this.busy) return toast("请等待当前操作完成。");
    await this.drafts.flush();
    const cached = this.drafts.cached(draft.id);
    this.sessionId = draft.id;
    this.drafts.open(cached && cached.updated_at > draft.updated_at ? cached : draft);
    await this.restoreInterview();
  }
  async restoreInterview(discardEdits = false) {
    this.draftReview.clear();
    this.navigation.show("draft", this.sessionId); this.contents.reset(); this.records.reset(); this.checklist.render();
    this.conversation.open();
    this.interview = true; this.view = null; this.undoSource = null;
    this.messages.replaceChildren(); this.turns = [];
    if (this.drafts.current?.id !== this.sessionId) {
      const draft = this.drafts.cached(this.sessionId) || await this.api.get("/api/drafts/" + this.sessionId);
      this.drafts.open(draft);
    }
    await this.refreshUndo(this.sessionId);
    const commands = await this.api.get("/api/sessions/" + this.sessionId + "/commands");
    this.process.setCommands(commands);
    for (const command of commands.filter(item => !item.undone && item.status === "failed")) this.showFailure(command);
    const pendingInput = this.drafts.current?.pending_input;
    if (pendingInput) {
      const accepted = commands.some(command => !command.undone && command.submission.payload.kind === "interview"
        && ["queued","running","completed"].includes(command.status)
        && command.submission.payload.turns.at(-1)?.content === pendingInput);
      this.drafts.update({pending_input:"",input_text:this.drafts.current.input_text || (accepted ? "" : pendingInput)});
    }
    const history = commands.filter(command => !command.undone && command.status === "completed" && command.output.interview);
    const last = history.at(-1);
    for (const command of history) {
      const turn = command.submission.payload.turns.at(-1);
      if (turn?.role === "user") this.bubble(turn.content, "user");
      this.interviewResult(command.output.interview, command, false);
    }
    this.turns = last ? [...last.submission.payload.turns, {role:"assistant",content:last.output.interview.reply}] : [];
    if (discardEdits) this.drafts.update({proposal:last?.output.interview.proposal || null,edits:[],editing_setup:false,
      title:last?.output.interview.proposal?.title || "新故事草稿"});
    document.querySelector("#delete-story").disabled = true;
    document.querySelector("#chat-input").value = this.drafts.current?.input_text || "";
    document.querySelector("#title").textContent = "一起构思一个新故事";
    document.querySelector("#novel").replaceChildren(element("div", "先聊聊世界与人物，再确认故事设定。", "empty"));
    this.status.textContent = "创作草稿 · 对话与输入自动保存";
    await this.library();
    if (this.drafts.current?.editing_setup && this.drafts.current.proposal) this.reviewProposal(this.drafts.current.proposal);
    const pending = commands.filter(command => !command.undone && command.submission.payload.kind === "interview"
      && ["queued","running"].includes(command.status));
    if (pending.length) {
      this.busy = true; document.querySelector("#chat-form button").disabled = true;
      const sessionId = this.sessionId;
      const turn = pending.at(-1).submission.payload.turns.at(-1);
      if (turn?.role === "user") this.bubble(turn.content,"user");
      this.status.textContent = "草稿已恢复 · AI 仍在处理这轮对话…";
      this.api.wait(pending.at(-1).submission.id, event => this.event(event)).then(async command => {
        if (sessionId !== this.sessionId) return;
        this.drafts.update({proposal:command.output.interview?.proposal || null,edits:[],editing_setup:false});
        await this.restoreInterview();
      }).catch(error => { if (error.view) this.showFailure(error.view); toast(error.message); }).finally(() => {
        this.busy = false; document.querySelector("#chat-form button").disabled = false;
      });
    }
  }
  async undo(source = this.view || this.undoSource, targetCommandId = null) {
    if (this.busy) return toast("请等待当前操作完成。");
    const scope = source?.story.id || this.sessionId;
    const state = await this.api.get("/api/undo/" + scope);
    if (!state.available) return toast(state.message);
    this.busy = true;
    try {
      const result = await this.command({kind: "undo", expected_operation_id: state.operation_id,
        target_command_id:targetCommandId}, source);
      await this.result(result);
      const oldest = await this.api.get("/api/commands/" + result.output.undone_command_id);
      if (oldest.submission.payload.kind === "create_story") {
        const created = oldest;
        if (created.submission.session_id) {
          this.sessionId = created.submission.session_id; await this.restoreInterview();
        }
      }
    } catch (error) { toast(error.message); }
    finally { this.busy = false; }
  }
  deleteStory() {
    if (!this.view) return;
    const source = this.view;
    const body = this.dialog.open("删除小说");
    body.append(element("p", "将《" + source.story.title + "》移入回收站？"));
    body.append(element("p", "正文、角色和版本历史会保留，可从回收站恢复。尚未完成的任务将取消。", "hint"));
    const actions = element("div", "", "actions");
    actions.append(button("取消", () => this.dialog.close()), button("移入回收站", async () => {
      const result = await this.command({ kind: "delete_story" }, source);
      this.dialog.close(); await this.result(result);
    }, "danger"));
    body.append(actions);
  }
  async trash() {
    const body = this.dialog.open("回收站");
    const stories = await this.api.get("/api/stories?deleted=true");
    if (!stories.length) body.append(element("p", "回收站为空。", "hint"));
    for (const story of stories) {
      const row = element("div", "", "version");
      const info = element("div", story.title);
      info.append(element("small", story.scene_count + " 个场景 · " + new Date(story.deleted_at).toLocaleString()));
      row.append(info, button("恢复小说", async () => {
        const result = await this.command({ kind: "restore_story" },
          { story: { id: story.id }, revision_id: story.revision_id });
        this.dialog.close(); await this.result(result);
      }));
      row.append(button("撤回上一步", () => this.undo({story: {id: story.id}, revision_id: story.revision_id})));
      body.append(row);
    }
  }
  async direction() {
    if (!this.view) return toast("请先创建或打开小说。");
    const input = document.querySelector("#direction-input");
    const scope = document.querySelector("#direction-scope").value;
    try {
      await this.result(await this.command({ kind: "direction", direction: {
        instruction: input.value.trim(), scope, hard: document.querySelector("#direction-hard").checked,
        chapter_id: scope === "chapter" ? (this.view.story.chapters.at(-1)?.id ?? null) : null,
      } }));
      input.value = "";
    } catch (error) { toast(error.message); }
  }
  rewrite(selection) {
    if (!selection || !this.view) return;
    if (selection.generation_command_id) {
      const {generation_command_id,...range} = selection;
      if (this.draftReview.command?.output.writing_draft.generation_command_id === generation_command_id) this.draftReview.edit(range);
      return;
    }
    const source = this.view;
    const body = this.dialog.open("讨论这段文字");
    body.append(element("pre", selection.selected_text, "prose"));
    const instruction = field(body, "你希望怎样修改？", "", true);
    body.append(button("生成修改建议", async () => {
      const result = await this.command({ kind: "rewrite", ...selection, instruction: instruction.value }, source);
      this.preview(result, source, selection, instruction.value);
    }, "primary"));
  }
  preview(command, source, selection, instruction) {
    const proposal = command.output.rewrite; const body = this.dialog.open("修改建议");
    const diff = element("div", "", "diff"); const before = element("div"); const after = element("div");
    before.append(element("small", "原文"), element("pre", proposal.original));
    after.append(element("small", "建议文本"), element("pre", proposal.replacement, "after"));
    diff.append(before, after); body.append(diff);
    this.process.attach(after, command);
    body.append(element("p", "接受会从此处建立新分支；后续场景可在版本历史中找回。", "hint"));
    const actions = element("div", "", "actions");
    actions.append(button("放弃", async () => {
      await this.command({ kind: "reject_rewrite", proposal_command_id: command.submission.id }, source);
      this.dialog.close();
    }), button("重新生成", async () => {
      const result = await this.command({ kind: "rewrite", ...selection, instruction }, source);
      this.preview(result, source, selection, instruction);
    }), button("接受修改", async () => {
      await this.result(await this.command({ kind: "accept_rewrite", proposal_command_id: command.submission.id }, source));
      this.dialog.close();
    }, "primary")); body.append(actions);
  }
  showDraft(command) {
    this.draftReview.render(command);
    this.process.attach(this.draftReview.panel, command);
    this.conversation.close();
    const bubble = this.bubble(command.output.message || "草稿已保存，修改满意后请确认提交。");
    this.attachUndo(bubble,command);
    bubble.append(button("查看草稿",() => { this.conversation.close(); this.draftReview.panel.scrollIntoView({behavior:"smooth",block:"start"}); }));
  }
  async reviewDraft(command, decision, feedback = "", selection = null) {
    if (this.busy) return toast("请等待当前操作完成。");
    this.busy = true; document.querySelector("#chat-form button").disabled = true;
    try {
      const draft = command.output.writing_draft;
      if (decision === "confirm") {
        const commands = await this.api.get("/api/stories/" + command.submission.story_id + "/commands");
        const failed = commands.filter(item => !item.undone && item.status === "failed"
          && item.submission.base_revision_id === command.submission.base_revision_id
          && item.submission.payload.kind === "review_draft" && item.submission.payload.decision === "confirm"
          && item.submission.payload.generation_command_id === draft.generation_command_id
          && item.submission.payload.expected_draft_version === draft.version).at(-1);
        if (failed) {
          this.status.textContent = "正在重试确认，复用已完成的步骤…";
          await this.api.recover(failed, "retry", event => this.event(event));
          const result = await this.api.wait(failed.submission.id, event => this.event(event));
          this.clearSelection(); await this.result(result);
          this.messages.querySelector(`[data-failed-command="${failed.submission.id}"]`)?.remove();
          return;
        }
      }
      const result = await this.command({kind:"review_draft",generation_command_id:draft.generation_command_id,expected_draft_version:draft.version,decision,feedback,selection}, {story:{id:command.submission.story_id},revision_id:command.submission.base_revision_id});
      this.clearSelection(); await this.result(result);
    } catch (error) { if (error.view) this.showFailure(error.view); throw error; }
    finally { this.busy = false; document.querySelector("#chat-form button").disabled = false; }
  }
  approval(command) {
    const output = command.output;
    const row = this.bubble(output.need?.reason || "请确认新角色档案");
    this.attachUndo(row, command);
    row.append(button(output.approval_phase === "profile" ? "查看角色档案" : "同意引入角色", async () => {
      let profile = output.character;
      if (!profile) {
        const result = await this.command({ kind: "resolve_character", generation_command_id: command.submission.id,
          decision: "consent" }, { story: { id: command.submission.story_id }, revision_id: command.submission.base_revision_id });
        profile = result.output.character;
      }
      this.characterApproval(command, profile);
    }), button("不要这个角色", async () => {
      await this.command({ kind: "resolve_character", generation_command_id: command.submission.id, decision: "reject" },
        { story: { id: command.submission.story_id }, revision_id: command.submission.base_revision_id });
      await this.result(await this.api.wait(command.submission.id, event => this.event(event)));
    }));
  }
  characterApproval(command, character) {
    const body = this.dialog.open("确认新角色档案");
    const name = field(body, "姓名", character.name);
    const aliases = field(body, "别名（逗号分隔）", character.aliases.map(alias => alias.name).join(","));
    const profile = field(body, "角色档案（每行一项，可写名称：内容）", recordText(character.profile.entries), true);
    body.append(button("确认档案，继续写作", async () => {
      await this.command({ kind: "resolve_character", generation_command_id: command.submission.id, decision: "accept",
        character: { ...character, name: name.value, aliases: textRecords(aliases.value.split(",").join("\n"),character.aliases),
          profile: { entries: textEntries(profile.value,character.profile.entries) } } },
        { story: { id: command.submission.story_id }, revision_id: command.submission.base_revision_id });
      this.dialog.close();
      await this.result(await this.api.wait(command.submission.id, event => this.event(event)));
    }, "primary"));
  }
  async versions() {
    if (!this.view) return toast("请先打开一部小说。");
    const body = this.dialog.open("版本历史 · 原版本永久保留");
    body.append(button("用当前模型重建检索索引", async () => {
      await this.result(await this.command({ kind: "reindex" }));
      this.dialog.close();
    }));
    const versions = await this.api.get("/api/stories/" + this.view.story.id + "/revisions");
    for (const revision of versions.reverse()) {
      const row = element("div", "", "version");
      const info = element("div", revision.reason);
      info.append(element("small", new Date(revision.created_at).toLocaleString() + " · " + revision.story.scenes.length
        + " 场景 · " + revision.id.slice(0, 8)));
      row.append(info, button("预览", () => {
        const preview = this.dialog.open("历史版本预览");
        for (const scene of revision.story.scenes) preview.append(element("h3", scene.title), element("div", scene.text, "prose"));
        preview.append(button("返回版本列表", () => this.versions()));
      }), button("回退", async () => {
        await this.result(await this.command({ kind: "revert", target_revision_id: revision.id }));
        this.dialog.close();
      }));
      body.append(row);
    }
  }
}

new Workspace().start().catch(error => toast(error.message));
