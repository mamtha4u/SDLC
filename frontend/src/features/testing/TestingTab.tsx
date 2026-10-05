import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ClipboardList, Code2, Download, FileCheck2, FlaskConical, MinusCircle, PlayCircle,
  ShieldCheck, Ticket, UserCheck, XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { AgentLive } from "../../components/AgentLive";
import { Markdown } from "../../components/Markdown";
import type { WorkspaceTab } from "../../components/NextStep";
import { Button, Skeleton } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type BuildState, type TestCase, type TestingState } from "../../lib/flow";
import { celebrate } from "../../lib/fx";
import { timeAgo } from "../../lib/time";

const dl = (pid: string, path: string) => `/api/projects/${pid}/files/content?path=${encodeURIComponent(path)}&download=1`;
const CAT_TONE: Record<string, string> = {
  "happy path": "var(--success)", negative: "var(--danger)", "edge case": "var(--warning)", "error handling": "var(--primary)",
  logging: "var(--primary-2)", security: "var(--danger)", "non-functional": "var(--text-muted)",
};
const PRIO: Record<string, string> = { high: "bg-danger/15 text-danger", medium: "bg-warning/15 text-warning", low: "bg-bg-2 text-muted" };

/** Quinn's work, the way the team's manual tester works: the test plan → you approve it as test lead → he tests every
 *  scenario live → tickets to Dev / Terra → retests → his sign-off report → your sign-off. */
export function TestingTab({ projectId, onOpenFile, onGo }: { projectId: string; onOpenFile: (p: string) => void; onGo: (t: WorkspaceTab) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["testing", projectId], queryFn: () => flowApi.testing(projectId),
    refetchInterval: (q) => ((q.state.data as TestingState | undefined)?.state?.status === "working" ? 3000 : 12000),
  });
  const legacy = useQuery({ queryKey: ["build", projectId], queryFn: () => flowApi.build(projectId) });
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-32" /><Skeleton className="h-64" /></div>;
  const working = data.state?.status === "working";
  const planning = working && /plan/i.test(data.state!.activity);
  return (
    <div className="space-y-5">
      <Lifecycle t={data} />
      {working && data.state && (
        <AgentLive projectId={projectId} agent="qa" startedAt={data.state.started_at} activity={data.state.activity}
          title={planning ? "Quinn is writing the test plan" : /retest/i.test(data.state.activity) ? "Quinn is retesting live" : "Quinn is testing the live flow in AWS"}
          steps={planning ? ["Reading the requirement & mapping", "Writing every scenario", "Checks: every example covered"] : ["Calling the live flow", "Reading queues & logs", "Results, tickets & report"]}
          step={planning ? 1 : /result|ticket|✅/i.test(data.state.activity) ? 2 : 1}
          hint={planning ? "No AWS calls: only the plan. You approve it as test lead before any test runs." : "With Quinn's own role, scenario by scenario from the approved plan. Every failure becomes a ticket."} />
      )}
      {working && !planning && data.plan && <LiveRun t={data} />}
      <PlanCard projectId={projectId} t={data} onOpenFile={onOpenFile} />
      <RunsCard t={data} />
      <DefectsCard t={data} onGo={onGo} />
      <SignoffCard projectId={projectId} t={data} />
      {legacy.data?.qa.data && <LegacyQa data={legacy.data.qa.data} onOpenFile={onOpenFile} />}
    </div>
  );
}

/* ── the live run, scenario by scenario (user, 10-05: "it's not showing how many test cases are done and which one is
 *    running now") ───────────────────────────────────────────────────────── */
