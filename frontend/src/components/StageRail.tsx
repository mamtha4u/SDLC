import clsx from "clsx";
import { motion } from "framer-motion";
import { AlertTriangle, Check, Cloud, Code2, FlaskConical, GitCompareArrows, MessagesSquare, Network, Rocket, Server } from "lucide-react";
import type { Agent, ProjectDetail } from "../lib/api";
import { helpingWho } from "../lib/flow";
import type { WorkspaceTab } from "./NextStep";

type S = "done" | "active" | "review" | "issue" | "todo";
const STAGES: { key: string; label: string; tab: WorkspaceTab; icon: typeof Rocket }[] = [
  { key: "intake", label: "Requirement", tab: "requirement", icon: MessagesSquare },
  { key: "ba", label: "Mapping", tab: "mapping", icon: GitCompareArrows },
  { key: "ta", label: "Design", tab: "design", icon: Network },
  { key: "tp", label: "Infra", tab: "build", icon: Server },
  { key: "check", label: "AWS check", tab: "aws", icon: Cloud },
  { key: "de", label: "Code", tab: "build", icon: Code2 },
  { key: "qa", label: "Testing", tab: "testing", icon: FlaskConical },
  { key: "live", label: "Done", tab: "testing", icon: Rocket },
];
const TONE: Record<S, string> = { done: "var(--primary)", active: "var(--primary-2)", review: "var(--warning)", issue: "var(--danger)", todo: "var(--border)" };
const WORD: Record<S, string> = { done: "done", active: "working", review: "needs you", issue: "problem", todo: "" };

function stateOf(a?: Agent): S {
  if (!a) return "todo";
  return a.status === "done" ? "done" : a.status === "working" ? "active" : a.status === "needs_approval" ? "review"
    : a.status === "failed" || a.status === "blocked" ? "issue" : "todo";
}

/** The project's journey at a glance, in the team's order: infrastructure first, you check it in AWS, then Dev's code,
 *  then Quinn's live tests (and the ticket loop). What's done, who's working, where you're needed. Click to jump. */
export function StageRail({ project, onGo, pending }: { project: ProjectDetail; onGo: (t: WorkspaceTab) => void; pending?: string }) {
  const ag = (k: string) => project.agents.find((a) => a.key === k);
  const p = project.progress;
  const busy = (k: string) => ag(k)?.status === "working";
  const broken = (k: string) => ["failed", "blocked"].includes(ag(k)?.status ?? "");
  const deStarted = !!ag("de") && ag("de")!.status !== "waiting";
  // Archie/Orion helping a stuck teammate: that teammate's step is the one being worked on, not Design
  const helped = new Set(project.agents.map((a) => helpingWho(a)).filter(Boolean));
  const states: S[] = STAGES.map((st) => {
    switch (st.key) {
      case "ta":
        if (ag("ta") && helpingWho(ag("ta")!)) return "done";
        return stateOf(ag("ta"));
      case "tp":
        if (pending === "infra" || pending === "deploy") return "review";
        if (helped.has("tp")) return "active";
        if (p >= 0.6 || deStarted) return busy("tp") ? "active" : "done";
        return stateOf(ag("tp"));
      case "check":
        if (pending === "infra_check") return "review";
        return deStarted || p >= 0.7 ? "done" : "todo";
      case "de":
        if (pending === "code" || pending === "code_review") return "review";
        if (busy("de") || helped.has("de")) return "active";
        if (broken("de")) return "issue";
        return p >= 0.85 || project.status === "completed" || ag("de")?.status === "done" ? "done" : "todo";
      case "qa":
        if (pending === "test_plan" || pending === "live_bugs" || pending === "live") return "review";
        if (busy("qa")) return "active";
        if (broken("qa")) return "issue";
        return project.status === "completed" ? "done" : ag("qa")?.status === "done" && p >= 0.85 ? "done" : "todo";
      case "live":
        return project.status === "completed" ? "done" : "todo";
      default:
        return stateOf(ag(st.key));
    }
  });
  const reached = states.reduce((m, s, i) => (s !== "todo" ? i : m), 0);
  const frac = reached / (STAGES.length - 1);
  return (
    <div className="mt-4 px-6 sm:px-8"><div className="relative">
      <div className="absolute left-4 right-4 top-[15px] h-[3px] rounded-full bg-bg-2" />
      <div className="absolute left-4 top-[15px] h-[3px] overflow-hidden rounded-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))]"
        style={{ width: `calc((100% - 32px) * ${frac})`, transition: "width 0.9s cubic-bezier(.2,.7,.2,1)", boxShadow: "0 0 14px color-mix(in srgb, var(--primary-2) 60%, transparent)" }}>
        <div className="rail-flow h-full w-full opacity-80" />
      </div>
      <ol className="relative flex justify-between">
        {STAGES.map((st, i) => {
          const s = states[i];
          const Icon = st.icon;
          return (
            <li key={st.key} className="flex w-8 flex-col items-center">
              <motion.button onClick={() => onGo(st.tab)} whileHover={{ y: -2 }} whileTap={{ scale: 0.94 }}
                title={`${st.label}${WORD[s] ? ` · ${WORD[s]}` : ""}`} aria-label={`${st.label}${WORD[s] ? `, ${WORD[s]}` : ""}`}
                initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.05 * i, type: "spring", stiffness: 380, damping: 22 }}
                className={clsx("focus-ring relative grid h-8 w-8 place-items-center rounded-full border-2 transition-colors",
                  s === "done" ? "border-transparent bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-on-primary"
                    : s === "todo" ? "border-line bg-surface text-muted" : "bg-surface",
                  (s === "active" || s === "review") && "pulse-ring")}
                style={s !== "done" && s !== "todo" ? { borderColor: TONE[s], color: TONE[s], ["--ring" as string]: TONE[s] } : undefined}>
                {s === "active" && <span className="spin-slow absolute -inset-[5px] rounded-full border-2 border-transparent"
                  style={{ borderTopColor: TONE[s], borderRightColor: `color-mix(in srgb, ${TONE[s]} 40%, transparent)` }} />}
                {s === "done" ? <Check className="h-4 w-4" strokeWidth={3} /> : s === "issue" ? <AlertTriangle className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
              </motion.button>
              <span className={clsx("mt-1.5 hidden whitespace-nowrap text-[11px] font-semibold sm:block", s === "todo" ? "text-muted" : "text-text")}>{st.label}</span>
              {WORD[s] && s !== "done" && <span className="hidden whitespace-nowrap text-[10px] font-semibold sm:block" style={{ color: TONE[s] }}>{WORD[s]}</span>}
            </li>
          );
        })}
      </ol>
    </div></div>
  );
}
