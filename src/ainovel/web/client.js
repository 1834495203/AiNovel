/** HTTP and replayable event transport, independent of the workspace view. */
export class CommandFailure extends Error {
  constructor(view) { super(view.error || "任务失败"); this.view = view; }
}

export class ApiClient {
  async get(path) {
    const response = await fetch(path);
    return this.decode(response);
  }
  async decode(response) {
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    return data;
  }
  async submit(payload, storyId = null, revisionId = null, sessionId = null, keepalive = false) {
    const body = { id: crypto.randomUUID(), story_id: storyId, base_revision_id: revisionId, session_id: sessionId, payload };
    return this.decode(await fetch("/api/commands", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), keepalive,
    }));
  }
  async wait(commandId, onEvent) {
    return new Promise((resolve, reject) => {
      const source = new EventSource("/api/commands/" + commandId + "/stream");
      source.onmessage = message => {
        try { onEvent(JSON.parse(message.data)); } catch (error) { source.close(); reject(error); }
      };
      source.addEventListener("done", async () => {
        source.close();
        try {
          const view = await this.get("/api/commands/" + commandId);
          if (view.status === "failed") reject(new CommandFailure(view));
          else resolve(view);
        } catch (error) { reject(error); }
      });
      source.onerror = () => {
        // EventSource reconnects automatically with Last-Event-ID.
        onEvent({ type: "connection.retrying" });
      };
    });
  }
  async command(payload, storyId, revisionId, onEvent, sessionId = null) {
    const view = await this.submit(payload, storyId, revisionId, sessionId);
    if (view.status === "completed" || view.status === "waiting") return view;
    if (view.status === "failed") throw new CommandFailure(view);
    return this.wait(view.submission.id, onEvent);
  }
  async recover(view, decision, onEvent = () => {}) {
    let after = 0, items;
    do { items = await this.get("/api/commands/" + view.submission.id + "/events?after=" + after); if (items.length) after = items.at(-1).id; } while (items.length === 100);
    const head = view.submission.story_id ? await this.get("/api/stories/" + view.submission.story_id) : null;
    return this.command({kind:"recover_command",target_command_id:view.submission.id,expected_event_id:after,decision},view.submission.story_id,head?.revision_id || null,onEvent,view.submission.session_id || (head ? null : view.submission.id));
  }
}

export class DialogView {
  constructor() {
    this.dialog = document.querySelector("#modal");
    this.body = document.querySelector("#modal-body");
    this.title = document.querySelector("#modal-title");
    document.querySelector("#modal-close").onclick = () => this.close();
  }
  open(title) {
    this.title.textContent = title;
    this.body.replaceChildren();
    if (!this.dialog.open) this.dialog.showModal();
    return this.body;
  }
  close() { this.dialog.close(); }
}

export function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
export function button(label, action, className = "") {
  const node = element("button", label, className);
  node.type = "button";
  node.onclick = async () => {
    node.disabled = true;
    try { await action(); } catch (error) { toast(error.message); }
    finally { node.disabled = false; }
  };
  return node;
}
export function field(parent, label, value, multiline = false) {
  const wrapper = element("label", label, "field");
  const input = document.createElement(multiline ? "textarea" : "input");
  input.value = value;
  wrapper.append(input); parent.append(wrapper); return input;
}
export function toast(message) {
  const node = document.querySelector("#toast");
  node.textContent = message; node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 8000);
}
