import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  CheckCircle2, ChevronDown, Cloud, Code2, Copy, Download, ExternalLink, KeyRound, Rocket, SearchCheck, Stethoscope, Trash2,
  UserCheck, XCircle,
} from "lucide-react";
import { createContext, useContext, useEffect, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { AgentLive, asDate, useNow } from "../../components/AgentLive";
import type { WorkspaceTab } from "../../components/NextStep";
import { Button, Input, Modal, Skeleton } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type Approval, type BuildState, type CodeDeploy, type CodeReviewState, type PlanAction, type TryIt } from "../../lib/flow";
import { celebrate } from "../../lib/fx";
import { ArchieAsks } from "./ArchieAsks";
import { AskArchie } from "./AskArchie";
import { DevWork } from "./DevWork";
import { HandOver } from "./HandOver";
import { CostPanel } from "../cost/CostPanel";
import { ACCENT as ACCENT_HEX } from "../../lib/themes";
import { timeAgo } from "../../lib/time";

/** Terra's AWS work (plan/apply/destroy) shows in the deployment card, Dev's deploy and sanity check in his card. */
const DEPLOYING = /terraform (init|plan|apply|destroy)|acting as|tearing|listing what's in aws|planning the change|🚀 terraform|🧹|🔑|📝|🗂/i;
const DEV_DEPLOYING = /deploy|upload|sanity|layers|packing|checking every|🚀|🩺|🔑|📦|🔎/i;
const LIVE_TESTING = /live|AWS|retest/i;

/** The team's relay after Archie: Terraform → created in AWS → you check it → Dev codes → Dev deploys and sanity-checks →
 *  Quinn tests live (tickets) → done. Each card below is one runner. */
