import { normalizeProposal } from "./records.js";
/** Local write-through cache + durable autosave commands; dialogue remains in the command log. */
export class SetupDraftController {
  constructor(api) {
    this.api = api; this.current = null; this.timer = null; this.lastSubmitted = null;
    window.addEventListener("pagehide", () => this.flush(true));
  }
  cached(id) {
    if (!id) return null;
    try { return JSON.parse(localStorage.getItem("ainovel.draft." + id)); }
    catch { return null; }
  }
  open(draft) {
    clearTimeout(this.timer);
    this.current = structuredClone(draft);
    this.current.proposal = normalizeProposal(this.current.proposal || this.current.setup);
    delete this.current.setup; this.lastSubmitted = null;
    this.update({});
  }
  update(fields) {
    if (!this.current) return;
    Object.assign(this.current, fields, {updated_at: new Date().toISOString()});
    localStorage.setItem("ainovel.draft." + this.current.id, JSON.stringify(this.current));
    localStorage.setItem("ainovel.setupSession", this.current.id);
    localStorage.setItem("ainovel.mode", "draft");
    clearTimeout(this.timer); this.timer = setTimeout(() => this.flush(), 650);
  }
  async flush(keepalive = false) {
    clearTimeout(this.timer);
    if (!this.current) return;
    const draft = structuredClone(this.current);
    const signature = JSON.stringify(draft);
    if (signature === this.lastSubmitted) return;
    try {
      await this.api.submit({kind:"save_setup_draft", draft}, null, null, draft.id, keepalive);
      this.lastSubmitted = signature;
    } catch {
      // The immediate local copy survives refresh/offline; opening it retries the durable save.
      this.lastSubmitted = null;
    }
  }
  complete(id) {
    clearTimeout(this.timer);
    localStorage.removeItem("ainovel.draft." + id);
    if (this.current?.id === id) this.current = null;
  }
}
