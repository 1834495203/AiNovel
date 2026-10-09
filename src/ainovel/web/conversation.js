/** Presentation only: opening/closing conversation never changes a command or its execution. */
export class ConversationPanel {
  constructor() {
    this.panel = document.querySelector("#chat-panel");
    this.toggle = document.querySelector("#toggle-chat");
    this.messages = document.querySelector("#messages");
    this.toggle.onclick = () => this.setOpen(this.panel.hidden);
    document.querySelector("#close-chat").onclick = () => this.close();
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !this.panel.hidden && !document.querySelector("#modal").open) {
        event.preventDefault(); this.close();
      }
    });
  }
  setOpen(open) {
    this.panel.hidden = !open;
    this.toggle.setAttribute("aria-expanded", String(open));
    this.toggle.textContent = open ? "收起对话 ↓" : "创作对话 ↑";
    if (open) this.messages.scrollTop = this.messages.scrollHeight;
  }
  open() { this.setOpen(true); }
  close() { this.setOpen(false); this.toggle.focus(); }
}
