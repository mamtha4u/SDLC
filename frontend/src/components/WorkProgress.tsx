import { motion } from "framer-motion";
import { useEffect, useState } from "react";

/** The same estimate as the backend (services/progress.py): steady to 90% at the usual time for this kind of job, then
 *  creeping towards 98%; never 100% before the job really ends. */
export function estimate(startedAt: string | null | undefined, expectedS: number | null | undefined, now: number): number | null {
  if (!startedAt || !expectedS) return null;
  const iso = startedAt.endsWith("Z") || /[+-]\d\d:\d\d$/.test(startedAt) ? startedAt : startedAt + "Z";
  const t = Math.max(0, (now - new Date(iso).getTime()) / 1000) / Math.max(expectedS, 5);
  const p = t <= 1 ? 90 * t : 90 + 8 * (1 - Math.exp(-(t - 1) * 1.5));
  return Math.max(1, Math.min(98, Math.floor(p)));
}

const usual = (s: number) => (s < 90 ? `${Math.round(s / 5) * 5 || 5} s` : `${Math.round(s / 60)} min`);

/** "≈ 42% · usually about 3 min" with a bar that fills as the agent works (user, 10-04: "show the progress in percentage
 *  so the user has hope that the agent is really working in the background"). */
export function WorkProgress({ startedAt, expectedS, tone = "var(--primary-2)", className }: {
  startedAt: string | null | undefined; expectedS: number | null | undefined; tone?: string; className?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const pct = estimate(startedAt, expectedS, now);
  if (pct === null || !expectedS) return null;
  const late = pct >= 90;
  return (
    <div className={className} title="An estimate from how long this kind of step usually takes on this host">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="font-semibold tabular-nums" style={{ color: tone }}>≈ {pct}%</span>
        <span className="text-muted">{late ? "taking a little longer than usual" : `usually about ${usual(expectedS)}`}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-2">
        <motion.div className="relative h-full rounded-full" style={{ background: `linear-gradient(90deg, ${tone}, var(--primary))` }}
          initial={false} animate={{ width: `${pct}%` }} transition={{ ease: "easeOut", duration: 0.8 }}>
          <span className="absolute inset-0 animate-pulse rounded-full bg-white/20" />
        </motion.div>
      </div>
    </div>
  );
}
