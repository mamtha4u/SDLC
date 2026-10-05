import clsx from "clsx";
import { motion } from "framer-motion";
import { Brain, CheckCircle2, ChevronRight, FileCode2, FlaskConical, Globe, MessageSquareText, Rocket, Send, ShieldCheck, Wrench, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { CREW, ORION } from "../lib/crew";
import { flowApi, type TestRun, type Workbench as WB, type WorkEvent } from "../lib/flow";
import { ACCENT } from "../lib/themes";
import { timeAgo } from "../lib/time";
import { AgentAvatar } from "./AgentAvatar";
import { asDate } from "./AgentLive";
import { Overlay } from "./Overlay";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));
type View = { tab: "files"; path: string | null } | { tab: "tests"; run: number } | { tab: "calls" } | { tab: "checks" } | { tab: "notes" };
type Tab = View["tab"];

/** Each agent's work looks different behind the scenes (user, 10-02: "for all agents the popup looks the same"). */
const LIVE = new Set(["http_request", "sqs_send", "sqs_receive", "invoke_lambda", "read_logs"]);
const RESEARCH = new Set(["web_search", "fetch_url", "pypi_package"]);
const PROFILE: Record<string, { tabs: Tab[]; files?: string; calls?: string; intro: string }> = {
  de: { tabs: ["files", "tests", "calls", "notes"], files: "Code & tests", calls: "Live calls", intro: "Dev writes the code and its unit tests, runs them with coverage, deploys and tests the live flow." },
  tp: { tabs: ["files", "checks", "notes"], files: "Terraform", intro: "Terra writes the Terraform; the platform checks every submission (lint, terraform validate, Orion's access check)." },
  qa: { tabs: ["calls", "checks", "notes"], calls: "Live calls", intro: "Quinn calls the real flow in AWS with his own role: the API, the queues, the functions and their logs." },
  ta: { tabs: ["calls", "checks", "notes"], calls: "Research", intro: "Archie looks up facts with sources, writes the design, and reviews Dev's code against it." },
  cto: { tabs: ["calls", "checks", "notes"], calls: "Research", intro: "Orion researches the facts behind the plan and reviews changes; every claim needs a source." },
  ba: { tabs: ["checks", "notes"], intro: "Atlas writes the mapping; the platform checks every worked example against his table." },
  intake: { tabs: ["checks", "notes"], intro: "Echo interviews you; her conversation is on the Requirement tab." },
};
const profileOf = (agent: string) => PROFILE[agent] ?? PROFILE.ba;

/** An agent's behind-the-scenes work, live: every step, the files it writes (code, tests, Terraform) with their content,
 *  every test run with coverage, and its own notes. Updates every few seconds while open. */
export function Workbench({ projectId, agent, open, onClose }: { projectId: string; agent: string; open: boolean; onClose: () => void }) {
  return (
    <Overlay open={open} onClose={onClose} label="Workbench" z={96}>
      {open && <Body projectId={projectId} agent={agent} onClose={onClose} />}
    </Overlay>
  );
}

