import { ApiError, apiFetch, fileToBase64, postFiles } from "./api";

export interface Approval {
  id: string; stage: "plan" | "mapping" | string; agent: string; title: string; summary: string; artifacts: string[];
  status: "pending" | "approved" | "changes_requested"; comment: string | null; created_at: string;
  decided_at: string | null; next_label: string; next_ready?: boolean; next_agent?: string | null;
  /** the work after this step already exists: you may accept it without redoing anything ("nothing to rebuild") */
  can_accept?: boolean;
}

export interface MappingRow {
  target: string; target_sample: string; target_moc: "M" | "C" | "O"; target_type: string; logic: string;
  rule_kind?: "copy" | "constant" | "derived";
  source_path: string; source_sample: string; source_moc: string; source_type: string; pii: boolean; comments: string;
}
export interface MappingSample { name: string; description: string; input: string; expect: "output" | "reject"; expected: string }
/** One worked example checked against the mapping table (`verified` false = a value rule left for Dev/Quinn). */
export interface Proof { name: string; expect: string; pass: boolean; actual: string; verified?: boolean; checked?: number }
export interface Mapping {
  title: string; summary: string; direction: string;
  source: { system: string; message_name: string; format: string; description: string };
  target: { system: string; message_name: string; format: string; description: string };
  rows: MappingRow[]; rules: { rule: string; condition: string; action: string }[]; samples: MappingSample[];
  assumptions: string[]; queries: { question: string; owner: string }[]; changes: string;
  proof: Proof[]; check_attempts?: number;
  /** only in mappings written before Atlas stopped writing code */
  transform_code?: string; sandbox_runs?: number;
}

export interface ArchNode { id: string; label: string; sub: string; icon: string; details?: string[]; under?: string }
export interface Design {
  summary: string; hld_markdown: string; lld_markdown: string; changes: string; open_points: string[];
  architecture: { title: string; subtitle: string; sources: ArchNode[]; path: ArchNode[]; destinations: ArchNode[]; support: ArchNode[];
    edges: { from: string; to: string; label: string; kind: string }[] };
  decisions: { decision: string; why: string; alternatives: string }[];
  resources: { name: string; type: string; purpose: string; key_settings: string }[];
  quality_gates?: { min_coverage_percent: number; rules: string[]; set_by?: string };
}
export interface AssistantMsg { id: number; role: "user" | "assistant"; text: string; refs: string[]; status: "streaming" | "done" | "error"; created_at: string }

