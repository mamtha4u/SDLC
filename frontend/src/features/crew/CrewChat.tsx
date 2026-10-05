import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowDown, ArrowRight, FileText, History, Radio, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Markdown } from "../../components/Markdown";
import { Skeleton } from "../../components/ui";
import type { Agent } from "../../lib/api";
import { CREW, ORION } from "../../lib/crew";
import { flowApi, storedName, type CrewMsg } from "../../lib/flow";
import { ACCENT } from "../../lib/themes";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));
const ORDER = ["cto", "intake", "ba", "ta", "tp", "de", "qa"];
const KIND: Record<string, { label: string; tone: string }> = {
  handoff: { label: "Hand-off", tone: "var(--primary)" },
  assign: { label: "Task", tone: "var(--primary)" },
  ack: { label: "On it", tone: "var(--success)" },
  chat: { label: "Chat", tone: "var(--primary-2)" },
  request: { label: "Change request", tone: "var(--primary)" },
  question: { label: "Needs you", tone: "var(--warning)" },
  answer: { label: "Answer", tone: "var(--primary-2)" },
  update: { label: "Update", tone: "var(--primary-2)" },
  issue: { label: "Issue", tone: "var(--danger)" },
  fix: { label: "Fixed", tone: "var(--success)" },
  decision: { label: "Decision", tone: "var(--primary)" },
  done: { label: "Done", tone: "var(--success)" },
};
const name = (k: string) => (k === "user" ? "You" : k === "crew" ? "everyone" : META[k]?.persona ?? k);
const VIEWS = {
  all: { label: "Everything", test: () => true },
  talk: { label: "Conversation", test: (m: CrewMsg) => !["work", "think"].includes(m.kind) },
  work: { label: "Work & thinking", test: (m: CrewMsg) => ["work", "think", "issue", "fix"].includes(m.kind) },
  issues: { label: "Issues & fixes", test: (m: CrewMsg) => ["issue", "fix"].includes(m.kind) },
} as const;
type View = keyof typeof VIEWS;

