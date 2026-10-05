import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Cloud, FileText, MousePointerClick } from "lucide-react";
import { useEffect, useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { LogoMark } from "../../components/Logo";
import type { AgentStatus } from "../../lib/api";
import { CREW } from "../../lib/crew";

export const BARS = CREW.length + 1; // six agents, then the final chord: live in AWS
const MELODY = [64, 40, 58, 30, 52, 36, 46]; // note heights (% of the staff), the last one is the AWS chord
const ARTIFACT = ["00_requirement.md", "01_data_mapping.md", "architecture.drawio", "infra/ plan", "src/ + tests/", "live_qa.md ✓"];
const xOf = (i: number) => 11 + (i * 82) / (BARS - 1); // room for the clef on the left
const statusOf = (i: number, active: number): AgentStatus => (i < active ? "done" : i === active ? "working" : "waiting");

/** The landing page's centrepiece: the crew as a musical score. A conductor's playhead moves bar by bar; each agent
 *  "plays" its part and drops the file it really produces; the final chord blooms into a live AWS flow. */
export function Score({ active, paused, compact, onHover, onPick }: {
  active: number; paused: boolean; compact?: boolean;
  onHover: (on: boolean) => void; onPick: (index: number, e: React.MouseEvent) => void;
}) {
  const avatar = compact ? 34 : 46;
  const live = active === BARS - 1;
  const path = smooth(MELODY.map((y, i) => [xOf(i), y] as [number, number]));
  return (
    <div className={clsx("glass sheen relative w-full overflow-hidden rounded-[26px]", compact ? "p-3.5" : "p-4 xl:p-5")}
      onPointerEnter={(e) => e.pointerType === "mouse" && onHover(true)} onPointerLeave={(e) => e.pointerType === "mouse" && onHover(false)}>
      {/* header: the conductor and the bar counter */}
      <div className="flex items-center justify-between gap-3">
        <button type="button" onClick={(e) => onPick(-1, e)} title="What does Orion do?"
          className="focus-ring -m-1 flex min-w-0 items-center gap-2.5 rounded-[14px] p-1 text-left transition-colors hover:bg-surface-2/60">
          <div className="relative grid place-items-center">
            <span className="sonar absolute h-9 w-9 rounded-[12px] border border-primary/60" />
            <LogoMark size={compact ? 30 : 36} animated={false} />
          </div>
          <div className="min-w-0 leading-tight">
            <p className="text-sm font-semibold">Orion conducting</p>
            <p className="truncate text-[11px] text-muted">Partner orders ingest · v1.1</p>
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-2 rounded-full px-2.5 py-1 text-[11px] font-semibold"
          style={{ color: live ? "var(--success)" : "var(--primary-2)", background: `color-mix(in srgb, ${live ? "var(--success)" : "var(--primary-2)"} 12%, transparent)` }}>
          <Equalizer paused={paused} />
          {paused ? "PAUSED" : live ? "LIVE IN AWS" : "PLAYING"} · bar {active + 1}/{BARS}
        </div>
      </div>

      {/* the staff */}
      <div className={clsx("relative mt-3", compact ? "h-[150px]" : "h-[clamp(150px,22vh,196px)]")}>
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
          <defs>
            <linearGradient id="staff" x1="0" x2="1">
              <stop offset="0" stopColor="var(--text)" stopOpacity="0.1" />
              <stop offset="0.5" stopColor="var(--primary-2)" stopOpacity="0.45" />
              <stop offset="1" stopColor="var(--text)" stopOpacity="0.1" />
              <animateTransform attributeName="gradientTransform" type="translate" values="-1 0; 1 0" dur="6s" repeatCount="indefinite" />
            </linearGradient>
            <linearGradient id="melody" x1="0" x2="1">
              <stop offset="0" stopColor="var(--primary)" />
              <stop offset="1" stopColor="var(--primary-2)" />
            </linearGradient>
          </defs>
          {[26, 38, 50, 62, 74].map((y) => <line key={y} x1="2" x2="98" y1={y} y2={y} stroke="url(#staff)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
          <line x1="2" x2="2" y1="26" y2="74" stroke="var(--text)" strokeOpacity="0.25" strokeWidth="3" vectorEffect="non-scaling-stroke" />
          <path d={path} fill="none" stroke="var(--border)" strokeWidth="2" strokeDasharray="2 5" vectorEffect="non-scaling-stroke" />
          <motion.path d={path} fill="none" stroke="url(#melody)" strokeWidth="3" strokeLinecap="round" vectorEffect="non-scaling-stroke"
            style={{ filter: "drop-shadow(0 0 6px var(--primary-2))" }}
            initial={false} animate={{ pathLength: active / (BARS - 1) }} transition={{ type: "spring", stiffness: 50, damping: 16 }} />
        </svg>

        {!compact && (
          <span aria-hidden className="pointer-events-none absolute left-[0.6%] top-1/2 -translate-y-[54%] select-none leading-none text-text/30"
            style={{ fontSize: 58, fontFamily: "'Segoe UI Symbol', 'Noto Music', 'Apple Symbols', serif" }}>𝄞</span>
        )}

        {/* the conductor's playhead */}
        <motion.div className="absolute bottom-[8%] top-[8%] w-0" initial={false} animate={{ left: `${xOf(active)}%` }}
          transition={active === 0 ? { duration: 0.6, ease: [0.7, 0, 0.3, 1] } : { type: "spring", stiffness: 90, damping: 18 }}>
          <div className="absolute inset-y-0 -left-px w-[2px] rounded-full bg-[linear-gradient(transparent,var(--primary-2),transparent)]"
            style={{ boxShadow: "0 0 14px 2px color-mix(in srgb, var(--primary-2) 60%, transparent)" }} />
          <div className="absolute -left-[5px] -top-1 h-2.5 w-2.5 rotate-45 rounded-[2px] bg-primary-2" />
        </motion.div>

        {/* the notes: one agent per bar */}
        {CREW.map((c, i) => (
          <div key={c.key} className="absolute -translate-x-1/2" style={{ left: `${xOf(i)}%`, top: `calc(${MELODY[i]}% - ${avatar / 2}px)` }}>
            <AnimatePresence>
              {i === active && (
                <motion.span key="chip" initial={{ opacity: 0, y: 6, scale: 0.8 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -18, scale: 0.9 }}
                  transition={{ type: "spring", stiffness: 380, damping: 24 }}
                  className="absolute bottom-full left-1/2 mb-2 hidden -translate-x-1/2 items-center gap-1 whitespace-nowrap rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[10.5px] shadow-lg sm:inline-flex">
                  <FileText className="h-3 w-3 text-primary-2" />{ARTIFACT[i]}
                </motion.span>
              )}
            </AnimatePresence>
            <motion.button type="button" onClick={(e) => onPick(i, e)} title={`What does ${c.persona} (${c.abbr}) do?`}
              animate={i === active ? { scale: [1, 1.18, 1.06] } : { scale: 1 }} transition={{ duration: 0.5 }}
              whileHover={{ y: -3 }} whileTap={{ scale: 0.92 }} className="focus-ring group relative flex flex-col items-center rounded-full">
              {i === active && <span className="sonar absolute left-1/2 top-0 -translate-x-1/2 rounded-full border-2" style={{ width: avatar, height: avatar, borderColor: "var(--primary-2)" }} />}
              <AgentAvatar agent={c.key} accent={c.accent} status={statusOf(i, active)} size={avatar} />
              <span className={clsx("mt-1 text-[11px] font-semibold transition-colors", i === active ? "text-text" : "text-muted group-hover:text-text")}>{c.persona}</span>
            </motion.button>
          </div>
        ))}

        {/* the final chord: live in AWS */}
        <div className="absolute -translate-x-1/2" style={{ left: `${xOf(BARS - 1)}%`, top: `calc(${MELODY[BARS - 1]}% - ${(avatar + 6) / 2}px)` }}>
          <motion.div className="relative grid place-items-center rounded-full border-2"
            style={{ width: avatar + 6, height: avatar + 6, borderColor: live ? "var(--success)" : "var(--border)",
              background: live ? "color-mix(in srgb, var(--success) 18%, var(--surface))" : "var(--surface)" }}
            animate={live ? { scale: [1, 1.25, 1.1] } : { scale: 1 }} transition={{ duration: 0.6 }}>
            {live && <Burst />}
            {live ? <Check className="h-5 w-5 text-success" strokeWidth={3} /> : <Cloud className="h-5 w-5 text-muted" />}
          </motion.div>
          <p className={clsx("mt-1 text-center text-[11px] font-semibold", live ? "text-success" : "text-muted")}>AWS</p>
        </div>
      </div>

      {/* what's being played right now */}
      <div className="neu-inset flex min-w-0 items-center gap-2 rounded-[12px] px-3 py-2 font-mono text-[12px]">
        <span className="shrink-0" style={{ color: live ? "var(--success)" : "var(--primary-2)" }}>
          {live ? "Orion ▸" : `${CREW[active].persona} (${CREW[active].abbr}) ▸`}
        </span>
        <span className="truncate text-muted">
          <TypeLine text={live ? "every step approved by you · live in AWS · every action audited" : CREW[active].doing} />
        </span>
      </div>
      {!compact && (
        <p className="mt-2 flex items-center justify-center gap-1 text-[11px] text-muted [@media(max-height:820px)]:hidden">
          <MousePointerClick className="h-3 w-3" /> Tap any player to see their part
        </p>
      )}
    </div>
  );
}

/** Sparks when the final chord lands. */
function Burst() {
  return (
    <>
      {Array.from({ length: 10 }).map((_, k) => {
        const a = (k / 10) * Math.PI * 2;
        return (
          <motion.span key={k} className="absolute h-1.5 w-1.5 rounded-full"
            style={{ background: k % 2 ? "var(--success)" : "var(--primary-2)" }}
            initial={{ x: 0, y: 0, opacity: 1, scale: 1 }} animate={{ x: Math.cos(a) * 40, y: Math.sin(a) * 40, opacity: 0, scale: 0.4 }}
            transition={{ duration: 0.9, ease: "easeOut" }} />
        );
      })}
      <span className="sonar absolute inset-0 rounded-full border-2 border-success" />
    </>
  );
}

/** Five little level bars: the orchestra is playing. */
function Equalizer({ paused }: { paused: boolean }) {
  return (
    <span className="flex h-3 items-end gap-[2px]" aria-hidden>
      {[0.9, 1.3, 0.7, 1.1, 0.8].map((d, i) => (
        <span key={i} className="w-[2px] origin-bottom rounded-full bg-current"
          style={{ height: "100%", animation: paused ? "none" : `eq ${d}s ease-in-out ${i * 0.12}s infinite alternate`, transform: paused ? "scaleY(0.35)" : undefined }} />
      ))}
    </span>
  );
}

function TypeLine({ text }: { text: string }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    const t = window.setInterval(() => setN((v) => (v >= text.length ? (window.clearInterval(t), v) : v + 1)), 18);
    return () => window.clearInterval(t);
  }, [text]);
  return <span className={clsx(n < text.length && "caret")}>{text.slice(0, n)}</span>;
}

/** A smooth curve through the notes (Catmull-Rom as cubic Béziers), in the SVG's 0–100 space. */
function smooth(p: [number, number][]): string {
  let d = `M${p[0][0]},${p[0][1]}`;
  for (let i = 0; i < p.length - 1; i++) {
    const [x0, y0] = p[Math.max(0, i - 1)], [x1, y1] = p[i], [x2, y2] = p[i + 1], [x3, y3] = p[Math.min(p.length - 1, i + 2)];
    d += ` C${x1 + (x2 - x0) / 6},${y1 + (y2 - y0) / 6} ${x2 - (x3 - x1) / 6},${y2 - (y3 - y1) / 6} ${x2},${y2}`;
  }
  return d;
}