export interface AgentRunState { status: string; activity: string; started_at: string | null; cost_usd: number }
export interface TestResult { id: string; name: string; outcome: "passed" | "failed" | "error" | "skipped"; message: string; time: number }
export interface Bug {
  id: string; status: "open" | "fixed"; severity: "critical" | "major" | "minor"; title: string; test_id: string;
  steps: string; expected: string; actual: string; found_in?: string; fixed_in?: string; check_id?: string; live?: boolean;
}
/** One agent's IAM role for one project, drafted by Orion (policy only in /access). */
export interface AccessRole { agent: string; persona: string; role: string; arn: string; purpose: string; can: string[]; cannot: string[]; policy?: unknown }
export interface AccessPlan {
  status: "proposed" | "active" | "blocked" | "removed"; prefix: string | null; services: string[]; unsupported: string[];
  state_bucket: string; region: string; boundary: string; platform_role: string; problem: string | null; version: string;
  roles: AccessRole[]; drafted_at: string; granted_at?: string;
}
export type PlanAction = "create" | "update" | "replace" | "delete";
export interface DeployIntent {
  reason: "build" | "change" | "tickets" | "packages" | "layers" | "restore"; tickets: string[]; tickets_labels?: string[]; version: string; changes: string; summary?: string;
  handover?: { code: HandOverItem[]; layers: HandOverItem[] };
}
/** One package Dev handed to Terra for a plan: `from` is what's live before the apply ("placeholder" or a version). */
export interface HandOverItem { key: string; kb: number; version: string; from: string; function_name?: string; source_dir?: string; name?: string }
export interface DeployState {
  status: "planned" | "deployed" | "partial" | "destroyed" | "destroy_failed";
  plan?: { version: string; role: string; counts: Record<PlanAction, number>; planned_at: string; reason?: string; changes_note?: string;
    handover?: { code: HandOverItem[]; layers: HandOverItem[] } | null;
    changes: { address: string; type: string; name: string; action: PlanAction; fields?: string[] }[]; layers?: { layer: string; zip: string; kb: number; cached: boolean }[] } | null;
  outputs?: Record<string, unknown>; applied_at?: string; destroyed_at?: string; version?: string; error?: string | null;
  intent?: DeployIntent | null; last_intent?: DeployIntent | null; applies?: number;
}
/** What Dev deployed into Terra's functions (deploy/code.json). */
/** One hop of Dev's "repeat my test in the AWS console" walkthrough. */
export interface TryIt { title: string; link: string; steps: string; input: string; expect: string }
export interface CodeDeploy {
  status: string; version: string; role: string; deploys: number; live_ok?: number; deployed_at: string;
  /** "terra": Terra deployed Dev's packages with Terraform (10-03); "dev": Dev uploaded the code himself (older projects) */
  code_by?: "terra" | "dev"; layers_by?: "terra" | "dev";
  functions: Record<string, { key: string; source_dir: string; sha: string; kb: number; layers: string[]; handler: string; runtime?: string; code_version: string; deployed_at?: string; by?: string }>;
  layers: Record<string, { key: string; folder?: string; arn: string; version: number; kb: number; published_at?: string; code_version: string; by?: string }>;
  sanity?: { passed: boolean; summary: string; steps: { what: string; how?: string; observed: string; ok: boolean }[]; calls: number; version: string;
    sent?: string; received?: string; left_for_user?: string; try_it?: TryIt[] };
}
/** Dev's packages on their way to AWS: Dev packs → Archie reviewed → Terra plans and applies → live (user, 10-03). */
export interface HandOverPackage {
  key: string; status: "live" | "with_terra"; kb?: number; version?: string; handed_at?: string; applied_version?: string; applied_at?: string;
  adopted?: boolean; function_name?: string; source_dir?: string; name?: string; uri?: string;
}
export interface HandOver {
  mode: "terra" | "layers" | "dev"; code: HandOverPackage[]; layers: HandOverPackage[]; images?: HandOverPackage[]; pending: boolean; planned: boolean;
  plan: { counts: Record<PlanAction, number>; planned_at: string; version: string } | null;
}
export interface LiveQa {
  summary: string; version: string; deployed_version: string; calls: number; role: string; left_for_user?: string;
  checks: { id: string; title: string; passed: boolean; evidence: string; did?: string; expected?: string }[];
  bugs?: Bug[]; created?: string[]; closed?: string[]; reopened?: string[];
  open?: { label: string; title: string; assignee: string; severity: string; status: string }[];
}

/* ── tickets ─────────────────────────────────────────────────────────────── */
export type TicketStatus = "open" | "in_progress" | "resolved" | "closed" | "reopened";
export interface TicketComment { id: number; author: string; kind: "created" | "comment" | "status" | "assign"; text: string; created_at: string }
export interface Ticket {
  id: string; label: string; title: string; description: string; steps: string; expected: string; actual: string;
  severity: "critical" | "major" | "minor"; area: "code" | "infra" | "design" | "other"; status: TicketStatus;
  assignee: string; reporter: string; check_id: string | null; version_found: string | null; version_fixed: string | null;
  created_at: string; updated_at: string; comments?: TicketComment[]; routed?: string;
}
export interface TicketBoard { tickets: Ticket[]; counts: Record<TicketStatus, number>; active: number }
export interface NewTicket { title: string; description: string; steps: string; expected: string; actual: string; severity: Ticket["severity"]; area: Ticket["area"]; assignee: string }

