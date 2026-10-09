import { button, element } from "./client.js";

/** Page navigation is presentation state; it never changes a novel revision. */
export class PageNavigation {
  constructor() {
    this.shelf = document.querySelector("#bookshelf");
    this.workspace = document.querySelector("#workspace");
  }
  show(kind, id = null, replace = false) {
    const hash = kind === "shelf" ? "" : `#${kind}/${id}`;
    if (location.hash !== hash) {
      history[replace ? "replaceState" : "pushState"](null, "", location.pathname + hash);
    }
    this.shelf.hidden = kind !== "shelf";
    this.workspace.hidden = kind === "shelf";
    document.title = kind === "shelf" ? "书架 · 纸间" : "创作工作台 · 纸间";
  }
  current() {
    const match = location.hash.match(/^#(story|draft)\/([0-9a-f-]{36})$/i);
    return match ? {kind:match[1],id:match[2]} : {kind:"shelf",id:null};
  }
}

/** Chapter/scene navigation observes the reader's own scroll area. */
export class StoryContents {
  constructor(conversation) {
    this.conversation = conversation;
    this.nav = document.querySelector("#story-contents");
    this.reader = document.querySelector(".reader");
    this.panel = document.querySelector("#contents");
    this.observer = null;
    this.collapsed = new Set();
    this.storyId = null;
    document.querySelector("#toggle-contents").onclick = () => this.toggle(!this.panel.classList.contains("contents-open"));
    document.querySelector("#close-contents").onclick = () => this.toggle(false);
    document.addEventListener("keydown", event => { if (event.key === "Escape") this.toggle(false); });
  }
  toggle(open) {
    this.panel.classList.toggle("contents-open", open);
    document.querySelector("#toggle-contents").setAttribute("aria-expanded", String(open));
  }
  reset(message = "确认设定后，这里会显示章节与场景。") {
    this.observer?.disconnect();
    this.nav.replaceChildren(element("p", message, "hint"));
    this.toggle(false);
  }
  render(story) {
    this.observer?.disconnect();
    this.nav.replaceChildren();
    if (this.storyId !== story.id) { this.collapsed.clear(); this.storyId = story.id; }
    if (!story.chapters.length) return this.reset("还没有章节，写下第一幕后就会出现目录。");
    for (const chapter of story.chapters) {
      const group = element("details", "", "contents-chapter");
      group.open = !this.collapsed.has(chapter.id);
      group.ontoggle = () => {
        if (group.open) this.collapsed.delete(chapter.id); else this.collapsed.add(chapter.id);
      };
      const summary = element("summary", chapter.title);
      const chapterLink = this.link("定位本章", "chapter-" + chapter.id, "contents-chapter-link");
      chapterLink.setAttribute("aria-label", "定位章节：" + chapter.title);
      group.append(summary, chapterLink);
      const scenes = story.scenes.filter(scene => scene.chapter_id === chapter.id);
      for (const [index, scene] of scenes.entries()) {
        group.append(this.link(`${index + 1}. ${scene.title}`, "scene-" + scene.id, "contents-scene"));
      }
      if (!scenes.length) group.append(element("p", "尚无场景", "hint"));
      this.nav.append(group);
    }
    this.observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      if (visible.length) this.activate(visible[0].target.id);
    }, {root:this.reader,rootMargin:"0px 0px -65% 0px",threshold:0});
    for (const target of this.reader.querySelectorAll(".scene")) this.observer.observe(target);
  }
  link(label, targetId, className) {
    const link = button(label, () => {
      const target = document.getElementById(targetId);
      if (!target) return;
      this.conversation.close(); this.toggle(false);
      target.scrollIntoView({behavior:"smooth",block:"start"});
      target.focus({preventScroll:true}); this.activate(targetId);
    }, className);
    link.dataset.target = targetId;
    return link;
  }
  activate(targetId) {
    for (const link of this.nav.querySelectorAll("[data-target]")) {
      const active = link.dataset.target === targetId;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "location"); else link.removeAttribute("aria-current");
    }
  }
}