function LiveRun({ t }: { t: TestingState }) {
  const p = t.progress;
  const cases = t.plan?.cases ?? [];
  const by = Object.fromEntries((p?.results ?? []).map((r) => [r.id, r]));
  const total = p?.total || cases.length;
  const done = p?.done ?? 0;
  const now = p?.current ? cases.find((c) => c.id === p.current) ?? { id: p.current, title: by[p.current]?.title ?? "" } : null;
  const last = [...(p?.results ?? [])].reverse().find((r) => r.status !== "running");
  return (
    <section className="relative overflow-hidden rounded-[24px] border border-line bg-surface p-5">
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">Live test run</p>
          <p className="font-display text-2xl font-bold tabular-nums">{done} <span className="text-base font-semibold text-muted">of {total} scenarios done</span></p>
          <p className="text-sm text-muted">
            <span className="text-success">{p?.passed ?? 0} passed</span> · <span className={(p?.failed ?? 0) ? "text-danger" : ""}>{p?.failed ?? 0} failed</span>
            {done < total ? ` · ${total - done} to go` : " · writing the report"}</p>
        </div>
        {now && (
          <div className="min-w-[240px] max-w-md rounded-[16px] border border-primary-2/40 bg-primary-2/[0.07] px-3.5 py-2.5">
            <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-primary-2">
              <span className="h-2 w-2 rounded-full bg-primary-2 pulse-ring" style={{ ["--ring" as string]: "var(--primary-2)" }} />Testing now</p>
            <p className="text-sm"><b className="font-mono">{now.id}</b> {now.title}</p>
          </div>
        )}
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-bg-2">
        <motion.div className="h-full rounded-full bg-[linear-gradient(90deg,var(--success),var(--primary))]" initial={false}
          animate={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} transition={{ ease: "easeOut", duration: 0.6 }} />
      </div>
      <ul className="mt-4 flex flex-wrap gap-1.5">{cases.map((c) => {
        const r = by[c.id];
        const s = r?.status ?? "todo";
        return (
          <li key={c.id} title={`${c.id} · ${c.title}${r?.saw ? `\n${r.saw}` : ""}`}
            className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] font-semibold",
              s === "passed" ? "border-success/40 bg-success/10 text-success" : s === "failed" ? "border-danger/40 bg-danger/10 text-danger"
                : s === "not_run" ? "border-warning/40 bg-warning/10 text-warning" : s === "running" ? "border-primary-2 bg-primary-2/15 text-text pulse-ring"
                  : "border-line text-muted")} style={s === "running" ? { ["--ring" as string]: "var(--primary-2)" } : undefined}>
            {s === "passed" ? <CheckCircle2 className="h-3 w-3" /> : s === "failed" ? <XCircle className="h-3 w-3" /> : s === "not_run" ? <MinusCircle className="h-3 w-3" /> : null}
            {c.id}
          </li>
        );
      })}</ul>
      {last && (
        <p className="mt-3 rounded-[12px] bg-bg-2/50 px-3 py-2 text-sm">
          <b className={last.status === "passed" ? "text-success" : last.status === "failed" ? "text-danger" : "text-warning"}>
            {last.status === "passed" ? "✓" : last.status === "failed" ? "✗" : "⏭"} {last.id}</b> {last.title}
          <span className="block text-xs text-muted">{last.did ? `${last.did} → ` : ""}{last.saw}</span>
        </p>
      )}
    </section>
  );
}

/* ── the QA life cycle ─────────────────────────────────────────────────── */
type S = "done" | "active" | "review" | "issue" | "todo";
const TONE: Record<S, string> = { done: "var(--success)", active: "var(--primary-2)", review: "var(--warning)", issue: "var(--danger)", todo: "var(--border)" };