/* ── the AWS page ────────────────────────────────────────────────────────── */
export interface Setting { key: string; value: unknown; kind: "string" | "number" | "bool" | "json"; desc: string; editable?: boolean }
export interface AwsResource {
  address: string; type: string; kind: string; service: string; service_label: string; name: string; primary: boolean; console: string | null;
  set: Setting[]; defaults: Setting[]; facts: Setting[]; drift?: string[]; code?: { placeholder?: boolean; sha?: string; kb?: number; code_version?: string; deployed_at?: string; layers?: string[]; handler?: string };
}
export interface Inventory {
  version: string | null; resources: AwsResource[]; services: { key: string; label: string; count: number; primary: number }[];
  count: number; settings: number; applied_at?: string; refreshed_at?: string; drifted?: number; tags: Record<string, string>;
}
/** The tech stack, read from the project's files (Design page). */
export interface TechStackInfo {
  language: { name: string; version: string; from: string } | null;
  runtime: { lambda: string[]; arch: string | null; memory_mb: number[]; timeout_s: number[]; from: string } | null;
  packages: { name: string; version: string; where: string; from: string }[];
  imports: { third_party: string[]; aws_sdk: string[]; own: string[]; stdlib: string[]; undeclared: string[] };
  tests: { name: string; what: string; from: string }[];
  services: string[];
  iac: { tool: string; version: string | null; providers: { name: string; version: string }[]; from: string } | null;
  gates: { coverage: number | null; rules: string[] };
  ready: { infra: boolean; code: boolean };
}
/** Terra's drift check (10-03): AWS compared with the Terraform state and Dev's packages. */
export interface DriftReport {
  checked_at: string; auto: boolean; mode: "terra" | "dev"; clean: boolean; status: "clean" | "found" | "restored" | "partly" | "keeping" | "left";
  settings: { address: string; type: string; name: string; fields: { key: string; terraform: string; aws: string; lines?: string[] }[] }[];
  deleted: { address: string; type: string; name: string }[];
  code: { function_name: string; key: string; expected_version?: string; expected_sha: string; live_sha: string; last_modified?: string;
    files: { file: string; change: string; diff: string }[] }[];
  restore: { counts: Record<PlanAction, number>; changes: { address: string; type: string; name: string; action: PlanAction; fields?: string[] }[] } | null;
  total: number; unchanged: number; restored?: { at: string; line: string }; kept?: { at: string; as: string[]; note: string };
  left_at?: string; error?: string; error_at?: string;
}
export interface InfraState {
  drift?: DriftReport | null; watch_minutes?: number;
  status: "none" | DeployState["status"]; applied_at: string | null; version: string | null; outputs: Record<string, unknown>; error: string | null;
  plan: DeployState["plan"]; intent: DeployIntent | null; inventory: Inventory | null; code: CodeDeploy | null; prefix: string | null; region: string;
  busy: string | null; can_change: boolean; why: string | null; changes: ChangeRequest[]; refreshing?: boolean;
}
export interface SettingEdit { address: string; key: string; to: unknown }

/* ── the workbench: an agent's behind-the-scenes work, live ──────────────── */
export interface WorkEvent {
  n: number; ts: string; phase: "turn" | "say" | "call" | "result" | "tests" | "deploy"; turn?: number; tool?: string;
  title: string; detail: string; error: boolean; paths?: string[];
  tests?: { run: number | string; total: number; passed: number; failed: number; error: number; coverage: number | null };
}
export interface TestRun {
  ts: string; run: number | string; total: number; passed: number; failed: number; error: number; coverage: number | null; output: string;
  files: Record<string, { percent: number; missing: number[] }>; tests: { id: string; outcome: string; message: string }[];
}
export interface Workbench {
  agent: string; total: number; runs: { index: number; started: string; purpose: string }[]; run: { index: number; started: string; purpose: string } | null;
  events: WorkEvent[]; files: Record<string, { content?: string; deleted?: boolean; step: number; ts: string }>; tests: TestRun[];
  state: { status: string; activity: string; started_at: string | null } | null;
}