function Body({ projectId, agent, onClose }: { projectId: string; agent: string; onClose: () => void }) {
  const m = META[agent];
  const accent = ACCENT[m?.accent ?? "violet"];
  const [run, setRun] = useState<number | undefined>(undefined);
  const [data, setData] = useState<WB | null>(null);
  const [events, setEvents] = useState<WorkEvent[]>([]);
  const [files, setFiles] = useState<WB["files"]>({});
  const [tests, setTests] = useState<TestRun[]>([]);
  const prof = profileOf(agent);
  const [view, setView] = useState<View>(() => (prof.tabs[0] === "files" ? { tab: "files", path: null } : { tab: prof.tabs[0] } as View));
  const [follow, setFollow] = useState(true);
  const since = useRef(0);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {  // a different run starts from scratch
    since.current = 0;
    setEvents([]); setFiles({}); setTests([]); setFollow(true);
  }, [run]);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await flowApi.workbench(projectId, agent, since.current, run);
        if (!alive) return;
        if (since.current && d.total < since.current) {  // a new run began: reload it whole
          since.current = 0; setEvents([]); setFiles({}); setTests([]);
          return;
        }
        since.current = d.total;
        setData(d);
        if (d.events.length) setEvents((e) => [...e, ...d.events]);
        if (Object.keys(d.files).length) setFiles((f) => ({ ...f, ...d.files }));
        if (d.tests.length) setTests((t) => [...t, ...d.tests]);
      } catch { /* keep polling */ }
    };
    tick();
    const t = window.setInterval(tick, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, [projectId, agent, run]);

  // follow the newest file/test while the agent works, until the user picks something
  const paths = useMemo(() => Object.entries(files).filter(([, f]) => !f.deleted).sort((a, b) => a[0].localeCompare(b[0])).map(([p]) => p), [files]);
  const newest = useMemo(() => Object.entries(files).filter(([, f]) => !f.deleted).sort((a, b) => b[1].step - a[1].step)[0]?.[0] ?? null, [files]);
  useEffect(() => {
    if (!follow) return;
    if (view.tab === "files" && newest && view.path !== newest) setView({ tab: "files", path: newest });
  }, [newest, follow]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (follow) list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" }); }, [events.length, follow]);

  const working = data?.state?.status === "working";
  const latest = tests[tests.length - 1];
  const pick = (e: WorkEvent) => {
    setFollow(false);
    if (e.paths?.length && prof.tabs.includes("files")) setView({ tab: "files", path: e.paths[0] });
    else if (e.phase === "tests") setView({ tab: "tests", run: Math.max(0, tests.findIndex((t) => t.ts === e.ts)) });
    else if (e.phase === "say") setView({ tab: "notes" });
    else if (e.tool && (LIVE.has(e.tool) || RESEARCH.has(e.tool)) && prof.tabs.includes("calls")) setView({ tab: "calls" });
    else if (e.tool?.startsWith("submit") || e.phase === "result") setView({ tab: "checks" });
  };
  // the calls (live or research) and the submissions, each with what came back
  const calls = useMemo(() => pairs(events, (t) => LIVE.has(t) || RESEARCH.has(t)), [events]);
  const subs = useMemo(() => pairs(events, (t) => t.startsWith("submit")), [events]);
  const steps = new Set(events.filter((e) => e.turn).map((e) => e.turn)).size;
  const fixes = subs.filter((s) => s.result?.error).length;
  const lastSub = subs[subs.length - 1];
  const count = (pred: (t: string) => boolean) => calls.filter((c) => pred(c.call.tool ?? "")).length;
  const stats: [string, string, string?][] = agent === "de"
    ? [["Steps", String(steps)], ["Files written", String(paths.length)], ["Test runs", String(tests.length)],
      ["Latest tests", latest ? `${latest.passed}/${latest.total}` : "–", latest ? (latest.failed + latest.error ? "var(--danger)" : "var(--success)") : undefined],
      ["Coverage", latest?.coverage != null ? `${latest.coverage}%` : "–"]]
    : agent === "qa"
      ? [["Steps", String(steps)], ["Live calls", String(calls.length)], ["API calls", String(count((t) => t === "http_request"))],
        ["Queue reads & sends", String(count((t) => t.startsWith("sqs")))], ["Log reads", String(count((t) => t === "read_logs"))]]
      : [["Steps", String(steps)],
        ...(agent === "tp" ? [["Terraform files", String(paths.length)] as [string, string]] : prof.tabs.includes("calls") ? [["Research lookups", String(calls.length)] as [string, string]] : []),
        ["Submissions", String(subs.length)], ["Fixed after a check", String(fixes), fixes ? "var(--warning)" : undefined],
        ["Last check", lastSub ? (lastSub.result ? (lastSub.result.error ? "✗ not accepted" : "✓ accepted") : "…") : "–",
          lastSub?.result ? (lastSub.result.error ? "var(--danger)" : "var(--success)") : undefined]];

  return (
    <motion.div role="dialog" aria-modal aria-label={`${m?.persona}'s workbench`}
      initial={{ y: 30, scale: 0.97, opacity: 0 }} animate={{ y: 0, scale: 1, opacity: 1 }} exit={{ y: 20, opacity: 0 }}
      transition={{ type: "spring", stiffness: 340, damping: 32 }}
      className="relative flex h-[90dvh] w-full max-w-[1320px] flex-col overflow-hidden rounded-[26px] border border-line bg-surface shadow-2xl">
      <header className="relative flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5"
        style={{ background: `linear-gradient(100deg, color-mix(in srgb, ${accent} 14%, var(--surface)), var(--surface) 60%)` }}>
        <AgentAvatar agent={agent} accent={m?.accent ?? "violet"} status={working ? "working" : "done"} size={44} />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: accent }}>
            {working ? <><span className="h-1.5 w-1.5 rounded-full pulse-ring" style={{ background: accent, ["--ring" as string]: accent }} />Live</> : "Last run"}
            · behind the scenes</p>
          <h2 className="font-display text-xl font-bold">{m?.persona}'s workbench</h2>
          <p className="truncate text-xs text-muted">{data?.state?.activity || "…"}</p>
        </div>
        {(data?.runs.length ?? 0) > 1 && (
          <div className="no-scrollbar flex max-w-[40%] gap-1 overflow-x-auto">
            {data!.runs.map((r) => (
              <button key={r.index} onClick={() => setRun(r.index === data!.runs.length - 1 ? undefined : r.index)}
                className={clsx("press shrink-0 rounded-full border px-2.5 py-1 text-[11px]", (run ?? data!.runs.length - 1) === r.index ? "border-primary bg-primary/12 text-text" : "border-line text-muted")}>
                Run {r.index + 1}{r.purpose ? ` · ${r.purpose}` : ""}<span className="ml-1 opacity-60">{timeAgo(r.started)}</span>
              </button>
            ))}
          </div>
        )}
        <button onClick={onClose} className="focus-ring rounded-full p-2 text-muted hover:bg-surface-2 hover:text-text" aria-label="Close"><X className="h-5 w-5" /></button>
      </header>

      <div className={clsx("grid grid-cols-2 gap-2 border-b border-line px-5 py-2.5", stats.length >= 5 ? "sm:grid-cols-5" : stats.length === 4 ? "sm:grid-cols-4" : "sm:grid-cols-3")}>
        {stats.map(([label, value, tone]) => <Stat key={label} label={label} value={value} tone={tone} />)}
      </div>
      <p className="border-b border-line px-5 py-1.5 text-xs text-muted">{prof.intro}</p>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-b border-line md:border-b-0 md:border-r">
          <div className="flex items-center justify-between px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted">
            Timeline
            {!follow && <button onClick={() => setFollow(true)} className="normal-case tracking-normal text-primary hover:underline">Follow live</button>}
          </div>
          <div ref={list} className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3" onWheel={() => setFollow(false)}>
            {!events.length && <p className="px-3 py-10 text-center text-sm text-muted">{data?.runs.length === 0 ? "Nothing recorded yet. The workbench fills in as soon as the agent starts its next step." : "Loading…"}</p>}
            {events.filter((e) => e.phase !== "result" || e.error).map((e) => <TimelineRow key={e.n} e={e} accent={accent} onClick={() => pick(e)} />)}
            {working && <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted"><span className="h-1.5 w-1.5 animate-pulse rounded-full" style={{ background: accent }} />thinking…</div>}
          </div>
        </aside>

        <main className="flex min-h-0 flex-col">
          <div className="flex gap-1 border-b border-line px-4 pt-2">
            {(([["files", `${prof.files ?? "Files"} (${paths.length})`, FileCode2], ["tests", `Tests (${tests.length})`, FlaskConical],
              ["calls", `${prof.calls ?? "Calls"} (${calls.length})`, Globe], ["checks", `Submissions & checks (${subs.length})`, ShieldCheck],
              ["notes", "Notes", MessageSquareText]] as const).filter(([k]) => prof.tabs.includes(k))).map(([k, label, Icon]) => (
              <button key={k} onClick={() => { setFollow(false); setView(k === "files" ? { tab: "files", path: view.tab === "files" ? view.path : newest } : k === "tests" ? { tab: "tests", run: tests.length - 1 } : { tab: k }); }}
                className={clsx("relative flex items-center gap-1.5 px-3 pb-2.5 pt-1.5 text-sm font-semibold", view.tab === k ? "text-text" : "text-muted hover:text-text")}>
                <Icon className="h-4 w-4" />{label}
                {view.tab === k && <motion.span layoutId="wb-tab" className="absolute inset-x-1 -bottom-px h-[2px] rounded-full" style={{ background: accent }} />}
              </button>
            ))}
          </div>
          {(data?.runs.length ?? 0) > 1 && run === undefined && !paths.length && !tests.length && !calls.length && !subs.length && (
            <button onClick={() => setRun(data!.runs.length - 2)}
              className="mx-4 mt-3 rounded-[12px] border border-primary/40 bg-primary/[0.07] px-3.5 py-2.5 text-left text-sm hover:border-primary">
              This run has just started, so nothing is written yet. <b>See the previous attempt (Run {data!.runs.length - 1}) →</b>
            </button>
          )}
          <div className="min-h-0 flex-1">
            {view.tab === "files" && <Files files={files} paths={paths} path={view.path} onPick={(p) => { setFollow(false); setView({ tab: "files", path: p }); }} />}
            {view.tab === "tests" && <Tests runs={tests} index={view.run} onPick={(i) => setView({ tab: "tests", run: i })} />}
            {view.tab === "calls" && <Calls items={calls} empty={agent === "qa" || agent === "de" ? "No live calls yet: they appear as the agent calls the flow in AWS." : "No lookups yet: every fact the agent checks appears here, with its source."} />}
            {view.tab === "checks" && <Calls items={subs} checks empty="Nothing submitted yet. Each submission appears here with the platform's verdict (accepted, or what it must fix)." />}
            {view.tab === "notes" && <Notes events={events} />}
          </div>
        </main>
      </div>
    </motion.div>
  );
}

