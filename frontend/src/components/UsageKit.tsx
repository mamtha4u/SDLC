import { motion } from "framer-motion";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { CREW, ORION } from "../lib/crew";
import { cacheHit, tok, usd, type CallRow, type ModelInfo, type UsageRow } from "../lib/usage";
import { AgentAvatar } from "./AgentAvatar";
import { AnimatedNumber } from "./ui";

const AGENTS = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));

/* ── KPI tiles ─────────────────────────────────────────────────────────────── */
export function KpiRow({ t, extra }: { t: UsageRow; extra?: ReactNode }) {
  const tiles = [
    { label: "Spend", node: <AnimatedNumber value={t.cost_usd} decimals={t.cost_usd >= 1 ? 2 : 4} prefix="$" />, sub: "exact, from the call ledger" },
    { label: "Tokens", node: tok(t.total_tokens), sub: `${tok(t.input_tokens + t.cache_read_tokens + t.cache_write_tokens)} in · ${tok(t.output_tokens)} out` },
    { label: "Model calls", node: <AnimatedNumber value={t.calls} />, sub: t.calls ? `${usd(t.cost_usd / t.calls)} per call avg` : "none yet" },
    { label: "Cache hit", node: `${Math.round(cacheHit(t) * 100)}%`, sub: `${tok(t.cache_read_tokens)} tokens read from cache` },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((x, i) => (
        <motion.div key={x.label} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
          className="spotlight sheen elev relative overflow-hidden rounded-[20px] border border-line bg-surface p-4">
          <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full opacity-25 blur-2xl" style={{ background: ["var(--warning)", "var(--primary)", "var(--primary-2)", "var(--success)"][i] }} />
          <p className="text-xs font-medium text-muted">{x.label}</p>
          <p className="mt-1 font-display text-2xl font-bold tabular-nums sm:text-3xl">{x.node}</p>
          <p className="mt-0.5 truncate text-[11px] text-muted">{x.sub}</p>
        </motion.div>
      ))}
      {extra}
    </div>
  );
}

