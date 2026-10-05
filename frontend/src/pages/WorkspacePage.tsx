import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft, BarChart3, Cloud, Code2, DollarSign, FilePlus2, FlaskConical, GitCompareArrows, Hammer, MessagesSquare, Network, PlayCircle,
  SlidersHorizontal, Ticket, Users, Workflow,
} from "lucide-react";
import { Fragment, lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { BudgetDialog } from "../components/BudgetDialog";
import { ChangeComposer } from "../components/ChangeComposer";
import { KillSwitch } from "../components/KillSwitch";
import { Workbench } from "../components/Workbench";
import { ORK } from "../components/CommandPalette";
import { NextStep, type WorkspaceTab } from "../components/NextStep";
import { StageRail } from "../components/StageRail";
import { Button, Skeleton, StatusPill } from "../components/ui";
import { ProjectAssistant } from "../features/assistant/ProjectAssistant";
import { BuildTab } from "../features/build/BuildTab";
import { CrewChat } from "../features/crew/CrewChat";
import { DesignTab } from "../features/design/DesignTab";
import { AwsTab } from "../features/infra/AwsTab";
import { DriftButton } from "../features/infra/DriftButton";
import { MappingTab } from "../features/mapping/MappingTab";
import { PipelineStage } from "../features/pipeline/PipelineStage";
import { RequirementTab } from "../features/requirement/RequirementTab";
import { TestingTab } from "../features/testing/TestingTab";
import { TicketsTab } from "../features/tickets/TicketsTab";
import { ProjectUsage } from "../features/usage/ProjectUsage";
import { ProjectSettings } from "../features/settings/ProjectSettings";
import { TalkGate } from "../features/talk/TalkGate";
import { leaveProjectTheme, showProjectTheme } from "../lib/themes";
import { api, ApiError, type Agent, type OrkEvent, type ProjectDetail } from "../lib/api";
import { AGENT_TAB, flowApi, helpingWho, STAGE_TAB, type CrewMsg } from "../lib/flow";
import { intakeApi } from "../lib/intake";
import { TALK_AGENTS, TALK_TAB, talkApi } from "../lib/talk";
import { useEvents } from "../lib/useEvents";

// Monaco is big: the Code view loads only when opened
const CodeTab = lazy(() => import("../features/code/CodeTab"));

type Tab = WorkspaceTab | "usage" | "settings";
const TABS: { id: Tab; label: string; icon: typeof Workflow }[] = [
  { id: "requirement", label: "Requirement", icon: MessagesSquare },
  { id: "mapping", label: "Mapping", icon: GitCompareArrows },
  { id: "design", label: "Design", icon: Network },
  { id: "build", label: "Build", icon: Hammer },
  { id: "aws", label: "AWS", icon: Cloud },
  { id: "testing", label: "Testing", icon: FlaskConical },
  { id: "tickets", label: "Tickets", icon: Ticket },
  // tools, after the divider
  { id: "crew", label: "Crew room", icon: Users },
  { id: "pipeline", label: "Pipeline", icon: Workflow },
  { id: "code", label: "Code", icon: Code2 },
  { id: "usage", label: "Usage", icon: BarChart3 },
  { id: "settings", label: "Settings", icon: SlidersHorizontal },
];
const FLOW_TABS = 7; // the first seven follow the project's order, then a divider and the tools
/** The tab where an agent's current work shows (Archie's code review is on Build, not Design). */
const tabOf = (a: Agent) => (helpingWho(a) || (a.key === "ta" && /review/i.test(a.activity)) ? "build" : AGENT_TAB[a.key]);

export function WorkspacePage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [events, setEvents] = useState<OrkEvent[]>([]);
  const [streamUp, setStreamUp] = useState(false);
  const project = useQuery({
    queryKey: ["project", id], queryFn: () => api.project(id),
    // safety net: keep agent states fresh even if a proxy drops the live stream
    refetchInterval: (q) => (!streamUp ? 5000 : q.state.data?.agents.some((a) => a.status === "working") ? 8000 : false),
  });
  useQuery({ queryKey: ["events", id], queryFn: async () => { const ev = await api.events(id); setEvents(ev); return ev; }, refetchInterval: streamUp ? false : 8000 });
  const intake = useQuery({ queryKey: ["intake", id], queryFn: () => intakeApi.get(id) });
  const approvals = useQuery({ queryKey: ["approvals", id], queryFn: () => flowApi.approvals(id) }); // shared with NextStep
  const pendingStage = approvals.data?.find((a) => a.status === "pending")?.stage;
  // an agent waiting for your answers in its kickoff conversation needs you on its tab too (agents/talk.py)
  const talks = useQuery({ queryKey: ["talks", id], queryFn: () => talkApi.overview(id), refetchInterval: streamUp ? 30000 : 10000 });
  const talkWaiting = TALK_AGENTS.find((a) => talks.data?.[a]?.waiting);
  const needsTab = pendingStage ? STAGE_TAB[pendingStage] : talkWaiting ? (TALK_TAB[talkWaiting] as Tab) : undefined;
  const tickets = useQuery({ queryKey: ["tickets", id], queryFn: () => flowApi.tickets(id), refetchInterval: streamUp ? 30000 : 10000 });
  const openTickets = tickets.data?.active ?? 0;

  const [tab, setTabRaw] = useState<Tab>("requirement");
  const [streaming, setStreaming] = useState("");
  const [composer, setComposer] = useState(false);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [codePath, setCodePath] = useState<string | null>(null);
  const [bench, setBench] = useState<string | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const [condensed, setCondensed] = useState(false);

  // Switching tabs while scrolled down lands you at the top of the new tab, not somewhere in the middle of it.
  const setTab = useCallback((t: Tab) => {
    setTabRaw(t);
    const el = anchor.current;
    if (el && el.getBoundingClientRect().top < 0) window.scrollTo({ top: window.scrollY + el.getBoundingClientRect().top - 90, behavior: "smooth" });
  }, []);

  const openFile = useCallback((path: string) => { setCodePath(path); setTab("code"); }, [setTab]);

  // the command palette (Ctrl+K) drives tabs and the change request from anywhere
  useEffect(() => {
    const toTab = (e: Event) => { const t = (e as CustomEvent<string>).detail; setTab((t === "names" ? "aws" : t) as Tab); }; // names live on the AWS tab
    const toChange = () => setComposer(true);
    const toBench = (e: Event) => setBench((e as CustomEvent<string>).detail);
    window.addEventListener(ORK.tab, toTab);
    window.addEventListener(ORK.change, toChange);
    window.addEventListener(ORK.workbench, toBench);
    return () => { window.removeEventListener(ORK.tab, toTab); window.removeEventListener(ORK.change, toChange); window.removeEventListener(ORK.workbench, toBench); };
  }, [setTab]);

  // Once the big header scrolls away, the sticky bar shows a slim live status + the next action.
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setCondensed(!e.isIntersecting && e.boundingClientRect.top < 120),
      { rootMargin: "-110px 0px 0px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [project.isLoading]);

  const connected = useEvents((e) => {
    if (e.type === "intake.delta") { setStreaming(String(e.data.text ?? "")); return; }
    if (e.type === "talk.delta") { qc.setQueryData(["talk-live", id, String(e.data.agent ?? "")], String(e.data.text ?? "")); return; }
    if (e.type === "talk.updated") {
      const who = String(e.data.agent ?? "");
      qc.setQueryData(["talk-live", id, who], "");
      ["talks", "files"].forEach((k) => qc.invalidateQueries({ queryKey: [k, id] }));
      qc.invalidateQueries({ queryKey: ["talk", id, who] });
    }
    if (e.type === "assistant.updated") { qc.invalidateQueries({ queryKey: ["assistant", id] }); return; }
    if (e.type === "crew.message") {
      const m = e.data as unknown as CrewMsg;
      qc.setQueryData<CrewMsg[]>(["crew", id], (old) => (old && !old.some((x) => x.id === m.id) ? [...old, m] : old));
      if (e.agent) qc.invalidateQueries({ queryKey: ["agent", id, e.agent] });
      return;
    }
    setEvents((prev) => (prev.some((p) => p.id && p.id === e.id) ? prev : [...prev, e]));
    if (e.type === "intake.updated") {
      setStreaming("");
      qc.invalidateQueries({ queryKey: ["intake", id] });
      qc.invalidateQueries({ queryKey: ["files", id] });
    }
    if (e.type.startsWith("approval.") || ["mapping.updated", "design.updated", "build.updated"].includes(e.type)) {
      ["approvals", "mapping", "design", "build", "files", "infra", "testing", "signoffs", "stack", "naming"].forEach((k) => qc.invalidateQueries({ queryKey: [k, id] }));
    }
    if (e.type === "infra.refreshed" || e.type === "drift.checked") {
      ["infra", "cost", "approvals"].forEach((k) => qc.invalidateQueries({ queryKey: [k, id] }));
      // the automatic drift watch stays quiet when everything matches; a manual check, a failure or a finding shows up
      if (e.message && !(e.type === "drift.checked" && e.data.clean && !e.data.manual)) (e.data.ok ? (e.data.clean === false ? toast.warning : toast.success) : toast.error)(e.message);
    }
    if (e.type === "testing.progress") { qc.invalidateQueries({ queryKey: ["testing", id] }); return; }  // Quinn recorded a scenario
    if (e.type === "ticket.updated") {
      qc.invalidateQueries({ queryKey: ["tickets", id] });
      qc.invalidateQueries({ queryKey: ["ticket", id] });
      qc.invalidateQueries({ queryKey: ["build", id] });
      qc.invalidateQueries({ queryKey: ["testing", id] });
    }
    if (e.type.startsWith("change.")) {
      qc.invalidateQueries({ queryKey: ["changes", id] });
      qc.invalidateQueries({ queryKey: ["intake", id] });
    }
    if (e.agent) qc.invalidateQueries({ queryKey: ["agent", id, e.agent] });
    if (e.type === "agent.state" && e.agent) {
      qc.setQueryData<ProjectDetail>(["project", id], (old) => old && {
        ...old,
        agents: old.agents.map((a) => a.key === e.agent ? {
          ...a, status: (e.data.status as Agent["status"]) ?? a.status, activity: e.message || a.activity,
          tokens_out: (e.data.tokens_out as number) ?? a.tokens_out,
        } : a),
      });
    } else {
      qc.invalidateQueries({ queryKey: ["project", id] });
    }
  }, id);
  useEffect(() => setStreamUp(connected), [connected]);

  const p = project.data;
  // the project's own theme while you're inside it (its Settings tab); your account's theme comes back when you leave
  useEffect(() => { if (p) showProjectTheme(p.theme); }, [p?.theme, p]);
  useEffect(() => () => leaveProjectTheme(), []);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast.success(ok); qc.invalidateQueries({ queryKey: ["project", id] }); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Action failed"); }
  };

  if (project.isLoading || !p) {
    return <div className="mx-auto max-w-[1600px] space-y-4"><Skeleton className="h-28" /><Skeleton className="h-[480px]" /></div>;
  }
  const live = p.status === "running" || p.status === "waiting";
  const canChange = intake.data?.status === "signed_off" || intake.data?.status === "amending";
  const blocked = p.agents.find((a) => a.status === "blocked" && /budget/i.test(a.activity) && a.key !== "intake");
  const budgetTight = p.cost_usd >= p.budget_usd * 0.85;
  const openBudget = () => setBudgetOpen(true);

  const changeButton = canChange && (
    <Button variant="neu" icon={<FilePlus2 className="h-4 w-4" />} onClick={() => setComposer(true)}
      title={intake.data?.status === "amending" ? "Add to the change Echo is working on" : "New or extra requirement, or something to fix. Orion routes it and versions the requirement"}>
      Change request
    </Button>
  );

  return (
    <div className="mx-auto max-w-[1600px]">
      <Link to="/projects" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text"><ArrowLeft className="h-4 w-4" />Projects</Link>

      <motion.header initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
        className="spotlight sheen elev mt-2 flex flex-col gap-4 rounded-[24px] border border-line bg-surface px-5 py-4 md:flex-row md:items-center">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-display font-bold tracking-tight" style={{ fontSize: "var(--fs-h1)" }}>{p.name}</h1>
            <StatusPill status={p.status} />
            <span className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[11px] text-muted">{p.current_version}</span>
            <span className={clsx("inline-flex items-center gap-1 text-[11px]", connected ? "text-success" : "text-muted")} title={connected ? "Live updates on" : "Refreshing every few seconds"}>
              <span className={clsx("h-1.5 w-1.5 rounded-full", connected ? "bg-success" : "bg-muted")} />{connected ? "live" : "auto-refresh"}
            </span>
          </div>
          <p className="mt-1 truncate text-sm text-muted">{p.last_activity || p.description}</p>
          <StageRail project={p} onGo={setTab} pending={pendingStage} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DriftButton projectId={id} onOpen={() => setTab("aws")} />
          {changeButton}
          <button onClick={openBudget} title="Spend and budget: click to change the budget"
            className={clsx("inline-flex h-10 items-center gap-1 rounded-[12px] border px-3 text-sm hover:border-primary/60",
              budgetTight ? "border-warning/60 bg-warning/10" : "border-line")}>
            <DollarSign className="h-4 w-4 text-warning" />{p.cost_usd.toFixed(2)}<span className="text-muted">/ {p.budget_usd.toFixed(0)}</span>
          </button>
          {!live && tab === "pipeline" && !p.agents.some((a) => ["done", "needs_approval", "failed", "blocked"].includes(a.status)) && (
            <Button variant="neu" icon={<PlayCircle className="h-4 w-4" />} title="Scripted run: no AWS or AI calls"
              onClick={() => act(() => api.simulate(id, 1.5), "Demo run started")}>Demo run</Button>
          )}
          <KillSwitch paused={p.paused} onPause={() => act(() => api.pause(id), "Kill switch on: the crew stops before its next step")}
            onResume={() => act(() => api.resume(id), "Resumed: the crew carries on")} />
        </div>
      </motion.header>

      <NextStep project={p} onGo={setTab} onBudget={openBudget} onOpenFile={openFile} />
      <div ref={sentinel} className="h-px" />

      {/* sticky: tabs always reachable; once scrolled, a slim live status with the next action rides along */}
      <div ref={anchor} className="sticky top-[76px] z-20 mt-4 sm:top-[88px]">
        <div className="glass overflow-hidden rounded-[18px] border border-line shadow-[0_12px_30px_-22px_rgba(0,0,0,0.6)]" style={{ background: "color-mix(in srgb, var(--surface) 92%, transparent)" }}>
          <AnimatePresence initial={false}>
            {condensed && (
              <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden border-b border-line">
                <div className="flex items-center gap-3 px-3 py-2">
                  <span className="hidden max-w-[180px] truncate font-display text-sm font-semibold sm:block">{p.name}</span>
                  <span className="hidden rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[10px] text-muted sm:inline">{p.current_version}</span>
                  <NextStep project={p} onGo={setTab} onBudget={openBudget} onOpenFile={openFile} compact />
                  {canChange && (
                    <Button size="sm" variant="neu" icon={<FilePlus2 className="h-3.5 w-3.5" />} onClick={() => setComposer(true)} className="hidden md:inline-flex">Change request</Button>
                  )}
                </div>
                <div className="h-[3px] bg-bg-2">
                  <motion.div className="h-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))]" animate={{ width: `${Math.max(2, p.progress * 100)}%` }} />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <nav className="no-scrollbar flex gap-1 overflow-x-auto p-1.5" aria-label="Project sections">
            {TABS.map(({ id: k, label, icon: Icon }, i) => {
              // dots instead of numbers (user, 10-02): glowing = an agent works here, amber = needs your approval, red = a problem
              const busy = p.agents.some((a) => a.status === "working" && (k === "crew" || tabOf(a) === k));
              const broken = p.agents.some((a) => (a.status === "failed" || a.status === "blocked") && tabOf(a) === k);
              return (
                <Fragment key={k}>
                {i === FLOW_TABS && <span aria-hidden className="mx-1.5 my-2 w-px shrink-0 self-stretch bg-line" />}
                <button onClick={() => setTab(k)}
                  className={clsx("focus-ring relative flex shrink-0 items-center gap-2 rounded-[13px] px-3 py-2.5 text-sm font-semibold transition-colors",
                    tab === k ? "text-on-primary" : "text-muted hover:text-text")}>
                  {tab === k && <motion.span layoutId="ws-tab" className="absolute inset-0 rounded-[13px] bg-primary shadow-[0_8px_20px_-10px_var(--primary)]"
                    transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                  <Icon className="relative h-4 w-4" />
                  <span className="relative">{label}</span>
                  {broken && !busy && <span title="An agent here ran into a problem" className="relative h-2 w-2 rounded-full bg-danger pulse-ring" style={{ ["--ring" as string]: "var(--danger)" }} />}
                  {busy && (
                    <span title="An agent is working here right now" className="relative flex h-2.5 w-2.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-70" style={{ background: tab === k ? "var(--on-primary)" : "var(--primary-2)" }} />
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: tab === k ? "var(--on-primary)" : "var(--primary-2)",
                        boxShadow: `0 0 10px ${tab === k ? "var(--on-primary)" : "var(--primary-2)"}` }} />
                    </span>
                  )}
                  {k === "tickets" && openTickets > 0 && (
                    <span className={clsx("relative rounded-full px-1.5 font-mono text-[10px] font-bold leading-4", tab === k ? "bg-on-primary/20 text-on-primary" : "bg-danger text-white")}>{openTickets}</span>
                  )}
                  {needsTab === k && <span title="Waiting for your approval" className={clsx("relative h-2 w-2 rounded-full pulse-ring", tab === k ? "bg-on-primary" : "bg-warning")} style={{ ["--ring" as string]: tab === k ? "var(--on-primary)" : "var(--warning)" }} />}
                </button>
                </Fragment>
              );
            })}
          </nav>
        </div>
      </div>

      <motion.div key={tab} className="mt-4" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, ease: [0.2, 0.7, 0.2, 1] }}>
        {tab === "requirement" && <RequirementTab projectId={id} streaming={streaming} />}
        {tab === "pipeline" && (
          <PipelineStage project={p} events={events}
            onOpenWorkspace={(agent) => setTab(AGENT_TAB[agent] ?? "requirement")} />
        )}
        {tab === "crew" && <CrewChat projectId={id} agents={p.agents} />}
        {tab === "mapping" && <TalkGate projectId={id} agents={["ba"]}><MappingTab projectId={id} agents={p.agents} onOpenDocs={() => openFile("01_data_mapping.md")} /></TalkGate>}
        {tab === "design" && <TalkGate projectId={id} agents={["ta"]}><DesignTab projectId={id} agents={p.agents} onOpenFile={openFile} /></TalkGate>}
        {tab === "build" && <TalkGate projectId={id} agents={["tp", "de"]}><BuildTab projectId={id} projectName={p.name} onOpenFile={openFile} onGo={setTab} /></TalkGate>}
        {tab === "aws" && <AwsTab projectId={id} onOpenFile={openFile} />}
        {tab === "testing" && <TalkGate projectId={id} agents={["qa"]}><TestingTab projectId={id} onOpenFile={openFile} onGo={setTab} /></TalkGate>}
        {tab === "tickets" && <TicketsTab projectId={id} />}
        {tab === "code" && (
          <Suspense fallback={<Skeleton className="h-[640px]" />}>
            <CodeTab projectId={id} projectName={p.name} openPath={codePath} />
          </Suspense>
        )}
        {tab === "usage" && <ProjectUsage projectId={id} />}
        {tab === "settings" && <ProjectSettings project={p} onGo={setTab} />}
      </motion.div>

      <ProjectAssistant projectId={id} projectName={p.name} onOpenFile={openFile} />
      <Workbench projectId={id} agent={bench ?? "de"} open={!!bench} onClose={() => setBench(null)} />
      <ChangeComposer projectId={id} open={composer} onClose={() => setComposer(false)}
        onSent={(where) => (where === "requirement" ? setTab("requirement") : window.scrollTo({ top: 0, behavior: "smooth" }))} />
      <BudgetDialog project={p} open={budgetOpen} onClose={() => setBudgetOpen(false)} blockedAgent={blocked?.key ?? null} />
    </div>
  );
}
