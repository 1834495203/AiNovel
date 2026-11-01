import type { components } from "./schema";
import { updateDefaultFieldHelp } from "../ui/fields";
import { formatApiError } from "./errors";

export type Schemas = components["schemas"];
type RequiredDeep<T> = T extends null | undefined ? T : T extends readonly (infer U)[] ? RequiredDeep<U>[] : T extends object
  ? { [K in keyof T]-?: RequiredDeep<Exclude<T[K], undefined>> } : T;
export type Story = RequiredDeep<Schemas["Story"]>;
export type StoryView = RequiredDeep<Schemas["StoryView"]>;
export type StorySummary = RequiredDeep<Schemas["StorySummary"]>;
export type CommandView = RequiredDeep<Schemas["CommandView"]>;
export type CommandSubmission = Schemas["CommandSubmission-Input"];
export type CommandPayload = CommandSubmission["payload"];
export type EventData = RequiredDeep<Schemas["Event"]>;
export type RunOverview = RequiredDeep<Schemas["RunOverview"]>;
export type ProjectHistory = RequiredDeep<Schemas["HistoryPage"]>;
export type SetupDraft = RequiredDeep<Schemas["SetupDraft-Output"]>;
export type DiscussionSession = RequiredDeep<Schemas["DiscussionSession"]>;
export type UndoState = RequiredDeep<Schemas["UndoState"]>;
export type CharacterSnapshot = RequiredDeep<Schemas["Snapshot"]>;
export type Revision = RequiredDeep<Schemas["Revision"]>;
export type PromptTemplateLibrary = RequiredDeep<Schemas["PromptTemplateLibrary"]>;
export type PromptTemplateVersion = RequiredDeep<Schemas["PromptTemplateVersion"]>;

export class ApiError extends Error {
  constructor(message: string, readonly command?: CommandView) { super(message); }
}

const streams = new Map<string, EventSource>();
const waits = new Map<string, Promise<CommandView>>();
export function closeStream(commandId: string) { streams.get(commandId)?.close(); streams.delete(commandId); waits.delete(commandId); }

export async function latestEventId(commandId: string): Promise<number> {
  let after = 0;
  while (true) {
    const events = await api.get<Schemas["EventView"][]>(`/api/commands/${commandId}/events?after=${after}`);
    if (!events.length) return after;
    const next = events.at(-1)!.id;
    if (next <= after || events.length < 100) return Math.max(after, next);
    after = next;
  }
}

async function decode<T>(response: Response): Promise<T> {
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new Error(response.ok
      ? `服务返回的数据格式不正确（HTTP ${response.status}），请查看 API 终端日志。`
      : `服务器请求失败（HTTP ${response.status}），请查看 API 终端日志。`);
  }
  if (!response.ok) {
    throw new Error(formatApiError(data, response.status));
  }
  return data as T;
}

export const api = {
  async post<T>(path: string, body: unknown): Promise<T> {
    return decode(await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
  },
  async remove(path: string): Promise<Schemas["PurgeResult"]> {
    return decode(await fetch(path, { method: "DELETE" }));
  },
  get<T>(path: string): Promise<T> {
    return fetch(path).then(response => decode<T>(response)).then(value => {
      if (path === "/api/field-templates") updateDefaultFieldHelp((value as Schemas["FieldTemplateLibrary"]).templates ?? []);
      return value;
    });
  },
  async submit(payload: CommandPayload, storyId: string | null, revisionId: string | null,
    sessionId: string | null = null): Promise<CommandView> {
    const submission: CommandSubmission = {
      id: crypto.randomUUID(), actor: "user", story_id: storyId, base_revision_id: revisionId,
      session_id: sessionId, payload,
    };
    return decode(await fetch("/api/commands", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(submission) }));
  },
  async wait(commandId: string, onEvent: (event: EventData) => void): Promise<CommandView> {
    const existing = waits.get(commandId);
    if (existing) return existing;
    const waiting = new Promise<CommandView>((resolve, reject) => {
      const source = new EventSource(`/api/commands/${commandId}/stream`);
      streams.set(commandId, source);
      source.onmessage = message => {
        try { onEvent(JSON.parse(message.data) as EventData); }
        catch (error) { source.close(); streams.delete(commandId); reject(error); }
      };
      source.addEventListener("done", async () => {
        source.close(); streams.delete(commandId); waits.delete(commandId);
        try {
          const view = await api.get<CommandView>(`/api/commands/${commandId}`);
          if (view.status === "failed") reject(new ApiError(view.error || "任务失败", view));
          else resolve(view);
        } catch (error) { reject(error); }
      });
      source.onerror = () => onEvent({ command_id: commandId, actor: "system", type: "connection.retrying" } as EventData);
    });
    waits.set(commandId, waiting);
    return waiting.finally(() => waits.delete(commandId));
  },
  async command(payload: CommandPayload, storyId: string | null, revisionId: string | null,
    sessionId: string | null, onEvent: (event: EventData) => void,
    onSubmitted?: (view: CommandView) => void): Promise<CommandView> {
    const view = await api.submit(payload, storyId, revisionId, sessionId);
    onSubmitted?.(view);
    if (["completed", "waiting"].includes(view.status)) return view;
    if (view.status === "failed") throw new ApiError(view.error || "任务失败", view);
    return api.wait(view.submission.id, onEvent);
  },
};