export function BuildTab({ projectId, projectName, onOpenFile, onGo }: {
  projectId: string; projectName: string; onOpenFile: (path: string) => void; onGo?: (t: WorkspaceTab) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["build", projectId], queryFn: () => flowApi.build(projectId),
    refetchInterval: (q) => {
      const b = q.state.data as BuildState | undefined;
      return [b?.infra.state, b?.code.state, b?.qa.state, b?.deploy.orion].some((s) => s?.status === "working") ? 3000 : 12000;
    },
  });
  const { data: approvals } = useQuery({ queryKey: ["approvals", projectId], queryFn: () => flowApi.approvals(projectId) });
  const [folds, setFolds] = useState<Record<string, boolean>>(readFolds);
  const save = (f: Record<string, boolean>) => { setFolds(f); try { localStorage.setItem(FOLD_KEY, JSON.stringify(f)); } catch { /* private window */ } };
  const folding = { folded: (t: string) => !!folds[t], toggle: (t: string) => save({ ...folds, [t]: !folds[t] }) };
  const [part, setPartRaw] = useState<Part | null>(() => { try { return localStorage.getItem(PART_KEY) as Part | null; } catch { return null; } });
  const setPart = (p: Part) => { setPartRaw(p); try { localStorage.setItem(PART_KEY, p); } catch { /* private window */ } };
  // your click wins over "follow whoever works" until the next agent starts (10-05: the Code part couldn't be opened while Terra worked)
  const [picked, setPicked] = useState<{ part: Part; during: string } | null>(null);
  useEffect(() => {  // "Deploy details" and similar links open a section you had collapsed (and its sub-section)
    const open = (e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      setFolds((f) => ({ ...f, [t]: false }));
      if (CODE_CARDS.includes(t)) setPartRaw("code");
      if (t === "Infrastructure" || t === "AWS access & deployment") setPartRaw("infra");
    };
    window.addEventListener("ork:unfold", open);
    return () => window.removeEventListener("ork:unfold", open);
  }, []);
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-32" /><Skeleton className="h-48" /><Skeleton className="h-48" /></div>;
  const go = onGo ?? (() => undefined);
  const deWorking = data.code.state?.status === "working" || (data.code.reviewer?.status === "working" && /review|question/i.test(data.code.reviewer.activity));
  const tpWorking = data.infra.state?.status === "working" || data.deploy.orion?.status === "working";
  // where to land: what's happening now, else where you were, else the furthest along
  const shipping = !!data.handover?.pending;  // Terra deploying Dev's packages: the hand-over lives with the code
  const following = `${shipping}-${deWorking}-${tpWorking}`;
  const auto: Part = shipping ? "code" : deWorking && !tpWorking ? "code" : tpWorking && !deWorking ? "infra" : part ?? (data.code.data || data.deploy.code ? "code" : "infra");
  const active: Part = picked && picked.during === following ? picked.part : auto;
  const choose = (p: Part) => { setPart(p); setPicked({ part: p, during: following }); };
  const shown = active === "infra" ? ["Infrastructure", "AWS access & deployment"] : CODE_CARDS;
  const anyOpen = shown.some((s) => !folds[s]);
  const parts: { id: Part; label: string; who: string; agent: string; accent: string; sub: string; busy: boolean }[] = [
    { id: "infra", label: "Infrastructure", who: "Terra · Orion", agent: "tp", accent: "orange", busy: tpWorking,
      sub: data.infra.preview ? `${data.infra.preview.resources.length} resources${data.deploy.state?.status === "deployed" ? " · live in AWS" : ""}` : "Terraform, then AWS" },
    { id: "code", label: "Code & deploy", who: "Dev · Archie", agent: "de", accent: "blue", busy: deWorking,
      sub: data.code.data ? `${data.code.data.version} · ${data.code.data.tests?.passed ?? 0} tests${data.deploy.code?.status === "deployed" ? " · deployed" : ""}` : "after the infrastructure" },
  ];
  return (
    <Folds.Provider value={folding}>
    <div className="space-y-5">
      <Relay b={data} approvals={approvals ?? []} onGo={go} />
      <div className="flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Build sections" className="flex flex-wrap gap-1.5 rounded-[18px] border border-line bg-surface p-1.5">
          {parts.map((p) => (
            <button key={p.id} role="tab" aria-selected={active === p.id} onClick={() => choose(p.id)}
              className={clsx("relative flex items-center gap-2.5 rounded-[13px] px-3.5 py-2 text-left transition-colors", active === p.id ? "text-text" : "text-muted hover:text-text")}>
              {active === p.id && <motion.span layoutId="build-part" className="absolute inset-0 rounded-[13px] bg-bg-2 ring-1 ring-line" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
              <span className="relative"><AgentAvatar agent={p.agent} accent={p.accent} status={p.busy ? "working" : "done"} size={28} plain /></span>
              <span className="relative leading-tight">
                <span className="flex items-center gap-1.5 text-sm font-semibold">{p.label}
                  {p.busy && <span className="h-2 w-2 rounded-full bg-primary-2 pulse-ring" style={{ ["--ring" as string]: "var(--primary-2)" }} title="Working now" />}</span>
                <span className="block text-[11px] text-muted">{p.who} · {p.sub}</span>
              </span>
            </button>
          ))}
        </div>
        <span className="hidden text-xs text-muted md:inline">Testing has its own tab: <button onClick={() => go("testing")} className="font-semibold text-primary hover:underline">Testing</button></span>
        <button onClick={() => save({ ...folds, ...Object.fromEntries(shown.map((s) => [s, anyOpen])) })}
          className="press ml-auto inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs font-semibold text-muted hover:border-primary hover:text-text">
          <ChevronDown className={clsx("h-3.5 w-3.5 transition-transform", anyOpen && "rotate-180")} />{anyOpen ? "Collapse all" : "Expand all"}</button>
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={active} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} className="space-y-5">
          {active === "infra" ? (
            <>
              <Infra projectId={projectId} b={data} onOpenFile={onOpenFile} onGo={go} />
              <DeploySection projectId={projectId} projectName={projectName} b={data} onOpenFile={onOpenFile} />
            </>
          ) : (
            <>
              <CodeSection projectId={projectId} b={data} onOpenFile={onOpenFile} />
              <ReviewSection projectId={projectId} b={data} onOpenFile={onOpenFile} />
              <DeployTestSection projectId={projectId} b={data} approvals={approvals ?? []} onOpenFile={onOpenFile} />
            </>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
    </Folds.Provider>
  );
}

type Part = "infra" | "code";
const PART_KEY = "ork-build-part";
const CODE_CARDS = ["Code & unit tests", "Code review", "Deploy & full-flow test"];

type RS = "done" | "active" | "review" | "issue" | "todo";
const RTONE: Record<RS, string> = { done: "var(--success)", active: "var(--primary-2)", review: "var(--warning)", issue: "var(--danger)", todo: "var(--border)" };

function Relay({ b, approvals, onGo }: { b: BuildState; approvals: Approval[]; onGo: (t: WorkspaceTab) => void }) {
  const pending = approvals.find((a) => a.status === "pending")?.stage;
  const approved = (stage: string) => approvals.some((a) => a.stage === stage && a.status === "approved");
  const tp = b.infra.state, de = b.code.state, qa = b.qa.state;
  const dep = b.deploy.state, code = b.deploy.code, live = b.deploy.live;
  const working = (s: typeof tp, re?: RegExp, not?: RegExp) => s?.status === "working" && (!re || re.test(s.activity)) && (!not || !not.test(s.activity));
  const t = b.tickets;
  const ho = b.handover;
  const terraShips = ho?.mode === "terra";  // Terra deploys Dev's packages (10-03): its own station between the review and the test
  const shipping = !!ho?.pending;
  const pkgs = [...(ho?.code ?? []), ...(ho?.layers ?? []), ...(ho?.images ?? [])];
  const stations: { key: string; label: string; sub: string; agent: string; accent: string; s: RS; tab: WorkspaceTab }[] = [
    { key: "tf", label: "Terraform", agent: "tp", accent: "orange", tab: "build",
      sub: b.infra.preview ? `${b.infra.preview.resources.length} resources` : "from the LLD",
      s: pending === "infra" ? "review" : working(tp, undefined, DEPLOYING) ? "active" : b.infra.preview ? "done" : tp?.status === "failed" ? "issue" : "todo" },
    { key: "aws", label: "In AWS", agent: "tp", accent: "orange", tab: "aws",
      sub: dep?.status === "deployed" ? `applied ${timeAgo(dep.applied_at)}` : dep?.status === "destroyed" ? "torn down" : "placeholder code",
      s: pending === "deploy" && !shipping ? "review" : (working(tp, DEPLOYING) && !shipping) || working(b.deploy.orion, /role|access/i) ? "active"
        : dep?.status === "deployed" ? "done" : dep?.status === "partial" ? "issue" : "todo" },
    { key: "check", label: "You check", agent: "user", accent: "violet", tab: "aws", sub: "in the AWS console",
      s: pending === "infra_check" ? "review" : approved("infra_check") || !!code ? "done" : "todo" },
    { key: "code", label: "Code + tests", agent: "de", accent: "blue", tab: "build",
      sub: b.code.data ? `${b.code.data.version} · cov ${b.code.data.coverage}%` : "unit tests, coverage gate",
      s: working(de, undefined, DEV_DEPLOYING) ? "active" : b.code.data ? "done" : de?.status === "failed" ? "issue" : "todo" },
    { key: "review", label: "Code review", agent: "ta", accent: "amber", tab: "build",
      sub: b.code.review ? (b.code.review.verdict === "approve" ? `Archie ✓ · round ${b.code.review.round}` : `${b.code.review.must} must-fix`) : "Archie, then you",
      s: pending === "code_review" ? "review" : b.code.reviewer?.status === "working" && /review|question/i.test(b.code.reviewer.activity) ? "active"
        : b.code.review ? (approved("code_review") || !!code?.sanity ? "done" : b.code.review.verdict === "approve" ? "review" : "issue") : "todo" },
    ...(terraShips ? [{ key: "ship", label: "Terra deploys", agent: "tp", accent: "orange", tab: "build" as WorkspaceTab,
      sub: shipping ? (ho?.planned ? "plan ready" : "Dev's packages") : pkgs.length && pkgs.every((p) => p.status === "live") ? "Dev's code live" : "Dev's packages",
      s: (pending === "deploy" && shipping ? "review" : working(tp, DEPLOYING) && shipping ? "active"
        : pkgs.length && pkgs.every((p) => p.status === "live") ? "done" : "todo") as RS }] : []),
    { key: "deploy", label: terraShips ? "Full-flow test" : "Deploy + full test", agent: "de", accent: "blue", tab: "build",
      sub: code?.sanity ? (code.sanity.passed ? "sanity ✓" : "sanity ✗") : terraShips ? "Dev, hop by hop" : "Dev's own role",
      s: pending === "code" ? "review" : working(de, DEV_DEPLOYING) ? "active" : code?.sanity ? (code.sanity.passed ? "done" : "issue") : "todo" },
    { key: "live", label: "Live tests", agent: "qa", accent: "rose", tab: "testing",
      sub: live ? `${live.checks.filter((c) => c.passed).length}/${live.checks.length} checks${t?.active ? ` · ${t.active} open ticket(s)` : ""}` : "every scenario, live",
      s: pending === "live_bugs" ? "review" : working(qa, LIVE_TESTING) ? "active" : live ? (t?.active ? "issue" : "done") : "todo" },
    { key: "done", label: "Done", agent: "cto", accent: "violet", tab: "testing", sub: approved("live") ? "signed off" : "test sign-off",
      s: pending === "live" ? "review" : approved("live") ? "done" : "todo" },
  ];
  const reached = stations.reduce((m, st, i) => (st.s !== "todo" ? i : m), 0);
  return (
    <section className="spotlight sheen elev relative overflow-hidden rounded-[26px] border border-line bg-surface p-5">
      <div className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--primary-2) 20%, transparent)" }} />
      <div className="relative">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">How the team builds it</p>
        <h2 className="font-display text-xl font-bold tracking-tight">Infrastructure first, then code, then live tests</h2>
      </div>
      <div className="no-scrollbar relative mt-4 overflow-x-auto pb-1">
        <ol className={clsx("relative flex justify-between", stations.length > 8 ? "min-w-[960px]" : "min-w-[860px]")}>
          <span className="absolute left-[6%] right-[6%] top-[26px] h-[3px] rounded-full bg-bg-2" />
          <span className="absolute left-[6%] top-[26px] h-[3px] overflow-hidden rounded-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))]"
            style={{ width: `calc(88% * ${reached / (stations.length - 1)})`, transition: "width 0.9s cubic-bezier(.2,.7,.2,1)" }}>
            <span className="rail-flow block h-full w-full opacity-80" />
          </span>
          {stations.map((st, i) => (
            <motion.li key={st.key} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i }}
              className={clsx("relative flex flex-col items-center text-center", stations.length > 8 ? "w-[10.5%]" : "w-[11.5%]")}>
              <button onClick={() => onGo(st.tab)} className={clsx("press focus-ring relative grid h-[54px] w-[54px] place-items-center rounded-full border-2 bg-surface",
                (st.s === "active" || st.s === "review") && "pulse-ring")}
                style={{ borderColor: RTONE[st.s], ["--ring" as string]: RTONE[st.s] }} title={`${st.label}: ${st.sub}`}>
                {st.agent === "user" ? <UserCheck className="h-6 w-6" style={{ color: st.s === "todo" ? "var(--text-muted)" : RTONE[st.s] }} />
                  : <AgentAvatar agent={st.agent} accent={st.accent} status={st.s === "active" ? "working" : st.s === "todo" ? "waiting" : st.s === "issue" ? "failed" : "done"} size={42} plain />}
                {st.s === "done" && <span className="absolute -bottom-0.5 -right-0.5 grid h-5 w-5 place-items-center rounded-full bg-success text-white ring-2 ring-surface"><CheckCircle2 className="h-3.5 w-3.5" /></span>}
              </button>
              <span className={clsx("mt-2 text-[12px] font-semibold", st.s === "todo" ? "text-muted" : "text-text")}>{st.label}</span>
              <span className="mt-0.5 line-clamp-2 text-[10.5px] leading-tight" style={{ color: st.s === "review" || st.s === "issue" ? RTONE[st.s] : "var(--text-muted)" }}>
                {st.s === "review" ? "needs you" : st.sub}</span>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Live({ projectId, agent, state, title, steps, hint, step: forced }: {
  projectId: string; agent: string; state: NonNullable<BuildState["infra"]["state"]>; title: string; steps: string[]; hint: string; step?: number;
}) {
  const now = useNow();
  const secs = state.started_at ? (now - asDate(state.started_at).getTime()) / 1000 : 0;
  const step = forced ?? (/final|validate|✅/i.test(state.activity) ? steps.length - 1 : /run|🧪/i.test(state.activity) || secs > 40 ? 1 : 0);
  return <AgentLive projectId={projectId} agent={agent} title={title} activity={state.activity} startedAt={state.started_at} steps={steps} step={Math.min(step, steps.length - 1)} hint={hint} />;
}

function Waiting({ agent, accent, title, body }: { agent: string; accent: string; title: string; body: string }) {
  return (
    <div className="flex items-center gap-4 rounded-[24px] border border-dashed border-line bg-surface/60 p-5">
      <div className="relative">
        <span className="sonar absolute inset-0 rounded-full border" style={{ borderColor: `color-mix(in srgb, ${ACCENT_HEX[accent] ?? "var(--primary)"} 55%, transparent)` }} />
        <AgentAvatar agent={agent} accent={accent} status="waiting" size={48} />
      </div>
      <div><p className="font-display font-semibold">{title}</p><p className="text-sm text-muted">{body}</p></div>
    </div>
  );
}

/** Which Build sections are folded (remembered per browser): the page shows Terra, Dev, Quinn and the deployment. */
const Folds = createContext<{ folded: (t: string) => boolean; toggle: (t: string) => void }>({ folded: () => false, toggle: () => undefined });
const FOLD_KEY = "ork-build-folded";
function readFolds(): Record<string, boolean> {
  try { return JSON.parse(localStorage.getItem(FOLD_KEY) || "{}"); } catch { return {}; }
}

function Card({ icon, title, agent, accent, right, children }: { icon: React.ReactNode; title: string; agent: string; accent: string; right?: React.ReactNode; children: React.ReactNode }) {
  const { folded, toggle } = useContext(Folds);
  const closed = folded(title);
  return (
    <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="sheen elev rounded-[24px] border border-line bg-surface">
      <div className={clsx("flex flex-wrap items-center gap-3 px-5 py-3.5", !closed && "border-b border-line")}>
        <AgentAvatar agent={agent} accent={accent} status="done" size={36} />
        <button onClick={() => toggle(title)} className="flex items-center gap-2 font-display text-lg font-semibold hover:text-primary" aria-expanded={!closed}>
          {icon}{title}</button>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {right}
          <button onClick={() => toggle(title)} aria-label={closed ? `Expand ${title}` : `Collapse ${title}`} title={closed ? "Expand" : "Collapse"}
            className="press grid h-8 w-8 place-items-center rounded-full border border-line text-muted hover:border-primary hover:text-primary">
            <ChevronDown className={clsx("h-4 w-4 transition-transform duration-300", !closed && "rotate-180")} /></button>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {!closed && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.2, 0.7, 0.2, 1] }} className="overflow-hidden">
            <div className="space-y-4 p-5">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function Infra({ projectId, b, onOpenFile, onGo }: { projectId: string; b: BuildState; onOpenFile: (p: string) => void; onGo: (t: WorkspaceTab) => void }) {
  const { state, preview } = b.infra;
  if (state?.status === "working" && !DEPLOYING.test(state.activity)) {
    return <Live projectId={projectId} agent="tp" state={state} title={/ticket|TKT|🎫/i.test(state.activity) ? "Terra is fixing a ticket" : "Terra is writing the Terraform"}
      steps={["Reading Archie's LLD", "Writing Terraform", "terraform validate + access check"]} hint="Usually 3–8 minutes. Validation runs with no AWS credentials." />;
  }
  if (!preview) return <Waiting agent="tp" accent="orange" title="Terra (TP) starts after you approve Archie's design" body="Terraform for every resource in the LLD, checked against the sandbox rules and terraform validate. Once you approve it, Terra creates it in AWS (functions start with placeholder code) and you check it before any code is written." />;
  const total = preview.resources.reduce((s, r) => s + (r.monthly_usd || 0), 0);
  const inAws = b.deploy.state?.status === "deployed";
  return (
    <Card icon={<Cloud className="h-4 w-4" />} title="Infrastructure" agent="tp" accent="orange"
      right={<>
        <Badge ok={preview.validate.ok} label={preview.validate.available ? "terraform validate ✓" : "static checks only"} />
        <span className="rounded-full bg-bg-2 px-2.5 py-1 font-mono text-xs">{preview.version}</span>
        <span className="rounded-full bg-bg-2 px-2.5 py-1 text-xs" title="Terra's estimate at the requirement's volume; the cost panel below works it out for any volume">Terra: ~${total.toFixed(2)}/month</span>
        {inAws && <Button size="sm" variant="primary" icon={<ExternalLink className="h-3.5 w-3.5" />} onClick={() => onGo("aws")}>Live in AWS: open</Button>}
      </>}>
      <p className="text-sm text-muted">{preview.summary}</p>
      {preview.changes && <p className="rounded-[12px] bg-primary/10 px-3 py-2 text-sm"><b>What changed:</b> {preview.changes}</p>}
      <div className="md"><div className="md-table"><table>
        <thead><tr><th>Name</th><th>Type</th><th>Key settings</th></tr></thead>
        <tbody>{preview.resources.map((r) => (
          <tr key={r.address}><td><code>{r.name}</code></td><td><code>{r.type}</code></td><td>{r.settings}</td></tr>
        ))}</tbody>
      </table></div></div>
      <CostPanel projectId={projectId} compact />
      {preview.notes.length > 0 && <Fold title={`Terra's notes for Dev (${preview.notes.length})`}><ul className="list-disc space-y-1 pl-5 text-sm text-muted">{preview.notes.map((n) => <li key={n}>{n}</li>)}</ul></Fold>}
      <Files files={preview.files} onOpen={onOpenFile} />
    </Card>
  );
}

function CodeSection({ projectId, b, onOpenFile }: { projectId: string; b: BuildState; onOpenFile: (p: string) => void }) {
  const { state, data, gates } = b.code;
  const code = b.deploy.code;
  if (state?.status === "working" && !DEV_DEPLOYING.test(state.activity)) {
    return <Live projectId={projectId} agent="de" state={state} title={/ticket|TKT|🎫|bug/i.test(state.activity) ? "Dev is fixing tickets" : /revis/i.test(state.activity) ? "Dev is revising the code" : "Dev is writing the code"}
      steps={["Reading LLD, mapping & infra", "Writing code + tests", "Final run with coverage"]} hint={`Tests run in the no-network sandbox. Coverage gate: ${gates.min_coverage_percent}%. Then Archie reviews it with you, before anything goes to AWS.`} />;
  }
  if (!data) return <Waiting agent="de" accent="blue" title="Dev (DE) starts after you check the infrastructure in AWS" body={`Archie briefs him: "the infrastructure is ready, waiting for your code". Every one of Atlas's examples becomes a test and coverage must reach ${gates.min_coverage_percent}%. Then Archie reviews the code with you, and only then does Dev deploy it and test the whole flow.`} />;
  const t = data.tests;
  const covOk = data.coverage >= data.coverage_gate;
  return (
    <Card icon={<Code2 className="h-4 w-4" />} title="Code & unit tests" agent="de" accent="blue"
      right={<>
        <Badge ok={!!t && t.failed + t.error === 0} label={t ? `${t.passed}/${t.total} tests` : "no tests"} />
        <Badge ok={covOk} label={`coverage ${data.coverage}%`} />
        {(data.reports ?? []).includes("reports/coverage.html") && (
          <a href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent("reports/coverage.html")}&download=1`} title="The coverage report (HTML)"
            className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary"><Download className="h-3.5 w-3.5" />Coverage</a>
        )}
        {code?.sanity && <Badge ok={code.sanity.passed} label={code.sanity.passed ? "sanity check ✓" : "sanity check ✗"} />}
      </>}>
      <DevWork projectId={projectId} data={data} onOpenFile={onOpenFile} />
    </Card>
  );
}

/** Archie's review of Dev's code and your check before the deploy: verdict, checklist, findings, recommendations, and
 *  your conversation with him (user, 10-02: "TA and user… once the user says all fine, only then move further"). */
function ReviewSection({ projectId, b, onOpenFile }: { projectId: string; b: BuildState; onOpenFile: (p: string) => void }) {
  const qc = useQueryClient();
  const reviewer = b.code.reviewer;
  const { data: cr } = useQuery({ queryKey: ["code-review", projectId], queryFn: () => flowApi.codeReview(projectId),
    refetchInterval: (q) => ((q.state.data as CodeReviewState | undefined)?.busy || reviewer?.status === "working" ? 2500 : 12000) });
  const [changes, setChanges] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reviewing = reviewer?.status === "working" && /🔍|reviewing dev/i.test(reviewer.activity);
  if (reviewing && reviewer) {
    return <Live projectId={projectId} agent="ta" state={reviewer} title="Archie is reviewing Dev's code"
      steps={["Reading his LLD & the mapping", "Going through the code & tests", "Verdict & recommendations"]}
      hint="Like a lead reviewing a pull request: design, mapping rules, errors, logging, security, tests, structure. Then it's your turn." />;
  }
  const r = cr?.review;
  const idle = !cr?.busy && reviewer?.status !== "working" && b.code.state?.status !== "working";
  const startReview = async () => {
    setBusy(true);
    try { await flowApi.startCodeReview(projectId); toast.success("Archie is reviewing the code"); ["build", "code-review", "project"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] })); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't start the review"); }
    finally { setBusy(false); }
  };
  const toDev = async (given?: string) => {  // a review without a gate: your change goes to Dev as a ticket, with the conversation
    const text = (given ?? changes ?? "").trim();
    setBusy(true);
    try {
      const talk = (cr?.chat ?? []).filter((m) => !m.error).map((m) => `${m.role === "user" ? "Me" : "Archie"}: ${m.text}`).join("\n\n");
      const first = text.split("\n")[0];
      const t = await flowApi.newTicket(projectId, { title: first.length > 120 ? `${first.slice(0, 117)}…` : first, area: "code", severity: "minor", assignee: "de",
        description: text + (talk ? `\n\nMy discussion with Archie about the code:\n\n${talk}` : ""), steps: "", expected: "", actual: "" });
      toast.success(`${t.label} for Dev: he changes it, Archie reviews it with you, then it's deployed and tested`);
      setChanges(null);
      ["tickets", "build", "project", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send it to Dev"); }
    finally { setBusy(false); }
  };
  if (!r) {
    if (!b.code.data) return null;
    return (
      <div className="flex flex-wrap items-center gap-4 rounded-[24px] border border-dashed border-line bg-surface/60 p-5">
        <AgentAvatar agent="ta" accent="amber" status="waiting" size={48} />
        <div className="min-w-0 flex-1">
          <p className="font-display font-semibold">Archie reviews Dev's code before it goes to AWS</p>
          <p className="text-sm text-muted">After Dev's code passes its tests and the coverage gate, Archie reviews it against his LLD. Then you check it and can ask him anything (security, classes or functions, comments or docstrings…). Only when you say it's fine is it deployed. This code was written before reviews existed: ask him to review it now.</p>
        </div>
        {idle && <Button variant="primary" icon={<SearchCheck className="h-4 w-4" />} loading={busy} onClick={startReview} title="About $0.2–0.5. A review only: nothing is redeployed">Have Archie review the code</Button>}
      </div>
    );
  }
  const decide = async (approve: boolean, origin?: Element | null, text?: string) => {
    if (!cr?.pending) return;
    setBusy(true);
    try {
      await flowApi.decide(projectId, cr.pending.id, approve ? "approve" : "changes", approve ? "" : text ?? changes ?? "");
      if (approve) { celebrate(origin ?? null, false); toast.success("Approved: Dev hands his packages to Terra, who deploys them after your go; then Dev tests the whole flow"); }
      else { toast.success("Sent to Dev, with your conversation with Archie. Archie reviews his new version"); setChanges(null); }
      ["build", "code-review", "approvals", "project", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setBusy(false); }
  };
  const sev = { must: "bg-danger/15 text-danger", should: "bg-warning/15 text-warning", nice: "bg-bg-2 text-muted" } as const;
  const areas = [...new Set(r.checks.map((c) => c.area))];
  const asks = !!(r.choices?.length || r.suggestions?.length);  // reviews since 10-05: Archie's walkthrough with questions
  return (
    <Card icon={<SearchCheck className="h-4 w-4" />} title="Code review" agent="ta" accent="amber"
      right={<>
        <Badge ok={r.verdict === "approve"} label={r.verdict === "approve" ? "Archie: approved" : `${r.findings.filter((f) => f.severity === "must").length} must-fix open`} />
        <span className="rounded-full bg-bg-2 px-2.5 py-1 text-xs">round {r.round} · {r.version}</span>
        {!cr.pending && idle && <Button size="sm" icon={<SearchCheck className="h-3.5 w-3.5" />} loading={busy} onClick={startReview} title="Archie reviews the code as it is now (about $0.2–0.5)">Review again</Button>}
        <a href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent("reports/code_review.md")}&download=1`}
          className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary"><Download className="h-3.5 w-3.5" />Review (.md)</a>
      </>}>
      <p className="text-sm">{r.summary}</p>
      {asks && (
        <ArchieAsks r={r} gate={!!cr.pending} busy={busy} onOpenFile={onOpenFile} onApprove={(o) => decide(true, o)}
          onSend={(text) => (cr.pending ? decide(false, null, text) : (setChanges(text), toDev(text)))} />
      )}
      {cr.pending && !asks && (
        <div className="beam-border relative overflow-hidden rounded-[18px] border border-warning/50 bg-warning/[0.07] p-4" style={{ ["--beam-1" as string]: "var(--warning)" }}>
          <p className="flex items-center gap-2 font-display font-semibold"><UserCheck className="h-4 w-4 text-warning" />Your check: is everything fine before it goes to AWS?</p>
          <p className="mt-0.5 text-sm text-muted">Read Archie's review and the code, ask him anything below. When you're happy, Dev hands it to Terra, who deploys it (you approve his plan), and Dev tests the whole flow. Want changes? Dev gets your request together with your conversation with Archie, and Archie reviews the new version.</p>
          {changes !== null && (
            <textarea autoFocus value={changes} onChange={(e) => setChanges(e.target.value)} rows={3}
              placeholder="e.g. Go with Archie's suggestion: docstrings on every public function, and split parse_order into two functions."
              className="mt-3 w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {changes === null ? (
              <>
                <Button variant="primary" className="shimmer" icon={<CheckCircle2 className="h-4 w-4" />} loading={busy} onClick={(e) => decide(true, e.currentTarget)}>All fine: deploy it</Button>
                <Button onClick={() => setChanges("")}>Request changes</Button>
              </>
            ) : (
              <>
                <Button variant="primary" loading={busy} disabled={!changes.trim()} onClick={() => decide(false)}>Send to Dev</Button>
                <Button variant="ghost" onClick={() => setChanges(null)}>Cancel</Button>
              </>
            )}
          </div>
        </div>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-[16px] border border-line p-3.5">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">What Archie checked ({r.checks.filter((c) => c.ok).length}/{r.checks.length} OK)</p>
          <div className="space-y-2">{areas.map((a) => (
            <div key={a}>
              <p className="text-xs font-semibold first-letter:uppercase">{a}</p>
              <ul className="mt-0.5 space-y-0.5">{r.checks.filter((c) => c.area === a).map((c, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs">{c.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />}
                  <span><b className="font-medium">{c.item}</b> <span className="text-muted">{c.note}</span></span></li>
              ))}</ul>
            </div>
          ))}</div>
        </div>
        <div className="space-y-3">
          <div className="rounded-[16px] border border-line p-3.5">
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Archie's recommendations</p>
            <ul className="space-y-2">{r.design_notes.map((n) => (
              <li key={n.topic} className="rounded-[12px] bg-bg-2/50 px-3 py-2 text-xs">
                <p className="text-[12.5px] font-semibold first-letter:uppercase">{n.topic}</p>
                <p className="mt-0.5"><span className="text-muted">Now:</span> {n.now}</p>
                <p><span className="text-muted">Recommends:</span> <b className="font-medium">{n.recommendation}</b></p>
                <p className="text-muted">{n.why}</p>
              </li>
            ))}</ul>
          </div>
          {r.findings.length > 0 && (
            <div className="rounded-[16px] border border-line p-3.5">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Findings</p>
              <ul className="space-y-1.5">{r.findings.map((f, i) => (
                <li key={i} className="text-xs">
                  <span className={clsx("mr-1.5 rounded-full px-2 py-0.5 text-[10.5px] font-bold", sev[f.severity])}>{f.severity}</span>
                  {f.file && <button onClick={() => onOpenFile(f.file)} className="mr-1 font-mono text-primary-2 hover:underline">{f.file}</button>}
                  {f.issue} <span className="text-muted">→ {f.fix}</span>
                </li>
              ))}</ul>
            </div>
          )}
        </div>
      </div>
      {r.for_user.length > 0 && (
        <div className="rounded-[14px] bg-primary/[0.06] px-3.5 py-2.5 text-sm">
          <p className="mb-1 font-semibold">Before you approve, look at</p>
          <ul className="list-disc space-y-0.5 pl-5 text-[13px]">{r.for_user.map((x) => <li key={x}>{x}</li>)}</ul>
          <p className="mt-1.5 text-xs text-muted">The code is on the Code tab (Lambda code, Unit tests); the coverage report is in the Reports row above.</p>
        </div>
      )}
      <AskArchie projectId={projectId} chat={cr.chat} busy={!!cr.busy} />
      {!cr.pending && (
        <div className="flex flex-wrap items-center gap-2 rounded-[14px] border border-line px-3.5 py-2.5">
          {changes === null ? (
            <>
              <p className="min-w-0 flex-1 text-sm text-muted">Want something changed? Dev gets it as a ticket with your conversation with Archie; then Archie reviews his change with you, and only then is it deployed and tested.</p>
              <Button size="sm" onClick={() => setChanges("")}>Send changes to Dev</Button>
            </>
          ) : (
            <>
              <textarea autoFocus value={changes} onChange={(e) => setChanges(e.target.value)} rows={2}
                placeholder="e.g. Do what Archie suggested: docstrings on the public functions and split parse_and_map."
                className="min-w-0 flex-1 rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
              <Button size="sm" variant="primary" loading={busy} disabled={(changes ?? "").trim().length < 3} onClick={() => toDev()}>Send to Dev</Button>
              <Button size="sm" variant="ghost" onClick={() => setChanges(null)}>Cancel</Button>
            </>
          )}
        </div>
      )}
      {r.history.length > 1 && <Fold title={`Earlier review rounds (${r.history.length - 1})`}><ul className="space-y-1 text-sm">{r.history.slice(0, -1).map((h) => (
        <li key={h.round}><b>Round {h.round}</b> <span className="text-muted">({h.at}): {h.verdict}, {h.must} must-fix. {h.summary}</span></li>))}</ul></Fold>}
    </Card>
  );
}

/** Dev's reviewed code goes to AWS (Terra deploys his packages, 10-03; older projects: Dev uploads it), then Dev tests the
 *  whole flow, with a walkthrough for you. */
function DeployTestSection({ projectId, b, approvals, onOpenFile }: { projectId: string; b: BuildState; approvals: Approval[]; onOpenFile: (p: string) => void }) {
  const { state, data } = b.code;
  const code = b.deploy.code;
  const terraShips = b.handover?.mode === "terra";
  const moving = terraShips && ((b.handover?.code.length ?? 0) + (b.handover?.layers.length ?? 0) + (b.handover?.images?.length ?? 0) > 0);
  const handOver = moving ? <HandOver b={b} approvals={approvals} onOpenFile={onOpenFile} /> : null;
  if (state?.status === "working" && DEV_DEPLOYING.test(state.activity)) {
    const live = terraShips
      ? <Live projectId={projectId} agent="de" state={state} title={/📦|packing/i.test(state.activity) ? "Dev is packing his code and layers for Terra" : "Dev is checking his code in AWS and testing the whole flow"}
          steps={["Building the layers", "Packing each function's code for Terra", "Checking it's his package", "Full-flow test (API → Lambda → queue → logs)"]}
          step={/sanity|🩺/i.test(state.activity) ? 3 : /🔎|checking/i.test(state.activity) ? 2 : /packing/i.test(state.activity) ? 1 : 0}
          hint="Dev never uploads code himself: Terra deploys his packages with Terraform, after your approval. Dev's role only reads and tests." />
      : <Live projectId={projectId} agent="de" state={state} title="Dev is deploying his code and testing the whole flow"
          steps={["Building the layers", "Uploading into the functions", "Full-flow test (API → Lambda → queue → logs)"]}
          step={/sanity|🩺/i.test(state.activity) ? 2 : /upload|🚀/i.test(state.activity) ? 1 : 0}
          hint="With Dev's own role: only this project's functions and layers. Only what changed is uploaded." />;
    return <div className="space-y-3">{live}{handOver}</div>;
  }
  if (!data) return null;
  if (!code || !Object.keys(code.functions ?? {}).length) {
    if (handOver) {
      return <Card icon={<Rocket className="h-4 w-4" />} title="Deploy & full-flow test" agent="tp" accent="orange">{handOver}</Card>;
    }
    return <Waiting agent="de" accent="blue" title={terraShips ? "Dev hands his code to Terra after you approve the code review" : "Dev deploys after you approve the code review"}
      body={terraShips ? "Dev packs each function's code and his layers and hands them to Terra, who swaps them in for the placeholder with Terraform (a plan you approve). Then Dev tests the whole flow (the API, the Lambda, the queue, the logs) and shows you how to repeat it in the AWS console. You check it before Quinn starts testing."
        : "He uploads his code into Terra's functions, then tests the whole flow (the API, the Lambda, the queue, the logs) and shows you how to repeat it in the AWS console. You check it before Quinn starts testing."} />;
  }
  return (
    <Card icon={<Rocket className="h-4 w-4" />} title="Deploy & full-flow test" agent="de" accent="blue"
      right={code.sanity ? <Badge ok={code.sanity.passed} label={code.sanity.passed ? "full-flow test ✓" : "full-flow test ✗"} /> : undefined}>
      {handOver}
      <LiveInAws projectId={projectId} code={code} reports={data.reports ?? []} firstSrc={data.files.find((f) => f.startsWith("src/"))} onOpenFile={onOpenFile} />
    </Card>
  );
}

const LAMBDA = "https://eu-west-1.console.aws.amazon.com/lambda/home?region=eu-west-1#";
/** "arn:aws:lambda:eu-west-1:123:layer:NAME:7" → "NAME v7" */
const layerLabel = (arn: string) => { const p = arn.split(":"); return `${p[p.length - 2]} v${p[p.length - 1]}`; };

/** What Dev put into AWS, who did what, where to look in the console, and the proof that it works (sent → received). */
function LiveInAws({ projectId, code, reports, firstSrc, onOpenFile }: {
  projectId: string; code: CodeDeploy; reports: string[]; firstSrc?: string; onOpenFile: (p: string) => void;
}) {
  const s = code.sanity;
  const layers = Object.entries(code.layers ?? {});
  const byTerra = code.code_by === "terra" || Object.values(code.functions).some((f) => f.by === "terra");
  const layersByTerra = byTerra || code.layers_by === "terra" || layers.some(([, l]) => l.by === "terra");
  return (
    <div id="deployed-in-aws" className="scroll-mt-40 space-y-3 rounded-[18px] border border-line bg-bg-2/40 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="relative grid h-7 w-7 place-items-center rounded-full bg-success/15"><Rocket className="h-3.5 w-3.5 text-success" />
          <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-success pulse-ring" style={{ ["--ring" as string]: "var(--success)" }} /></span>
        <b className="text-sm">Live in AWS now</b>
        <span className="text-xs text-muted">{byTerra ? <>deployed by Terra (Terraform) from Dev's packages · checked by Dev as <code>{code.role}</code></>
          : <>deployed by Dev as <code>{code.role}</code></>} · {timeAgo(code.deployed_at)}</span>
        {firstSrc && <Button size="sm" className="ml-auto" icon={<Code2 className="h-3.5 w-3.5" />} onClick={() => onOpenFile(firstSrc)}>See the code</Button>}
      </div>
      <ul className="grid gap-2 md:grid-cols-2">{Object.entries(code.functions).map(([name, f]) => (
        <li key={name} className="rounded-[14px] border border-line bg-surface px-3 py-2.5">
          <div className="flex items-start gap-2">
            <code className="min-w-0 flex-1 break-all text-[12.5px] font-semibold">{name}</code>
            <a href={`${LAMBDA}/functions/${encodeURIComponent(name)}?tab=code`} target="_blank" rel="noreferrer"
              className="press inline-flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-semibold text-primary hover:bg-primary/20">
              Open in Lambda<ExternalLink className="h-3 w-3" /></a>
          </div>
          <span className="mt-0.5 block text-xs text-muted">code from <code>{f.source_dir}/</code> · {f.kb} KB · {f.handler}{f.runtime ? ` · ${f.runtime}` : ""} · {f.code_version}</span>
          {f.layers.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1">{f.layers.map((a) => (
            <span key={a} className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[10.5px]">📦 {layerLabel(a)}</span>
          ))}</div>}
        </li>
      ))}</ul>
      {layers.length > 0 && (
        <div className="rounded-[14px] bg-surface px-3 py-2.5 text-xs">
          <p className="mb-1.5 text-sm"><b>Layers</b> <span className="text-muted">· {layersByTerra
            ? <>Dev built each zip from <code>layers/</code>; Terra published it as a new layer version and attached it to the functions, in a plan you approved</>
            : <>Dev built each zip from <code>layers/</code>, published it as a new layer version and attached it to the functions</>}</span></p>
          <ul className="flex flex-wrap gap-2">{layers.map(([n, l]) => (
            <li key={n}><a href={`${LAMBDA}/layers/${encodeURIComponent(n)}/versions/${l.version}`} target="_blank" rel="noreferrer"
              className="press inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 hover:border-primary hover:text-primary">
              📦 <code>{n}</code> v{l.version}<span className="text-muted">{l.folder ? ` · layers/${l.folder}/` : ""} · {l.kb} KB</span><ExternalLink className="h-3 w-3" /></a></li>
          ))}</ul>
        </div>
      )}
      <p className="text-xs text-muted">Each Lambda holds only its <code>src/</code> folder. Tests, docs and reports stay here in Orkestra.</p>
      {s && (
        <div className={clsx("rounded-[14px] px-3 py-2.5", s.passed ? "bg-success/[0.08]" : "bg-danger/[0.08]")}>
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold"><Stethoscope className={clsx("h-4 w-4", s.passed ? "text-success" : "text-danger")} />
            Dev's sanity check {s.passed ? "passed" : "failed"}<span className="font-normal text-muted">· {s.calls} real call(s) in AWS</span></p>
          <p className="mt-0.5 text-sm">{s.summary}</p>
          {(s.sent || s.received) && (
            <div className="mt-2 grid items-stretch gap-2 md:grid-cols-[1fr_auto_1fr]">
              <Evidence label="Sent (the real caller's way)" text={s.sent} />
              <span className="hidden self-center text-lg text-muted md:block">→</span>
              <Evidence label="Received at the destination" text={s.received || "nothing arrived"} ok={!!s.received} />
            </div>
          )}
          {s.left_for_user && <p className="mt-2 rounded-[10px] bg-surface px-2.5 py-1.5 text-xs">📬 <b>Waiting for you in SQS:</b> {s.left_for_user} <span className="text-muted">(open the queue → Send and receive messages → Poll for messages)</span></p>}
          <p className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wider text-muted">How Dev tested the flow, hop by hop</p>
          <ol className="relative space-y-2 pl-1">{s.steps.map((x, i) => (
            <li key={i} className="relative flex items-start gap-2.5">
              {i < s.steps.length - 1 && <span className="absolute left-[11px] top-6 h-[calc(100%-8px)] w-px bg-line" />}
              <span className={clsx("relative grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-bold", x.ok ? "bg-success/15 text-success" : "bg-danger/15 text-danger")}>
                {x.ok ? i + 1 : <XCircle className="h-3.5 w-3.5" />}</span>
              <div className="min-w-0 text-xs">
                <p><b className="text-[12.5px]">{x.what}</b> <span className="text-muted">· {x.observed}</span></p>
                {x.how && <p className="mt-0.5 break-words rounded-[8px] bg-surface px-2 py-1 font-mono text-[11px] text-muted">{x.how}</p>}
              </div>
            </li>
          ))}</ol>
          {reports.includes("reports/lambda_test_event.json") && (
            <p className="mt-2 text-xs">🧪 <b>Try it yourself:</b> Lambda console → Test → paste{" "}
              <a className="font-semibold text-primary hover:underline" href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent("reports/lambda_test_event.json")}&download=1`}>Dev's test event</a>
              {" "}→ Test.</p>
          )}
        </div>
      )}
      {(s?.try_it ?? []).length > 0 && <TryItYourself items={s!.try_it!} />}
    </div>
  );
}

/** Dev's walkthrough: the same test, hop by hop, in the AWS console, so the user can see it work before approving. */
function TryItYourself({ items }: { items: TryIt[] }) {
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied"), () => toast.error("Couldn't copy"));
  return (
    <div className="rounded-[16px] border border-primary/30 bg-primary/[0.05] p-3.5">
      <p className="text-sm font-semibold">🧭 Test it yourself in AWS</p>
      <p className="mb-2.5 text-xs text-muted">Dev's test, hop by hop, in the AWS console. Follow it before you approve: you'll see the same results he saw.</p>
      <ol className="grid gap-2.5 lg:grid-cols-2">{items.map((t, i) => (
        <li key={i} className="flex min-w-0 flex-col gap-1.5 rounded-[14px] border border-line bg-surface p-3">
          <div className="flex items-start gap-2">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary/15 text-[11px] font-bold text-primary">{i + 1}</span>
            <p className="min-w-0 flex-1 text-[13px] font-semibold leading-snug">{t.title.replace(/^\d+[.)]\s*/, "")}</p>
            {/console\.aws\.amazon\.com/.test(t.link) ? (
              <a href={t.link} target="_blank" rel="noreferrer"
                className="press inline-flex shrink-0 items-center gap-1 rounded-full bg-primary px-2.5 py-1 text-[11px] font-semibold text-on-primary hover:opacity-90">
                Open in AWS<ExternalLink className="h-3 w-3" /></a>
            ) : (  // an API URL: opening it in a browser sends a GET (403), so copy it for curl / Postman instead
              <button onClick={() => copy(t.link)} title={t.link}
                className="press inline-flex shrink-0 items-center gap-1 rounded-full border border-primary/50 px-2.5 py-1 text-[11px] font-semibold text-primary hover:bg-primary/10">
                Copy URL<Copy className="h-3 w-3" /></button>
            )}
          </div>
          <p className="whitespace-pre-line text-xs text-muted">{t.steps}</p>
          {t.input && (
            <div className="relative">
              <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-all rounded-[10px] bg-bg-2 p-2 pr-9 font-mono text-[11px] leading-snug">{t.input}</pre>
              <button onClick={() => copy(t.input)} aria-label="Copy" title="Copy"
                className="absolute right-1.5 top-1.5 grid h-7 w-7 place-items-center rounded-full bg-surface text-muted hover:text-primary"><Copy className="h-3.5 w-3.5" /></button>
            </div>
          )}
          <p className="text-xs"><CheckCircle2 className="mr-1 inline h-3.5 w-3.5 text-success" /><b>You should see:</b> <span className="text-muted">{t.expect}</span></p>
        </li>
      ))}</ol>
    </div>
  );
}

