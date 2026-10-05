import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion, useReducedMotion } from "framer-motion";
import {
  AlertTriangle, ArrowRight, Bell, CheckCircle2, ChevronDown, ClipboardPlus, Coins, Cpu, Download, FileCheck2, FileCode2, FileText, FolderOpen,
  GitPullRequestArrow, History, KeyRound, MessagesSquare, RefreshCw, Rocket, Wrench, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { DiffView } from "../../components/DiffView";
import { Overlay } from "../../components/Overlay";
import { CodeBlock, Markdown } from "../../components/Markdown";
import { Button, Skeleton, StatusPill } from "../../components/ui";
import { ApiError, type Agent } from "../../lib/api";
import { CREW, ORION } from "../../lib/crew";
import { fileUrl, flowApi, type AgentDetailData, type ChangeRequest, type ConvoEntry, type SignoffDoc } from "../../lib/flow";
import { ACCENT } from "../../lib/themes";
import { AgentAccess } from "./AgentAccess";
import { AgentTask } from "./AgentTask";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));
const PHASE: Record<string, string> = {}; // every agent is built
type Section = "needs" | "files" | "activity" | "conversation" | "signoff" | "access";

const CR_CHIP: Record<ChangeRequest["status"], { label: string; cls: string }> = {
  triage: { label: "Orion triaging", cls: "bg-primary/15 text-primary" },
  clarifying: { label: "Echo needs you", cls: "bg-warning/15 text-warning" },
  planning: { label: "Re-planning", cls: "bg-primary-2/15 text-primary-2" },
  in_progress: { label: "In progress", cls: "bg-primary-2/15 text-primary-2" },
  reviewing: { label: "Orion asks you to confirm", cls: "bg-warning/15 text-warning" },
  done: { label: "Done", cls: "bg-success/15 text-success" },
};

/** Everything about one agent, in one place: what needs you, its files, its timeline and its full conversation. */
export function AgentModal({ projectId, agent, onClose, onOpenWorkspace }: {
  projectId: string; agent: Agent | null; onClose: () => void; onOpenWorkspace: (k: string) => void;
}) {
  return (
    <Overlay open={!!agent} onClose={onClose} label="Agent details" z={80} align="bottom-sheet">
      {agent && <Sheet key={agent.key} projectId={projectId} a={agent} onClose={onClose} onOpenWorkspace={onOpenWorkspace} />}
    </Overlay>
  );
}

