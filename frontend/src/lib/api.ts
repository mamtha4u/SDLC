export type ProjectStatus = "draft" | "running" | "waiting" | "completed" | "failed" | "paused";
export type AgentStatus = "waiting" | "working" | "needs_approval" | "done" | "failed" | "paused" | "blocked";

export interface UserPrefs { motion: "full" | "calm"; celebrate: boolean; default_budget_usd: number }
export interface User { id: string; username: string; display_name: string; theme: string; prefs?: UserPrefs; created_at?: string | null }

export interface Project {
  id: string; name: string; description: string; status: ProjectStatus; current_agent: string | null;
  current_version: string; progress: number; cost_usd: number; budget_usd: number; paused: boolean;
  archived: boolean; accent: string; last_activity: string; created_at: string; updated_at: string;
  /** the project's own theme while you're inside it (null: your account's), and its settings */
  theme?: string | null; settings?: { drift_watch?: boolean; talks?: boolean } | null;
}

export interface Agent {
  key: string; persona: string; role: string; accent: string; model: string; status: AgentStatus;
  activity: string; started_at: string | null; ended_at: string | null; retries: number;
  tokens_in: number; tokens_out: number; cost_usd: number;
  /** while working: ≈ % done and how long this kind of step usually takes (an estimate, services/progress.py) */
  progress?: number | null; expected_s?: number | null; job_started_at?: string | null;
}

export interface ProjectDetail extends Project { agents: Agent[]; versions: string[] }

export interface OrkEvent {
  id: number; project_id: string | null; type: string; agent: string | null; message: string;
  data: Record<string, unknown>; created_at: string;
}

export interface Stats { total: number; running: number; waiting: number; completed: number; failed: number; cost_usd: number }
export interface SystemInfo {
  environment: string; region: string; account: string; identity_ok: boolean; role: string | null;
  crew?: { key: string; persona: string; role: string; accent: string; model: string }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** fetch with a hard timeout. A request that hangs (e.g. held by a corporate proxy) would otherwise keep one of the
 *  browser's ~6 connections to this site forever; enough of those and every button that needs the server freezes. */
/** A file as base64 for a JSON upload. Files go as JSON, not multipart: company proxies block some file types (a `.py`)
 *  in multipart uploads before they reach the server (user, 10-04: "Echo should allow literally any kind of file"). */
export function fileToBase64(file: File): Promise<{ name: string; data: string }> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve({ name: file.name, data: String(r.result).split(",", 2)[1] ?? "" });
    r.onerror = () => reject(new ApiError(0, `Couldn't read ${file.name} from your computer`));
    r.readAsDataURL(file);
  });
}

/** POST JSON carrying files; a network or proxy refusal becomes a message that says so. */
export async function postFiles<T>(url: string, body: unknown, fallback: string): Promise<T> {
  let res: Response;
  try {
    res = await apiFetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, 120_000);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "Your network stopped the upload before it reached Orkestra (a company proxy?). Try again, or paste the file's text into the chat.");
  }
  const data = await res.json().catch(() => null);
  if (data === null) throw new ApiError(res.status, `Your network answered instead of Orkestra (HTTP ${res.status}): a company proxy may have blocked the upload.`);
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : fallback);
  return data as T;
}

export async function apiFetch(url: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<Response> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { credentials: "same-origin", ...init, signal: ctrl.signal });
  } catch (e) {
    if (ctrl.signal.aborted) throw new ApiError(0, "The server didn't answer in time. Please try again.");
    throw e;
  } finally {
    window.clearTimeout(timer);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await apiFetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail) ? data.detail.map((d: { msg: string }) => d.msg).join(", ") : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const api = {
  me: () => request<User>("GET", "/api/auth/me"),
  login: (username: string, password: string) => request<User>("POST", "/api/auth/login", { username, password }),
  register: (username: string, password: string, display_name: string) =>
    request<User>("POST", "/api/auth/register", { username, password, display_name }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  setTheme: (theme: string) => request<User>("PATCH", "/api/auth/me", { theme }),
  updateMe: (patch: { display_name?: string; prefs?: Partial<UserPrefs> }) => request<User>("PATCH", "/api/auth/me", patch),
  changePassword: (current: string, next: string) => request<void>("POST", "/api/auth/password", { current, new: next }),

  projects: (view: string, q: string, sort: string) =>
    request<Project[]>("GET", `/api/projects?${new URLSearchParams({ view, q, sort })}`),
  stats: () => request<Stats>("GET", "/api/projects/stats"),
  project: (id: string) => request<ProjectDetail>("GET", `/api/projects/${id}`),
  createProject: (name: string, description: string, budget_usd: number) =>
    request<ProjectDetail>("POST", "/api/projects", { name, description, budget_usd }),
  patchProject: (id: string, patch: Partial<Pick<Project, "name" | "description" | "archived" | "budget_usd" | "accent">>
    & { theme?: string; settings?: { drift_watch?: boolean; talks?: boolean } }) => request<Project>("PATCH", `/api/projects/${id}`, patch),
  deleteProject: (id: string, confirm: string) =>
    request<void>("DELETE", `/api/projects/${id}?${new URLSearchParams({ confirm })}`),
  pause: (id: string) => request<Project>("POST", `/api/projects/${id}/pause`),
  resume: (id: string) => request<Project>("POST", `/api/projects/${id}/resume`),
  simulate: (id: string, speed = 1) => request<{ job_id: string }>("POST", `/api/projects/${id}/simulate?speed=${speed}`),
  events: (id: string) => request<OrkEvent[]>("GET", `/api/projects/${id}/events?limit=150`),

  info: () => request<SystemInfo>("GET", "/api/system/info"),
  bedrockCheck: () => request<{ model: string; ok: boolean; reply?: string; ms?: number; error?: string }[]>(
    "POST", "/api/system/bedrock-check"),
};