/** The crew room: a read-only group chat of what the agents tell each other: hand-offs, questions, issues, fixes. */
export function CrewChat({ projectId, agents }: { projectId: string; agents: Agent[] }) {
  const working = agents.filter((a) => a.status === "working");
  const { data, isLoading } = useQuery({
    queryKey: ["crew", projectId], queryFn: () => flowApi.crew(projectId), refetchInterval: working.length ? 3000 : 9000,
  });
  const [focus, setFocus] = useState<string | null>(null);
  const [view, setView] = useState<View>("all");
  const list = useMemo(() => (data ?? []).filter((m) => (!focus || m.sender === focus || m.to === focus) && VIEWS[view].test(m)),
    [data, focus, view]);
  const counts = useMemo(() => Object.fromEntries((Object.keys(VIEWS) as View[]).map((v) => [v, (data ?? []).filter(VIEWS[v].test).length])), [data]);
  const scroller = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const reduce = useReducedMotion();

  useEffect(() => {
    const el = scroller.current;
    if (el && atBottom) el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : "smooth" });
  }, [list.length, working.length, atBottom, reduce]);

  const onScroll = () => {
    const el = scroller.current;
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };

  return (
    <div className="sheen elev relative flex h-[calc(100dvh-13rem)] min-h-[520px] flex-col overflow-hidden rounded-[28px] border border-line bg-surface">
      {/* header: who's in the room */}
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 sm:px-6">
        <div className="mr-auto">
          <h3 className="flex items-center gap-2 font-display text-lg font-semibold"><Users className="h-5 w-5 text-primary" />Crew room</h3>
          <p className="text-xs text-muted">What the agents tell each other, live. Read-only: you talk to the crew through Echo and the approvals.</p>
        </div>
        <div className="no-scrollbar flex items-center gap-1 overflow-x-auto">
          {(Object.keys(VIEWS) as View[]).map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={clsx("shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold", view === v ? "bg-text text-bg" : "text-muted hover:text-text")}>
              {VIEWS[v].label} <span className="opacity-60">{counts[v]}</span>
            </button>
          ))}
        </div>
        <div className="no-scrollbar flex w-full items-center gap-1 overflow-x-auto sm:w-auto">
          <button onClick={() => setFocus(null)}
            className={clsx("rounded-full px-3 py-1.5 text-xs font-semibold", !focus ? "bg-primary text-on-primary" : "text-muted hover:text-text")}>All agents</button>
          {ORDER.map((k) => {
            const a = agents.find((x) => x.key === k);
            const m = META[k];
            if (!a || !m) return null;
            return (
              <button key={k} onClick={() => setFocus(focus === k ? null : k)} title={`${m.persona} · ${a.status === "working" ? "working" : a.status}`}
                className={clsx("relative rounded-full p-0.5 transition-transform hover:-translate-y-0.5", focus === k && "ring-2 ring-primary")}>
                <AgentAvatar agent={k} accent={a.accent} status={a.status} size={32} />
              </button>
            );
          })}
        </div>
      </div>

      {/* messages */}
      <div ref={scroller} onScroll={onScroll} className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6">
        {isLoading ? <div className="space-y-3"><Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
          : !list.length ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <Radio className="h-10 w-10 text-muted" />
              <p className="font-display text-lg font-semibold">The room is quiet</p>
              <p className="max-w-sm text-sm text-muted">Agents post here at every hand-off: when Echo hands the requirement to Orion, when Orion briefs the crew, when something goes wrong and how it gets fixed.</p>
            </div>
          ) : (
            <>
              {list.some((m) => m.data?.backfilled) && (
                <p className="mx-auto mb-4 flex w-fit items-center gap-1.5 rounded-full bg-bg-2 px-3 py-1 text-[11px] text-muted">
                  <History className="h-3.5 w-3.5" />Earlier history was rebuilt from this project's activity log
                </p>
              )}
              <Messages list={list} projectId={projectId} />
            </>
          )}
        <AnimatePresence>
          {working.map((a) => (
            <motion.div key={a.key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className="mt-3 flex items-center gap-2.5">
              <AgentAvatar agent={a.key} accent={a.accent} status="working" size={30} />
              <div className="flex min-w-0 items-center gap-2 rounded-[16px] border border-line bg-bg-2/60 px-3 py-2 text-[13px]">
                <span className="flex gap-1">
                  {[0, 1, 2].map((d) => (
                    <motion.span key={d} className="h-1.5 w-1.5 rounded-full" style={{ background: ACCENT[a.accent] }}
                      animate={reduce ? undefined : { opacity: [0.25, 1, 0.25], y: [0, -2, 0] }} transition={{ repeat: Infinity, duration: 1, delay: d * 0.15 }} />
                  ))}
                </span>
                <span className="truncate"><b>{META[a.key]?.persona}</b> <span className="text-muted">is working: {a.activity}</span></span>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      <AnimatePresence>
        {!atBottom && (
          <motion.button initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}
            onClick={() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" })}
            className="absolute bottom-4 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-primary px-3.5 py-2 text-xs font-semibold text-on-primary shadow-lg">
            <ArrowDown className="h-3.5 w-3.5" />Latest
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}

function Messages({ list, projectId }: { list: CrewMsg[]; projectId: string }) {
  let lastDay = "";
  return (
    <ol className="space-y-1.5">
      {list.map((m, i) => {
        const prev = list[i - 1];
        const day = dayOf(m.created_at);
        const sep = day !== lastDay;
        lastDay = day;
        // merge only consecutive lines from the same agent TO THE SAME recipient, so it's always clear who it's for
        const solo = (k: string) => ["work", "think", "chat"].includes(k);
        const grouped = !sep && prev && prev.sender === m.sender && prev.to === m.to && !solo(prev.kind) && !solo(m.kind)
          && asDate(m.created_at).getTime() - asDate(prev.created_at).getTime() < 180_000;
        return (
          <li key={m.id}>
            {sep && <p className="my-3 text-center text-[11px] font-semibold uppercase tracking-wider text-muted">{day}</p>}
            <Line m={m} grouped={!!grouped} projectId={projectId} />
          </li>
        );
      })}
    </ol>
  );
}

/** **bold** and `code` in one-line work notes (too short for the full markdown renderer). */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
        p.length > 4 && p.startsWith("**") && p.endsWith("**") ? <b key={i} className="text-text">{p.slice(2, -2)}</b>
          : p.length > 2 && p.startsWith("`") && p.endsWith("`") ? <code key={i} className="rounded bg-bg-2 px-1 font-mono text-[11.5px]">{p.slice(1, -1)}</code>
            : p)}
    </>
  );
}

function Line({ m, grouped, projectId }: { m: CrewMsg; grouped: boolean; projectId: string }) {
  const reduce = useReducedMotion();
  const enter = reduce || m.data?.backfilled ? false : { opacity: 0, y: 10 };
  if (m.sender === "user" && m.kind === "chat") {
    return (
      <motion.div initial={enter} animate={{ opacity: 1, y: 0 }} className="mt-3 flex justify-end">
        <div className="max-w-[min(640px,85%)]">
          <p className="mb-1 text-right text-[12px] text-muted"><b className="text-text">You</b> <ArrowRight className="inline h-3 w-3" /> {name(m.to)} · <span className="font-mono text-[10.5px]">{timeOf(m.created_at)}</span></p>
          <div className="rounded-[16px] rounded-tr-[6px] bg-primary/14 px-3.5 py-2 text-[13.5px]">
            <Fold text={m.text} limit={420} plain />
            <Files files={m.data?.files} projectId={projectId} />
          </div>
        </div>
      </motion.div>
    );
  }
  if (m.sender === "user") {
    return (
      <motion.div initial={enter} animate={{ opacity: 1, y: 0 }} className="my-2 flex justify-center">
        <div className="max-w-[90%] rounded-[18px] border border-primary/30 bg-primary/10 px-4 py-1.5 text-center text-[12.5px]">
          <b>You</b> <span className="text-muted">·</span> {m.text.length > 260 ? m.text.slice(0, 260) + "…" : m.text}
          <span className="ml-2 font-mono text-[10.5px] text-muted">{timeOf(m.created_at)}</span>
        </div>
      </motion.div>
    );
  }
  const meta = META[m.sender];
  const accent = meta ? ACCENT[meta.accent] : "var(--primary)";
  if (m.kind === "work") {
    return (
      <motion.div initial={enter} animate={{ opacity: 1, y: 0 }} className="flex items-baseline gap-2 py-0.5 pl-11 text-[12.5px] text-muted">
        <span className="h-1 w-1 shrink-0 translate-y-[-2px] rounded-full" style={{ background: accent }} />
        <span className="min-w-0 flex-1 break-words"><b className="text-text/80">{meta?.persona}</b> <Inline text={m.text} /></span>
        <span className="shrink-0 font-mono text-[10.5px]">{timeOf(m.created_at)}</span>
      </motion.div>
    );
  }
  if (m.kind === "think") {
    return (
      <motion.div initial={enter} animate={{ opacity: 1, y: 0 }} className="flex gap-2 py-1 pl-11 text-[12.5px]">
        <span className="shrink-0">💭</span>
        <div className="min-w-0 flex-1 border-l-2 pl-2.5 italic text-muted" style={{ borderColor: `color-mix(in srgb, ${accent} 45%, transparent)` }}>
          <b className="not-italic text-text/80">{meta?.persona}</b> <span className="not-italic font-mono text-[10.5px]">{timeOf(m.created_at)}</span>
          <Fold text={m.text} limit={320} />
        </div>
      </motion.div>
    );
  }
  const k = KIND[m.kind] ?? KIND.update;
  return (
    <motion.div initial={enter} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 320, damping: 30 }}
      className={clsx("flex gap-2.5", grouped ? "mt-0.5" : "mt-3")}>
      <div className="w-[34px] shrink-0">{!grouped && meta && <AgentAvatar agent={meta.key} accent={meta.accent} status="done" size={34} plain />}</div>
      <div className="min-w-0 max-w-[min(760px,92%)]">
        {!grouped && (
          <p className="mb-1 flex flex-wrap items-center gap-1.5 text-[12px]">
            <b style={{ color: accent }}>{meta?.persona ?? m.sender}</b>
            <span className="font-mono text-[10px] font-bold text-muted">{meta?.abbr}</span>
            {m.to !== "crew" && <span className="inline-flex items-center gap-0.5 text-muted"><ArrowRight className="h-3 w-3" />{name(m.to)}</span>}
            <span className="font-mono text-[10.5px] text-muted">{timeOf(m.created_at)}</span>
          </p>
        )}
        <div className="relative rounded-[16px] rounded-tl-[6px] border px-3.5 py-2.5 text-[13.5px]"
          style={{ borderColor: `color-mix(in srgb, ${k.tone} ${m.kind === "update" || m.kind === "ack" ? 22 : 45}%, var(--border))`,
            background: `linear-gradient(135deg, color-mix(in srgb, ${accent} 10%, var(--bg-2)), color-mix(in srgb, var(--bg-2) 60%, transparent) 70%)` }}>
          {m.kind !== "update" && m.kind !== "chat" && (
            <span className="mb-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider"
              style={{ color: k.tone, background: `color-mix(in srgb, ${k.tone} 14%, transparent)` }}>{k.label}</span>
          )}
          {grouped && m.to !== "crew" && <p className="mb-0.5 text-[11px] text-muted"><ArrowRight className="inline h-3 w-3" /> {name(m.to)}</p>}
          <Fold text={m.text} limit={m.kind === "chat" ? 520 : 900} />
          <Files files={m.data?.files} projectId={projectId} />
        </div>
      </div>
    </motion.div>
  );
}

