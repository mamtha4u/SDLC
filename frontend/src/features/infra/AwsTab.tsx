import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity, AlertTriangle, Archive, BellRing, Box, CalendarClock, Check, ChevronDown, Cloud, Container, Copy, Database, ExternalLink, Globe,
  HardDrive, Inbox, Info, KeyRound, Layers, Megaphone, MemoryStick, MessagesSquare, Network, Package, PencilLine, Radio, RefreshCw, RotateCcw,
  ScrollText, Search, Send, Server, Settings2, ShieldCheck, Split, Undo2, Workflow, Zap,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { CountUp } from "../../components/CountUp";
import { Button, Skeleton } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type AwsResource, type InfraState, type NamingState, type Setting } from "../../lib/flow";
import { intakeApi } from "../../lib/intake";
import { talkApi } from "../../lib/talk";
import { ACCENT } from "../../lib/themes";
import { duration, timeAgo } from "../../lib/time";
import { CostPanel } from "../cost/CostPanel";
import { DriftPanel } from "./DriftPanel";

const SERVICE: Record<string, { icon: typeof Zap; accent: string }> = {
  lambda: { icon: Zap, accent: "orange" }, apigateway: { icon: Globe, accent: "violet" }, sqs: { icon: Inbox, accent: "rose" },
  sns: { icon: Megaphone, accent: "rose" }, logs: { icon: ScrollText, accent: "emerald" }, alarms: { icon: BellRing, accent: "amber" },
  events: { icon: CalendarClock, accent: "rose" }, iam: { icon: ShieldCheck, accent: "cyan" }, s3: { icon: Archive, accent: "emerald" },
  dynamodb: { icon: Database, accent: "blue" }, states: { icon: Workflow, accent: "rose" }, secrets: { icon: KeyRound, accent: "amber" },
  ssm: { icon: Settings2, accent: "indigo" }, ecs: { icon: Container, accent: "orange" }, ecr: { icon: Package, accent: "orange" },
  elb: { icon: Split, accent: "violet" }, ec2: { icon: Server, accent: "orange" }, vpc: { icon: Network, accent: "violet" },
  mq: { icon: MessagesSquare, accent: "rose" }, elasticache: { icon: MemoryStick, accent: "blue" }, rds: { icon: Database, accent: "blue" },
  msk: { icon: Radio, accent: "violet" }, kinesis: { icon: Activity, accent: "violet" }, efs: { icon: HardDrive, accent: "emerald" },
  other: { icon: Box, accent: "indigo" },
};
const LABEL: Record<string, string> = {
  memory_size: "Memory (MB)", timeout: "Timeout (seconds)", runtime: "Runtime", handler: "Handler", architectures: "Architecture",
  reserved_concurrent_executions: "Reserved concurrency", function_name: "Function name", role: "Execution role", environment: "Environment variables",
  message_retention_seconds: "Message retention", visibility_timeout_seconds: "Visibility timeout", delay_seconds: "Delivery delay",
  max_message_size: "Max message size (bytes)", receive_wait_time_seconds: "Long polling wait", fifo_queue: "FIFO queue",
  content_based_deduplication: "Content-based deduplication", redrive_policy: "Dead-letter queue (redrive)", sqs_managed_sse_enabled: "Encryption (SSE-SQS)",
  retention_in_days: "Log retention (days)", batch_size: "Batch size", maximum_batching_window_in_seconds: "Batching window",
  ephemeral_storage: "Temp storage (/tmp)", tracing_config: "X-Ray tracing", logging_config: "Logging format", stage_name: "Stage",
  xray_tracing_enabled: "X-Ray tracing", endpoint_configuration: "Endpoint type", name: "Name", tags: "Tags", description: "Description",
  deduplication_scope: "Deduplication scope", fifo_throughput_limit: "FIFO throughput", kms_master_key_id: "KMS key", package_type: "Package type",
  publish: "Publish versions", layers: "Layers", event_source_arn: "Source", enabled: "Enabled", function_response_types: "Partial batch failures",
  assume_role_policy: "Who can use this role", permissions_boundary: "Permissions boundary", max_session_duration: "Max session (seconds)",
  policy: "Policy", path_part: "Path", http_method: "Method", authorization: "Authorization", integration_http_method: "Integration method",
  type: "Type", uri: "Target", log_group_class: "Log class", skip_destroy: "Keep on destroy",
};
const SECONDS = /(_seconds$|^timeout$|^delay_seconds$)/;
const human = (k: string) => LABEL[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const empty = (v: unknown) => v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length) || (typeof v === "object" && v !== null && !Object.keys(v as object).length);
const editKey = (r: AwsResource, s: Setting) => `${r.address}::${s.key}`;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Everything this project has in AWS, as the user would check it in the console: every service, every setting (what
 *  Terra set, and what AWS left at its defaults), read-only facts and console links. Edit many settings across many
 *  services at once: the edits go to Orion and Terra as one change request (plan → you approve → apply → you check). */