/* ── Horizontal bars (one measure, one hue; labels carry identity) ─────────── */
export function BarList<T extends UsageRow>({ title, rows, label, icon }: {
  title: string; rows: T[]; label: (r: T) => string; icon?: (r: T) => ReactNode;
}) {
  const max = Math.max(...rows.map((r) => r.cost_usd), 1e-9);
  return (
    <div className="spotlight sheen elev rounded-[20px] border border-line bg-surface/60 p-4">
      <h3 className="mb-3 font-display font-semibold">{title}</h3>
      {rows.length === 0 ? <p className="py-6 text-center text-sm text-muted">No calls yet.</p> : (
        <ul className="space-y-3">
          {rows.map((r, i) => (
            <li key={label(r)} className="group">
              <div className="mb-1 flex items-center gap-2 text-sm">
                {icon?.(r)}
                <span className="min-w-0 flex-1 truncate font-medium">{label(r)}</span>
                <span className="tabular-nums">{usd(r.cost_usd)}</span>
              </div>
              <div className="h-2 rounded-full bg-bg-2">
                <motion.div className="h-full rounded-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))] shadow-[0_0_10px_-2px_var(--primary)]" initial={{ width: 0 }}
                  animate={{ width: `${Math.max(1.5, (r.cost_usd / max) * 100)}%` }} transition={{ delay: 0.1 + i * 0.05, duration: 0.6, ease: "easeOut" }} />
              </div>
              <p className="mt-1 text-[11px] text-muted tabular-nums">
                {r.calls} call{r.calls === 1 ? "" : "s"} · {tok(r.input_tokens)} in · {tok(r.output_tokens)} out
                {r.cache_read_tokens ? ` · ${tok(r.cache_read_tokens)} cached` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const agentIcon = (key: string) => {
  const m = AGENTS[key];
  return m ? <AgentAvatar agent={m.key} accent={m.accent} status="done" size={22} /> : null;
};
export const agentName = (key: string) => (AGENTS[key] ? `${AGENTS[key].persona} · ${AGENTS[key].abbr}` : key);

/* ── Daily spend: single-series SVG bars with hover tooltip ───────────────── */
export function DailyBars({ daily, days }: { daily: (UsageRow & { day: string })[]; days: number }) {
  const [hover, setHover] = useState<number | null>(null);
  // the chart is drawn at its real pixel width, so labels stay 10px on any screen (a scaled viewBox made them huge)
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const byDay = Object.fromEntries(daily.map((d) => [d.day, d]));
  const series = Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.now() - (days - 1 - i) * 86400000).toISOString().slice(0, 10);
    return { day: d, row: byDay[d] };
  });
  const max = Math.max(...series.map((s) => s.row?.cost_usd ?? 0), 0.01);
  const W = width, H = 220, pad = { l: 52, r: 8, t: 12, b: 24 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const bw = iw / days;
  const ticks = [0, max / 2, max];
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  const h = hover !== null ? series[hover] : null;

  return (
    <div className="spotlight sheen elev rounded-[20px] border border-line bg-surface/60 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="font-display font-semibold">Spend per day</h3>
        <span className="text-xs text-muted">last {days} days · UTC</span>
      </div>
      <div className="relative" ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block w-full" role="img" aria-label={`Daily spend over the last ${days} days`}
          onMouseLeave={() => setHover(null)}>
          <defs>
            <linearGradient id="dailybar" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--primary-2)" />
              <stop offset="100%" stopColor="var(--primary)" />
            </linearGradient>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth="1" />
              <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--text-muted)">{usd(t)}</text>
            </g>
          ))}
          {series.map((s, i) => {
            const v = s.row?.cost_usd ?? 0;
            const x = pad.l + i * bw + 1;
            const w = Math.max(2, bw - 2); // 2px gap between bars
            const top = y(v);
            const barH = Math.max(v > 0 ? 2 : 0, pad.t + ih - top);
            return (
              <g key={s.day} onMouseEnter={() => setHover(i)}>
                <rect x={pad.l + i * bw} y={pad.t} width={bw} height={ih} fill="transparent" />{/* hit area */}
                {barH > 0 && (
                  <motion.path d={`M${x},${pad.t + ih} V${pad.t + ih - barH + Math.min(4, barH)} q0,-${Math.min(4, barH)} ${Math.min(4, w / 2)},-${Math.min(4, barH)} H${x + w - Math.min(4, w / 2)} q${Math.min(4, w / 2)},0 ${Math.min(4, w / 2)},${Math.min(4, barH)} V${pad.t + ih} Z`}
                    fill="url(#dailybar)" opacity={hover === null || hover === i ? 1 : 0.45}
                    initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ delay: 0.15 + i * 0.012, duration: 0.5, ease: [0.2, 0.7, 0.2, 1] }}
                    style={{ originY: 1 }} />
                )}
              </g>
            );
          })}
          {[0, Math.floor(days / 2), days - 1].map((i) => (
            <text key={i} x={pad.l + i * bw + bw / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--text-muted)">
              {series[i].day.slice(5)}
            </text>
          ))}
        </svg>
        {h && (
          <div className="pointer-events-none absolute top-0 rounded-[12px] border border-line bg-surface px-3 py-2 text-xs shadow-xl"
            style={{ left: `clamp(0px, calc(${((pad.l + (hover! + 0.5) * bw) / W) * 100}% - 70px), calc(100% - 150px))` }}>
            <p className="font-semibold">{h.day}</p>
            <p className="tabular-nums">{usd(h.row?.cost_usd ?? 0)} · {h.row?.calls ?? 0} calls</p>
            <p className="text-muted tabular-nums">{tok(h.row?.total_tokens ?? 0)} tokens</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Prices in effect ─────────────────────────────────────────────────────── */
export function PriceTable({ models }: { models: Record<string, ModelInfo> }) {
  return (
    <div className="spotlight sheen elev rounded-[20px] border border-line bg-surface/60 p-4">
      <h3 className="mb-1 font-display font-semibold">Prices used (USD per million tokens)</h3>
      <p className="mb-3 text-xs text-muted">From backend/config/agents.yaml. Each call stores the price it was charged at.</p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] text-sm">
          <thead><tr className="text-left text-xs text-muted">
            <th className="pb-2 font-medium">Model</th><th className="pb-2 text-right font-medium">Input</th>
            <th className="pb-2 text-right font-medium">Output</th><th className="pb-2 text-right font-medium">Cache read</th>
            <th className="pb-2 text-right font-medium">Cache write</th></tr></thead>
          <tbody>
            {Object.entries(models).map(([k, m]) => (
              <tr key={k} className="border-t border-line tabular-nums">
                <td className="py-2 font-medium">{m.label}</td>
                <td className="py-2 text-right">${m.price.input.toFixed(2)}</td><td className="py-2 text-right">${m.price.output.toFixed(2)}</td>
                <td className="py-2 text-right">${m.price.cache_read.toFixed(2)}</td><td className="py-2 text-right">${m.price.cache_write.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Call ledger ──────────────────────────────────────────────────────────── */
export function LedgerTable({ calls, models }: { calls: CallRow[]; models: Record<string, ModelInfo> }) {
  return (
    <div className="spotlight sheen elev rounded-[20px] border border-line bg-surface/60 p-4">
      <h3 className="mb-3 font-display font-semibold">Every model call</h3>
      {calls.length === 0 ? <p className="py-6 text-center text-sm text-muted">No calls yet.</p> : (
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full min-w-[720px] text-[13px]">
            <thead className="sticky top-0 bg-surface"><tr className="text-left text-xs text-muted">
              {["Time", "Agent", "Model", "Purpose", "In", "Cached", "Out", "Cost", "Time taken"].map((h, i) => (
                <th key={h} className={`pb-2 font-medium ${i >= 4 ? "text-right" : ""}`}>{h}</th>))}
            </tr></thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id} className="border-t border-line tabular-nums">
                  <td className="py-1.5 whitespace-nowrap text-muted">{new Date(c.at + (c.at.endsWith("Z") ? "" : "Z")).toLocaleString()}</td>
                  <td className="py-1.5 whitespace-nowrap"><span className="inline-flex items-center gap-1.5">{agentIcon(c.agent)}{agentName(c.agent)}</span></td>
                  <td className="py-1.5 whitespace-nowrap">{models[c.model_key]?.label ?? c.model_key}</td>
                  <td className="py-1.5 text-muted">{c.purpose}</td>
                  <td className="py-1.5 text-right">{tok(c.input_tokens + c.cache_write_tokens)}</td>
                  <td className="py-1.5 text-right">{tok(c.cache_read_tokens)}</td>
                  <td className="py-1.5 text-right">{tok(c.output_tokens)}</td>
                  <td className="py-1.5 text-right font-medium">{usd(c.cost_usd)}</td>
                  <td className="py-1.5 text-right text-muted">{(c.duration_ms / 1000).toFixed(1)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
