import { ApiClient, element, toast } from "./client.js";
import { theme } from "./theme.js";

/** Read-only projection of the same patch replay used by stage-specific character chat. */
class CharacterCards {
  constructor() { this.api = new ApiClient(); this.container = document.querySelector("#character-cards"); this.stage = document.querySelector("#card-stage"); this.request = 0; }
  async start() {
    theme.bind(document.querySelector("#theme-toggle"));
    const params = new URLSearchParams(location.search);
    const storyId = params.get("story"), revisionId = params.get("revision");
    if (!storyId && !revisionId) throw new Error("请从小说页面打开角色卡。");
    this.view = revisionId ? await this.api.get("/api/revisions/" + revisionId) : await this.api.get("/api/stories/" + storyId);
    this.revisionId = this.view.revision_id || this.view.id; this.story = this.view.story;
    document.querySelector("#cards-title").textContent = this.story.title + " · 角色卡";
    document.querySelector("#back-story").href = "/#story/" + this.story.id;
    for (const scene of this.story.scenes) {
      const chapter = this.story.chapters.find(item => item.id === scene.chapter_id);
      const option = element("option", (chapter?.title || "") + " / " + scene.title); option.value = scene.id; this.stage.append(option);
    }
    if (this.story.scenes.some(scene => scene.id === params.get("scene"))) this.stage.value = params.get("scene");
    this.stage.onchange = () => this.render().catch(error => toast(error.message));
    await this.render();
    if (params.get("character")) document.getElementById("card-" + params.get("character"))?.scrollIntoView();
  }
  sceneName(id) { return this.story.scenes.find(scene => scene.id === id)?.title || "初始设定"; }
  entries(parent, items) {
    if (!items.length) parent.append(element("p", "尚无记录", "card-empty"));
    for (const entry of items) {
      const row = element("div", "", "card-entry"); row.append(element("strong", entry.name));
      if (entry.value !== null && entry.value !== undefined) row.append(element("p", String(entry.value)));
      if (entry.description) row.append(element("p", entry.description));
      if (entry.reason) row.append(element("small", "记录依据：" + entry.reason));
      if (entry.source_scene_id) row.append(element("small", "来源：" + this.sceneName(entry.source_scene_id)));
      if (entry.used_by_character_id) row.append(element("small", "使用者：" + (this.story.characters.find(item => item.id === entry.used_by_character_id)?.name || "未知角色")));
      if (entry.children?.length) { const children = element("div", "", "entry-children"); this.entries(children, entry.children); row.append(children); }
      parent.append(row);
    }
  }
  section(card, title, items) { card.append(element("h3", title)); this.entries(card, items); }
  async render() {
    const request = ++this.request, selected = this.stage.value;
    const snapshots = await this.api.get("/api/revisions/" + this.revisionId + "/characters" + (selected ? "?scene_id=" + selected : ""));
    if (request !== this.request) return;
    this.container.replaceChildren(); const index = document.querySelector("#card-index"); index.replaceChildren();
    for (const snapshot of snapshots) {
      const character = this.story.characters.find(item => item.id === snapshot.character_id);
      const card = element("article", "", "character-card"); card.id = "card-" + character.id;
      const link = element("a", character.name); link.href = "#" + card.id; index.append(link);
      card.append(element("h2", character.name), element("small", "首次出现：" + this.sceneName(character.introduced_scene_id)));
      this.section(card, "此时的状态与心理", snapshot.state.entries);
      this.section(card, "完整档案", snapshot.profile.entries);
      this.section(card, "称呼与绰号", snapshot.aliases);
      this.section(card, "自定义记录", snapshot.custom_fields);
      card.append(element("h3", "经历"));
      if (!snapshot.experiences.length) card.append(element("p", "尚无经历记录", "card-empty"));
      for (const experience of snapshot.experiences) {
        const row = element("div", "", "card-entry"); row.append(element("strong", experience.event), element("small", "发生于：" + this.sceneName(experience.scene_id)));
        if (experience.keywords.length) row.append(element("p", "关键词：" + experience.keywords.join("、"), "experience-keywords"));
        if (experience.entries.length) this.entries(row, experience.entries); card.append(row);
      }
      const history = element("details"); history.append(element("summary", "展开逐幕变化记录"));
      const baseline = this.story.initial_snapshots.find(item => item.character_id === character.id);
      const names = new Map();
      for (const key of ["state", "profile", "aliases", "custom_fields"]) {
        const items = key === "state" || key === "profile" ? baseline?.[key]?.entries : baseline?.[key];
        names.set(key, new Map((items || []).map(item => [item.id, item.name])));
      }
      const limit = selected ? this.story.scenes.findIndex(scene => scene.id === selected) + 1 : this.story.scenes.length;
      for (const scene of this.story.scenes.slice(0, limit)) for (const patch of scene.character_patches.filter(item => item.character_id === character.id)) {
        const changes = element("div", "", "card-entry"); changes.append(element("strong", scene.title));
        if (patch.reason) changes.append(element("p", patch.reason));
        for (const [key, title] of [["state", "状态与心理"], ["profile", "档案"], ["aliases", "称呼"], ["custom_fields", "自定义记录"]]) {
          const delta = key === "state" || key === "profile" ? patch[key].entries : patch[key];
          if (delta.upsert.length) { changes.append(element("h3", title + " · 新增或更新")); this.entries(changes, delta.upsert); }
          for (const item of delta.upsert) names.get(key).set(item.id, item.name);
          for (const id of delta.remove) { changes.append(element("small", title + " · 移除「" + (names.get(key).get(id) || "旧条目") + "」")); names.get(key).delete(id); }
        }
        history.append(changes);
      }
      if (history.children.length === 1) history.append(element("p", "尚无逐幕变化记录", "card-empty"));
      card.append(history);
      const sceneId = selected || this.story.scenes.at(-1)?.id;
      if (sceneId) { const actions = element("div", "", "card-actions"), chat = element("a", "与此时的角色聊天"); chat.href = "/character?revision=" + this.revisionId + "&character=" + character.id + "&scene=" + sceneId; actions.append(chat); card.append(actions); }
      this.container.append(card);
    }
    if (!snapshots.length) this.container.append(element("p", "这个阶段还没有角色。", "hint"));
  }
}

new CharacterCards().start().catch(error => toast(error.message));