export function AwsTab({ projectId, onOpenFile }: { projectId: string; onOpenFile: (p: string) => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["infra", projectId], queryFn: () => flowApi.infra(projectId),
    refetchInterval: (q) => ((q.state.data as InfraState | undefined)?.busy ? 4000 : 15000),
  });
  const { data: naming } = useQuery({ queryKey: ["naming", projectId], queryFn: () => flowApi.naming(projectId) });
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [prefixDraft, setPrefixDraft] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);

  const inv = data?.inventory;
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-44" /><Skeleton className="h-[480px]" /></div>;
  if (!inv || !inv.resources.length) return <NothingYet data={data} naming={naming} projectId={projectId} />;
  const current = inv.resources.find((r) => r.address === sel) ?? inv.resources.find((r) => r.primary) ?? inv.resources[0];

  const prefix = prefixDraft ?? naming?.prefix ?? "";
  const items = naming?.editable ? naming.items : [];
  const fullOf = (key: string, suffix: string) => `${prefix}-${renames[key] ?? suffix}`;
  const renamed = items.filter((i) => fullOf(i.key, i.name) !== i.full).map((i) => ({ key: i.key, from: i.full, to: fullOf(i.key, i.name) }));
  const nameOf = (r: AwsResource) => items.find((i) => i.full === r.name);
  const followsOf = (r: AwsResource) => items.find((i) => r.name === `/aws/lambda/${i.full}`);

  const byAddr = Object.fromEntries(inv.resources.map((r) => [r.address, r]));
  const editList = Object.entries(edits).map(([k, to]) => {
    const [address, key] = k.split("::");
    const r = byAddr[address];
    const s = [...(r?.set ?? []), ...(r?.defaults ?? [])].find((x) => x.key === key);
    return { k, address, key, to, from: s?.value, r, kind: s?.kind };
  });
  const touched = new Set(editList.map((e) => e.address)).size;
  const setEdit = (r: AwsResource, s: Setting, v: unknown) => setEdits((o) => {
    const n = { ...o };
    if (same(v, s.value) || (s.kind === "json" && typeof v === "string" && same(safeJson(v), s.value))) delete n[editKey(r, s)];
    else n[editKey(r, s)] = v;
    return n;
  });
  const send = async () => {
    setSending(true);
    try {
      const cr = await flowApi.infraChanges(projectId, editList.map((e) => ({ address: e.address, key: e.key, to: e.to })), note,
        renamed.length ? { prefix, names: Object.fromEntries(items.map((i) => [i.key, renames[i.key] ?? i.name])) } : undefined);
      toast.success(`${cr.label} sent to Orion`, { description: "He reviews the impact first (code, layers, the requirement, risks). Safe infrastructure "
        + "changes go straight to Terra; anything bigger comes back to you to confirm. Nothing changes in AWS before you approve the plan." });
      setEdits({}); setRenames({}); setPrefixDraft(null); setNote(""); setEditing(false);
      ["infra", "naming", "approvals", "project", "changes", "crew", "build", "design"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send the change"); }
    finally { setSending(false); }
  };
  const pending = editList.length + renamed.length;
  const discard = () => { setEdits({}); setRenames({}); setPrefixDraft(null); };
  const refresh = async () => {
    try {
      await flowApi.refreshInfra(projectId);
      toast.success("Terra is checking AWS against the source of truth", { description: "Every resource and every function's code, compared with the Terraform state and Dev's packages. Nothing changes in AWS; about a minute." });
      qc.invalidateQueries({ queryKey: ["infra", projectId] });
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't refresh"); }
  };
  const functions = inv.resources.filter((r) => r.type === "aws_lambda_function");
  const withCode = functions.filter((r) => r.code && !r.code.placeholder).length;

  return (
    <div className="space-y-4 pb-28">
      <Hero data={data} editing={editing} onEdit={() => setEditing((e) => !e)} onRefresh={refresh}
        prefix={naming?.editable ? { current: naming.prefix ?? "", draft: prefixDraft, set: setPrefixDraft } : null} />

      {(data.status === "deployed" || data.status === "partial") && <DriftPanel projectId={projectId} data={data} onRefresh={refresh} onOpenFile={onOpenFile} />}

      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-5">
        <Stat label="Resources" value={inv.count} />
        <Stat label="Services" value={inv.services.length} />
        <Stat label="Set by Terra" value={inv.resources.reduce((n, r) => n + r.set.length, 0)} />
        <Stat label="AWS defaults" value={inv.resources.reduce((n, r) => n + r.defaults.filter((s) => !empty(s.value)).length, 0)} />
        <Stat label="Functions with Dev's code" value={withCode} suffix={` / ${functions.length}`} tone={functions.length && withCode === functions.length ? "var(--success)" : "var(--warning)"} />
      </div>

      <PendingChanges data={data} />

      <section className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <ResourceList inv={inv} prefix={naming?.prefix ?? data.prefix} selected={current.address} onSelect={setSel} edits={edits}
          renamedNames={new Set(renamed.map((x) => x.from))} />
        {(() => {
          const item = nameOf(current), follows = followsOf(current);
          const text = (x: AwsResource) => JSON.stringify([...x.set, ...x.defaults, ...x.facts].map((s) => s.value));
          const related = inv.resources.filter((x) => x.address !== current.address && !x.primary && current.name && text(x).includes(current.name)).slice(0, 12);
          return <ResourceDetail key={current.address} r={current} editing={editing} edits={edits} onEdit={setEdit} related={related} onSelect={setSel}
            name={item ? { prefix, suffix: renames[item.key] ?? item.name, full: fullOf(item.key, item.name), changed: fullOf(item.key, item.name) !== item.full,
              set: (v) => setRenames((o) => { const n = { ...o }; if (v === item.name) delete n[item.key]; else n[item.key] = v; return n; }) } : null}
            follows={follows ? `/aws/lambda/${fullOf(follows.key, follows.name)}` : null} />;
        })()}
      </section>
      {data.code && Object.keys(data.code.layers ?? {}).length > 0 && (
        <section className="rounded-[22px] border border-line bg-surface p-4">
          <h3 className="mb-2 flex items-center gap-2 font-display font-semibold"><Layers className="h-4 w-4 text-primary" />
            {data.code.layers_by === "terra" ? "Layers: built by Dev, published by Terra" : "Layers published by Dev"}</h3>
          <ul className="grid gap-2 md:grid-cols-2">{Object.entries(data.code.layers).map(([name, l]) => (
            <li key={name} className="flex items-center gap-3 rounded-[14px] bg-bg-2/50 px-3 py-2 text-sm">
              <AgentAvatar agent="de" accent="blue" status="done" size={26} plain />
              <span className="min-w-0 flex-1"><code className="break-all text-[12px]">{name}</code><span className="block text-[11px] text-muted">v{l.version} · {l.kb} KB · from {l.code_version} · {timeAgo(l.published_at)}</span></span>
              <a href={`https://${data.region}.console.aws.amazon.com/lambda/home?region=${data.region}#/layers/${encodeURIComponent(name)}`} target="_blank" rel="noreferrer"
                className="rounded-full p-1.5 text-muted hover:text-primary" aria-label={`Open ${name} in the AWS console`}><ExternalLink className="h-4 w-4" /></a>
            </li>
          ))}</ul>
        </section>
      )}

      <Outputs outputs={data.outputs} onOpenFile={onOpenFile} />
      <CostPanel projectId={projectId} />

      <AnimatePresence>
        {pending > 0 && (
          <motion.div initial={{ y: 80, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 80, opacity: 0 }} transition={{ type: "spring", stiffness: 380, damping: 34 }}
            className="fixed inset-x-0 bottom-4 z-30 mx-auto w-[min(980px,calc(100%-24px))]">
            <div className="glass beam-border elev rounded-[22px] border border-line p-3.5" style={{ background: "color-mix(in srgb, var(--surface) 96%, transparent)", ["--beam-1" as string]: "var(--primary)" }}>
              <div className="flex flex-wrap items-center gap-2">
                <PencilLine className="h-4 w-4 text-primary" />
                <b className="text-sm">{[editList.length && `${editList.length} setting${editList.length === 1 ? "" : "s"} across ${touched} resource${touched === 1 ? "" : "s"}`,
                  renamed.length && `${renamed.length} rename${renamed.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ")}</b>
                <div className="no-scrollbar flex min-w-0 flex-1 gap-1.5 overflow-x-auto">
                  {renamed.map((r) => (
                    <span key={r.key} className="inline-flex shrink-0 items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[11.5px]" title={`${r.from} → ${r.to}`}>
                      <b>Rename</b> <s className="text-muted">{r.from.replace(`${naming?.prefix}-`, "")}</s>→<b className="font-mono">{r.to.replace(`${prefix}-`, "")}</b>
                    </span>
                  ))}
                  {editList.map((e) => (
                    <span key={e.k} className="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary/12 px-2 py-0.5 text-[11.5px]">
                      <b title={e.r?.name}>{e.r?.kind}</b> {human(e.key)}: <s className="text-muted">{short(e.from)}</s>→<b>{short(e.to)}</b>
                      <button onClick={() => setEdits((o) => { const n = { ...o }; delete n[e.k]; return n; })} className="ml-0.5 text-muted hover:text-danger" aria-label="Undo this change"><Undo2 className="h-3 w-3" /></button>
                    </span>
                  ))}
                </div>
              </div>
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything Terra should know? (optional)"
                  className="neu-inset h-10 min-w-[220px] flex-1 rounded-[12px] px-3 text-sm outline-none placeholder:text-muted/70" aria-label="Note for Terra" />
                <Button variant="ghost" icon={<RotateCcw className="h-4 w-4" />} onClick={discard}>Discard</Button>
                <Button variant="primary" className="shimmer" loading={sending} icon={<Send className="h-4 w-4" />} onClick={send}>Send to Orion &amp; Terra</Button>
              </div>
              <p className="mt-1.5 text-[11px] text-muted">One change request: Orion reviews the impact (and asks you if it touches the code or the requirement) → Terra updates the Terraform → you approve the exact plan → Terra applies it → you check it here.
                {renamed.length > 0 && " A renamed resource is replaced in AWS (new one created, old one removed; messages left in an old queue are lost)."}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function safeJson(s: string): unknown { try { return JSON.parse(s); } catch { return s; } }
function short(v: unknown): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s === undefined ? "–" : s.length > 22 ? `${s.slice(0, 20)}…` : s;
}

function Hero({ data, editing, onEdit, onRefresh, prefix }: {
  data: InfraState; editing: boolean; onEdit: () => void; onRefresh: () => void;
  prefix: { current: string; draft: string | null; set: (v: string | null) => void } | null;
}) {
  const inv = data.inventory!;
  const shownPrefix = prefix?.draft ?? data.prefix ?? "orkestra-";
  const tone = data.status === "deployed" ? "var(--success)" : data.status === "partial" ? "var(--warning)" : "var(--danger)";
  return (
    <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className="spotlight sheen elev relative overflow-hidden rounded-[26px] border border-line bg-surface p-5">
      <div className="pointer-events-none absolute -left-24 -top-24 h-72 w-72 rounded-full blur-3xl" style={{ background: `color-mix(in srgb, ${tone} 18%, transparent)` }} />
      <div className="relative flex flex-wrap items-center gap-5">
        <div className="relative grid h-16 w-16 shrink-0 place-items-center">
          <span className="sonar absolute inset-0 rounded-full border-2" style={{ borderColor: tone }} />
          <span className="absolute inset-2 rounded-full" style={{ background: `radial-gradient(circle at 35% 30%, color-mix(in srgb, ${tone} 70%, white), ${tone})`, boxShadow: `0 0 34px -4px ${tone}` }} />
          <Cloud className="relative h-7 w-7 text-white" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em]" style={{ color: tone }}>
            {data.status === "deployed" ? "Live in AWS" : data.status === "partial" ? "Partly in AWS" : data.status.replace("_", " ")} · {data.region}</p>
          <h2 className="font-display text-2xl font-bold tracking-tight">What's in AWS</h2>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-1 text-sm text-muted">
            {inv.count} resources named
            {editing && prefix ? (
              <span className="inline-flex items-center rounded-[10px] border border-primary/50 bg-bg-2 pl-2 font-mono text-[12.5px]">
                <span className="select-none text-muted">orkestra-</span>
                <input value={shownPrefix.replace(/^orkestra-/, "")} aria-label="Project prefix" size={Math.max(8, shownPrefix.length - 9)}
                  onChange={(e) => { const v = `orkestra-${e.target.value.toLowerCase()}`; prefix.set(v === prefix.current ? null : v); }}
                  className="bg-transparent py-0.5 pr-2 text-text outline-none" />
                <span className="pr-2 text-muted">*</span>
              </span>
            ) : <code className={clsx(prefix?.draft ? "text-primary" : "text-text")}>{shownPrefix}*</code>}
            , tagged <code>created_by=orkestra</code>
            {data.applied_at && <> · applied {timeAgo(data.applied_at)}</>}{data.version && <> · {data.version}</>}
            {inv.refreshed_at && <> · read from AWS {timeAgo(inv.refreshed_at)}</>}
          </p>
          {editing && prefix?.draft && <p className="mt-1 text-xs text-warning">A new prefix renames every resource: Terra's plan replaces them all.</p>}
          {!!inv.drifted && <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-full bg-warning/15 px-2.5 py-0.5 text-xs font-semibold text-warning">
            <AlertTriangle className="h-3.5 w-3.5" />{inv.drifted} resource{inv.drifted === 1 ? "" : "s"} changed in AWS outside Terraform</p>}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex gap-2">
            <Button variant="neu" loading={!!data.refreshing} disabled={!!data.busy && !data.refreshing} onClick={onRefresh}
              title="Terra reads every resource and every function's code from AWS and compares them with the Terraform state and Dev's packages (nothing changes)"
              icon={<RefreshCw className="h-4 w-4" />}>Check AWS now</Button>
            <Button variant={editing ? "primary" : "neu"} disabled={!data.can_change && !editing} title={data.why ?? "Change settings and names across services, then send them as one change request"}
              icon={<PencilLine className="h-4 w-4" />} onClick={onEdit}>{editing ? "Done editing" : "Change settings"}</Button>
          </div>
          {!data.can_change && data.why && <span className="text-[11px] text-muted">{data.why}</span>}
        </div>
      </div>
      {data.error && <p className="relative mt-3 rounded-[12px] border border-danger/30 bg-danger/[0.06] px-3 py-2 text-xs"><b className="text-danger">Last error:</b> <span className="font-mono">{data.error.slice(0, 400)}</span></p>}
    </motion.section>
  );
}

function Stat({ label, value, suffix = "", tone }: { label: string; value: number; suffix?: string; tone?: string }) {
  return (
    <div className="lift rounded-[18px] border border-line bg-surface px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</p>
      <p className="font-display text-2xl font-bold tabular-nums" style={tone ? { color: tone } : undefined}><CountUp value={value} />{suffix && <span className="text-base text-muted">{suffix}</span>}</p>
    </div>
  );
}

const VERDICT: Record<string, { label: string; tone: string }> = {
  infra_only: { label: "Infrastructure only", tone: "var(--success)" }, affects_code: { label: "Affects the code", tone: "var(--warning)" },
  requirement_change: { label: "A requirement change", tone: "var(--danger)" },
};

function PendingChanges({ data }: { data: InfraState }) {
  const open = data.changes.filter((c) => c.status !== "done");
  if (!open.length && !data.busy) return null;
  return (
    <div className="space-y-2">
      {open.map((c) => {
        const rv = c.triage?.review ? c.triage : null;
        const steps = ["Orion reviews the impact", "Terra updates the Terraform", "You approve the plan", "Terra applies it", "You check it here",
          ...(rv?.verdict === "affects_code" ? ["Dev adapts the code"] : [])];
        const step = c.status === "triage" || c.status === "reviewing" ? 0 : data.busy === "tp.apply" ? 3 : data.busy === "tp.deploy" ? 2
          : data.busy === "tp.iac" ? 1 : data.plan && data.intent ? 2 : data.status === "deployed" ? 4 : 1;
        const v = rv?.verdict ? VERDICT[rv.verdict] : null;
        return (
          <motion.div key={c.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
            className="beam-border rounded-[20px] border border-primary/40 bg-primary/[0.06] p-4" style={{ ["--beam-1" as string]: "var(--primary)" }}>
            <div className="flex flex-wrap items-center gap-2">
              <AgentAvatar agent={step === 0 ? "cto" : "tp"} accent={step === 0 ? "violet" : "orange"} status={c.status === "reviewing" ? "needs_approval" : "working"} size={30} plain />
              <b className="font-mono text-sm">{c.label}</b>
              <span className="text-sm">{c.status === "triage" ? "Orion is reviewing the impact…" : c.status === "reviewing" ? "Orion's review needs your confirmation" : "infrastructure change in progress"}</span>
              {v && <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold" style={{ color: v.tone, background: `color-mix(in srgb, ${v.tone} 14%, transparent)` }}>{v.label}</span>}
              <span className="ml-auto text-xs text-muted">{timeAgo(c.created_at)}</span>
            </div>
            <p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-sm text-muted">{c.text.replace(/\*\*/g, "").replace(/`/g, "")}</p>
            {rv && (
              <div className="mt-3 rounded-[14px] border border-line bg-surface/80 p-3 text-sm">
                <p className="flex items-center gap-2 font-semibold"><AgentAvatar agent="cto" accent="violet" status="done" size={22} plain />Orion's review</p>
                <p className="mt-1">{rv.summary}</p>
                {(rv.risks ?? []).length > 0 && <ul className="mt-2 space-y-1">{rv.risks!.map((r, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-[12.5px]"><AlertTriangle className={clsx("mt-0.5 h-3.5 w-3.5 shrink-0", r.severity === "high" ? "text-danger" : r.severity === "medium" ? "text-warning" : "text-muted")} />
                    <span><b>{r.risk}</b> <span className="text-muted">({r.severity}) · {r.mitigation}</span></span></li>
                ))}</ul>}
                {(rv.affected ?? []).length > 0 && <p className="mt-2 flex flex-wrap gap-1.5">{rv.affected!.map((a) => (
                  <span key={a.agent} className="rounded-full bg-bg-2 px-2 py-0.5 text-[11.5px]"><b>{({ intake: "Echo", ta: "Archie", tp: "Terra", de: "Dev", qa: "Quinn" } as Record<string, string>)[a.agent] ?? a.agent}</b>: {a.what}</span>
                ))}</p>}
                {(rv.questions ?? []).length > 0 && <div className="mt-2 rounded-[10px] bg-warning/10 px-3 py-2 text-[12.5px]"><b>Please confirm:</b> {rv.questions!.join(" ")}</div>}
              </div>
            )}
          <ol className="mt-3 flex flex-wrap items-center gap-1.5">
            {steps.map((s, i) => (
              <li key={s} className="flex items-center gap-1.5">
                <span className={clsx("rounded-full px-2.5 py-1 text-xs font-semibold", i < step ? "bg-success/15 text-success" : i === step ? "bg-primary/15 text-primary pulse-ring" : "bg-bg-2 text-muted")}
                  style={i === step ? { ["--ring" as string]: "var(--primary)" } : undefined}>{i < step ? "✓ " : ""}{s}</span>
                {i < steps.length - 1 && <span className="h-px w-3 bg-line" />}
              </li>
            ))}
          </ol>
          </motion.div>
        );
      })}
    </div>
  );
}

