import { button, element, field } from "./client.js";

/** Pending prose is independent of canonical text and character states. */
export class DraftReviewPanel {
  constructor(dialog, review) {
    this.dialog = dialog; this.review = review;
    this.panel = document.querySelector("#writing-draft"); this.command = null; this.awaitingCharacter = false;
  }
  clear() { this.command = null; this.awaitingCharacter = false; this.panel.hidden = true; this.panel.replaceChildren(); }
  pauseForCharacter() { this.awaitingCharacter = true; for (const button of this.panel.querySelectorAll("button")) button.disabled = true; }
  render(command) {
    const draft = command.output.writing_draft;
    if (!draft || draft.status !== "pending") return this.clear();
    this.command = command; this.awaitingCharacter = false; this.panel.hidden = false; this.panel.replaceChildren();
    const heading = element("div", "", "chapter-heading");
    heading.append(element("h2", `待确认草稿 · 第 ${draft.version} 版`));
    this.panel.append(heading, element("p", "你可以选中文字修改，也可以在下方聊天提意见。确认后才更新正式正文、角色和设定。", "hint"), element("h3", draft.content.title));
    const prose = element("div", draft.content.text, "prose");
    prose.dataset.draft = draft.generation_command_id; this.panel.append(prose);
    const actions = element("div", "", "actions");
    actions.append(button("修改草稿", () => this.edit()), button("放弃草稿", () => this.review(command,"discard")), button("确认并提交", () => this.review(command,"confirm"),"primary"));
    this.panel.append(actions);
  }
  edit(selection = null) {
    if (!this.command) return;
    const command = this.command;
    const body = this.dialog.open(selection ? "修改草稿选区" : "修改这份草稿");
    if (selection) body.append(element("pre",selection.selected_text,"prose"));
    const feedback = field(body,"你希望怎样修改？","",true);
    body.append(button("修改并重新预览", async () => {
      await this.review(command,"revise",feedback.value,selection);
      this.dialog.close();
    },"primary"));
  }
}