/** Long messages fold; tap to read all of it. */
function Fold({ text, limit, plain }: { text: string; limit: number; plain?: boolean }) {
  const [open, setOpen] = useState(false);
  const long = text.length > limit;
  const shown = !long || open ? text : text.slice(0, limit).replace(/\s+\S*$/, "") + " …";
  return (
    <>
      {plain ? <p className="whitespace-pre-wrap">{shown}</p> : <Markdown className="crew-md">{shown}</Markdown>}
      {long && (
        <button onClick={() => setOpen(!open)} className="mt-0.5 text-[11.5px] font-semibold not-italic text-primary-2">
          {open ? "Show less" : "Read all"}
        </button>
      )}
    </>
  );
}

function Files({ files, projectId }: { files?: string[]; projectId: string }) {
  if (!files?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {files.map((f) => (
        <a key={f} href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent(f.includes("/") || /\.(md|json|tf|drawio)$/.test(f) ? f : `inputs/${storedName(f)}`)}&download=1`}
          className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-0.5 text-[11.5px] not-italic hover:border-primary hover:text-primary">
          <FileText className="h-3 w-3" />{f}
        </a>
      ))}
    </div>
  );
}

const asDate = (iso: string) => new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
const timeOf = (iso: string) => asDate(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
function dayOf(iso: string) {
  const d = asDate(iso);
  const today = new Date();
  const y = new Date(today); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}