interface NameEdit { prefix: string; suffix: string; full: string; changed: boolean; set: (v: string) => void }

const first = (v: unknown) => (Array.isArray(v) ? v[0] : v) as Record<string, unknown> | undefined;
const secs = (v: unknown) => (typeof v === "number" ? (v >= 60 ? duration(v) : `${v} s`) : String(v ?? "–"));
const onoff = (v: unknown) => (v ? "On" : "Off");
const GLANCE: Record<string, [string, string, (v: unknown) => string][]> = {
  aws_lambda_function: [["memory_size", "Memory", (v) => `${v} MB`], ["timeout", "Timeout", secs], ["runtime", "Runtime", String],
    ["architectures", "Architecture", (v) => (Array.isArray(v) ? v.join(", ") : String(v))],
    ["reserved_concurrent_executions", "Concurrency", (v) => (v === -1 ? "Unreserved" : String(v))],
    ["ephemeral_storage", "Temp storage", (v) => `${first(v)?.size ?? 512} MB`]],
  aws_sqs_queue: [["fifo_queue", "Type", (v) => (v ? "FIFO" : "Standard")], ["visibility_timeout_seconds", "Visibility", secs],
    ["message_retention_seconds", "Keeps messages", secs],
    ["redrive_policy", "To the DLQ after", (v) => { try { return `${JSON.parse(String(v)).maxReceiveCount} tries`; } catch { return "–"; } }],
    ["sqs_managed_sse_enabled", "Encryption", (v) => (v ? "SSE-SQS" : "Off")], ["delay_seconds", "Delivery delay", secs]],
  aws_cloudwatch_log_group: [["retention_in_days", "Keeps logs", (v) => (v ? `${v} days` : "Forever")], ["log_group_class", "Class", String]],
  aws_api_gateway_rest_api: [["endpoint_configuration", "Endpoint", (v) => ((first(v)?.types as string[] | undefined) ?? []).join(", ") || "–"],
    ["api_key_source", "API key from", String]],
  aws_api_gateway_stage: [["stage_name", "Stage", String], ["xray_tracing_enabled", "X-Ray", onoff], ["cache_cluster_enabled", "Cache", onoff]],
  aws_iam_role: [["max_session_duration", "Max session", secs], ["permissions_boundary", "Boundary", (v) => String(v ?? "–").split("/").pop() ?? "–"]],
};