/* ── what it costs ───────────────────────────────────────────────────────── */
export interface PriceTier { from: number; to: number | null; usd: number }
export interface Prices {
  region: string; fetched_at: string; stale?: string; snapshot?: boolean;
  sources: Record<string, { url: string; published: string }>;
  prices: Record<string, { tiers: PriceTier[]; unit: string; description: string }>;
}
export interface CostResource {
  address: string; type: string; name: string; kind: string; service: string;
  model: "lambda" | "sqs" | "apigw_rest" | "apigw_http" | "logs" | "alarm" | "sns" | "free" | "unpriced";
  params: { memory_mb?: number; arch?: string; timeout_s?: number; fifo?: boolean; dlq?: boolean; retention_days?: number };
  terra_usd: number | null;
}
export interface CostModel { resources: CostResource[]; from: "aws" | "terraform"; terra_total: number; prices: Prices }
/** Resource names (the Names tab): prefix + one editable name per resource. */
export interface NamingState {
  editable: boolean; reason: string; prefix: string | null; default_prefix?: string | null; deployed: boolean;
  items: { key: string; name: string; full: string; kind: string; default: string }[];
  /** the user's convention, set before Terra writes the infrastructure (Archie and Terra build with it) */
  convention?: { prefix: string; pattern: string; by: string; at: string } | null;
}
export interface AwsCall { ts: string; agent: string; role: string; action: string; target: string; ok: boolean; detail: string }
export interface AccessInfo {
  platform: { role: string; arn: string; policy: unknown; can: string[]; cannot: string[] };
  boundary: { arn: string; policy: unknown }; plan: AccessPlan | null; deploy: DeployState | null; calls: AwsCall[];
  extras?: Record<string, PolicyExtra>;
}
export interface BuildState {
  infra: {
    state: AgentRunState | null;
    preview: { summary: string; version: string; files: string[]; notes: string[]; changes: string;
      resources: { address: string; type: string; name: string; settings: string; tags: string; monthly_usd: number }[];
      validate: { available: boolean; ok: boolean; diagnostics: string[]; reformatted?: string[]; note?: string } } | null;
  };
  deploy: { orion: AgentRunState | null; access: AccessPlan | null; draft?: AccessPlan | null; state: DeployState | null; live: LiveQa | null; code?: CodeDeploy | null };
  handover?: HandOver;
  tickets?: { active: number; resolved: number; closed: number; total: number };
  code: {
    state: AgentRunState | null; gates: { min_coverage_percent: number; rules: string[]; set_by?: string };
    data: { summary: string; notes: string[]; changes: string; files: string[]; version: string; test_runs: number; coverage: number;
      coverage_gate: number; fixed_bugs: string[]; reports?: string[];
      tests: { total: number; passed: number; failed: number; error: number; tests: TestResult[];
        coverage: { percent: number; files: Record<string, { percent: number; missing: number[] }> } | null } | null } | null;
    reviewer?: AgentRunState | null; review?: { verdict: "approve" | "changes"; round: number; version: string; at: string; must: number } | null;
  };
  qa: {
    state: AgentRunState | null;
    data: { summary: string; version: string; runs: number; plan: { id: string; title: string; type: string; expected: string }[];
      results: Record<string, { outcome: string; test: string } | null>; bugs: Bug[];
      counts: { total: number; passed: number; failed: number; error: number } } | null;
  };
}

export interface DesignState {
  status: string; activity: string; cost_usd: number; started_at: string | null; design: Design | null; drawio: string | null; editor_url: string | null;
}

/** One line in the crew room: what an agent told another agent (or the whole crew). */
export interface CrewMsg {
  id: number; sender: string; to: string; text: string; created_at: string;
  kind: "handoff" | "ack" | "assign" | "question" | "answer" | "update" | "work" | "think" | "chat" | "issue" | "fix" | "done" | "decision" | "request";
  data: { files?: string[]; version?: string; cr?: string; ticket?: string; backfilled?: boolean | number };
}
export interface MappingState { status: string; activity: string; cost_usd: number; mapping: Mapping | null }