function Evidence({ label, text, ok = true }: { label: string; text?: string; ok?: boolean }) {
  return (
    <div className="min-w-0 rounded-[12px] border border-line bg-surface p-2.5">
      <p className={clsx("mb-1 text-[11px] font-semibold uppercase tracking-wider", ok ? "text-muted" : "text-danger")}>{label}</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-[11.5px] leading-snug">{text || "–"}</pre>
    </div>
  );
}

const ACCENT_OF: Record<string, string> = { tp: "orange", de: "blue", qa: "rose" };
const SYMBOL: Record<PlanAction, { sign: string; cls: string }> = {
  create: { sign: "+", cls: "text-success" }, update: { sign: "~", cls: "text-warning" },
  replace: { sign: "±", cls: "text-warning" }, delete: { sign: "−", cls: "text-danger" },
};

/** The crew's AWS access (Orion's roles), Terra's latest plan, the outputs, and tearing it all down. */
export function DeploySection({ projectId, projectName, b, onOpenFile }: { projectId: string; projectName: string; b: BuildState; onOpenFile: (p: string) => void }) {
  const { orion, state } = b.deploy;
  const access = b.deploy.draft ?? b.deploy.access;
  const [tearOpen, setTearOpen] = useState(false);
  const tp = b.infra.state;
  const working = orion?.status === "working" && /AWS|role|access/i.test(orion.activity) ? { agent: "cto", s: orion, title: "Orion is creating the crew's AWS roles" }
    : tp?.status === "working" && DEPLOYING.test(tp.activity) ? { agent: "tp", s: tp, title: /destroy|tearing/i.test(tp.activity) ? "Terra is tearing down" : /apply|🚀|listing/i.test(tp.activity) ? "Terra is creating it in AWS" : "Terra is planning the change in AWS" }
    : null;
  if (!access && !state && !working) {
    return <Waiting agent="cto" accent="violet" title="AWS access and the deployment"
      body="With Terra's infrastructure, Orion drafts one AWS role per agent for this project (shown in the same approval). Approving creates the roles, then Terra creates the infrastructure with its own role." />;
  }
  const destroyed = state?.status === "destroyed";
  const inAws = !!state && ["deployed", "partial", "destroy_failed", "planned"].includes(state.status) || b.deploy.access?.status === "active";
  return (
    <div className="space-y-3">
      {working && <Live projectId={projectId} agent={working.agent} state={working.s} title={working.title}
        steps={working.agent === "cto" ? ["Reading Terra's Terraform", "Drafting the roles", "Creating the roles"]
          : ["terraform init", /apply|🚀|destroy|tearing|listing/i.test(working.s.activity) ? "terraform apply / destroy" : "terraform plan", "What's in AWS"]}
        step={working.agent === "tp" ? (/listing|🗂/i.test(working.s.activity) ? 2 : /plan|apply|destroy|📝|🚀/i.test(working.s.activity) ? 1 : 0) : undefined}
        hint="Every AWS call uses this agent's own short-lived role, and is listed in its AWS access tab." />}
      <Card icon={<KeyRound className="h-4 w-4" />} title="AWS access & deployment" agent="cto" accent="violet"
        right={<>
          {state && <Badge ok={state.status === "deployed" || destroyed} label={{ deployed: "live in AWS", planned: "planned", partial: "partly deployed", destroyed: "torn down", destroy_failed: "tear down failed" }[state.status]} />}
          {inAws && <Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setTearOpen(true)}>Tear down</Button>}
        </>}>
        {access && (
          <div>
            <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
              <b>Orion's access plan</b>
              <span className="text-muted">· names <code>{access.prefix}*</code> · {access.services.join(", ")} · {access.region}</span>
              <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", access.status === "active" ? "bg-success/15 text-success" : access.status === "proposed" ? "bg-warning/15 text-warning" : access.status === "blocked" ? "bg-danger/15 text-danger" : "bg-bg-2 text-muted")}>
                {{ active: "roles active", proposed: "waiting for your approval", blocked: "blocked", removed: "roles removed" }[access.status]}</span>
            </p>
            {access.problem && <p className="mb-2 rounded-[12px] bg-danger/10 px-3 py-2 text-sm text-danger">{access.problem}</p>}
            <div className="grid gap-2 md:grid-cols-3">
              {access.roles.map((r) => (
                <div key={r.role} className="lift rounded-[16px] border border-line bg-bg-2/40 p-3">
                  <div className="flex items-center gap-2"><AgentAvatar agent={r.agent} accent={ACCENT_OF[r.agent]} status="done" size={28} /><b>{r.persona}</b></div>
                  <p className="mt-1.5 break-all font-mono text-[11.5px]">{r.role}</p>
                  <ul className="mt-1.5 space-y-0.5 text-xs text-muted">{r.can.slice(0, 3).map((c) => <li key={c} className="line-clamp-2" title={c}>✓ {c}</li>)}</ul>
                </div>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-muted">Each role's full policy, the crew boundary and every AWS action it took: open the agent (Pipeline) → <b>AWS access</b>.</p>
          </div>
        )}
        {state?.plan && !destroyed && (
          <div>
            <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
              <b>Terra's latest plan</b><span className="text-muted">· {state.plan.version} · as <code>{state.plan.role}</code></span>
              {(["create", "update", "replace", "delete"] as PlanAction[]).filter((k) => state.plan!.counts[k]).map((k) => (
                <span key={k} className={clsx("rounded-full bg-bg-2 px-2 py-0.5 font-mono text-xs", SYMBOL[k].cls)}>{SYMBOL[k].sign}{state.plan!.counts[k]} {k}</span>
              ))}
            </p>
            {state.plan.changes_note && <p className="mb-2 rounded-[12px] bg-primary/10 px-3 py-2 text-sm"><b>What Terra changed:</b> {state.plan.changes_note}</p>}
            <Fold title={`Resources in the plan (${state.plan.changes.length})`}>
              <ul className="space-y-0.5 font-mono text-[12px]">{state.plan.changes.map((c) => (
                <li key={c.address} className="flex gap-2"><span className={clsx("w-3", SYMBOL[c.action].cls)}>{SYMBOL[c.action].sign}</span>
                  <span className="min-w-0 flex-1 break-all">{c.address}{c.fields?.length ? <span className="text-muted"> ({c.fields.join(", ")})</span> : null}</span><span className="text-muted">{c.name}</span></li>
              ))}</ul>
            </Fold>
          </div>
        )}
        {state?.error && <p className="rounded-[12px] border border-danger/30 bg-danger/[0.06] px-3 py-2 text-sm"><b className="text-danger">Last error:</b> <span className="break-words font-mono text-xs">{state.error.slice(0, 600)}</span></p>}
        {state?.status === "deployed" && state.outputs && (
          <div className="rounded-[16px] border border-success/40 bg-success/[0.05] p-4">
            <p className="flex items-center gap-2 font-semibold"><Cloud className="h-4 w-4 text-success" />Live in AWS · {state.version}</p>
            <ul className="mt-2 space-y-1.5 text-sm">{Object.entries(state.outputs).filter(([k]) => k !== "code_deploy").map(([k, v]) => {
              const text = typeof v === "string" ? v : JSON.stringify(v);
              return (
                <li key={k} className="flex flex-wrap items-center gap-2"><span className="w-32 shrink-0 text-muted">{k}</span>
                  <code className="min-w-0 flex-1 break-all text-[12px]">{text}</code>
                  <button onClick={() => { navigator.clipboard.writeText(text); toast.success("Copied"); }} className="rounded-full p-1 text-muted hover:text-text" aria-label={`Copy ${k}`}><Copy className="h-3.5 w-3.5" /></button></li>
              );
            })}</ul>
          </div>
        )}
        {destroyed && <p className="rounded-[12px] bg-bg-2 px-3 py-2 text-sm">🧹 Torn down {state?.destroyed_at ? new Date(state.destroyed_at).toLocaleString() : ""}: the project's resources, Dev's layers, the crew's roles and the state bucket are gone. Code and documents stay here.</p>}
        <Files files={[access && "reports/aws_access.md", state?.plan && "reports/deploy_plan.md", state?.status === "deployed" && "reports/deploy.md",
          state?.status === "deployed" && "reports/aws_inventory.md"].filter(Boolean) as string[]} onOpen={onOpenFile} />
      </Card>
      <TearDown open={tearOpen} onClose={() => setTearOpen(false)} projectId={projectId} projectName={projectName} />
    </div>
  );
}

function TearDown({ open, onClose, projectId, projectName }: { open: boolean; onClose: () => void; projectId: string; projectName: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      await flowApi.teardown(projectId, text);
      toast.success("Terra is tearing down the AWS resources");
      ["build", "access", "project"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
      onClose();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't start the tear down"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title="Tear down from AWS" width={460}>
      <div className="space-y-4">
        <div className="rounded-[14px] border border-danger/30 bg-danger/10 p-3.5 text-sm">
          Terra runs <code>terraform destroy</code> with its own role: every AWS resource of <b>{projectName}</b> is deleted, then Orion removes
          the crew's roles and the Terraform state. Nothing else in the account is touched. The code, documents and history stay here.
        </div>
        <Input label={`Type the project name to confirm: ${projectName}`} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={busy} disabled={text.trim() !== projectName} icon={<Trash2 className="h-4 w-4" />} onClick={go}>Tear down</Button>
        </div>
      </div>
    </Modal>
  );
}

function Files({ files, onOpen }: { files: string[]; onOpen: (p: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {files.map((f) => (
        <button key={f} onClick={() => onOpen(f)} className="rounded-full border border-line px-2.5 py-0.5 font-mono text-[11.5px] hover:border-primary hover:text-primary">{f}</button>
      ))}
    </div>
  );
}

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return <span className={clsx("rounded-full px-2.5 py-1 text-xs font-semibold", ok ? "bg-success/15 text-success" : "bg-danger/15 text-danger")}>{label}</span>;
}

function Fold({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 rounded-[14px] border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-semibold">
        {title}<ChevronDown className={clsx("ml-auto h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-line p-3">{children}</div>}
    </div>
  );
}