function shortName(name: string, prefix: string | null) {
  if (prefix && name.includes(`${prefix}-`)) return name.replace(`${prefix}-`, "…");
  return name;
}

/** The resource list: every service, its main resources and their wiring, with what needs attention. */
function ResourceList({ inv, prefix, selected, onSelect, edits, renamedNames }: {
  inv: NonNullable<InfraState["inventory"]>; prefix: string | null; selected: string; onSelect: (a: string) => void;
  edits: Record<string, unknown>; renamedNames: Set<string>;
}) {
  const [q, setQ] = useState("");
  const [wiring, setWiring] = useState<Record<string, boolean>>({});
  const match = (r: AwsResource) => !q || `${r.name} ${r.kind} ${r.address}`.toLowerCase().includes(q.toLowerCase());
  const services = inv.services.filter((s, i) => inv.services.findIndex((x) => x.key === s.key) === i);
  return (
    <aside className="flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-line bg-surface lg:sticky lg:top-[172px] lg:max-h-[calc(100vh-190px)]">
      <label className="m-3 flex items-center gap-2 rounded-[12px] border border-line bg-bg-2/60 px-3 py-2 text-sm focus-within:border-primary">
        <Search className="h-4 w-4 text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${inv.count} resources`} aria-label="Search resources"
          className="w-full bg-transparent outline-none placeholder:text-muted/70" />
      </label>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-2 pb-3">
        {services.map((g) => {
          const meta = SERVICE[g.key] ?? SERVICE.other;
          const Icon = meta.icon;
          const rs = inv.resources.filter((r) => r.service === g.key && match(r));
          const main = rs.filter((r) => r.primary), wire = rs.filter((r) => !r.primary);
          if (!rs.length) return null;
          return (
            <div key={g.key}>
              <p className="flex items-center gap-2 px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
                <span className="grid h-5 w-5 place-items-center rounded-[6px] text-white" style={{ background: ACCENT[meta.accent] }}><Icon className="h-3 w-3" /></span>
                {g.label}<span className="ml-auto font-mono normal-case">{g.count}</span>
              </p>
              {main.map((r) => <ListItem key={r.address} r={r} prefix={prefix} active={selected === r.address} onSelect={onSelect} edits={edits} renamed={renamedNames.has(r.name)} />)}
              {wire.length > 0 && (
                <>
                  <button onClick={() => setWiring((w) => ({ ...w, [g.key]: !w[g.key] }))}
                    className="flex w-full items-center gap-1.5 rounded-[10px] px-2.5 py-1.5 text-left text-[11.5px] text-muted hover:text-text">
                    <ChevronDown className={clsx("h-3.5 w-3.5 transition-transform", (wiring[g.key] || q) && "rotate-180")} />
                    Wiring · {wire.length} <span className="truncate">({[...new Set(wire.map((w) => w.kind))].slice(0, 3).join(", ")})</span>
                  </button>
                  {(wiring[g.key] || q) && wire.map((r) => <ListItem key={r.address} r={r} prefix={prefix} active={selected === r.address} onSelect={onSelect} edits={edits} small renamed={false} />)}
                </>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function ListItem({ r, prefix, active, onSelect, edits, small, renamed }: {
  r: AwsResource; prefix: string | null; active: boolean; onSelect: (a: string) => void; edits: Record<string, unknown>; small?: boolean; renamed: boolean;
}) {
  const changed = Object.keys(edits).some((k) => k.startsWith(`${r.address}::`)) || renamed;
  const meta = SERVICE[r.service] ?? SERVICE.other;
  return (
    <button onClick={() => onSelect(r.address)} title={r.name}
      className={clsx("relative flex w-full items-center gap-2.5 rounded-[12px] px-2.5 text-left transition-colors", small ? "py-1.5 pl-7" : "py-2",
        active ? "bg-primary/12 text-text" : "hover:bg-bg-2/70")}>
      {active && <motion.span layoutId="aws-sel" className="absolute inset-y-1.5 left-0 w-1 rounded-full" style={{ background: ACCENT[meta.accent] }} />}
      <span className="min-w-0 flex-1">
        <span className={clsx("block truncate font-mono font-semibold", small ? "text-[11.5px]" : "text-[12.5px]")}>{shortName(r.name || r.address, prefix)}</span>
        {!small && <span className="block truncate text-[10.5px] text-muted">{r.kind}</span>}
      </span>
      {r.drift && <span title={`Changed in AWS outside Terraform: ${r.drift.join(", ")}`}><AlertTriangle className="h-3.5 w-3.5 text-warning" /></span>}
      {r.code && <span title={r.code.placeholder ? "Placeholder code" : "Dev's code is deployed"} className={clsx("h-2 w-2 rounded-full", r.code.placeholder ? "bg-warning" : "bg-success")} />}
      {changed && <span title="Change pending" className="h-2 w-2 rounded-full bg-primary pulse-ring" style={{ ["--ring" as string]: "var(--primary)" }} />}
    </button>
  );
}

/** One resource, roomy: what it is, the settings that matter at a glance, every setting (set by Terra, AWS defaults,
 *  facts), what's changed outside Terraform, and its wiring. */
function ResourceDetail({ r, editing, edits, onEdit, name, follows, related, onSelect }: {
  r: AwsResource; editing: boolean; edits: Record<string, unknown>; onEdit: (r: AwsResource, s: Setting, v: unknown) => void;
  name: NameEdit | null; follows: string | null; related: AwsResource[]; onSelect: (a: string) => void;
}) {
  const [naming, setNaming] = useState(false);
  const [tab, setTab] = useState<"set" | "defaults" | "facts">(r.set.length ? "set" : "defaults");
  const [all, setAll] = useState(false);
  const meta = SERVICE[r.service] ?? SERVICE.other;
  const Icon = meta.icon;
  const accent = ACCENT[meta.accent];
  const changed = Object.keys(edits).filter((k) => k.startsWith(`${r.address}::`)).length;
  const rows = tab === "set" ? r.set : tab === "defaults" ? r.defaults : r.facts;
  const shown = tab === "defaults" && !all ? rows.filter((s) => !empty(s.value) || edits[editKey(r, s)] !== undefined) : rows;
  const hidden = rows.length - shown.length;
  const all3 = [...r.set, ...r.defaults, ...r.facts];
  const glance = (GLANCE[r.type] ?? []).map(([k, label, f]) => {
    const s = all3.find((x) => x.key === k);
    if (!s) return null;
    const ed = edits[editKey(r, s)];
    return { k, label, text: f(ed !== undefined ? ed : s.value), edited: ed !== undefined };
  }).filter(Boolean) as { k: string; label: string; text: string; edited: boolean }[];
  return (
    <motion.article key={r.address} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22 }}
      className={clsx("relative overflow-hidden rounded-[24px] border bg-surface", changed ? "border-primary shadow-[0_0_0_1px_var(--primary)]" : "border-line")}>
      <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full blur-3xl" style={{ background: `color-mix(in srgb, ${accent} 18%, transparent)` }} />
      <header className="relative flex flex-wrap items-start gap-4 p-5">
        <span className="grid h-14 w-14 shrink-0 place-items-center rounded-[18px] text-white shadow-[0_14px_30px_-16px_currentColor]"
          style={{ background: `linear-gradient(135deg, ${accent}, color-mix(in srgb, ${accent} 55%, var(--primary-2)))`, color: accent }}>
          <Icon className="h-7 w-7 text-white" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: accent }}>{r.service_label} · {r.kind}</p>
          {naming && name ? (
            <span className="mt-1 flex items-center rounded-[12px] border border-primary/60 bg-bg-2 font-mono text-sm">
              <span className="select-none truncate pl-3 text-muted" title={name.prefix}>{name.prefix}-</span>
              <input autoFocus value={name.suffix} onChange={(e) => name.set(e.target.value)} onKeyDown={(e) => (e.key === "Enter" || e.key === "Escape") && setNaming(false)}
                aria-label={`New name for ${r.name}`} className="min-w-0 flex-1 bg-transparent py-1.5 pr-2 font-semibold text-text outline-none" />
              <button onClick={() => setNaming(false)} className="px-3 text-primary" aria-label="Done"><Check className="h-4 w-4" /></button>
            </span>
          ) : (
            <h3 className="mt-0.5 flex items-start gap-2 break-all font-mono text-lg font-bold leading-snug">
              <span className={clsx(name?.changed && "text-primary")}>{name?.changed ? name.full : follows && follows !== r.name ? follows : r.name || r.address}</span>
              {editing && name && <button onClick={() => setNaming(true)} className="mt-1 shrink-0 rounded-full p-1 text-muted hover:bg-bg-2 hover:text-primary" aria-label={`Rename ${r.name}`} title="Rename"><PencilLine className="h-4 w-4" /></button>}
            </h3>
          )}
          {(name?.changed || (follows && follows !== r.name)) && <p className="break-all font-mono text-[11.5px] text-muted line-through">{r.name}</p>}
          {editing && follows && <p className="text-[11.5px] text-muted">Its name follows its function's name.</p>}
          <p className="mt-0.5 font-mono text-[11px] text-muted">{r.address}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button onClick={() => { navigator.clipboard.writeText(r.name); toast.success("Name copied"); }} className="grid h-9 w-9 place-items-center rounded-full border border-line text-muted hover:text-text" aria-label={`Copy ${r.name}`}><Copy className="h-4 w-4" /></button>
          {r.console && <a href={r.console} target="_blank" rel="noreferrer" className="press inline-flex h-9 items-center gap-1.5 rounded-full px-4 text-sm font-semibold text-on-primary shadow-[0_8px_20px_-10px_var(--primary)]"
            style={{ background: "linear-gradient(120deg, var(--primary), color-mix(in srgb, var(--primary) 55%, var(--primary-2)))" }}>Open in AWS<ExternalLink className="h-3.5 w-3.5" /></a>}
        </div>
      </header>

      <div className="relative space-y-4 px-5 pb-5">
        {r.drift && (
          <p className="flex items-start gap-2 rounded-[14px] border border-warning/40 bg-warning/[0.08] px-3.5 py-2.5 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <span><b>Changed in AWS outside Terraform:</b> {r.drift.map(human).join(", ")}. See the drift panel above: Restore puts the Terraform values back; Keep the changes turns them into a change request.</span>
          </p>
        )}
        {r.code && (
          <div className={clsx("flex items-center gap-2.5 rounded-[14px] px-3.5 py-2.5 text-sm", r.code.placeholder ? "bg-warning/10" : "bg-success/10")}>
            <AgentAvatar agent="de" accent="blue" status={r.code.placeholder ? "waiting" : "done"} size={26} plain />
            {r.code.placeholder ? <span><b>Placeholder code.</b> After Archie's review and yours, Dev hands his code to Terra, who swaps it in here (a plan you approve).</span>
              : <span><b>Dev's code {r.code.code_version}</b> · {r.code.kb} KB · handler <code>{r.code.handler}</code>{r.code.layers?.length ? ` · ${r.code.layers.length} layer(s)` : ""}{r.code.deployed_at ? ` · deployed ${timeAgo(r.code.deployed_at)}` : ""}</span>}
          </div>
        )}
        {glance.length > 0 && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
            {glance.map((g, i) => (
              <motion.div key={g.k} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
                className={clsx("rounded-[16px] border px-3 py-2.5", g.edited ? "border-primary bg-primary/[0.07]" : "border-line bg-bg-2/40")}>
                <p className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">{g.label}</p>
                <p className="mt-0.5 truncate font-display text-base font-bold" title={g.text}>{g.text}</p>
              </motion.div>
            ))}
          </div>
        )}

        <div>
          <div className="mb-3 inline-flex gap-1 rounded-[14px] bg-bg-2/70 p-1">
            {([["set", "Set by Terra", r.set.length], ["defaults", "AWS defaults", r.defaults.filter((s) => !empty(s.value)).length], ["facts", "Facts", r.facts.length]] as const).map(([k, label, n]) => (
              <button key={k} onClick={() => setTab(k)} className={clsx("relative rounded-[10px] px-4 py-1.5 text-sm font-semibold", tab === k ? "text-text" : "text-muted hover:text-text")}>
                {tab === k && <motion.span layoutId="aws-detail-tab" className="absolute inset-0 rounded-[10px] bg-surface shadow-sm" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                <span className="relative">{label} <span className="font-mono text-xs text-muted">{n}</span></span>
              </button>
            ))}
          </div>
          <p className="mb-2 text-xs text-muted">{tab === "set" ? "What Terra wrote in the Terraform for this resource." : tab === "defaults"
            ? "Settings Terra didn't write: AWS (or Terraform) chose these. You can still change them." : "Read-only: AWS assigns these (ARNs, URLs, ids)."}</p>
          <ul className="grid gap-2 md:grid-cols-2">
            {shown.map((s) => <Row key={s.key} r={r} s={s} editing={editing && tab !== "facts"} value={edits[editKey(r, s)]} onEdit={onEdit} />)}
            {!shown.length && <li className="col-span-full py-4 text-center text-sm text-muted">Nothing here</li>}
          </ul>
          {tab === "defaults" && (hidden > 0 || all) && (
            <button onClick={() => setAll(!all)} className="mt-2 text-xs font-semibold text-primary hover:underline">{all ? "Hide unset options" : `Show ${hidden} unset option${hidden === 1 ? "" : "s"} you could set`}</button>
          )}
        </div>

        {related.length > 0 && (
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Wired to it</p>
            <div className="flex flex-wrap gap-1.5">{related.map((w) => (
              <button key={w.address} onClick={() => onSelect(w.address)} className="press rounded-full border border-line bg-bg-2/50 px-2.5 py-1 text-xs hover:border-primary">
                <b>{w.kind}</b> <span className="font-mono text-muted">{w.name.length > 40 ? `${w.name.slice(0, 38)}…` : w.name}</span></button>
            ))}</div>
          </div>
        )}
      </div>
    </motion.article>
  );
}

function Row({ r, s, editing, value, onEdit }: { r: AwsResource; s: Setting; editing: boolean; value: unknown; onEdit: (r: AwsResource, s: Setting, v: unknown) => void }) {
  const edited = value !== undefined;
  const v = edited ? value : s.value;
  const canEdit = editing && s.editable;
  return (
    <li className={clsx("min-w-0 rounded-[14px] border px-3.5 py-2.5 text-sm transition-colors",
      edited ? "border-primary bg-primary/[0.07]" : canEdit ? "border-line bg-bg-2/30 hover:border-primary/50" : "border-line/70 bg-bg-2/20")}>
      <p className="flex items-center gap-1.5 text-[12px] font-semibold text-muted" title={s.desc || s.key}>
        <span className="truncate">{human(s.key)}</span>{s.desc && <Info className="h-3 w-3 shrink-0 cursor-help opacity-60" />}
        <span className="ml-auto truncate font-mono text-[10px] font-normal opacity-60">{s.key}</span>
      </p>
      <div className="mt-1 min-w-0">
        {canEdit ? <Editor s={s} value={v} onChange={(x) => onEdit(r, s, x)} /> : <Value s={s} v={v} />}
        {edited && <span className="mt-0.5 block text-[11px] text-muted">was <s>{short(s.value)}</s></span>}
      </div>
    </li>
  );
}

function Value({ s, v }: { s: Setting; v: unknown }) {
  if (empty(v)) return <span className="text-xs italic text-muted">not set</span>;
  if (typeof v === "boolean") return <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold", v ? "bg-success/15 text-success" : "bg-bg-2 text-muted")}>{v ? "on" : "off"}</span>;
  if (typeof v === "number") return <span className="font-mono text-[13px]">{v}{SECONDS.test(s.key) && v >= 60 && <span className="ml-1.5 font-sans text-xs text-muted">({duration(v)})</span>}</span>;
  if (typeof v === "string") {
    if (v.startsWith("{") || v.startsWith("[")) return <Structured v={safeJson(v)} />;
    return <span className="break-all font-mono text-[12.5px]">{v}</span>;
  }
  return <Structured v={v} />;
}

const scalar = (x: unknown) => x === null || ["string", "number", "boolean"].includes(typeof x);

/** Terraform blocks arrive as one-item lists of objects: show them as readable key/value pairs, short lists as chips,
 *  and only fall back to JSON for anything deeper (policies, nested blocks). */
function Structured({ v, depth = 0 }: { v: unknown; depth?: number }) {
  let x = v;
  if (Array.isArray(x) && x.length === 1 && x[0] && typeof x[0] === "object" && !Array.isArray(x[0])) x = x[0];
  if (Array.isArray(x) && x.every(scalar)) {
    return <span className="flex flex-wrap gap-1">{x.map((i, n) => <span key={n} className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[11.5px]">{String(i)}</span>)}</span>;
  }
  if (x && typeof x === "object" && !Array.isArray(x) && depth < 2 && Object.keys(x).length <= 12 && !("Statement" in (x as object))) {
    const entries = Object.entries(x as Record<string, unknown>).filter(([, val]) => !empty(val));
    if (!entries.length) return <span className="text-xs italic text-muted">not set</span>;
    return (
      <dl className={clsx("grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-0.5 text-[12px]", depth && "rounded-[8px] bg-bg-2/50 px-2 py-1")}>
        {entries.map(([k, val]) => (
          <div key={k} className="contents">
            <dt className="font-mono text-muted">{k}</dt>
            <dd className="min-w-0 break-all font-mono">{scalar(val) ? String(val) : <Structured v={val} depth={depth + 1} />}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <Json v={x} />;
}

function Json({ v }: { v: unknown }) {
  const text = JSON.stringify(v, null, 2);
  const [open, setOpen] = useState(text.length < 120);
  return open ? <pre className="max-h-56 overflow-auto rounded-[10px] bg-bg-2/70 px-2.5 py-1.5 font-mono text-[11.5px] leading-snug">{text}</pre>
    : <button onClick={() => setOpen(true)} className="font-mono text-[12px] text-primary hover:underline">{text.replace(/\s+/g, " ").slice(0, 60)}… show</button>;
}

function Editor({ s, value, onChange }: { s: Setting; value: unknown; onChange: (v: unknown) => void }) {
  if (s.kind === "bool") {
    const on = value === true || value === "true";
    return (
      <button role="switch" aria-checked={on} onClick={() => onChange(!on)}
        className={clsx("relative h-6 w-11 rounded-full transition-colors", on ? "bg-success" : "bg-bg-2 ring-1 ring-line")}>
        <motion.span layout className="absolute top-0.5 h-5 w-5 rounded-full bg-white shadow" style={{ left: on ? 22 : 2 }} />
      </button>
    );
  }
  if (s.kind === "json") {
    const text = typeof value === "string" ? value : JSON.stringify(value ?? null, null, 2);
    return <textarea defaultValue={text} rows={Math.min(8, text.split("\n").length + 1)} onBlur={(e) => onChange(e.target.value)}
      className="neu-inset w-full rounded-[10px] px-2.5 py-1.5 font-mono text-[12px] outline-none focus:shadow-[0_0_0_2px_var(--primary)]" aria-label={s.key} />;
  }
  return (
    <span className="flex items-center gap-2">
      <input type={s.kind === "number" ? "number" : "text"} value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(s.kind === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
        className="neu-inset h-8 w-full max-w-[260px] rounded-[10px] px-2.5 font-mono text-[12.5px] outline-none focus:shadow-[0_0_0_2px_var(--primary)]" aria-label={s.key} />
      {s.kind === "number" && SECONDS.test(s.key) && typeof value === "number" && value >= 60 && <span className="shrink-0 text-xs text-muted">{duration(value)}</span>}
    </span>
  );
}

function Outputs({ outputs, onOpenFile }: { outputs: Record<string, unknown>; onOpenFile: (p: string) => void }) {
  const entries = Object.entries(outputs).filter(([k]) => k !== "code_deploy");
  if (!entries.length) return null;
  return (
    <section className="rounded-[22px] border border-line bg-surface p-4">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="font-display font-semibold">Outputs</h3>
        <span className="text-xs text-muted">what Dev and Quinn work from</span>
        <button onClick={() => onOpenFile("reports/aws_inventory.md")} className="ml-auto rounded-full border border-line px-2.5 py-0.5 font-mono text-[11.5px] hover:border-primary hover:text-primary">reports/aws_inventory.md</button>
      </div>
      <ul className="space-y-1.5 text-sm">{entries.map(([k, v]) => {
        const text = typeof v === "string" ? v : JSON.stringify(v);
        return (
          <li key={k} className="flex flex-wrap items-center gap-2"><span className="w-40 shrink-0 text-muted">{human(k)}</span>
            <code className="min-w-0 flex-1 break-all text-[12px]">{text}</code>
            <button onClick={() => { navigator.clipboard.writeText(text); toast.success("Copied"); }} className="rounded-full p-1 text-muted hover:text-text" aria-label={`Copy ${k}`}><Copy className="h-3.5 w-3.5" /></button></li>
        );
      })}</ul>
    </section>
  );
}

const KIND_SERVICE: [RegExp, string][] = [[/layer|lambda/i, "lambda"], [/sqs/i, "sqs"], [/api gateway/i, "apigateway"], [/log group/i, "logs"],
  [/alarm/i, "alarms"], [/iam/i, "iam"], [/sns/i, "sns"], [/s3/i, "s3"], [/dynamo/i, "dynamodb"], [/ecs|task definition/i, "ecs"],
  [/ecr|image/i, "ecr"], [/load balancer|target group|listener/i, "elb"], [/ec2|instance|launch template|auto scaling/i, "ec2"],
  [/vpc|subnet|route|gateway|security group|elastic ip/i, "vpc"], [/mq/i, "mq"], [/cache/i, "elasticache"], [/database|rds/i, "rds"],
  [/kafka|msk/i, "msk"], [/kinesis/i, "kinesis"]];
const serviceOfKind = (kind: string) => SERVICE[KIND_SERVICE.find(([re]) => re.test(kind))?.[1] ?? "other"];

function NothingYet({ data, naming, projectId }: { data: InfraState; naming?: NamingState; projectId: string }) {
  const destroyed = data.status === "destroyed";
  const steps = [
    { agent: "tp", accent: "orange", t: "Terra writes the infrastructure", d: "Terraform from Archie's LLD, validated, with Orion's access plan" },
    { agent: "cto", accent: "violet", t: "You approve it", d: "Orion creates the crew's roles; Terra creates everything in AWS (functions start with placeholder code)" },
    { agent: "tp", accent: "orange", t: "You check it here", d: "Every service, every setting, a console link for each; change anything as one request" },
    { agent: "de", accent: "blue", t: "Then Dev's code goes in", d: "Dev hands his packages to Terra, who swaps them in for the placeholder; Dev tests it live" },
  ];
  return (
    <div className="space-y-4">
      <div className="spotlight sheen elev rounded-[26px] border border-line bg-surface p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">{destroyed ? "Torn down" : "Nothing in AWS yet"}</p>
        <h2 className="font-display text-2xl font-bold tracking-tight">{destroyed ? "This project has nothing left in AWS" : "The infrastructure comes first"}</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">{destroyed ? "Terra tore everything down; the code, documents and history are still here." :
          "Like your team works: the AWS resources exist and you've checked them before any code is written."}</p>
        {!destroyed && (
          <ol className="mt-5 grid gap-3 md:grid-cols-4">
            {steps.map((s, i) => (
              <motion.li key={s.t} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i }}
                className="lift relative rounded-[18px] border border-line bg-bg-2/40 p-4">
                <span className="absolute right-3 top-3 font-display text-3xl font-bold text-muted/25">{i + 1}</span>
                <AgentAvatar agent={s.agent} accent={s.accent} status="waiting" size={36} />
                <p className="mt-2 font-semibold">{s.t}</p><p className="mt-0.5 text-xs text-muted">{s.d}</p>
              </motion.li>
            ))}
          </ol>
        )}
      </div>
      {naming && !naming.editable && !destroyed && <NamingConvention naming={naming} projectId={projectId} />}
      {naming && naming.items.length > 0 && !destroyed && <PlannedNames naming={naming} projectId={projectId} />}
    </div>
  );
}

/** The user's naming convention before Terra writes the infrastructure (user, 10-03: "by default orkestra; if the user
 *  wants their own prefix, allow it, from the requirement or later"). In this sandbox orkestra- is required and fixed. */
function NamingConvention({ naming, projectId }: { naming: NamingState; projectId: string }) {
  const qc = useQueryClient();
  const { data: intake } = useQuery({ queryKey: ["intake", projectId], queryFn: () => intakeApi.get(projectId) });
  // the technical lead's answer in Archie's kickoff (since 10-05), or the requirement's (older projects)
  const { data: archie } = useQuery({ queryKey: ["talk", projectId, "ta"], queryFn: () => talkApi.get(projectId, "ta") });
  const fromTalk = archie?.answers?.["build.naming"];
  const fromReq = fromTalk || intake?.answers?.["build.naming"];
  const c = naming.convention;
  const [prefix, setPrefix] = useState<string>(c?.prefix.replace(/^orkestra-/, "") ?? "");
  const [pattern, setPattern] = useState<string>(c?.pattern ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = `orkestra-${prefix}` !== (c?.prefix ?? "") || pattern !== (c?.pattern ?? "");
  const save = async () => {
    setBusy(true);
    try {
      await flowApi.setConvention(projectId, `orkestra-${prefix.trim().toLowerCase()}`, pattern);
      toast.success("Naming convention saved", { description: "Archie and Terra build with it. After Terra writes the infrastructure you can still rename anything here." });
      ["naming", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save"); }
    finally { setBusy(false); }
  };
  return (
    <section className="sheen elev overflow-hidden rounded-[26px] border border-line bg-surface p-5">
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-[13px] bg-primary/15 text-primary"><PencilLine className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-lg font-semibold">Your naming convention</h3>
          <p className="text-xs text-muted">Optional. In this shared sandbox every name starts with <code>orkestra-</code> (the crew may only touch
            <code> orkestra-*</code>, so colleagues' resources stay safe); your team's convention follows it. Set it now and Archie and Terra build with it;
            later you can still rename every resource here (free until the deploy, then as a plan you approve).</p>
          {fromReq && <p className="mt-1.5 rounded-[10px] bg-bg-2/60 px-2.5 py-1 text-xs"><b>{fromTalk ? "From your technical lead:" : "From your requirement:"}</b> {fromReq}</p>}
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] md:items-end">
        <label className="text-xs font-semibold text-muted">Prefix (every name starts with it)
          <span className="mt-1 flex items-center rounded-[12px] border border-line bg-bg-2 font-mono text-sm focus-within:border-primary">
            <span className="select-none pl-3 text-muted">orkestra-</span>
            <input value={prefix} onChange={(e) => setPrefix(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} placeholder="mint-dev"
              aria-label="Prefix after orkestra-" className="min-w-0 flex-1 bg-transparent py-2 pr-3 text-text outline-none" />
          </span>
        </label>
        <label className="text-xs font-semibold text-muted">Pattern after the prefix (optional)
          <input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="<type>-euwe1-<interface>-<purpose>-01"
            className="mt-1 w-full rounded-[12px] border border-line bg-bg-2 px-3 py-2 font-mono text-sm text-text outline-none focus:border-primary" />
        </label>
        <Button variant="primary" loading={busy} disabled={!prefix.trim() || !dirty} icon={<Check className="h-4 w-4" />} onClick={save}>Save convention</Button>
      </div>
      <p className="mt-2 text-xs text-muted">Example: <code className="text-text">orkestra-{prefix || "<prefix>"}-{pattern ? pattern.replace(/<type>/, "lmb").replace(/<purpose>/, "transform") : "transform"}</code>
        {c && <> · saved {timeAgo(c.at)}</>}</p>
    </section>
  );
}

/** Before anything exists in AWS: the names Terra will create, renamed for free (no replacement, no AI call). */
function PlannedNames({ naming, projectId }: { naming: NamingState; projectId: string }) {
  const qc = useQueryClient();
  const [prefix, setPrefix] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const p = prefix ?? naming.prefix ?? "";
  const full = (k: string, suffix: string) => `${p}-${names[k] ?? suffix}`;
  const changed = naming.items.filter((i) => full(i.key, i.name) !== i.full);
  const save = async () => {
    setBusy(true);
    try {
      const r = await flowApi.setNaming(projectId, p, Object.fromEntries(naming.items.map((i) => [i.key, names[i.key] ?? i.name])));
      toast.success(r.renamed.length ? `Renamed ${r.renamed.length} resource(s)` : "Saved",
        { description: "The Terraform, HLD, LLD, diagram and Orion's access plan use the new names." });
      setPrefix(null); setNames({});
      ["naming", "infra", "design", "build", "crew", "files", "approvals"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save the names"); }
    finally { setBusy(false); }
  };
  return (
    <section className="sheen elev overflow-hidden rounded-[26px] border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
        <span className="grid h-10 w-10 place-items-center rounded-[13px] bg-primary/15 text-primary"><PencilLine className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-lg font-semibold">{naming.editable ? "What Terra will create, and its names" : "What Archie planned"}</h3>
          <p className="text-xs text-muted">{naming.editable
            ? "Rename anything now: it's free, nothing exists in AWS yet. Every name starts with orkestra- in this shared sandbox (the crew may only touch orkestra-*)."
            : naming.reason}</p>
        </div>
        {naming.editable && (
          <span className="flex items-center rounded-[12px] border border-line bg-bg-2 font-mono text-sm focus-within:border-primary">
            <span className="select-none pl-3 text-muted">orkestra-</span>
            <input value={p.replace(/^orkestra-/, "")} onChange={(e) => { const v = `orkestra-${e.target.value.toLowerCase()}`; setPrefix(v === naming.prefix ? null : v); }}
              aria-label="Project prefix" className="w-56 bg-transparent py-2 pr-3 outline-none" />
          </span>
        )}
      </div>
      <ul className="grid gap-2.5 p-4 md:grid-cols-2 xl:grid-cols-3">
        {naming.items.map((i, n) => {
          const meta = serviceOfKind(i.kind);
          const Icon = meta.icon;
          const dirty = full(i.key, i.name) !== i.full;
          return (
            <motion.li key={i.key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(n, 12) * 0.03 }}
              className={clsx("lift flex items-center gap-3 rounded-[18px] border p-3", dirty ? "border-primary bg-primary/[0.06]" : "border-line bg-bg-2/40")}>
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[12px] text-white"
                style={{ background: `linear-gradient(135deg, ${ACCENT[meta.accent]}, color-mix(in srgb, ${ACCENT[meta.accent]} 60%, var(--primary-2)))` }}>
                <Icon className="h-5 w-5" /></span>
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{i.kind}</p>
                {naming.editable ? (
                  <span className="mt-0.5 flex items-center rounded-[10px] border border-line bg-surface font-mono text-[12.5px] focus-within:border-primary">
                    <span className="max-w-[45%] select-none truncate pl-2 text-muted" title={p}>{p}-</span>
                    <input value={names[i.key] ?? i.name} onChange={(e) => setNames({ ...names, [i.key]: e.target.value })} aria-label={`Name of ${i.kind} ${i.key}`}
                      className="min-w-0 flex-1 bg-transparent py-1 pr-2 font-semibold outline-none" />
                  </span>
                ) : <p className="break-all font-mono text-[12.5px] font-semibold">{i.full}</p>}
              </div>
            </motion.li>
          );
        })}
      </ul>
      {naming.editable && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-3">
          <span className="mr-auto text-sm text-muted">{changed.length ? `${changed.length} name(s) changed` : `${naming.items.length} resources`}</span>
          <Button size="sm" variant="ghost" icon={<RotateCcw className="h-4 w-4" />} disabled={!changed.length} onClick={() => { setPrefix(null); setNames({}); }}>Undo</Button>
          <Button size="sm" variant="primary" loading={busy} icon={<Check className="h-4 w-4" />} disabled={!changed.length} onClick={save}>Save names</Button>
        </div>
      )}
    </section>
  );
}
