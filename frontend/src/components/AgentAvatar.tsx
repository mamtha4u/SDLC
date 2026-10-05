import clsx from "clsx";
import { motion } from "framer-motion";
import { Check, ClipboardList, Code2, Crown, FlaskConical, Layers, MessageSquareText, Server, Sparkles, X } from "lucide-react";
import type { AgentStatus } from "../lib/api";
import { ACCENT } from "../lib/themes";

const ICONS: Record<string, typeof Crown> = {
  cto: Crown, intake: MessageSquareText, ba: ClipboardList, ta: Layers, tp: Server, de: Code2, qa: FlaskConical, guide: Sparkles,
};

/** Agent avatar whose animation encodes its live state (the spec's state language):
 *  waiting = dim · working = pulsing glow + rotating ring · needs_approval = amber bounce ·
 *  done = check pop · failed = red shake · paused/blocked = frosted. */
export function AgentAvatar({ agent, accent, status = "waiting", size = 44, plain }: {
  agent: string; accent: string; status?: AgentStatus; size?: number; /** identity only: no status badge */ plain?: boolean;
}) {
  const Icon = ICONS[agent] ?? Crown;
  const color = status === "failed" ? "var(--danger)" : status === "needs_approval" ? "var(--warning)" : ACCENT[accent] ?? "var(--primary)";
  const working = status === "working";
  return (
    <motion.div className="relative shrink-0" style={{ width: size, height: size }}
      animate={status === "failed" ? { x: [0, -4, 4, -3, 3, 0] } : status === "needs_approval" ? { y: [0, -3, 0] } : { x: 0, y: 0 }}
      transition={status === "needs_approval" ? { repeat: Infinity, duration: 1.4 } : { duration: 0.45 }}>
      {working && (
        <span className="spin-slow absolute -inset-[3px] rounded-full"
          style={{ background: `conic-gradient(from 0deg, transparent 0 55%, ${color} 85%, transparent)` }} />
      )}
      <div className={clsx("relative grid h-full w-full place-items-center rounded-full neu-sm transition-all duration-500",
        status === "waiting" && "opacity-55 saturate-50", (status === "paused" || status === "blocked") && "opacity-70 blur-[0.3px]",
        working && "pulse-ring")}
        style={{ color, ["--ring" as string]: color, boxShadow: working || status === "done" ? `0 0 18px -2px ${color}` : undefined,
          background: `radial-gradient(circle at 30% 22%, color-mix(in srgb, ${color} 24%, var(--surface)), var(--surface) 72%)` }}>
        <Icon style={{ width: size * 0.45, height: size * 0.45 }} strokeWidth={2} />
      </div>
      {(status === "done" || status === "failed") && size >= 30 && !plain && (
        <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 500, damping: 15 }}
          className="absolute -right-0.5 -bottom-0.5 grid h-4 w-4 place-items-center rounded-full text-bg ring-2 ring-surface"
          style={{ background: status === "done" ? "var(--success)" : "var(--danger)" }}>
          {status === "done" ? <Check className="h-2.5 w-2.5" strokeWidth={3.5} /> : <X className="h-2.5 w-2.5" strokeWidth={3.5} />}
        </motion.span>
      )}
    </motion.div>
  );
}
