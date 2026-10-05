import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Cloud, Code2, Eye, MessagesSquare, Rocket, RotateCcw, Users, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import type { ProjectDetail } from "../lib/api";
import { ApiError } from "../lib/api";
import { CREW, ORION } from "../lib/crew";
import { AGENT_TAB, flowApi, helpingWho, STAGE_TAB, type Approval } from "../lib/flow";
import { celebrate } from "../lib/fx";
import { TALK_AGENTS, TALK_TAB, talkApi } from "../lib/talk";
import { AgentAvatar } from "./AgentAvatar";
import { Button } from "./ui";
import { estimate, WorkProgress } from "./WorkProgress";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));
/** who each agent interviews before it works (agents/talk.py), and about what */
const PERSON: Record<string, { who: string; about: string; doing: string }> = {
  ba: { who: "your data analyst", about: "the data", doing: "the data mapping" },
  ta: { who: "your technical lead", about: "the architecture and tech stack", doing: "the HLD, LLD and diagram" },
  tp: { who: "your platform engineer", about: "platform", doing: "the Terraform" },
  de: { who: "your developer", about: "coding", doing: "the code" },
  qa: { who: "your tester", about: "testing", doing: "the test plan" },
};
export type WorkspaceTab = "requirement" | "mapping" | "design" | "build" | "code" | "pipeline" | "crew" | "aws" | "testing" | "tickets";
/** What "request changes" means at each gate (the Change request button sends it). */
const CHANGE_HINT: Record<string, string> = {
  infra: "it goes straight to Orion and Terra", infra_check: "it goes straight to Orion and Terra", deploy: "it goes straight to Orion and Terra",
  live_bugs: "or open the Tickets tab to comment or re-assign",
  change_review: "say cancel to drop it, or what you want instead",
  test_plan: "or Request changes on the Testing tab: tell Quinn which scenarios to add or change",
  live: "or ask Quinn for more tests on the Testing tab",
  code_review: "or ask Archie anything about the code, or Request changes for Dev, on the Build tab",
};
/** The gate's own words, where "Approve & continue" isn't enough. */
const GATE: Record<string, { kicker: string; review: string; approve: string }> = {
  infra_check: { kicker: "Check AWS, then give the go-ahead", review: "Open AWS", approve: "Looks right: go ahead" },
  deploy: { kicker: "Your approval is needed", review: "Review", approve: "Apply in AWS" },
  test_plan: { kicker: "You're the test lead: review Quinn's scenarios", review: "Read the plan", approve: "Approve the test plan" },
  code_review: { kicker: "Archie reviewed Dev's code: your check before the deploy", review: "Read the review & ask Archie", approve: "All fine: deploy it" },
  drift: { kicker: "Terra found changes made outside Terraform", review: "See what changed", approve: "Restore from Terraform" },
  live: { kicker: "Quinn signed off the testing: your sign-off", review: "Read the report", approve: "Sign off" },
};

interface Action { label: string; icon: React.ReactNode; onClick: () => void; primary?: boolean; loading?: boolean }
interface Step {
  key: string; tone: string; beam?: boolean; kicker: string; title: string; line?: React.ReactNode;
  agent?: { key: string; accent: string; status: string }; icon?: React.ReactNode; actions: Action[];
  progress?: { startedAt: string | null; expectedS: number | null | undefined };
}

/** "What happens now, and what do I do?" One source of truth, shown as the full card under the project header and as
 *  a slim strip in the sticky bar once you scroll down. */
export function NextStep({ project, onGo, onBudget, onOpenFile, compact }: {
  project: ProjectDetail; onGo: (tab: WorkspaceTab) => void; onBudget: () => void; onOpenFile?: (path: string) => void; compact?: boolean;
}) {
  const step = useStep(project, onGo, onBudget, onOpenFile);
  if (compact) return step ? <Compact s={step} /> : null;
  return <AnimatePresence mode="wait">{step && <Full key={step.key} s={step} />}</AnimatePresence>;
}