export interface ChangeRequest {
  id: string; label: string; text: string; attachments: string[]; source: string;
  status: "triage" | "clarifying" | "planning" | "in_progress" | "reviewing" | "done";
  route: "requirement" | "plan" | "mapping" | "design" | "infra" | "code" | "tests" | null;
  triage: { summary: string; route: string; reason: string; brief: string; affected_agents: { agent: string; why: string }[]; needs_from_user: string[];
    // Orion's impact review of an infrastructure change
    review?: boolean; verdict?: "infra_only" | "affects_code" | "requirement_change"; recommendation?: "go" | "confirm" | "advise_against";
    risks?: { risk: string; severity: "low" | "medium" | "high"; mitigation: string }[]; affected?: { agent: string; what: string }[]; questions?: string[] } | null;
  version_from: string | null; version_to: string | null; diff: string | null; created_at: string; updated_at: string;
}
export interface ConvoEntry { ts: string; who: "user" | "agent" | "orion" | "peer" | "work"; title: string; body: string; sender?: string }
export interface AgentDetailData {
  agent: string; stage: string | null; version: string;
  state: { status: string; activity: string; tokens_in: number; tokens_out: number; cost_usd: number; retries: number; started_at: string | null; ended_at: string | null } | null;
  approvals: { id: string; title: string; summary: string; status: string; comment: string | null; created_at: string; decided_at: string | null; stage: string }[];
  changes: ChangeRequest[];
  files: { path: string; size: number; modified: string }[];
  events: { id: number; type: string; message: string; created_at: string; data: Record<string, unknown> }[];
  conversation: ConvoEntry[];
}

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

