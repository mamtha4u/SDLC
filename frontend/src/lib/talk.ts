import { ApiError, apiFetch, fileToBase64, postFiles } from "./api";

/** Kickoff conversations (backend agents/talk.py): each agent interviews the person who owns its phase before it works. */
export type TalkAgent = "ba" | "ta" | "tp" | "de" | "qa";
export const TALK_AGENTS: TalkAgent[] = ["ba", "ta", "tp", "de", "qa"];
/** where each conversation lives in the workspace */
export const TALK_TAB: Record<TalkAgent, string> = { ba: "mapping", ta: "design", tp: "build", de: "build", qa: "testing" };

export interface TalkMsg {
  role: "agent" | "user" | "note"; text: string; ts: string; filled?: string[]; quick_replies?: string[]; attachments?: string[];
}
export interface TalkTopic { id: string; label: string; required?: boolean; example?: string }
export interface TalkSection { id: string; title: string; questions: TalkTopic[] }
export interface TalkState {
  agent: TalkAgent; name: string; person: string; doing: string; sections: TalkSection[];
  status: "none" | "talking" | "done" | "skipped";
  chat: TalkMsg[]; answers: Record<string, string>;
  busy: null | "thinking" | "starting"; draft: string | null;
  last_error: { kind: string; message: string; at: string } | null;
  uploads: { name: string; kind: string; chars: number }[];
  done_at: string | null;
}
export interface TalkSummary { status: TalkState["status"]; busy: TalkState["busy"]; waiting: boolean; messages: number; error: boolean }
export interface TalkDraft { busy: TalkState["busy"]; draft: string | null; chat_len: number; status: TalkState["status"]; last_error: TalkState["last_error"] }

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await apiFetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : res.statusText);
  return data as T;
}

const base = (id: string) => `/api/projects/${id}/talks`;
export const talkApi = {
  overview: (id: string) => req<Record<TalkAgent, TalkSummary>>("GET", base(id)),
  get: (id: string, agent: TalkAgent) => req<TalkState>("GET", `${base(id)}/${agent}`),
  draft: (id: string, agent: TalkAgent) => req<TalkDraft>("GET", `${base(id)}/${agent}/draft`),
  message: (id: string, agent: TalkAgent, message: string, attachments: string[] = []) =>
    req<TalkState>("POST", `${base(id)}/${agent}/message`, { message, attachments }),
  start: (id: string, agent: TalkAgent) => req<TalkState>("POST", `${base(id)}/${agent}/start`),
  retry: (id: string, agent: TalkAgent) => req<TalkState>("POST", `${base(id)}/${agent}/retry`),
  removeUpload: (id: string, agent: TalkAgent, name: string) =>
    req<TalkState>("DELETE", `${base(id)}/${agent}/uploads/${encodeURIComponent(name)}`),
  upload: async (id: string, agent: TalkAgent, file: File) =>
    postFiles<{ name: string; chars: number; state: TalkState }>(`${base(id)}/${agent}/upload-json`, await fileToBase64(file), "Upload failed"),
};
