import { motion } from "framer-motion";

/** The baton arc through the orbiting crew — the Orkestra mark. */
export function LogoMark({ size = 36, animated = true }: { size?: number; animated?: boolean }) {
  const dots = [
    { cx: 12, cy: 44, c: "var(--primary-2)" }, { cx: 22, cy: 26, c: "var(--primary)" }, { cx: 32, cy: 20, c: "var(--success)" },
    { cx: 42, cy: 26, c: "var(--warning)" }, { cx: 52, cy: 44, c: "var(--danger)" },
  ];
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <defs>
        <linearGradient id="ork-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--primary)" /><stop offset="1" stopColor="var(--primary-2)" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="18" fill="var(--surface-2)" />
      <motion.path d="M10 46 C 20 10, 44 10, 54 46" fill="none" stroke="url(#ork-g)" strokeWidth="4.5" strokeLinecap="round"
        initial={animated ? { pathLength: 0 } : false} animate={{ pathLength: 1 }} transition={{ duration: 1.2, ease: "easeInOut" }} />
      {dots.map((d, i) => (
        <motion.circle key={i} cx={d.cx} cy={d.cy} r="3.4" fill={d.c}
          initial={animated ? { scale: 0, opacity: 0 } : false} animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.6 + i * 0.12, type: "spring", stiffness: 400, damping: 14 }} />
      ))}
    </svg>
  );
}

export function Wordmark({ size = 36 }: { size?: number }) {
  return (
    <div className="flex items-center gap-3">
      <LogoMark size={size} />
      <span className="font-display text-xl font-bold tracking-tight">
        <span className="text-gradient">Ork</span>estra
      </span>
    </div>
  );
}