function Sheet({ projectId, a, onClose, onOpenWorkspace }: {
  projectId: string; a: Agent; onClose: () => void; onOpenWorkspace: (k: string) => void;
}) {
  const reduce = useReducedMotion();
  const m = META[a.key];
  const accent = ACCENT[a.accent] ?? "var(--primary)";
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["agent", projectId, a.key], queryFn: () => flowApi.agent(projectId, a.key),
    refetchInterval: a.status === "working" ? 4000 : 12000,
  });
  const pendingApprovals = data?.approvals.filter((x) => x.status === "pending") ?? [];
  const openChanges = data?.changes.filter((c) => c.status !== "done") ?? [];
  const failed = a.status === "failed" || a.status === "blocked";
  const needsCount = pendingApprovals.length + openChanges.length + (failed ? 1 : 0);
  const [section, setSection] = useState<Section>("needs");
  const [task, setTask] = useState(false);
  const { data: signoffs } = useQuery({ queryKey: ["signoffs", projectId], queryFn: () => flowApi.signoffs(projectId) });
  const picked = useRef(false);
  // land on the most useful section once: needs-you if anything is waiting, else the conversation
  useEffect(() => {
    if (!data || picked.current) return;
    picked.current = true;
    if (!needsCount) setSection(data.conversation.length ? "conversation" : "activity");
  }, [data, needsCount]);
  const hasWorkspace = true;

  const tabs: { id: Section; label: string; icon: typeof Bell; count?: number; hot?: boolean }[] = [
    { id: "needs", label: "Needs you", icon: Bell, count: needsCount, hot: needsCount > 0 },
    { id: "files", label: "Files", icon: FolderOpen, count: data?.files.length },
    { id: "activity", label: "Activity", icon: History, count: data?.events.length },
    { id: "conversation", label: "Conversation", icon: MessagesSquare, count: data?.conversation.length },
    { id: "signoff", label: "Sign-off", icon: FileCheck2, count: signoffs?.filter((s) => s.agent === a.key).length },
    { id: "access", label: "AWS access", icon: KeyRound },
  ];

  return (
    <>
      {/* accent glow behind the card */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[60vh] w-[70vw] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-25 blur-3xl"
        style={{ background: `radial-gradient(closest-side, ${accent}, transparent)` }} />
      <motion.div role="dialog" aria-modal aria-label={`${m?.persona} details`}
        initial={reduce ? { opacity: 0 } : { y: 40, scale: 0.94, opacity: 0 }} animate={{ y: 0, scale: 1, opacity: 1 }}
        exit={reduce ? { opacity: 0 } : { y: 24, scale: 0.97, opacity: 0 }} transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="relative flex h-[94dvh] w-full max-w-[1040px] flex-col overflow-hidden rounded-t-[28px] border border-line bg-surface shadow-2xl sm:h-[86dvh] sm:rounded-[30px]"
        style={{ boxShadow: `0 40px 120px -40px ${accent}` }}>

        {/* hero */}
        <div className="relative shrink-0 px-5 pb-3 pt-5 sm:px-7 sm:pt-6"
          style={{ background: `linear-gradient(150deg, color-mix(in srgb, ${accent} 26%, var(--surface)), var(--surface) 72%)` }}>
          <div className="absolute right-4 top-4 flex gap-2">
            <button onClick={() => refetch()} aria-label="Refresh" title="Refresh"
              className="grid h-9 w-9 place-items-center rounded-full bg-black/10 hover:bg-black/25">
              <RefreshCw className={clsx("h-4 w-4", isFetching && "animate-spin")} />
            </button>
            <button onClick={onClose} aria-label="Close" className="grid h-9 w-9 place-items-center rounded-full bg-black/10 hover:bg-black/25"><X className="h-4 w-4" /></button>
          </div>
          <div className="flex flex-wrap items-center gap-4 pr-24">
            <AgentAvatar agent={a.key} accent={a.accent} status={a.status} size={64} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-display text-2xl font-bold sm:text-[28px]">{m?.persona}</h2>
                <span className="rounded-md px-1.5 py-0.5 font-mono text-[11px] font-bold" style={{ color: accent, background: `color-mix(in srgb, ${accent} 18%, transparent)` }}>{m?.abbr}</span>
                <StatusPill status={a.status} agent />
                {data && <span className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[11px] text-muted">requirement {data.version}</span>}
              </div>
              <p className="text-sm text-text/85">{m?.role} · {a.model.replace("eu.anthropic.claude-", "").replace(/-\d{8}.*$/, "")}</p>
            </div>
          </div>

          <div className={clsx("mt-4 flex flex-wrap items-center gap-3 rounded-[16px] border px-3.5 py-2.5",
            failed ? "border-danger/40 bg-danger/10" : "border-line bg-bg-2/60")}>
            {failed ? <AlertTriangle className="h-4 w-4 shrink-0 text-danger" /> : a.status === "working"
              ? <span className="h-2 w-2 shrink-0 rounded-full pulse-ring" style={{ background: accent, ["--ring" as string]: accent }} />
              : <span className="h-2 w-2 shrink-0 rounded-full bg-muted" />}
            <p className="min-w-0 flex-1 text-sm">
              <span className="mr-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Right now</span>
              {a.status === "waiting" && PHASE[a.key] ? `Arrives in ${PHASE[a.key]}.` : a.activity || "Idle"}
            </p>
            <div className="flex gap-3 text-xs text-muted">
              <span className="inline-flex items-center gap-1"><Cpu className="h-3.5 w-3.5" />{(a.tokens_in + a.tokens_out).toLocaleString()}</span>
              <span className="inline-flex items-center gap-1"><Coins className="h-3.5 w-3.5" />${a.cost_usd.toFixed(3)}</span>
              {a.retries > 0 && <span className="inline-flex items-center gap-1"><RefreshCw className="h-3.5 w-3.5" />{a.retries}</span>}
            </div>
            {a.key !== "guide" && (
              <Button size="sm" icon={<ClipboardPlus className="h-4 w-4" />} onClick={() => setTask(true)} title={`Give ${m?.persona} something to do: it becomes a ticket`}>
                Give {m?.persona} a task</Button>
            )}
            {hasWorkspace && (
              <Button size="sm" variant="primary" icon={<ArrowRight className="h-4 w-4" />} onClick={() => onOpenWorkspace(a.key)}>Open workspace</Button>
            )}
          </div>

          <nav className="no-scrollbar -mx-1 mt-3 flex gap-1 overflow-x-auto px-1" aria-label="Agent sections">
            {tabs.map(({ id, label, icon: Icon, count, hot }) => (
              <button key={id} onClick={() => setSection(id)}
                className={clsx("relative flex shrink-0 items-center gap-2 rounded-[12px] px-3.5 py-2 text-sm font-semibold transition-colors",
                  section === id ? "text-text" : "text-muted hover:text-text")}>
                {section === id && <motion.span layoutId="agent-sec" className="absolute inset-0 rounded-[12px] bg-surface shadow-[0_6px_18px_-10px_rgba(0,0,0,.5)] ring-1 ring-line"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                <Icon className="relative h-4 w-4" /><span className="relative">{label}</span>
                {count !== undefined && count > 0 && (
                  <span className={clsx("relative rounded-full px-1.5 text-[10px] font-bold", hot ? "bg-warning text-black" : "bg-bg-2 text-muted")}>{count}</span>
                )}
              </button>
            ))}
          </nav>
        </div>

        {/* body */}
        <div className="relative min-h-0 flex-1 border-t border-line">
          {isLoading || !data ? (
            <div className="space-y-3 p-6"><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-40" /></div>
          ) : (
            // no exit animation: a section swap must never leave a layer behind that blocks clicks
            <motion.div key={section} className="absolute inset-0"
              initial={reduce ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.16 }}>
              {section === "needs" && <NeedsYou projectId={projectId} a={a} data={data} onGo={() => onOpenWorkspace(a.key)} />}
              {section === "files" && <Files projectId={projectId} data={data} />}
              {section === "activity" && <Activity data={data} accent={accent} />}
              {section === "conversation" && <Conversation a={a} data={data} accent={accent} />}
              {section === "signoff" && <Signoffs projectId={projectId} agent={a.key} persona={m?.persona ?? a.key} docs={(signoffs ?? []).filter((s) => s.agent === a.key)} />}
              {section === "access" && <AgentAccess projectId={projectId} agent={a.key} persona={m?.persona ?? a.key} accent={accent} />}
            </motion.div>
          )}
        </div>
      </motion.div>
      <AgentTask projectId={projectId} agent={a.key} persona={m?.persona ?? a.key} open={task} onClose={() => setTask(false)} />
    </>
  );
}

/* ── Sign-off: what the user signed off for this agent (written from the approval records) ── */
function Signoffs({ projectId, agent, persona, docs }: { projectId: string; agent: string; persona: string; docs: SignoffDoc[] }) {
  const [open, setOpen] = useState<string | null>(docs[docs.length - 1]?.path ?? null);
  const { data: text } = useQuery({ queryKey: ["file", projectId, "signoff-doc", open], queryFn: () => flowApi.fileText(projectId, open!), enabled: !!open });
  if (!docs.length) {
    return (
      <div className="no-scrollbar h-full overflow-y-auto p-6 sm:p-8">
        <div className="rounded-[20px] border border-dashed border-line p-6 text-center">
          <FileCheck2 className="mx-auto h-8 w-8 text-muted" />
          <p className="mt-2 font-display font-semibold">No sign-off yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted">{agent === "qa"
            ? "Quinn's test plan (you approve it as test lead) and his test sign-off report appear here."
            : `When you approve ${persona}'s work, a sign-off document is written: what was delivered, the files, the key facts, the review history and when you approved it.`}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      <ul className="no-scrollbar flex shrink-0 gap-1.5 overflow-x-auto border-b border-line p-3 md:w-64 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r">
        {docs.map((d) => (
          <li key={d.path} className="shrink-0">
            <button onClick={() => setOpen(d.path)}
              className={clsx("flex w-full items-start gap-2 rounded-[12px] px-3 py-2 text-left text-sm", open === d.path ? "bg-primary/15 text-text" : "text-muted hover:bg-bg-2 hover:text-text")}>
              <FileCheck2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
              <span><span className="block font-semibold leading-snug">{d.title}</span><span className="block font-mono text-[10.5px] opacity-70">{d.path}</span></span>
            </button>
          </li>
        ))}
      </ul>
      <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-4 sm:px-7">
        {open && (
          <div className="mb-3 flex justify-end">
            <a href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent(open)}&download=1`}
              className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary"><Download className="h-3.5 w-3.5" />Download</a>
          </div>
        )}
        {text ? <Markdown>{text}</Markdown> : <Skeleton className="h-48" />}
      </div>
    </div>
  );
}

/* ── Needs you ──────────────────────────────────────────────────────────── */
function NeedsYou({ projectId, a, data, onGo }: { projectId: string; a: Agent; data: AgentDetailData; onGo: () => void }) {
  const qc = useQueryClient();
  const m = META[a.key];
  const [busy, setBusy] = useState<string | null>(null);
  const pending = data.approvals.filter((x) => x.status === "pending");
  const decided = data.approvals.filter((x) => x.status !== "pending");
  const open = data.changes.filter((c) => c.status !== "done");
  const failed = a.status === "failed" || a.status === "blocked";

  const approve = async (id: string, title: string) => {
    setBusy(id);
    try {
      const r = await flowApi.decide(projectId, id, "approve");
      toast.success(`Approved: ${title}. ${r.next_label}`);
      ["agent", "approvals", "project", "mapping", "intake", "changes"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setBusy(null); }
  };

  return (
    <div className="no-scrollbar h-full space-y-4 overflow-y-auto p-5 sm:p-7">
      {!pending.length && !open.length && !failed && (
        <div className="flex flex-col items-center gap-3 rounded-[22px] border border-success/30 bg-success/[0.06] px-6 py-10 text-center">
          <CheckCircle2 className="h-10 w-10 text-success" />
          <p className="font-display text-lg font-semibold">Nothing needs you right now</p>
          <p className="max-w-md text-sm text-muted">
            {a.status === "working" ? `${m?.persona} is working. You'll see an approval card here when there's something to review.`
              : a.status === "done" ? `${m?.persona} has finished its part.` : `${m?.persona} is waiting for its turn.`}
          </p>
        </div>
      )}

      {failed && (
        <Card tone="var(--danger)" icon={<AlertTriangle className="h-5 w-5 text-danger" />} kicker={`${m?.persona} ran into a problem`} title={a.activity || "Failed"}>
          <p className="text-sm text-muted">Details are in the Activity and Conversation sections. Raise a change request to adjust the input, or retry from the workspace.</p>
        </Card>
      )}

      {pending.map((p) => (
        <Card key={p.id} tone="var(--warning)" icon={<Rocket className="h-5 w-5 text-warning" />} kicker="Waiting for your approval" title={p.title}
          actions={<>
            <Button size="sm" icon={<ArrowRight className="h-4 w-4" />} onClick={onGo}>Review in workspace</Button>
            <Button size="sm" variant="primary" className="shimmer" loading={busy === p.id} icon={<Rocket className="h-4 w-4" />} onClick={() => approve(p.id, p.title)}>Approve & continue</Button>
          </>}>
          <Clamp><Markdown>{p.summary}</Markdown></Clamp>
          <p className="mt-2 text-xs text-muted">Not right? Close this and use <b className="text-text">Change request</b> at the top of the project.</p>
        </Card>
      ))}

      {open.map((c) => <ChangeCard key={c.id} c={c} projectId={projectId} onGo={c.status === "clarifying" ? onGo : undefined} />)}

      {decided.length > 0 && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Earlier decisions</p>
          <ul className="space-y-1.5">
            {decided.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-2 rounded-[12px] bg-bg-2/60 px-3 py-2 text-sm">
                <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-bold uppercase",
                  d.status === "approved" ? "bg-success/15 text-success" : "bg-warning/15 text-warning")}>{d.status.replace("_", " ")}</span>
                <span className="min-w-0 flex-1 truncate">{d.title}</span>
                {d.comment && <span className="w-full truncate text-xs text-muted sm:w-auto">“{d.comment}”</span>}
                <span className="text-xs text-muted">{stamp(d.decided_at ?? d.created_at)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.changes.some((c) => c.status === "done") && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Completed change requests</p>
          <div className="space-y-2">{data.changes.filter((c) => c.status === "done").map((c) => <ChangeCard key={c.id} c={c} projectId={projectId} compact />)}</div>
        </div>
      )}
    </div>
  );
}

function ChangeCard({ c, projectId, onGo, compact }: { c: ChangeRequest; projectId: string; onGo?: () => void; compact?: boolean }) {
  const [diffOpen, setDiffOpen] = useState(false);
  const chip = CR_CHIP[c.status];
  return (
    <Card tone={c.status === "clarifying" ? "var(--warning)" : "var(--primary)"} icon={<GitPullRequestArrow className="h-5 w-5 text-primary" />}
      kicker={<span className="flex flex-wrap items-center gap-2">{c.label}<span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-bold normal-case tracking-normal", chip.cls)}>{chip.label}</span>
        {c.version_to && <span className="font-mono normal-case tracking-normal text-muted">{c.version_from} → {c.version_to}</span>}</span>}
      title={c.triage?.summary ?? c.text} compact={compact}
      actions={onGo ? <Button size="sm" variant="primary" icon={<MessagesSquare className="h-4 w-4" />} onClick={onGo}>Answer Echo</Button> : undefined}>
      {!compact && (
        <div className="space-y-2 text-sm">
          {c.triage && <p className="whitespace-pre-wrap text-muted">“{c.text}”</p>}
          {!!c.attachments.length && (
            <div className="flex flex-wrap gap-1.5">
              {c.attachments.map((f) => (
                <a key={f} href={fileUrl(projectId, f)} className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-0.5 text-xs hover:border-primary hover:text-primary">
                  <Download className="h-3 w-3" />{f}
                </a>
              ))}
            </div>
          )}
          {!!c.triage?.affected_agents.length && (
            <div className="flex flex-wrap gap-1.5">
              {c.triage.affected_agents.map((x) => (
                <span key={x.agent} title={x.why} className="inline-flex items-center gap-1 rounded-full bg-bg-2 px-2 py-0.5 text-xs">
                  {META[x.agent]?.persona ?? x.agent}<span className="text-muted">informed</span>
                </span>
              ))}
            </div>
          )}
          {!!c.triage?.needs_from_user.length && c.status === "clarifying" && (
            <ul className="list-disc space-y-0.5 pl-5 text-[13px]">{c.triage.needs_from_user.map((n) => <li key={n}>{n}</li>)}</ul>
          )}
        </div>
      )}
      {c.diff && (
        <div className="mt-2">
          <button onClick={() => setDiffOpen(!diffOpen)} className="inline-flex items-center gap-1 text-xs font-semibold text-primary-2">
            <ChevronDown className={clsx("h-3.5 w-3.5 transition-transform", diffOpen && "rotate-180")} />What changed in the requirement
          </button>
          {diffOpen && <DiffView diff={c.diff} className="mt-2" />}
        </div>
      )}
    </Card>
  );
}

function Card({ tone, icon, kicker, title, children, actions, compact }: {
  tone: string; icon: React.ReactNode; kicker: React.ReactNode; title: string; children?: React.ReactNode; actions?: React.ReactNode; compact?: boolean;
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className={clsx("relative overflow-hidden rounded-[20px] border bg-bg-2/40", compact ? "px-4 py-3" : "p-4 sm:p-5")}
      style={{ borderColor: `color-mix(in srgb, ${tone} 40%, transparent)` }}>
      <div className="pointer-events-none absolute inset-y-0 left-0 w-1" style={{ background: tone }} />
      <div className="flex items-start gap-3">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">{kicker}</div>
          <p className={clsx("font-display font-semibold leading-snug", compact ? "text-sm" : "text-lg")}>{title}</p>
          {children && <div className="mt-2">{children}</div>}
          {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
        </div>
      </div>
    </motion.div>
  );
}

function Clamp({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div className={clsx("relative text-sm", !open && "max-h-40 overflow-hidden")}>
        {children}
        {!open && <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-[linear-gradient(transparent,color-mix(in_srgb,var(--bg-2)_40%,var(--surface)))]" />}
      </div>
      <button onClick={() => setOpen(!open)} className="mt-1 text-xs font-semibold text-primary-2">{open ? "Show less" : "Show more"}</button>
    </div>
  );
}

/* ── Files ──────────────────────────────────────────────────────────────── */
function Files({ projectId, data }: { projectId: string; data: AgentDetailData }) {
  const [sel, setSel] = useState<string | null>(data.files[0]?.path ?? null);
  const { data: text, isLoading } = useQuery({
    queryKey: ["file-text", projectId, sel, data.version], queryFn: () => flowApi.fileText(projectId, sel!), enabled: !!sel,
  });
  if (!data.files.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <FolderOpen className="h-10 w-10 text-muted" />
        <p className="font-display text-lg font-semibold">No files yet</p>
        <p className="text-sm text-muted">Files this agent produces in {data.version} appear here.</p>
      </div>
    );
  }
  const ext = sel?.split(".").pop()?.toLowerCase() ?? "";
  const textual = ["md", "txt", "json", "xml", "yaml", "yml", "py", "tf", "csv", "xsd", "sql", "sh", "diff", "drawio", "log"].includes(ext);
  return (
    <div className="grid h-full min-h-0 md:grid-cols-[280px_minmax(0,1fr)]">
      <ul className="no-scrollbar max-h-48 space-y-1 overflow-y-auto border-b border-line p-3 md:max-h-none md:border-b-0 md:border-r">
        {data.files.map((f) => (
          <li key={f.path}>
            <button onClick={() => setSel(f.path)}
              className={clsx("flex w-full items-center gap-2.5 rounded-[12px] px-3 py-2 text-left transition-colors",
                sel === f.path ? "bg-primary/12 ring-1 ring-primary/40" : "hover:bg-bg-2/70")}>
              {f.path.endsWith(".md") ? <FileText className="h-4 w-4 shrink-0 text-primary-2" /> : <FileCode2 className="h-4 w-4 shrink-0 text-primary" />}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[12.5px]">{f.path}</span>
                <span className="block text-[11px] text-muted">{fmtSize(f.size)} · {stamp(f.modified)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="flex min-h-0 flex-col">
        {sel && (
          <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
            <span className="min-w-0 flex-1 truncate font-mono text-sm">{sel}</span>
            <span className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[11px] text-muted">{data.version}</span>
            <a href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent(sel)}&download=1`}
              className="inline-flex items-center gap-1 rounded-[10px] border border-line px-2.5 py-1 text-xs hover:border-primary/60">
              <Download className="h-3.5 w-3.5" />Download
            </a>
          </div>
        )}
        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
          {isLoading ? <Skeleton className="h-64" />
            : !textual ? <p className="text-sm text-muted">Binary file: use Download to open it.</p>
              : ext === "md" ? <Markdown>{text ?? ""}</Markdown>
                : ext === "diff" ? <DiffView diff={text ?? ""} />
                  : <CodeBlock raw={text ?? ""} />}
        </div>
      </div>
    </div>
  );
}

