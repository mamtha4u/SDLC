import { ApiError, apiFetch } from "./api";

export interface UsageRow {
  calls: number; input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number;
  total_tokens: number; cost_usd: number;
}
export interface ModelInfo { id: string; label: string; price: { input: number; output: number; cache_read: number; cache_write: number } }
export interface CallRow {
  id: number; at: string; agent: string; model_key: string; purpose: string; input_tokens: number; output_tokens: number;
  cache_read_tokens: number; cache_write_tokens: number; cost_usd: number; duration_ms: number;
}
export interface ProjectUsage {
  budget_usd: number; totals: UsageRow; models: Record<string, ModelInfo>;
  by_agent: (UsageRow & { agent: string })[]; by_model: (UsageRow & { model_key: string })[]; calls: CallRow[];
}
export interface OverallUsage {
  days: number; totals: UsageRow; models: Record<string, ModelInfo>;
  by_project: (UsageRow & { project_id: string; name: string })[];
  by_agent: (UsageRow & { agent: string })[]; by_model: (UsageRow & { model_key: string })[];
  daily: (UsageRow & { day: string })[];
}

async function get<T>(url: string): Promise<T> {
  const res = await apiFetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : res.statusText);
  return data as T;
}

export const usageApi = {
  project: (id: string) => get<ProjectUsage>(`/api/projects/${id}/usage`),
  overall: (days: number) => get<OverallUsage>(`/api/usage?days=${days}`),
};

export const usd = (v: number) => (v >= 1 ? `$${v.toFixed(2)}` : v >= 0.01 ? `$${v.toFixed(3)}` : `$${v.toFixed(4)}`);
export const tok = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
export const cacheHit = (r: UsageRow) => {
  const inTotal = r.input_tokens + r.cache_read_tokens + r.cache_write_tokens;
  return inTotal ? r.cache_read_tokens / inTotal : 0;
};
