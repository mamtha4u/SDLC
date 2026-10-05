import clsx from "clsx";
import { motion } from "framer-motion";
import { Radio } from "lucide-react";
import { useCallback, useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { StatusPill } from "../../components/ui";
import type { Agent, OrkEvent, ProjectDetail } from "../../lib/api";
import { CREW, ORION } from "../../lib/crew";
import { ACCENT } from "../../lib/themes";
import { AgentModal } from "./AgentModal";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));
const PHASE: Record<string, string> = {}; // every agent is built; kept for future agents that arrive later
const tone = (s: Agent["status"]) =>
  s === "failed" || s === "blocked" ? "var(--danger)" : s === "needs_approval" ? "var(--warning)" : s === "done" ? "var(--success)" : s === "working" ? "var(--primary-2)" : "var(--border)";

/** The live stage: Orion conducts, six stations on a rail, hand-offs animate; every station opens its full popup. */
export function PipelineStage({ project, events, onOpenWorkspace }: {
  project: ProjectDetail; events: OrkEvent[]; onOpenWorkspace: (agent: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const close = useCallback(() => setOpen(null), []);
  const cto = project.agents.find((a) => a.key === "cto")!;
  const crew = project.agents.filter((a) => a.key !== "cto");
  const activeIdx = crew.findIndex((a) => a.status === "working" || a.status === "needs_approval" || a.status === "failed" || a.status === "blocked");
  const doneCount = crew.filter((a) => a.status === "done").length;

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="sheen elev relative overflow-hidden rounded-[28px] border border-line bg-surface p-5 sm:p-7">
        {/* soft stage light */}
        <div className="pointer-events-none absolute -top-40 left-1/2 h-80 w-[46rem] -translate-x-1/2 rounded-full opacity-30"
          style={{ background: "radial-gradient(closest-side, var(--primary), transparent)" }} />

        {/* conductor */}
        <motion.button onClick={() => setOpen("cto")} whileHover={{ y: -3 }} whileTap={{ scale: 0.98 }}
          className="relative mx-auto flex w-full max-w-md items-center gap-4 rounded-[22px] border border-line bg-bg-2/70 p-4 text-left shadow-sm transition-colors hover:border-primary/60">
          <div className="relative">
            <span className="sonar absolute inset-0 rounded-full border border-primary/50" />
            <AgentAvatar agent="cto" accent="violet" status={cto.status} size={56} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="font-display text-lg font-bold">Orion</p>
              <span className="rounded-md bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] font-bold text-primary">CTO</span>
              <span className="ml-auto"><StatusPill status={cto.status} agent /></span>
            </div>
            <p className="truncate text-sm text-muted">{cto.activity || "Conducting the crew"}</p>
          </div>
        </motion.button>

        {/* baton line down to the rail */}
        <div className="mx-auto h-8 w-px bg-[linear-gradient(var(--primary),transparent)]" />

        <div className="mb-3 flex items-center justify-between text-xs text-muted">
          <span>{doneCount} of {crew.length} agents done</span>
          <span className="hidden sm:inline">Click any agent for approvals, files, activity and its full conversation</span>
        </div>

        {/* rail + stations */}
        <div className="relative">
          <div className="absolute left-[8%] right-[8%] top-[42px] hidden h-[3px] rounded-full bg-line lg:block" />
          <motion.div className="absolute left-[8%] top-[42px] hidden h-[3px] overflow-hidden rounded-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))] lg:block"
            style={{ boxShadow: "0 0 12px var(--primary-2)" }}
            animate={{ width: `${(Math.max(0, activeIdx >= 0 ? activeIdx : doneCount - 1) / (crew.length - 1)) * 84}%` }}
            transition={{ type: "spring", stiffness: 60, damping: 18 }}>
            <div className="rail-flow h-full w-full" />
          </motion.div>
          <div className="relative grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
            {crew.map((a, i) => (
              <Station key={a.key} a={a} i={i} active={i === activeIdx} onClick={() => setOpen(a.key)} />
            ))}
          </div>
        </div>
      </div>

      <ActivityFeed events={events} onPick={setOpen} />

      <AgentModal projectId={project.id} agent={project.agents.find((x) => x.key === open) ?? null} onClose={close}
        onOpenWorkspace={(k) => { setOpen(null); onOpenWorkspace(k); }} />
    </div>
  );
}

function Station({ a, i, active, onClick }: { a: Agent; i: number; active: boolean; onClick: () => void }) {
  const m = META[a.key];
  const c = tone(a.status);
  return (
    <motion.button onClick={onClick} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
      whileHover={{ y: -4 }} whileTap={{ scale: 0.97 }}
      className={clsx("spotlight group relative flex flex-col items-center gap-2 rounded-[20px] border bg-bg-2/60 px-3 pb-3 pt-4 text-center transition-colors",
        active ? "border-transparent" : "border-line hover:border-primary/50")}
      style={active ? { boxShadow: `0 0 0 2px ${c}, 0 14px 34px -14px ${c}` } : undefined}>
      <AgentAvatar agent={a.key} accent={a.accent} status={a.status} size={52} />
      <div className="min-w-0">
        <p className="font-display font-semibold leading-tight">{m?.persona}</p>
        <p className="font-mono text-[10px] font-bold tracking-wider" style={{ color: ACCENT[a.accent] }}>{m?.abbr}</p>
      </div>
      <StatusPill status={a.status} agent />
      <p className={clsx("line-clamp-2 min-h-[2.2em] text-[11px] leading-snug", a.status === "failed" ? "text-danger" : "text-muted")}>
        {a.status === "waiting" && PHASE[a.key] ? `Arrives in ${PHASE[a.key]}` : a.activity || "—"}
      </p>
      <span className="text-[10px] text-muted opacity-0 transition-opacity group-hover:opacity-100">Open ›</span>
    </motion.button>
  );
}

function ActivityFeed({ events, onPick }: { events: OrkEvent[]; onPick: (agent: string) => void }) {
  const shown = events.filter((e) => !["agent.state", "project.updated", "intake.delta"].includes(e.type)).slice(-40).reverse();
  return (
    <aside className="sheen elev rounded-[28px] border border-line bg-surface p-4">
      <h3 className="mb-3 flex items-center gap-2 font-display font-semibold"><Radio className="h-4 w-4 text-primary-2" />Activity</h3>
      <ol className="no-scrollbar max-h-[520px] space-y-0.5 overflow-y-auto">
        {shown.length === 0 && <p className="py-8 text-center text-sm text-muted">Nothing yet.</p>}
        {shown.map((e) => {
          const m = e.agent ? META[e.agent] : null;
          return (
            <li key={`${e.id}-${e.created_at}`}>
              <button disabled={!m} onClick={() => m && onPick(m.key)} className="flex w-full gap-3 rounded-[12px] px-2 py-2 text-left hover:bg-bg-2/70">
                {m ? <AgentAvatar agent={m.key} accent={m.accent} status="done" size={26} /> : <span className="mt-1.5 h-2 w-2 rounded-full bg-primary" />}
                <div className="min-w-0">
                  <p className="text-[13px] leading-snug">{e.message}</p>
                  <p className="text-[11px] text-muted">{m ? `${m.persona} · ` : ""}{when(e.created_at)}</p>
                </div>
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

function when(iso: string) {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