function Lifecycle({ t }: { t: TestingState }) {
  const working = t.state?.status === "working";
  const planning = working && /plan/i.test(t.state?.activity ?? "");
  const open = t.tickets.filter((x) => ["open", "reopened", "in_progress", "resolved"].includes(x.status));
  const lastRun = t.runs[t.runs.length - 1];
  const stations: { label: string; sub: string; s: S; icon: React.ReactNode }[] = [
    { label: "Test plan", sub: t.plan ? `${t.plan.cases.length} scenarios` : "Quinn writes it", icon: <ClipboardList className="h-5 w-5" />,
      s: planning ? "active" : t.plan ? "done" : "todo" },
    { label: "You approve", sub: t.plan?.status === "approved" ? "approved" : "as test lead", icon: <UserCheck className="h-5 w-5" />,
      s: t.pending?.stage === "test_plan" ? "review" : t.plan?.status === "approved" ? "done" : "todo" },
    { label: "Live testing", sub: lastRun ? `${lastRun.passed}/${lastRun.passed + lastRun.failed} passed` : t.live ? `${t.live.checks.filter((c) => c.passed).length}/${t.live.checks.length} passed` : "every scenario",
      icon: <FlaskConical className="h-5 w-5" />, s: working && !planning ? "active" : t.live ? (t.live.checks.every((c) => c.passed) ? "done" : "issue") : "todo" },
    { label: "Tickets & retests", sub: t.tickets.length ? `${t.tickets.length} raised · ${open.length} open` : t.live ? "none raised" : "Dev / Terra fix",
      icon: <Ticket className="h-5 w-5" />, s: open.length ? (t.pending?.stage === "live_bugs" ? "review" : "issue") : t.live ? "done" : "todo" },
    { label: "Sign-off", sub: t.signed_off ? "signed off" : t.pending?.stage === "live" ? "needs you" : "Quinn, then you", icon: <FileCheck2 className="h-5 w-5" />,
      s: t.signed_off ? "done" : t.pending?.stage === "live" ? "review" : "todo" },
  ];
  const reached = stations.reduce((m, st, i) => (st.s !== "todo" ? i : m), 0);
  return (
    <section className="spotlight sheen elev relative overflow-hidden rounded-[26px] border border-line bg-surface p-5">
      <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--danger) 16%, transparent)" }} />
      <div className="relative flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">How Quinn tests it</p>
          <h2 className="font-display text-xl font-bold tracking-tight">Plan, your approval, live tests, sign-off</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted">Like your team's manual tester: Quinn writes every scenario, you approve them as test lead, he runs them on the real flow in AWS, failures become tickets for Dev or Terra, and he signs off when everything passes.</p>
        </div>
        <AgentAvatar agent="qa" accent="rose" status={working ? "working" : t.signed_off ? "done" : "waiting"} size={44} />
      </div>
      <div className="no-scrollbar relative mt-4 overflow-x-auto pb-1">
        <ol className="relative flex min-w-[620px] justify-between">
          <span className="absolute left-[8%] right-[8%] top-[26px] h-[3px] rounded-full bg-bg-2" />
          <span className="absolute left-[8%] top-[26px] h-[3px] rounded-full bg-[linear-gradient(90deg,var(--primary),var(--danger))]"
            style={{ width: `calc(84% * ${reached / (stations.length - 1)})`, transition: "width 0.9s cubic-bezier(.2,.7,.2,1)" }} />
          {stations.map((st, i) => (
            <motion.li key={st.label} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i }}
              className="relative flex w-[18%] flex-col items-center text-center">
              <span className={clsx("relative grid h-[54px] w-[54px] place-items-center rounded-full border-2 bg-surface", (st.s === "active" || st.s === "review") && "pulse-ring")}
                style={{ borderColor: TONE[st.s], color: st.s === "todo" ? "var(--text-muted)" : TONE[st.s], ["--ring" as string]: TONE[st.s] }}>
                {st.icon}
                {st.s === "done" && <span className="absolute -bottom-0.5 -right-0.5 grid h-5 w-5 place-items-center rounded-full bg-success text-white ring-2 ring-surface"><CheckCircle2 className="h-3.5 w-3.5" /></span>}
              </span>
              <span className={clsx("mt-2 text-[12px] font-semibold", st.s === "todo" ? "text-muted" : "text-text")}>{st.label}</span>
              <span className="mt-0.5 text-[10.5px] leading-tight" style={{ color: st.s === "review" || st.s === "issue" ? TONE[st.s] : "var(--text-muted)" }}>{st.s === "review" ? "needs you" : st.sub}</span>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ── the test plan ─────────────────────────────────────────────────────── */
function PlanCard({ projectId, t, onOpenFile }: { projectId: string; t: TestingState; onOpenFile: (p: string) => void }) {
  const qc = useQueryClient();
  const [cat, setCat] = useState<string | null>(null);
  const [changes, setChanges] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const p = t.plan;
  // during a live run, the results of this run (as Quinn records them); otherwise the last finished run
  const running = t.state?.status === "working" && !/plan/i.test(t.state.activity) && !!t.progress;
  const results = useMemo(() => Object.fromEntries(running
    ? (t.progress?.results ?? []).filter((r) => r.status === "passed" || r.status === "failed")
      .map((r) => [r.id, { passed: r.status === "passed", evidence: r.saw, did: r.did }])
    : (t.live?.checks ?? []).map((c) => [c.id, c])), [t.live, t.progress, running]);
  const notRun = useMemo(() => Object.fromEntries(running
    ? (t.progress?.results ?? []).filter((r) => r.status === "not_run").map((r) => [r.id, r.saw])
    : (t.live?.not_run ?? []).map((x) => [x.id, x.why])), [t.live, t.progress, running]);
  const refresh = () => ["testing", "approvals", "project", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
  const decide = async (approve: boolean, origin?: Element | null) => {
    if (!t.pending) return;
    setBusy(true);
    try {
      await flowApi.decide(projectId, t.pending.id, approve ? "approve" : "changes", approve ? "" : changes ?? "");
      if (approve) { celebrate(origin ?? null, false); toast.success("Test plan approved: Quinn starts testing live"); }
      else { toast.success("Sent to Quinn: he revises the plan"); setChanges(null); }
      refresh();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setBusy(false); }
  };
  const start = async () => {
    setBusy(true);
    try { await flowApi.newTestCycle(projectId); toast.success("Quinn is writing the test plan"); refresh(); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't start"); }
    finally { setBusy(false); }
  };
  const idle = t.state?.status !== "working" && !t.pending;

  if (!p) {
    return (
      <Card icon={<ClipboardList className="h-4 w-4" />} title="Test plan" sub="Quinn's scenarios, approved by you before any test runs">
        <div className="flex flex-wrap items-center gap-4 rounded-[18px] border border-dashed border-line bg-bg-2/40 p-4">
          <p className="min-w-0 flex-1 text-sm text-muted">{t.deployed
            ? "This project was tested before test plans existed (Quinn's checks are below). Start a new test cycle to get a full plan: happy path, negative, edge cases, error handling, logging. You approve it, then he tests."
            : "After Dev's code is deployed and sanity-checked, Quinn writes the test plan: every scenario with steps, data and the expected result. You approve it as test lead; only then does he test."}</p>
          {t.deployed && idle && <Button variant="primary" icon={<PlayCircle className="h-4 w-4" />} loading={busy} onClick={start} title="About $0.10 for the plan; the live test run after your approval about $1">Start a test cycle</Button>}
        </div>
      </Card>
    );
  }
  const cats = Object.entries(p.cases.reduce<Record<string, number>>((m, c) => ({ ...m, [c.category]: (m[c.category] ?? 0) + 1 }), {}));
  const shown = cat ? p.cases.filter((c) => c.category === cat) : p.cases;
  const waiting = t.pending?.stage === "test_plan";
  return (
    <Card icon={<ClipboardList className="h-4 w-4" />} title="Test plan" sub={`${p.version} · written by Quinn${p.written_at ? ` ${p.written_at}` : ""}`}
      right={<>
        {p.status === "approved"
          ? <Chip cls="bg-success/15 text-success" label={`Approved by you${p.approved_at ? ` · ${p.approved_at}` : ""}`} />
          : <Chip cls="bg-warning/15 text-warning" label="Waiting for your approval" />}
        <a href={dl(projectId, "reports/test_plan.md")} className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary"><Download className="h-3.5 w-3.5" />Plan (.md)</a>
        <button onClick={() => onOpenFile("reports/test_plan.md")} className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary"><Code2 className="h-3.5 w-3.5" />In Code</button>
        {t.deployed && idle && <Button size="sm" icon={<PlayCircle className="h-3.5 w-3.5" />} loading={busy} onClick={start} title="Quinn refreshes the plan for your approval, then tests everything again (about $1)">New test cycle</Button>}
      </>}>
      {!t.plan_current && (
        <p className="flex items-start gap-2 rounded-[12px] bg-warning/10 px-3 py-2 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          The requirement or the mapping changed since this plan. Quinn refreshes it (for your approval) before the next test run.</p>
      )}
      <p className="text-sm">{p.summary}</p>
      {p.changes && <p className="rounded-[12px] bg-primary/10 px-3 py-2 text-sm"><b>What changed:</b> {p.changes}</p>}
      {waiting && (
        <div className="beam-border relative overflow-hidden rounded-[18px] border border-warning/50 bg-warning/[0.07] p-4" style={{ ["--beam-1" as string]: "var(--warning)" }}>
          <p className="flex items-center gap-2 font-display font-semibold"><UserCheck className="h-4 w-4 text-warning" />You're the test lead: are these the right scenarios?</p>
          <p className="mt-0.5 text-sm text-muted">Approve, and Quinn runs every one of them live in AWS. Missing a case, or one is wrong? Ask for changes: he revises the plan and asks you again.</p>
          {changes !== null && (
            <textarea autoFocus value={changes} onChange={(e) => setChanges(e.target.value)} rows={3}
              placeholder="e.g. Add a scenario for an order with 3 line items, and check the DLQ after the SQS failure case."
              className="mt-3 w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {changes === null ? (
              <>
                <Button variant="primary" className="shimmer" icon={<CheckCircle2 className="h-4 w-4" />} loading={busy} onClick={(e) => decide(true, e.currentTarget)}>Approve the test plan</Button>
                <Button onClick={() => setChanges("")}>Request changes</Button>
              </>
            ) : (
              <>
                <Button variant="primary" loading={busy} disabled={!changes.trim()} onClick={() => decide(false)}>Send to Quinn</Button>
                <Button variant="ghost" onClick={() => setChanges(null)}>Cancel</Button>
              </>
            )}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <button onClick={() => setCat(null)} className={clsx("rounded-full px-3 py-1 text-xs font-semibold", !cat ? "bg-primary text-on-primary" : "bg-bg-2 text-muted hover:text-text")}>All {p.cases.length}</button>
        {cats.map(([k, n]) => (
          <button key={k} onClick={() => setCat(cat === k ? null : k)}
            className={clsx("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold", cat === k ? "bg-primary text-on-primary" : "bg-bg-2 text-muted hover:text-text")}>
            <span className="h-2 w-2 rounded-full" style={{ background: CAT_TONE[k] ?? "var(--text-muted)" }} />{k} {n}</button>
        ))}
      </div>
      <ul className="space-y-1.5">{shown.map((c) => <CaseRow key={c.id} c={c} r={results[c.id]} why={notRun[c.id]}
        now={running && t.progress?.current === c.id} />)}</ul>
      <Fold title="Scope, approach and criteria">
        <div className="grid gap-4 text-sm md:grid-cols-2">
          <div><Label>Scope</Label><p>{p.scope}</p><Label className="mt-3">Approach</Label><p>{p.approach}</p></div>
          <div>
            {p.out_of_scope.length > 0 && (<><Label>Not in scope</Label><ul className="list-disc space-y-1 pl-5">{p.out_of_scope.map((x) => <li key={x.what}><b>{x.what}</b>: <span className="text-muted">{x.why}</span></li>)}</ul></>)}
            <Label className="mt-3">Entry criteria</Label><ul className="list-disc space-y-0.5 pl-5">{p.entry_criteria.map((x) => <li key={x}>{x}</li>)}</ul>
            <Label className="mt-3">Exit criteria</Label><ul className="list-disc space-y-0.5 pl-5">{p.exit_criteria.map((x) => <li key={x}>{x}</li>)}</ul>
          </div>
        </div>
      </Fold>
    </Card>
  );
}

function CaseRow({ c, r, why, now }: { c: TestCase; r?: { passed: boolean; evidence: string; did?: string }; why?: string; now?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={clsx("overflow-hidden rounded-[14px] border bg-bg-2/40", now ? "border-primary-2" : "border-line")}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-bg-2/70" aria-expanded={open}>
        {now ? <span className="h-4 w-4 shrink-0 rounded-full bg-primary-2 pulse-ring" title="Quinn is testing this now" style={{ ["--ring" as string]: "var(--primary-2)" }} />
          : r ? (r.passed ? <CheckCircle2 className="h-4 w-4 shrink-0 text-success" /> : <XCircle className="h-4 w-4 shrink-0 text-danger" />)
          : why ? <MinusCircle className="h-4 w-4 shrink-0 text-warning" /> : <span className="h-4 w-4 shrink-0 rounded-full border-2 border-line" title="Not run yet" />}
        <b className="shrink-0 font-mono text-xs">{c.id}</b>
        <span className="min-w-0 flex-1 truncate">{c.title}</span>
        <span className="hidden items-center gap-1 text-[11px] text-muted sm:inline-flex"><span className="h-2 w-2 rounded-full" style={{ background: CAT_TONE[c.category] }} />{c.category}</span>
        <span className={clsx("rounded-full px-2 py-0.5 text-[10.5px] font-semibold", PRIO[c.priority])}>{c.priority}</span>
        <ChevronDown className={clsx("h-4 w-4 shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="space-y-3 border-t border-line px-3 py-3 text-sm">
              {/* plain words first (user, 10-05: "show the flow and the expected output with one example") */}
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-[12px] bg-primary/[0.06] px-3 py-2.5">
                  <Label>What Quinn does</Label>
                  <p className="whitespace-pre-line">{c.flow || plainSteps(c.steps)}</p>
                </div>
                <div className="rounded-[12px] bg-success/[0.06] px-3 py-2.5">
                  <Label>Expected result</Label>
                  <p>{c.expected}</p>
                  {c.example && <><Label className="mt-2">Example</Label><p className="font-mono text-[12px]">{c.example}</p></>}
                </div>
              </div>
              {(r || why) && (
                <div className={clsx("rounded-[12px] px-3 py-2", r ? (r.passed ? "bg-success/[0.08]" : "bg-danger/[0.08]") : "bg-warning/[0.08]")}>
                  <Label>{r ? (r.passed ? "✓ Passed: what happened" : "✗ Failed: what happened") : "Not run"}</Label>
                  {r?.did && <p className="text-[13px]"><span className="text-muted">Quinn did:</span> {r.did}</p>}
                  <p className="break-words text-[13px]">{r ? <><span className="text-muted">{r.did ? "Came back: " : ""}</span>{r.evidence}</> : why}</p>
                </div>
              )}
              <Fold title="For the record: exact steps and test data">
                <div className="grid gap-3 md:grid-cols-2">
                  <Field k="From" v={c.requirement_ref} />
                  {c.preconditions && <Field k="Preconditions" v={c.preconditions} />}
                  <Field k="Exact steps" v={c.steps} />
                  <div className="md:col-span-2"><Label>Test data</Label><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-[10px] bg-surface p-2 font-mono text-[11.5px]">{c.test_data}</pre></div>
                </div>
              </Fold>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

/* ── runs, defects, sign-off ───────────────────────────────────────────── */
function RunsCard({ t }: { t: TestingState }) {
  const live = t.live;
  if (!live && !t.runs.length) return null;
  return (
    <Card icon={<FlaskConical className="h-4 w-4" />} title="Test runs" sub={live ? `last: ${live.version} · ${live.calls} real calls as ${live.role}` : ""}>
      {live && <p className="text-sm text-muted">{live.summary}</p>}
      {t.runs.length > 0 ? (
        <ol className="relative space-y-2 border-l-2 border-line pl-4">{[...t.runs].reverse().map((r) => (
          <li key={r.run} className="relative rounded-[14px] bg-bg-2/50 px-3 py-2 text-sm">
            <span className="absolute -left-[23px] top-3 h-3 w-3 rounded-full ring-4 ring-surface" style={{ background: r.failed ? "var(--danger)" : "var(--success)" }} />
            <span className="flex flex-wrap items-center gap-2">
              <b>Run {r.run}</b><span className="text-xs text-muted">{r.at} · code {r.deployed_version ?? r.version} · {r.kind === "retest" ? "retest + regression" : "full plan"}</span>
              <span className="ml-auto flex flex-wrap gap-1.5 text-xs">
                <Chip cls="bg-success/15 text-success" label={`${r.passed} passed`} />
                {r.failed > 0 && <Chip cls="bg-danger/15 text-danger" label={`${r.failed} failed`} />}
                {r.not_run > 0 && <Chip cls="bg-warning/15 text-warning" label={`${r.not_run} not run`} />}
                {r.created.map((x) => <Chip key={x} cls="bg-danger/10 text-danger" label={`+ ${x}`} />)}
                {r.closed.map((x) => <Chip key={x} cls="bg-success/10 text-success" label={`✓ ${x}`} />)}
                {r.reopened.map((x) => <Chip key={x} cls="bg-warning/10 text-warning" label={`↺ ${x}`} />)}
              </span>
            </span>
          </li>
        ))}</ol>
      ) : live && (
        <p className="rounded-[12px] bg-bg-2/50 px-3 py-2 text-sm">One run before run history existed: {live.checks.filter((c) => c.passed).length}/{live.checks.length} checks passed.</p>
      )}
      {!t.plan && live && (
        <ul className="space-y-1">{live.checks.map((c) => (
          <li key={c.id} className="rounded-[12px] bg-bg-2/40 px-3 py-2 text-sm">
            <span className="flex items-start gap-2">{c.passed ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />}
              <b className="font-mono text-xs">{c.id}</b><span className="min-w-0 flex-1">{c.title}</span></span>
            <span className="mt-0.5 block break-words pl-6 text-xs text-muted">{c.evidence.slice(0, 300)}</span>
          </li>
        ))}</ul>
      )}
      {live?.left_for_user && <p className="rounded-[12px] bg-bg-2 px-3 py-2 text-xs">📬 <b>Waiting for you in SQS:</b> {live.left_for_user} <span className="text-muted">(the queue → Send and receive messages → Poll for messages)</span></p>}
      {(live?.risks?.length ?? 0) > 0 && (
        <div className="rounded-[12px] bg-warning/[0.08] px-3 py-2 text-sm"><Label>Residual risks</Label><ul className="list-disc space-y-0.5 pl-5">{live!.risks!.map((x) => <li key={x}>{x}</li>)}</ul></div>
      )}
    </Card>
  );
}

function DefectsCard({ t, onGo }: { t: TestingState; onGo: (tab: WorkspaceTab) => void }) {
  if (!t.live && !t.tickets.length) return null;
  const name: Record<string, string> = { qa: "Quinn", de: "Dev", tp: "Terra", user: "You", cto: "Orion" };
  const tone: Record<string, string> = { open: "bg-danger/15 text-danger", reopened: "bg-danger/15 text-danger", in_progress: "bg-primary/15 text-primary",
    resolved: "bg-warning/15 text-warning", closed: "bg-success/15 text-success" };
  return (
    <Card icon={<Ticket className="h-4 w-4" />} title="Defects" sub={t.tickets.length ? `${t.tickets.length} raised · ${t.tickets.filter((x) => x.status === "closed").length} closed` : "none raised"}
      right={<Button size="sm" icon={<Ticket className="h-3.5 w-3.5" />} onClick={() => onGo("tickets")}>Tickets</Button>}>
      {t.tickets.length === 0 ? <p className="flex items-center gap-2 text-sm text-muted"><ShieldCheck className="h-4 w-4 text-success" />No defects: every scenario passed.</p> : (
        <div className="md"><div className="md-table"><table>
          <thead><tr><th>Ticket</th><th>Title</th><th>Severity</th><th>Area</th><th>Raised by</th><th>With</th><th>Fixed in</th><th>Status</th></tr></thead>
          <tbody>{t.tickets.map((x) => (
            <tr key={x.id}><td className="font-mono">{x.label}</td><td>{x.title}</td><td>{x.severity}</td><td>{x.area}</td><td>{name[x.reporter] ?? x.reporter}</td>
              <td>{name[x.assignee] ?? x.assignee}</td><td>{x.version_fixed ?? "–"}</td>
              <td><span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", tone[x.status])}>{x.status.replace("_", " ")}</span></td></tr>
          ))}</tbody>
        </table></div></div>
      )}
    </Card>
  );
}

function SignoffCard({ projectId, t }: { projectId: string; t: TestingState }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const { data: report } = useQuery({ queryKey: ["file", projectId, "signoff", t.signoff, t.runs.length, !!t.signed_off], enabled: !!t.signoff,
    queryFn: () => flowApi.fileText(projectId, t.signoff!) });
  if (!t.signoff && !t.live) return null;
  const pendingLive = t.pending?.stage === "live";
  const sign = async (origin: Element | null) => {
    if (!t.pending) return;
    setBusy(true);
    try {
      await flowApi.decide(projectId, t.pending.id, "approve");
      celebrate(origin, true);
      toast.success("Signed off: the flow is live in AWS and tested");
      ["testing", "approvals", "project", "crew", "build"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't sign off"); }
    finally { setBusy(false); }
  };
  const statement = t.live?.signoff;
  return (
    <Card icon={<FileCheck2 className="h-4 w-4" />} title="Test sign-off" sub="Quinn's report: what was tested, what works, what was raised and fixed, by whom"
      right={<>
        {t.signed_off ? <Chip cls="bg-success/15 text-success" label={`Signed off by you · ${timeAgo(t.signed_off.at)}`} />
          : statement ? <Chip cls="bg-warning/15 text-warning" label="Quinn signed it: waiting for you" />
          : <Chip cls="bg-bg-2 text-muted" label="Not signed off yet" />}
        {t.signoff && <a href={dl(projectId, t.signoff)} className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary"><Download className="h-3.5 w-3.5" />Report (.md)</a>}
      </>}>
      {statement && (
        <blockquote className="relative rounded-[16px] border border-success/30 bg-success/[0.06] px-4 py-3 text-sm">
          <span className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-success"><AgentAvatar agent="qa" accent="rose" status="done" size={20} plain />Quinn signs off</span>
          {statement}
        </blockquote>
      )}
      {pendingLive && (
        <div className="flex flex-wrap items-center gap-3 rounded-[16px] border border-warning/50 bg-warning/[0.07] p-3.5">
          <p className="min-w-0 flex-1 text-sm"><b>Your sign-off as test lead.</b> <span className="text-muted">Read the report below. Sign off, or use Change request / the gate's Request changes to ask Quinn for more scenarios (they go into his plan for your approval).</span></p>
          <Button variant="primary" className="shimmer" icon={<FileCheck2 className="h-4 w-4" />} loading={busy} onClick={(e) => sign(e.currentTarget)}>Sign off</Button>
        </div>
      )}
      {t.signoff ? (
        <div className="rounded-[16px] border border-line bg-bg-2/30">
          <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm font-semibold" aria-expanded={open}>
            The full report<span className="text-xs font-normal text-muted">scenarios, evidence, tickets with who raised and fixed them, runs, risks, exit criteria</span>
            <ChevronDown className={clsx("ml-auto h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
          </button>
          {open && <div className="border-t border-line px-5 py-4">{report ? <Markdown>{report}</Markdown> : <Skeleton className="h-40" />}</div>}
        </div>
      ) : <p className="text-sm text-muted">The report appears after Quinn's first run with a test plan.</p>}
    </Card>
  );
}

/* older projects: component tests in the sandbox */
function LegacyQa({ data, onOpenFile }: { data: NonNullable<BuildState["qa"]["data"]>; onOpenFile: (p: string) => void }) {
  return (
    <Card icon={<FlaskConical className="h-4 w-4" />} title="Component QA (earlier runs)" sub={`${data.counts.passed}/${data.counts.total} passed · ${data.version}`}>
      <p className="text-sm text-muted">{data.summary}</p>
      <button onClick={() => onOpenFile("reports/qa_report.md")} className="text-sm font-semibold text-primary hover:underline">Open the QA report</button>
    </Card>
  );
}

/* ── small pieces ──────────────────────────────────────────────────────── */
function Card({ icon, title, sub, right, children }: { icon: React.ReactNode; title: string; sub?: string; right?: React.ReactNode; children: React.ReactNode }) {
  const [closed, setClosed] = useState(false);
  return (
    <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="sheen elev rounded-[24px] border border-line bg-surface">
      <div className={clsx("flex flex-wrap items-center gap-3 px-5 py-3.5", !closed && "border-b border-line")}>
        <button onClick={() => setClosed(!closed)} className="flex min-w-0 items-center gap-2 text-left" aria-expanded={!closed}>
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[10px] bg-danger/10 text-danger">{icon}</span>
          <span className="min-w-0"><span className="block font-display text-lg font-semibold leading-tight">{title}</span>
            {sub && <span className="block truncate text-xs text-muted">{sub}</span>}</span>
        </button>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {right}
          <button onClick={() => setClosed(!closed)} aria-label={closed ? `Expand ${title}` : `Collapse ${title}`} title={closed ? "Expand" : "Collapse"}
            className="press grid h-8 w-8 place-items-center rounded-full border border-line text-muted hover:border-primary hover:text-primary">
            <ChevronDown className={clsx("h-4 w-4 transition-transform duration-300", !closed && "rotate-180")} /></button>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {!closed && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.28 }} className="overflow-hidden">
            <div className="space-y-4 p-5">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function Fold({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-[14px] border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-semibold" aria-expanded={open}>
        {title}<ChevronDown className={clsx("ml-auto h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-line p-3">{children}</div>}
    </div>
  );
}

const Chip = ({ cls, label }: { cls: string; label: string }) => <span className={clsx("rounded-full px-2.5 py-0.5 text-[11px] font-semibold", cls)}>{label}</span>;

/** Plans written before 10-05 have no plain-words flow: at least put each numbered step on its own line. */
function plainSteps(steps: string): string {
  return steps.replace(/\s*(\d+)[).]\s+/g, (_, n: string) => `\n${n}. `).trim();
}
const Label = ({ children, className }: { children: React.ReactNode; className?: string }) =>
  <p className={clsx("mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted", className)}>{children}</p>;
const Field = ({ k, v }: { k: string; v: string }) => <div><Label>{k}</Label><p className="whitespace-pre-line text-[13px]">{v}</p></div>;
