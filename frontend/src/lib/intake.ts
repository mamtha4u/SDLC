import { ApiError, apiFetch, fileToBase64, postFiles } from "./api";

export type QType = "text" | "longtext" | "code" | "select" | "number";
export interface Question {
  id: string; label: string; type: QType; required?: boolean; help?: string; example?: string; options?: string[];
}
export interface Section { id: string; title: string; icon: string; questions: Question[] }

export interface Gap { id: string; question: string; why: string; section: string; blocking: boolean; suggested_answer: string }
export interface Suggestion {
  id: string; title: string; detail: string; category: string; impact: "low" | "medium" | "high";
  question_id: string; proposed_value: string;
}
export interface Round {
  round: number; mode: "review" | "reflect"; at: string; headline: string; understanding: string; completeness: number;
  services: { service: string; purpose: string }[]; gaps: Gap[]; suggestions: Suggestion[];
  assumptions: string[]; conflicts: string[]; ready_for_signoff: boolean;
}
export interface ChatMsg {
  role: "user" | "echo" | "orion" | "note"; text: string; ts: string; filled?: string[]; quick_replies?: string[]; attachments?: string[];
  /** Orion's hand-off of a change request (or, with kind "reply", his answer to Echo) */
  cr?: string; label?: string; summary?: string; brief?: string; version?: string; kind?: "reply";
}
export interface Plan {
  summary: string; services: { service: string; purpose: string }[];
  steps: { agent: string; task: string; approval: string }[];
  risks: { risk: string; mitigation: string; evidence?: string }[]; open_points: string[];
  research?: { claim: string; finding: string; verdict: "confirmed" | "refuted" | "partly" | "unverified"; source: string }[];
  feedback_addressed?: string;
}
export interface IntakeState {
  questionnaire: { sections: Section[] };
  suggest_token: string;
  answers: Record<string, string>;
  completeness: { answered: number; total: number; required_done: number; required_total: number; missing_required: string[] };
  uploads: { name: string; kind: string; chars: number }[];
  chat: ChatMsg[];
  rounds: Round[];
  decisions: Record<string, "accepted" | "rejected">;
  gap_answers: Record<string, string>;
  status: "collecting" | "reviewing" | "signed_off" | "amending";
  active_cr: string | null;
  busy: null | "reviewing" | "chatting" | "finalizing";
  min_rounds: number;
  requirement_md: string | null;
  plan: Plan | null;
  signed_off_at: string | null;
  draft: string | null;
  last_error: { kind: string; message: string; at: string } | null;
}
export interface DraftState { busy: IntakeState["busy"]; draft: string | null; chat_len: number; rounds: number; last_error: IntakeState["last_error"] }

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

const base = (id: string) => `/api/projects/${id}/intake`;
export const intakeApi = {
  get: (id: string) => req<IntakeState>("GET", base(id)),
  draft: (id: string) => req<DraftState>("GET", `${base(id)}/draft`),
  retry: (id: string) => req<IntakeState>("POST", `${base(id)}/retry`),
  saveAnswers: (id: string, answers: Record<string, string>) =>
    req<{ answers: Record<string, string>; completeness: IntakeState["completeness"] }>("PUT", `${base(id)}/answers`, { answers }),
  chat: (id: string, message: string, attachments: string[] = []) =>
    req<IntakeState>("POST", `${base(id)}/chat`, { message, attachments }),
  review: (id: string, mode: "review" | "reflect") => req<IntakeState>("POST", `${base(id)}/review`, { mode }),
  decide: (id: string, decisions: Record<string, string>, gap_answers: Record<string, string>) =>
    req<IntakeState>("POST", `${base(id)}/decisions`, { decisions, gap_answers }),
  signoff: (id: string, accept_open_gaps = false) => req<IntakeState>("POST", `${base(id)}/signoff`, { accept_open_gaps }),
  removeUpload: (id: string, name: string) => req<IntakeState>("DELETE", `${base(id)}/uploads/${encodeURIComponent(name)}`),
  upload: async (id: string, file: File) =>
    postFiles<{ kind: "template" | "document"; name: string; filled?: number; chars?: number; state: IntakeState }>(
      `${base(id)}/upload-json`, await fileToBase64(file), "Upload failed"),
};