export const flowApi = {
  approvals: (pid: string) => req<Approval[]>("GET", `/api/projects/${pid}/approvals`),
  decide: (pid: string, aid: string, decision: "approve" | "changes" | "accept", comment = "") =>
    req<Approval>("POST", `/api/projects/${pid}/approvals/${aid}`, { decision, comment }),
  mapping: (pid: string) => req<MappingState>("GET", `/api/projects/${pid}/mapping`),
  changes: (pid: string) => req<ChangeRequest[]>("GET", `/api/projects/${pid}/changes`),
  agent: (pid: string, agent: string) => req<AgentDetailData>("GET", `/api/projects/${pid}/agents/${agent}`),
  crew: (pid: string) => req<CrewMsg[]>("GET", `/api/projects/${pid}/crew`),
  retryAgent: (pid: string, agent: string) => req<{ queued: string }>("POST", `/api/projects/${pid}/agents/${agent}/retry`),
  continuePipeline: (pid: string) => req<{ started: string }>("POST", `/api/projects/${pid}/approvals/continue`),
  design: (pid: string) => req<DesignState>("GET", `/api/projects/${pid}/design`),
  uploadDiagram: async (pid: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await apiFetch(`/api/projects/${pid}/design/diagram`, { method: "POST", body: fd }, 60_000);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : "Upload failed");
    return data as { summary: string; diff: Record<string, string[]> };
  },
  applyDiagram: (pid: string, note: string) => req<{ queued: boolean; summary: string }>("POST", `/api/projects/${pid}/design/diagram/apply`, { note }),
  build: (pid: string) => req<BuildState>("GET", `/api/projects/${pid}/build`),
  access: (pid: string) => req<AccessInfo>("GET", `/api/projects/${pid}/access`),
  naming: (pid: string) => req<NamingState>("GET", `/api/projects/${pid}/naming`),
  setNaming: (pid: string, prefix: string, names: Record<string, string>) =>
    req<{ state: NamingState; renamed: [string, string][] }>("PUT", `/api/projects/${pid}/naming`, { prefix, names }),
  stack: (pid: string) => req<TechStackInfo>("GET", `/api/projects/${pid}/design/stack`),
  setConvention: (pid: string, prefix: string, pattern: string) =>
    req<NamingState>("PUT", `/api/projects/${pid}/naming/convention`, { prefix, pattern }),
  teardown: (pid: string, confirm: string) => req<{ job_id: string }>("POST", `/api/projects/${pid}/deploy/teardown`, { confirm }),
  tickets: (pid: string) => req<TicketBoard>("GET", `/api/projects/${pid}/tickets`),
  ticket: (pid: string, tid: string) => req<Ticket>("GET", `/api/projects/${pid}/tickets/${tid}`),
  newTicket: (pid: string, t: NewTicket) => req<Ticket>("POST", `/api/projects/${pid}/tickets`, t),
  updateTicket: (pid: string, tid: string, patch: { status?: TicketStatus; assignee?: string; severity?: string; note?: string }) =>
    req<Ticket>("PATCH", `/api/projects/${pid}/tickets/${tid}`, patch),
  commentTicket: (pid: string, tid: string, text: string) => req<Ticket>("POST", `/api/projects/${pid}/tickets/${tid}/comments`, { text }),
  infra: (pid: string) => req<InfraState>("GET", `/api/projects/${pid}/infra`),
  refreshInfra: (pid: string) => req<{ job_id: string }>("POST", `/api/projects/${pid}/infra/refresh`),
  cost: (pid: string) => req<CostModel>("GET", `/api/projects/${pid}/infra/cost`),
  workbench: (pid: string, agent: string, since = 0, run?: number) =>
    req<Workbench>("GET", `/api/projects/${pid}/agents/${agent}/workbench?since=${since}${run !== undefined ? `&run=${run}` : ""}`),
  infraChanges: (pid: string, changes: SettingEdit[], note: string, renames?: { prefix: string; names: Record<string, string> }) =>
    req<ChangeRequest & { edits: { address: string; key: string; from: unknown; to: unknown }[]; renamed: [string, string][] }>(
      "POST", `/api/projects/${pid}/infra/changes`, { changes, note, ...(renames ? { renames } : {}) }),
  assistant: (pid: string) => req<AssistantMsg[]>("GET", `/api/projects/${pid}/assistant`),
  ask: (pid: string, question: string) => req<AssistantMsg[]>("POST", `/api/projects/${pid}/assistant`, { question }),
  clearAssistant: (pid: string) => apiFetch(`/api/projects/${pid}/assistant`, { method: "DELETE" }),
  setGates: (pid: string, min_coverage_percent: number) =>
    req<{ min_coverage_percent: number }>("PUT", `/api/projects/${pid}/design/gates`, { min_coverage_percent }),
  files: (pid: string, version?: string) =>
    req<{ versions: string[]; current: string; files: { path: string; size: number; modified: string }[] }>(
      "GET", `/api/projects/${pid}/files${version ? `?version=${version}` : ""}`),
  fileAt: async (pid: string, path: string, version?: string) =>
    (await apiFetch(`/api/projects/${pid}/files/content?path=${encodeURIComponent(path)}${version ? `&version=${version}` : ""}`)).text(),
  raiseChange: async (pid: string, text: string, files: File[], approvalId?: string) =>
    postFiles<ChangeRequest>(`/api/projects/${pid}/changes/json`,
      { text, approval_id: approvalId ?? null, files: await Promise.all(files.map(fileToBase64)) }, "Couldn't send"),
  fileText: async (pid: string, path: string) =>
    (await apiFetch(`/api/projects/${pid}/files/content?path=${encodeURIComponent(path)}`)).text(),
  codeReview: (pid: string) => req<CodeReviewState>("GET", `/api/projects/${pid}/code-review`),
  askArchie: (pid: string, question: string) => req<{ chat: ReviewChat[] }>("POST", `/api/projects/${pid}/code-review/ask`, { question }),
  startCodeReview: (pid: string) => req<{ job_id: string }>("POST", `/api/projects/${pid}/code-review/start`),
  testing: (pid: string) => req<TestingState>("GET", `/api/projects/${pid}/testing`),
  newTestCycle: (pid: string) => req<{ job_id: string }>("POST", `/api/projects/${pid}/testing/cycle`),
  signoffs: (pid: string) => req<SignoffDoc[]>("GET", `/api/projects/${pid}/signoffs`),
  /** Your change to an agent's policy: apply=false only checks it (Orion's rules) and returns the resulting policy. */
  policyChange: async (pid: string, agent: string, statements: unknown[], reason: string, apply: boolean) => {
    const res = await apiFetch(`/api/projects/${pid}/access/${agent}/extra`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ statements, reason, apply }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      return { ok: false as const, problems: Array.isArray(d?.problems) ? (d.problems as string[]) : [typeof d === "string" ? d : "Couldn't check it"] };
    }
    return { ok: true as const, statements: data.statements as PolicyStatement[], policy: data.policy as { Statement: PolicyStatement[] } };
  },
};