/** Each call of a kind of tool, with the result that came back (results follow their call in the log). */
function pairs(events: WorkEvent[], want: (tool: string) => boolean): { call: WorkEvent; result?: WorkEvent }[] {
  const out: { call: WorkEvent; result?: WorkEvent }[] = [];
  const open: Record<string, { call: WorkEvent; result?: WorkEvent }[]> = {};
  for (const e of events) {
    if (!e.tool || !want(e.tool)) continue;
    if (e.phase === "call") { const p = { call: e }; out.push(p); (open[e.tool] ??= []).push(p); }
    else if (e.phase === "result") { const p = open[e.tool]?.shift(); if (p) p.result = e; }
  }
  return out;
}

function Calls({ items, checks, empty }: { items: { call: WorkEvent; result?: WorkEvent }[]; checks?: boolean; empty: string }) {
  if (!items.length) return <p className="px-6 py-10 text-center text-sm text-muted">{empty}</p>;
  return (
    <ol className="h-full space-y-2 overflow-y-auto p-4">
      {[...items].reverse().map(({ call, result }) => {
        const bad = !!result?.error;
        return (
          <li key={call.n} className={clsx("rounded-[14px] border px-3.5 py-2.5", bad ? "border-danger/40 bg-danger/[0.05]" : "border-line bg-bg-2/40")}>
            <p className="flex items-start gap-2 text-[13px] font-semibold">
              <span className="min-w-0 flex-1 break-words">{call.title}</span>
              <span className={clsx("shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-bold", !result ? "bg-bg-2 text-muted" : bad ? "bg-danger/15 text-danger" : "bg-success/15 text-success")}>
                {!result ? "…" : bad ? (checks ? "not accepted" : "error") : (checks ? "accepted" : "ok")}</span>
              <span className="shrink-0 text-[10px] font-normal text-muted">{asDate(call.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
            </p>
            {call.detail && <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-muted">{call.detail}</pre>}
            {result?.detail && (
              <div className="mt-1.5 rounded-[10px] bg-surface px-2.5 py-1.5">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">{checks ? "The platform's verdict" : "What came back"}</p>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]">{result.detail}</pre>
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-[12px] bg-bg-2/50 px-3 py-1.5">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">{label}</p>
      <motion.p key={value} initial={{ opacity: 0.4, y: 3 }} animate={{ opacity: 1, y: 0 }} className="font-display text-lg font-bold tabular-nums" style={tone ? { color: tone } : undefined}>{value}</motion.p>
    </div>
  );
}

function TimelineRow({ e, accent, onClick }: { e: WorkEvent; accent: string; onClick: () => void }) {
  const Icon = e.phase === "turn" ? Brain : e.phase === "say" ? MessageSquareText : e.phase === "tests" ? FlaskConical : e.phase === "deploy" ? Rocket
    : e.phase === "result" ? XCircle : e.tool?.startsWith("submit") ? Send : e.paths ? FileCode2 : Wrench;
  const t = e.tests;
  const bad = e.error || (t && t.failed + t.error > 0);
  if (e.phase === "turn") {
    return <div className="flex items-center gap-2 px-3 pb-0.5 pt-2.5 text-[10.5px] font-semibold uppercase tracking-wider text-muted">
      <span className="h-px flex-1 bg-line" />Step {e.turn}<span className="h-px flex-1 bg-line" /></div>;
  }
  return (
    <motion.button initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} onClick={onClick}
      className={clsx("group flex w-full items-start gap-2.5 rounded-[12px] px-2.5 py-2 text-left hover:bg-bg-2/70", bad && "bg-danger/[0.05]")}>
      <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-[8px]"
        style={{ background: bad ? "color-mix(in srgb, var(--danger) 16%, transparent)" : `color-mix(in srgb, ${accent} 16%, transparent)`, color: bad ? "var(--danger)" : accent }}>
        <Icon className="h-3.5 w-3.5" /></span>
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] font-semibold leading-snug">{e.title}</span>
        {t && (
          <span className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-bg-2">
            <span className="bg-success" style={{ width: `${t.total ? (t.passed / t.total) * 100 : 0}%` }} />
            <span className="bg-danger" style={{ width: `${t.total ? ((t.failed + t.error) / t.total) * 100 : 0}%` }} />
          </span>
        )}
        {e.detail && <span className={clsx("mt-0.5 block text-[11.5px] text-muted", e.phase === "say" ? "line-clamp-3 italic" : "line-clamp-2 font-mono")}>{e.detail}</span>}
      </span>
      <span className="shrink-0 text-[10px] text-muted">{asDate(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
    </motion.button>
  );
}

function Files({ files, paths, path, onPick }: { files: WB["files"]; paths: string[]; path: string | null; onPick: (p: string) => void }) {
  const f = path ? files[path] : undefined;
  const lines = (f?.content ?? "").split("\n");
  const groups = useMemo(() => {
    const g: Record<string, string[]> = {};
    for (const p of paths) (g[p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "."] ??= []).push(p);
    return Object.entries(g);
  }, [paths]);
  if (!paths.length) return <p className="p-10 text-center text-sm text-muted">No files yet. They appear here as soon as the agent writes them.</p>;
  return (
    <div className="grid h-full min-h-0 grid-cols-[240px_minmax(0,1fr)]">
      <nav className="min-h-0 overflow-y-auto border-r border-line p-2 text-[12.5px]">
        {groups.map(([dir, ps]) => (
          <div key={dir} className="mb-2">
            <p className="px-2 py-1 font-mono text-[10.5px] text-muted">{dir}/</p>
            {ps.map((p) => (
              <button key={p} onClick={() => onPick(p)} title={p}
                className={clsx("flex w-full items-center gap-1.5 truncate rounded-[8px] px-2 py-1 text-left font-mono", p === path ? "bg-primary/14 text-text" : "text-text/80 hover:bg-bg-2")}>
                <ChevronRight className="h-3 w-3 shrink-0 opacity-50" /><span className="truncate">{p.slice(p.lastIndexOf("/") + 1)}</span>
                <span className="ml-auto shrink-0 text-[10px] text-muted">#{files[p].step}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="flex min-h-0 flex-col">
        {f && (
          <div className="flex items-center gap-2 border-b border-line px-4 py-2 text-xs text-muted">
            <FileCode2 className="h-3.5 w-3.5" /><span className="font-mono text-text">{path}</span>
            <span>· {lines.length} lines · written {timeAgo(f.ts)}</span>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto bg-bg-2/40">
          <motion.pre key={`${path}-${f?.step}`} initial={{ opacity: 0.3 }} animate={{ opacity: 1 }} className="min-w-max p-0 font-mono text-[12.5px] leading-[1.55]">
            {lines.map((l, i) => (
              <div key={i} className="flex hover:bg-primary/[0.05]">
                <span className="w-12 shrink-0 select-none pr-3 text-right text-muted/60">{i + 1}</span>
                <code className="whitespace-pre pr-6">{l || " "}</code>
              </div>
            ))}
          </motion.pre>
        </div>
      </div>
    </div>
  );
}

function Tests({ runs, index, onPick }: { runs: TestRun[]; index: number; onPick: (i: number) => void }) {
  const r = runs[Math.min(Math.max(index, 0), runs.length - 1)];
  if (!r) return <p className="p-10 text-center text-sm text-muted">No test runs yet.</p>;
  const failing = r.tests.filter((t) => t.outcome === "failed" || t.outcome === "error");
  const maxTotal = Math.max(...runs.map((x) => x.total), 1);
  return (
    <div className="h-full space-y-4 overflow-y-auto p-4">
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Every run</p>
        <div className="flex items-end gap-1.5">
          {runs.map((x, i) => (
            <button key={i} onClick={() => onPick(i)} title={`Run ${x.run}: ${x.passed}/${x.total}${x.coverage != null ? `, coverage ${x.coverage}%` : ""}`}
              className={clsx("flex w-10 flex-col items-center gap-1 rounded-[10px] p-1", runs.indexOf(r) === i && "bg-primary/12 ring-1 ring-primary/40")}>
              <span className="flex w-5 flex-col-reverse overflow-hidden rounded-[4px] bg-bg-2" style={{ height: 64 }}>
                <span className="bg-success" style={{ height: `${(x.passed / maxTotal) * 100}%` }} />
                <span className="bg-danger" style={{ height: `${((x.failed + x.error) / maxTotal) * 100}%` }} />
              </span>
              <span className="text-[10px] text-muted">{x.run === "final" ? "final" : `#${x.run}`}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Passed" value={`${r.passed}/${r.total}`} tone="var(--success)" />
        <Stat label="Failing" value={String(r.failed + r.error)} tone={r.failed + r.error ? "var(--danger)" : undefined} />
        <Stat label="Coverage" value={r.coverage != null ? `${r.coverage}%` : "–"} />
      </div>
      {failing.length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-danger">Failing ({failing.length})</p>
          <ul className="space-y-1.5">{failing.map((t) => (
            <li key={t.id} className="rounded-[12px] border border-danger/30 bg-danger/[0.05] px-3 py-2">
              <p className="flex items-start gap-1.5 font-mono text-[12px]"><XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" /><span className="break-all">{t.id}</span></p>
              {t.message && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11.5px] text-muted">{t.message}</pre>}
            </li>
          ))}</ul>
        </div>
      )}
      {Object.keys(r.files).length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Coverage by file</p>
          <ul className="space-y-1.5">{Object.entries(r.files).sort((a, b) => a[1].percent - b[1].percent).map(([p, f]) => (
            <li key={p} className="text-[12px]">
              <div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate font-mono">{p}</span><b className="tabular-nums">{f.percent}%</b></div>
              <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-bg-2">
                <motion.div className="h-full rounded-full" initial={{ width: 0 }} animate={{ width: `${f.percent}%` }}
                  style={{ background: f.percent >= 80 ? "var(--success)" : f.percent >= 60 ? "var(--warning)" : "var(--danger)" }} />
              </div>
              {f.missing.length > 0 && <p className="mt-0.5 text-[11px] text-muted">untested lines {f.missing.slice(0, 20).join(", ")}{f.missing.length > 20 ? "…" : ""}</p>}
            </li>
          ))}</ul>
        </div>
      )}
      <details className="rounded-[12px] border border-line">
        <summary className="cursor-pointer px-3 py-2 text-[12px] font-semibold">All tests ({r.tests.length})</summary>
        <ul className="max-h-72 space-y-0.5 overflow-y-auto px-3 pb-2">{r.tests.map((t) => (
          <li key={t.id} className="flex items-start gap-1.5 font-mono text-[11.5px]">
            {t.outcome === "passed" ? <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-success" /> : <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-danger" />}
            <span className="break-all">{t.id}</span></li>
        ))}</ul>
      </details>
      {r.output && <pre className="max-h-64 overflow-auto rounded-[12px] bg-bg-2/60 p-3 font-mono text-[11.5px]">{r.output}</pre>}
    </div>
  );
}

function Notes({ events }: { events: WorkEvent[] }) {
  const said = events.filter((e) => e.phase === "say" || (e.phase === "result" && e.error));
  if (!said.length) return <p className="p-10 text-center text-sm text-muted">The agent hasn't written any notes in this run.</p>;
  return (
    <ul className="h-full space-y-2 overflow-y-auto p-4">
      {said.map((e) => (
        <li key={e.n} className={clsx("rounded-[14px] px-3.5 py-2.5 text-sm", e.error ? "border border-danger/30 bg-danger/[0.05]" : "bg-bg-2/50")}>
          <p className="mb-0.5 text-[10.5px] text-muted">{e.error ? "Rejected by the platform check" : `Step ${e.turn}`} · {asDate(e.ts).toLocaleTimeString()}</p>
          <p className="whitespace-pre-wrap break-words">{e.detail}</p>
        </li>
      ))}
    </ul>
  );
}
