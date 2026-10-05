import { motion } from "framer-motion";
import { useId } from "react";

/** A soft area sparkline that draws itself in. One series, theme colours, no axes: a trend, not a chart. */
export function Sparkline({ values, color = "var(--primary)", height = 56, className }: {
  values: number[]; color?: string; height?: number; className?: string;
}) {
  const id = useId().replace(/:/g, "");
  const w = 300;
  if (values.length < 2) return <div style={{ height }} className={className} />;
  const max = Math.max(...values, 0.0001);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, height - 4 - (v / max) * (height - 10)] as const);
  // smooth with a simple Catmull-Rom → Bézier pass
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[Math.max(0, i - 1)], [x1, y1] = pts[i], [x2, y2] = pts[i + 1], [x3, y3] = pts[Math.min(pts.length - 1, i + 2)];
    d += ` C${x1 + (x2 - x0) / 6},${y1 + (y2 - y0) / 6} ${x2 - (x3 - x1) / 6},${y2 - (y3 - y1) / 6} ${x2},${y2}`;
  }
  const last = pts[pts.length - 1];
  return (
    <div className={`relative ${className ?? ""}`} style={{ height }}>
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ height, width: "100%", overflow: "visible" }} aria-hidden>
      <defs>
        <linearGradient id={`sg${id}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <motion.path d={`${d} L${w},${height} L0,${height} Z`} fill={`url(#sg${id})`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4, duration: 0.8 }} />
      <motion.path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" vectorEffect="non-scaling-stroke"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.2, ease: [0.2, 0.7, 0.2, 1] }} />
    </svg>
    {/* the latest point, as a real circle (an SVG one would stretch with the chart) */}
    <motion.span className="pulse-ring absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full" initial={{ scale: 0 }} animate={{ scale: 1 }}
      transition={{ delay: 1.1 }} style={{ left: `${(last[0] / w) * 100}%`, top: last[1], background: color, ["--ring" as string]: color }} />
    </div>
  );
}