/** Archie's review of Dev's code, and the user's conversation with him about it, before the deploy. */
export interface CodeReview {
  verdict: "approve" | "changes"; summary: string; version: string; round: number; at: string;
  checks: { area: string; item: string; ok: boolean; note: string }[];
  findings: { severity: "must" | "should" | "nice"; file: string; issue: string; fix: string }[];
  design_notes: { topic: string; now: string; recommendation: string; why: string }[];
  /** Archie asks you (10-05): where the code could go either way, and improvements to accept or skip */
  choices?: { topic: string; question: string; options: string[]; current: string; recommended: string; why: string }[];
  suggestions?: { title: string; detail: string; example: string; benefit: string }[];
  for_user: string[]; files: string[]; tests: { passed?: number; total?: number; coverage?: number; gate?: number };
  history: { round: number; at: string; verdict: string; must: number; summary: string }[];
}
export interface ReviewChat { role: "user" | "archie"; text: string; at: string; by?: string; error?: boolean }
export interface CodeReviewState {
  review: CodeReview | null; chat: ReviewChat[]; round: number | null; version: string | null;
  pending: { id: string; title: string } | null; busy: string | null; state: AgentRunState | null;
}

/** Quinn's test plan: the scenarios the user approves as test lead before any test runs. */
export interface TestCase {
  id: string; title: string; category: string; priority: "high" | "medium" | "low"; requirement_ref: string;
  preconditions: string; steps: string; test_data: string; expected: string;
  /** plain words (plans since 10-05): what Quinn does, and one concrete before → after */
  flow?: string; example?: string;
}
/** Quinn's live run so far (backend qa.record_scenario), scenario by scenario */
export interface QaProgress {
  version: string; total: number; current: string | null; done: number; passed: number; failed: number; updated_at: string;
  results: { id: string; title: string; status: "running" | "passed" | "failed" | "not_run"; did: string; saw: string }[];
}
export interface TestPlan {
  version: string; status: "proposed" | "approved"; approved_at?: string; approval_comment?: string; written_at?: string;
  summary: string; scope: string; approach: string; out_of_scope: { what: string; why: string }[];
  entry_criteria: string[]; exit_criteria: string[]; cases: TestCase[]; changes?: string;
}
export interface QaRun {
  run: number; at: string; version: string; deployed_version?: string; kind: "full" | "retest"; passed: number; failed: number;
  not_run: number; created: string[]; closed: string[]; reopened: string[]; checks: { id: string; passed: boolean }[]; calls: number;
}
export interface TestingState {
  state: AgentRunState | null; plan: TestPlan | null; plan_current: boolean;
  live: (LiveQa & { not_run?: { id: string; why: string }[]; risks?: string[]; signoff?: string }) | null; runs: QaRun[];
  progress?: QaProgress | null;
  tickets: { id: string; label: string; title: string; severity: string; area: string; status: TicketStatus; assignee: string; reporter: string;
    check_id: string | null; version_found: string | null; version_fixed: string | null }[];
  signoff: string | null; deployed: boolean; sanity?: boolean; pending: { stage: string; id: string; title: string } | null;
  signed_off: { at: string; comment: string | null } | null;
}
export interface SignoffDoc { stage: string; order: string; agent: string; title: string; path: string }
export interface PolicyStatement { Sid?: string; Effect: "Allow" | "Deny"; Action: string | string[]; Resource: string | string[]; Condition?: unknown }
export interface PolicyExtra { statements: PolicyStatement[]; reason: string; by: string; at: string; history: { at: string; by: string; reason: string; count: number }[] }