/* ── Activity ───────────────────────────────────────────────────────────── */
function Activity({ data, accent }: { data: AgentDetailData; accent: string }) {
  const groups = useMemo(() => {
    const out: { day: string; items: AgentDetailData["events"] }[] = [];
    for (const e of data.events.filter((x) => x.type !== "intake.delta")) {
      const day = dayOf(e.created_at);
      const g = out[out.length - 1];
      if (g?.day === day) g.items.push(e); else out.push({ day, items: [e] });
    }
    return out;
  }, [data.events]);
  if (!groups.length) return <Empty icon={<History className="h-10 w-10 text-muted" />} title="No activity yet" />;
  return (
    <div className="no-scrollbar h-full overflow-y-auto px-5 py-5 sm:px-7">
      {groups.map((g) => (
        <section key={g.day} className="mb-5">
          <p className="sticky top-0 z-10 mb-2 inline-block rounded-full bg-surface/90 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted backdrop-blur">{g.day}</p>
          <ol className="relative ml-2 border-l border-line pl-5">
            {g.items.map((e) => <TimelineItem key={e.id} e={e} accent={accent} />)}
          </ol>
        </section>
      ))}
    </div>
  );
}

function TimelineItem({ e, accent }: { e: AgentDetailData["events"][number]; accent: string }) {
  const [open, setOpen] = useState(false);
  const d = e.data as { asked?: string; captured?: { topic: string; value: string }[]; status?: string };
  const extra = !!(d?.asked || d?.captured?.length);
  const color = e.type.includes("fail") || d?.status === "failed" ? "var(--danger)"
    : e.type.startsWith("approval") ? "var(--warning)" : e.type.startsWith("change") ? "var(--primary)" : d?.status === "done" ? "var(--success)" : accent;
  return (
    <li className="relative pb-3">
      <span className="absolute -left-[26px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-surface" style={{ background: color }} />
      <button disabled={!extra && e.message.length < 140} onClick={() => setOpen(!open)} className="w-full text-left">
        <div className="flex items-baseline gap-3">
          <span className="w-12 shrink-0 font-mono text-[11px] text-muted">{timeOf(e.created_at)}</span>
          <span className={clsx("min-w-0 flex-1 text-[13.5px] leading-snug", !open && "line-clamp-2")}>{e.message}</span>
          {extra && <ChevronDown className={clsx("h-3.5 w-3.5 shrink-0 text-muted transition-transform", open && "rotate-180")} />}
        </div>
      </button>
      {open && extra && (
        <div className="ml-[60px] mt-2 space-y-2 rounded-[12px] border border-line bg-bg-2/60 p-3 text-[12.5px]">
          {!!d.captured?.length && (
            <div>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-success">Captured</p>
              {d.captured.map((c) => <p key={c.topic}><span className="text-muted">{c.topic}:</span> {c.value}</p>)}
            </div>
          )}
          {d.asked && (
            <div>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">Asked</p>
              <Markdown>{d.asked}</Markdown>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/* ── Conversation ───────────────────────────────────────────────────────── */
function Conversation({ a, data, accent }: { a: Agent; data: AgentDetailData; accent: string }) {
  const [filter, setFilter] = useState<"all" | "talk" | "work">("all");
  const end = useRef<HTMLDivElement>(null);
  const items = data.conversation.filter((c) => filter === "all" || (filter === "work" ? c.who === "work" : c.who !== "work"));
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [filter, data.conversation.length]);
  const m = META[a.key];
  if (!data.conversation.length) return <Empty icon={<MessagesSquare className="h-10 w-10 text-muted" />} title="No conversation yet" />;
  let lastDay = "";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b border-line px-5 py-2 sm:px-7">
        {([["all", "Everything"], ["talk", "Messages"], ["work", "Work steps"]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={clsx("rounded-full px-3 py-1 text-xs font-semibold", filter === k ? "bg-primary text-on-primary" : "text-muted hover:text-text")}>{l}</button>
        ))}
        <span className="ml-auto text-xs text-muted">{items.length} entries</span>
      </div>
      <div className="no-scrollbar min-h-0 flex-1 space-y-2.5 overflow-y-auto px-4 py-4 sm:px-7">
        {items.map((c, i) => {
          const day = dayOf(c.ts);
          const sep = day !== lastDay;
          lastDay = day;
          return (
            <div key={i}>
              {sep && <p className="my-3 text-center text-[11px] font-semibold uppercase tracking-wider text-muted">{day}</p>}
              <Bubble c={c} persona={m?.persona ?? a.key} agentKey={a.key} accentKey={a.accent} accent={accent} />
            </div>
          );
        })}
        <div ref={end} />
      </div>
    </div>
  );
}

function Bubble({ c, persona, agentKey, accentKey, accent }: { c: ConvoEntry; persona: string; agentKey: string; accentKey: string; accent: string }) {
  if (c.who === "work") {
    return (
      <div className="flex items-start gap-2 px-1 text-[12.5px]">
        <Wrench className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
        <p className="min-w-0 flex-1"><span className="font-semibold">{c.title}</span>{c.body && <span className="break-words text-muted"> · {c.body}</span>}</p>
        <span className="shrink-0 font-mono text-[10.5px] text-muted">{timeOf(c.ts)}</span>
      </div>
    );
  }
  const user = c.who === "user";
  const orion = c.who === "orion";
  const peer = c.who === "peer" && c.sender ? META[c.sender] : null;
  const avatarKey = orion ? "cto" : peer ? peer.key : agentKey;
  const avatarAccent = orion ? "violet" : peer ? peer.accent : accentKey;
  const tone = orion ? "var(--primary)" : peer ? ACCENT[peer.accent] : accent;
  return (
    <div className={clsx("flex gap-2.5", user && "flex-row-reverse")}>
      {!user && <div className="mt-1 shrink-0"><AgentAvatar agent={avatarKey} accent={avatarAccent} status="done" size={30} /></div>}
      <div className={clsx("max-w-[min(680px,85%)] rounded-[18px] px-4 py-2.5 text-[13.5px]",
        user ? "rounded-tr-[6px] bg-primary/14" : "rounded-tl-[6px] border bg-bg-2/60")}
        style={!user ? { borderColor: `color-mix(in srgb, ${tone} 30%, var(--border))` } : undefined}>
        <p className="mb-0.5 flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-wider text-muted">
          {user ? "You" : orion ? "Orion · CTO" : peer ? `${peer.persona} · ${peer.abbr}` : persona}<span className="font-mono normal-case tracking-normal">{timeOf(c.ts)}</span>
        </p>
        {c.title && <p className="font-semibold">{c.title}</p>}
        {c.body && (user ? <p className="whitespace-pre-wrap">{c.body}</p> : <Markdown>{c.body}</Markdown>)}
      </div>
    </div>
  );
}

function Empty({ icon, title }: { icon: React.ReactNode; title: string }) {
  return <div className="flex h-full flex-col items-center justify-center gap-2 p-8">{icon}<p className="font-display text-lg font-semibold">{title}</p></div>;
}

/* ── helpers ────────────────────────────────────────────────────────────── */
const asDate = (iso: string) => new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
const timeOf = (iso: string) => asDate(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const stamp = (iso: string) => asDate(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
function dayOf(iso: string) {
  const d = asDate(iso);
  const today = new Date();
  const y = new Date(today); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}
const fmtSize = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`);