/** Scroll to an element once its tab has rendered (again a bit later in case the tab was still loading). */
const reveal = (id: string) => [350, 1100].forEach((ms) => window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }), ms));
/** Open a Build tab section the user had collapsed: for the next mount (localStorage) and a mounted tab (event). */
const unfold = (section: string) => {
  try {
    const f = JSON.parse(localStorage.getItem("ork-build-folded") || "{}");
    delete f[section];
    localStorage.setItem("ork-build-folded", JSON.stringify(f));
  } catch { /* private window */ }
  window.dispatchEvent(new CustomEvent("ork:unfold", { detail: section }));
};

function useStep(project: ProjectDetail, onGo: (t: WorkspaceTab) => void, onBudget: () => void, onOpenFile?: (p: string) => void): Step | null {
  const qc = useQueryClient();
  const busy = project.agents.some((a) => a.status === "working");
  const { data: approvals } = useQuery({
    queryKey: ["approvals", project.id], queryFn: () => flowApi.approvals(project.id), refetchInterval: busy ? 3000 : 8000,
  });
  const { data: changes } = useQuery({
    queryKey: ["changes", project.id], queryFn: () => flowApi.changes(project.id), refetchInterval: busy ? 3000 : 10000,
  });
  const [sending, setSending] = useState(false);
  const now = useNow(busy);

  const pending = approvals?.find((a) => a.status === "pending");
  const working = project.agents.find((a) => a.status === "working" && a.key !== "intake"); // Echo has her own live UI
  const failed = project.agents.find((a) => (a.status === "failed" || a.status === "blocked") && a.key !== "intake");
  const clarifying = changes?.find((c) => c.status === "clarifying");
  const { data: talks } = useQuery({ queryKey: ["talks", project.id], queryFn: () => talkApi.overview(project.id), refetchInterval: busy ? 4000 : 10000 });
  const talking = TALK_AGENTS.find((a) => talks?.[a]?.status === "talking");

  const approve = async (a: Approval, accept = false) => {
    const origin = document.activeElement;
    setSending(true);
    try {
      await flowApi.decide(project.id, a.id, accept ? "accept" : "approve");
      celebrate(origin, a.stage === "live");
      toast.success(accept ? "Accepted. Nothing after it is redone: Terra, Dev and Quinn keep their work" : `Approved. ${a.next_label}`);
      ["approvals", "project", "mapping", "intake", "changes", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, project.id] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setSending(false); }
  };

  if (talking && !pending) {  // an agent's kickoff conversation with its specialist (10-05)
    const m = META[talking];
    const t = talks![talking];
    const who = PERSON[talking];
    const thinking = !!t.busy;
    return {
      key: `talk-${talking}`, tone: thinking ? "var(--primary-2)" : "var(--warning)", beam: !thinking,
      kicker: thinking ? `Kickoff · ${m?.persona} is ${t.messages ? "replying" : "reading the earlier phases"}` : "Kickoff · waiting for your answers",
      title: `${m?.persona} has a few questions for ${who.who}`,
      line: <>Before {who.doing}, {m?.persona} asks only {who.about} questions. Answer on the {TALK_TAB[talking]} tab, in your own words or with a tap;
        when {m?.persona} understands, it plays back the plan and starts once you say go.</>,
      agent: { key: talking, accent: m?.accent ?? "violet", status: thinking ? "working" : "needs_approval" },
      actions: [{ label: `Answer ${m?.persona}`, icon: <MessagesSquare className="h-4 w-4" />, primary: true,
        onClick: () => onGo(TALK_TAB[talking] as WorkspaceTab) }],
    };
  }
  if (working) {
    const m = META[working.key];
    const since = working.started_at ? elapsed(now - asDate(working.started_at).getTime()) : "";
    const helps = helpingWho(working);  // Archie/Orion unblocking a teammate: say so plainly (10-05)
    const whom = helps ? META[helps]?.persona : null;
    return {
      key: `w-${working.key}`, tone: "var(--primary-2)",
      kicker: helps ? `Helping ${whom}${since ? ` · ${since}` : ""}` : `Working now${since ? ` · ${since}` : ""}`,
      title: helps ? `${m?.persona} is helping ${whom} fix a problem` : `${m?.persona} · ${m?.role}`,
      line: helps ? <>{working.activity.replace(/^\W+\s*/, "")} · then {whom} carries on from where it stopped (a fixed plan still waits for your OK).</> : working.activity,
      agent: { key: working.key, accent: working.accent, status: "working" },
      progress: { startedAt: working.job_started_at ?? working.started_at, expectedS: working.expected_s },
      actions: helps ? [
        { label: `${whom}'s step`, icon: <Eye className="h-4 w-4" />, onClick: () => onGo("build") },
        { label: "Watch them work it out", icon: <Users className="h-4 w-4" />, primary: true, onClick: () => onGo("crew") },
      ] : [
        { label: "Crew room", icon: <Users className="h-4 w-4" />, onClick: () => onGo("crew") },
        ...(AGENT_TAB[working.key] ? [{ label: "Watch live", icon: <Eye className="h-4 w-4" />, primary: true, onClick: () => {
          // the agent's live card on its tab (its "Watch behind the scenes" opens the workbench); Archie's code review is on Build
          onGo(working.key === "ta" && /review/i.test(working.activity) ? "build" : AGENT_TAB[working.key]!);
          window.setTimeout(() => document.getElementById(`live-${working.key}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 350);
        } }] : []),
      ],
    };
  }
  if (clarifying) {
    return {
      key: `cr-${clarifying.id}`, tone: "var(--primary)", kicker: `${clarifying.label} · requirement ${clarifying.version_to}`,
      title: "Echo is updating the requirement with you",
      line: <>Answer Echo in the Requirement tab, then press <b className="text-text">Sign off amendment</b>.</>,
      agent: { key: "intake", accent: "cyan", status: "needs_approval" },
      actions: [{ label: "Go to Echo", icon: <MessagesSquare className="h-4 w-4" />, onClick: () => onGo("requirement"), primary: true }],
    };
  }
  if (pending?.stage === "code" && /deployed/i.test(pending.title)) {
    // Dev's code is already running in AWS: say so, and show where to look before Quinn starts
    const firstCode = pending.artifacts.find((p) => p.startsWith("src/"));
    return {
      key: `p-${pending.id}`, tone: "var(--warning)", kicker: "Live in AWS: check it, then approve", title: pending.title, beam: true,
      line: <>Dev's code and its layers are <b className="text-text">running in AWS now</b>{/Terra deployed/.test(pending.title) ? " (Terra deployed his packages with Terraform)" : ""}, and he tested it: what he sent and what arrived in the
        queue are in <b className="text-text">Deploy details</b>, with Lambda console links. If you approve: <b className="text-text">{pending.next_label}</b>.</>,
      agent: { key: pending.agent, accent: META[pending.agent]?.accent ?? "blue", status: "needs_approval" },
      actions: [
        { label: "See the code", icon: <Code2 className="h-4 w-4" />, onClick: () => (firstCode && onOpenFile ? onOpenFile(firstCode) : onGo("code")) },
        { label: "Deploy details", icon: <Cloud className="h-4 w-4" />, onClick: () => { unfold("Deploy & full-flow test"); onGo("build"); reveal("deployed-in-aws"); } },
        { label: "Approve & continue", icon: <Rocket className="h-4 w-4" />, onClick: () => approve(pending), primary: true, loading: sending },
      ],
    };
  }
  if (pending?.stage === "deploy" && /Dev's (code|layer)/.test(pending.title)) {  // Terra deploys Dev's packages (10-03)
    return {
      key: `p-${pending.id}`, tone: "var(--warning)", kicker: "Dev handed his packages to Terra: approve the deploy", title: pending.title, beam: true,
      line: <>Archie reviewed the code and you approved it; Terra's plan <b className="text-text">swaps Dev's code in for the placeholder</b> (or the previous
        version) and publishes the layers, nothing else. If you approve: <b className="text-text">terraform apply</b>, then Dev checks it and tests the whole flow.</>,
      agent: { key: "tp", accent: META.tp?.accent ?? "orange", status: "needs_approval" },
      actions: [
        { label: "See the hand-over", icon: <Eye className="h-4 w-4" />, onClick: () => { unfold("Deploy & full-flow test"); onGo("build"); } },
        ...(onOpenFile ? [{ label: "Terra's plan", icon: <Cloud className="h-4 w-4" />, onClick: () => onOpenFile("reports/deploy_plan.md") }] : []),
        { label: "Apply: deploy Dev's code", icon: <Rocket className="h-4 w-4" />, onClick: () => approve(pending), primary: true, loading: sending },
      ],
    };
  }
  if (pending?.stage === "code_review") {  // Archie's walkthrough (10-05): see the code in the code view, answer his questions
    const firstCode = pending.artifacts.find((p) => /^src\/.*handler\.py$/.test(p)) ?? pending.artifacts.find((p) => p.startsWith("src/"));
    return {
      key: `p-${pending.id}`, tone: "var(--warning)", kicker: GATE.code_review.kicker, title: pending.title, beam: true,
      line: <>Open the code in the code view, then answer Archie's questions on the Build tab (functions or classes, his suggestions).
        Nothing changes unless you pick it; <b className="text-text">Code is fine</b> sends it to Terra's deploy plan.</>,
      agent: { key: "ta", accent: META.ta?.accent ?? "amber", status: "needs_approval" },
      actions: [
        { label: "Open the code", icon: <Code2 className="h-4 w-4" />, onClick: () => (firstCode && onOpenFile ? onOpenFile(firstCode) : onGo("code")) },
        { label: "Archie's questions", icon: <Eye className="h-4 w-4" />, onClick: () => onGo("build"), primary: true },
      ],
    };
  }
  if (pending?.stage === "drift") {  // Terra's drift watch: Restore directly, keep the changes, or leave it (10-03)
    return {
      key: `p-${pending.id}`, tone: "var(--warning)", kicker: GATE.drift.kicker, title: pending.title, beam: true,
      line: <>The Terraform state and Dev's packages are the source of truth. <b className="text-text">Restore</b> puts AWS back exactly (no Dev or
        Quinn steps); intended? <b className="text-text">Keep the changes</b> on the AWS tab and the Terraform catches up.</>,
      agent: { key: "tp", accent: META.tp?.accent ?? "orange", status: "needs_approval" },
      actions: [
        { label: "See what changed", icon: <Eye className="h-4 w-4" />, onClick: () => onGo("aws") },
        { label: "Leave it for now", icon: <CheckCircle2 className="h-4 w-4" />, onClick: () => approve(pending, true), loading: sending },
        { label: "Restore from Terraform", icon: <Rocket className="h-4 w-4" />, onClick: () => approve(pending), primary: true, loading: sending },
      ],
    };
  }
  if (pending) {
    const m = META[pending.agent];
    const g = GATE[pending.stage];
    return {
      key: `p-${pending.id}`, tone: "var(--warning)", kicker: g?.kicker ?? "Your approval is needed",
      title: pending.title, beam: true,
      line: pending.can_accept
        ? <><b className="text-text">Approve & continue</b>: {pending.next_label}. <b className="text-text">Accept: nothing to rebuild</b>: keep it, and the
          work after it stays as it is (for wording or diagram-only changes).</>
        : <>If you approve: <b className="text-text">{pending.next_label}</b>. Not right? Use <b className="text-text">Change request</b> at the top
          {CHANGE_HINT[pending.stage] ? <> ({CHANGE_HINT[pending.stage]})</> : null}.</>,
      agent: { key: pending.agent, accent: m?.accent ?? "violet", status: "needs_approval" },
      actions: [
        { label: g?.review ?? "Review", icon: <Eye className="h-4 w-4" />, onClick: () => onGo(STAGE_TAB[pending.stage] ?? "requirement") },
        ...(pending.can_accept ? [{ label: "Accept: nothing to rebuild", icon: <CheckCircle2 className="h-4 w-4" />, onClick: () => approve(pending, true), loading: sending }] : []),
        { label: g?.approve ?? "Approve & continue", icon: <Rocket className="h-4 w-4" />, onClick: () => approve(pending), primary: true, loading: sending },
      ],
    };
  }
  if (failed && failed.status === "blocked" && /Archie and Orion|Retrying|retrying automatically/i.test(failed.activity)) {
    // agents/unblock.py: the stuck agent asked Archie and Orion; nothing for the user to do (user, 10-04: "the user can't
    // keep clicking Try again")
    const m = META[failed.key];
    const busy = /retrying automatically/i.test(failed.activity);  // Claude on AWS was busy: the step re-runs by itself
    return {
      key: `u-${failed.key}`, tone: "var(--primary)", kicker: busy ? `${m?.persona} retries by itself` : `${m?.persona} asked Archie and Orion for help`,
      title: failed.activity, line: busy ? "Claude on AWS was busy for a moment. The step runs again by itself; nothing to do now."
        : "They work out a fix together and hand it back; a fixed plan still waits for your approval. Nothing to do now.",
      agent: { key: "ta", accent: "amber", status: "working" },
      actions: [{ label: "Watch in the crew room", icon: <Users className="h-4 w-4" />, onClick: () => onGo("crew"), primary: true }],
    };
  }
  if (failed) {
    const m = META[failed.key];
    const budget = /budget/i.test(failed.activity);
    return {
      key: `f-${failed.key}`, tone: "var(--danger)", kicker: budget ? `${m?.persona} paused: budget reached` : `${m?.persona} ran into a problem`,
      title: budget ? `Spent $${project.cost_usd.toFixed(2)} of $${project.budget_usd.toFixed(2)}` : failed.activity,
      line: budget ? "Raise the budget and the crew continues where it stopped." : undefined,
      icon: <AlertTriangle className="h-8 w-8 shrink-0 text-danger" />,
      actions: budget ? [{ label: "Raise budget", icon: <Wallet className="h-4 w-4" />, onClick: onBudget, primary: true }]
        : [
          { label: "Crew room", icon: <Users className="h-4 w-4" />, onClick: () => onGo("crew") },
          ...(["cto", "ba", "ta", "tp", "de", "qa"].includes(failed.key) ? [{
            label: "Try again", icon: <RotateCcw className="h-4 w-4" />, primary: true, loading: sending, onClick: async () => {
              setSending(true);
              try { await flowApi.retryAgent(project.id, failed.key); toast.success(`${m?.persona} is trying again`); qc.invalidateQueries({ queryKey: ["project", project.id] }); }
              catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't retry"); }
              finally { setSending(false); }
            },
          }] : []),
        ],
    };
  }
  // the last approval handed over to an agent that hasn't started: start it, or say it isn't built yet
  const last = approvals?.find((a) => a.status === "approved");
  const nextAgent = last?.next_agent ? project.agents.find((x) => x.key === last.next_agent) : undefined;
  if (last && nextAgent && nextAgent.status === "waiting") {
    const m = META[nextAgent.key];
    const tight = project.cost_usd >= project.budget_usd * 0.85;
    return last.next_ready ? {
      key: `start-${nextAgent.key}`, tone: "var(--primary)", kicker: `${last.title} approved`,
      title: `${m?.persona} (${m?.abbr}) is ready to start`,
      line: tight ? `Budget nearly used ($${project.cost_usd.toFixed(2)} of $${project.budget_usd.toFixed(0)}): raise it first, or ${m?.persona} will pause mid-way.`
        : `Orion has handed everything over. ${last.next_label}.`,
      agent: { key: nextAgent.key, accent: nextAgent.accent, status: "waiting" },
      actions: [
        tight ? { label: "Raise budget", icon: <Wallet className="h-4 w-4" />, onClick: onBudget }
          : { label: "See the hand-off", icon: <Users className="h-4 w-4" />, onClick: () => onGo("crew") },
        { label: `Start ${m?.persona}`, icon: <Rocket className="h-4 w-4" />, primary: true, loading: sending, onClick: async () => {
          setSending(true);
          try { await flowApi.continuePipeline(project.id); toast.success(`${m?.persona} is starting`); qc.invalidateQueries({ queryKey: ["project", project.id] }); }
          catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't start"); }
          finally { setSending(false); }
        } },
      ],
    } : {
      key: `paused-${nextAgent.key}`, tone: "var(--success)", kicker: `${last.title} approved`,
      title: `Orion handed everything to ${m?.persona} (${m?.abbr})`,
      line: `${m?.persona} isn't part of this build yet, so the project pauses here until it arrives.`,
      icon: <CheckCircle2 className="h-8 w-8 shrink-0 text-success" />,
      actions: [{ label: "See the hand-off", icon: <Users className="h-4 w-4" />, onClick: () => onGo("crew") }],
    };
  }
  return null;
}

function Full({ s }: { s: Step }) {
  return (
    <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
      className={clsx("relative mt-4 flex flex-wrap items-center gap-4 overflow-hidden rounded-[22px] border bg-surface px-4 py-3.5 sm:px-5", s.beam && "beam-border")}
      style={{ borderColor: `color-mix(in srgb, ${s.tone} 45%, transparent)`, boxShadow: `0 14px 40px -24px ${s.tone}`,
        background: `linear-gradient(100deg, color-mix(in srgb, ${s.tone} 10%, var(--surface)), var(--surface) 55%)`,
        ["--beam-1" as string]: s.tone }}>
      <div className="pointer-events-none absolute inset-y-0 left-0 w-1.5" style={{ background: s.tone }} />
      {s.agent ? <AgentAvatar agent={s.agent.key} accent={s.agent.accent} status={s.agent.status as never} size={44} /> : s.icon}
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: s.tone }}>{s.kicker}</p>
        <p className="font-display text-lg font-semibold leading-tight">{s.title}</p>
        {s.line && <p className="line-clamp-2 text-sm text-muted">{s.line}</p>}
        {s.progress && <WorkProgress startedAt={s.progress.startedAt} expectedS={s.progress.expectedS} tone={s.tone} className="mt-2 max-w-md" />}
      </div>
      <div className="flex flex-wrap gap-2">
        {s.actions.map((a) => (
          <Button key={a.label} variant={a.primary ? "primary" : undefined} className={a.primary ? "shimmer" : undefined}
            loading={a.loading} icon={a.icon} onClick={a.onClick}>{a.label}</Button>
        ))}
      </div>
    </motion.div>
  );
}

function Compact({ s }: { s: Step }) {
  const main = s.actions.find((a) => a.primary) ?? s.actions[0];
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2.5">
      <span className="h-2 w-2 shrink-0 rounded-full pulse-ring" style={{ background: s.tone, ["--ring" as string]: s.tone }} />
      <p className="min-w-0 flex-1 truncate text-[13px]">
        <span className="font-semibold" style={{ color: s.tone }}>{s.kicker}</span>
        {s.progress && <CompactPct p={s.progress} />}
        <span className="text-muted"> · </span>{s.title}
        {typeof s.line === "string" && <span className="hidden text-muted lg:inline"> · {s.line}</span>}
      </p>
      {main && (
        <Button size="sm" variant={main.primary ? "primary" : undefined} loading={main.loading} icon={main.icon} onClick={main.onClick}
          className={clsx("shrink-0")}>{main.label}</Button>
      )}
    </div>
  );
}

function CompactPct({ p }: { p: NonNullable<Step["progress"]> }) {
  const pct = estimate(p.startedAt, p.expectedS, useNow(true));
  return pct === null ? null : <span className="tabular-nums text-muted"> ≈ {pct}%</span>;
}

/** Current time, ticking once a second while something is running (for "working for 2m 10s"). */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}
const asDate = (iso: string) => new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
function elapsed(ms: number) {
  if (ms < 0 || !Number.isFinite(ms)) return "";
  const s = Math.floor(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}