/** The name a file is stored under in inputs/ (same rule as the backend), and its download link. */
export const storedName = (name: string) => name.replace(/[^A-Za-z0-9._ -]+/g, "_").slice(0, 120);
export const fileUrl = (pid: string, name: string) =>
  `/api/projects/${pid}/files/content?path=${encodeURIComponent(`inputs/${storedName(name)}`)}&download=1`;

/** Which workspace tab shows each stage / agent. */
export type StageTab = "requirement" | "mapping" | "design" | "build" | "code" | "aws" | "testing" | "tickets";
export const STAGE_TAB: Record<string, StageTab> = {
  plan: "requirement", mapping: "mapping", design: "design", infra: "build", deploy: "aws", infra_check: "aws", change_review: "aws", code_review: "build", code: "build",
  test_plan: "testing", qa: "testing", qa_bugs: "testing", access: "build", live: "testing", live_bugs: "tickets", drift: "aws",
};
export const AGENT_TAB: Record<string, StageTab | undefined> = {
  intake: "requirement", cto: "requirement", ba: "mapping", ta: "design", tp: "build", de: "build", qa: "testing",
};
/** Archie or Orion helping a stuck or slow teammate (backend agents/unblock.py, agents/watch.py): whom ("de" | "tp"), else
 *  null. The help belongs on that teammate's step, not on Archie's design (10-05: "it looks like the flow went back to
 *  Design and started a new design"). */
export function helpingWho(a: { key: string; status: string; activity: string }): "de" | "tp" | null {
  if (a.status !== "working" || (a.key !== "ta" && a.key !== "cto")) return null;
  const m = /(?:helping|unblock|over) (Dev|Terra)/i.exec(a.activity);
  return m ? (m[1].toLowerCase() === "dev" ? "de" : "tp") : null;
}
/** Who can hold a ticket, and how they show on the board. */
export const ASSIGNEES: { key: string; name: string; role: string; accent: string }[] = [
  { key: "de", name: "Dev", role: "code", accent: "blue" }, { key: "tp", name: "Terra", role: "infrastructure", accent: "orange" },
  { key: "qa", name: "Quinn", role: "retest", accent: "rose" }, { key: "ta", name: "Archie", role: "design (change request)", accent: "amber" },
  { key: "ba", name: "Atlas", role: "mapping (change request)", accent: "emerald" }, { key: "intake", name: "Echo", role: "requirement (change request)", accent: "cyan" },
  { key: "cto", name: "Orion", role: "triage (change request)", accent: "violet" }, { key: "user", name: "You", role: "", accent: "violet" },
];

/** Who owns which part of the project's codebase (for the Code view's owner badges). */
export function ownerOf(path: string): string {
  const rules: [RegExp, string][] = [
    [/^inputs\//, "user"], [/^00_requirement/, "intake"], [/^(plan\.md|changes\/|reports\/aws_access)/, "cto"],
    [/^(01_data_mapping|mapping\/)/, "ba"], [/^(02_hld|03_lld|diagrams\/)/, "ta"], [/^(infra\/|reports\/infra|reports\/deploy|reports\/aws_inventory)/, "tp"],
    [/^(src\/|tests\/|layers\/|reports\/pytest|reports\/build|reports\/code_deploy)/, "de"], [/^(reports\/qa|reports\/live_qa|bugs\/|qa\/)/, "qa"],
  ];
  return rules.find(([re]) => re.test(path))?.[1] ?? "platform";
}
