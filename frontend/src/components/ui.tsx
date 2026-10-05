import clsx from "clsx";
import { animate, motion, useMotionValue, useTransform } from "framer-motion";
import { Loader2, X } from "lucide-react";
import { forwardRef, useEffect, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import type { AgentStatus, ProjectStatus } from "../lib/api";
import { Overlay } from "./Overlay";

/* ── Button ──────────────────────────────────────────────────────────────── */
type Variant = "primary" | "neu" | "ghost" | "danger";
interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onDrag" | "onDragStart" | "onDragEnd" | "onAnimationStart"> {
  variant?: Variant; loading?: boolean; icon?: ReactNode; size?: "sm" | "md" | "lg";
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "neu", loading, icon, size = "md", className, children, disabled, ...rest }, ref) {
  const styles: Record<Variant, string> = {
    primary: "text-on-primary bg-[linear-gradient(120deg,var(--primary),color-mix(in_srgb,var(--primary)_55%,var(--primary-2)))] shadow-[inset_0_1px_0_rgba(255,255,255,0.28),0_8px_24px_-8px_var(--primary)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.35),0_12px_34px_-6px_var(--primary)]",
    neu: "neu-sm text-text hover:text-primary",
    ghost: "text-muted hover:text-text hover:bg-surface-2",
    danger: "text-white bg-danger shadow-[0_8px_24px_-10px_var(--danger)]",
  };
  const sizes = { sm: "h-8 px-3 text-xs gap-1.5", md: "h-10 px-4 text-sm gap-2", lg: "h-12 px-6 text-base gap-2.5" };
  return (
    <motion.button ref={ref} whileHover={{ y: -1 }} whileTap={{ scale: 0.96, y: 0 }}
      transition={{ type: "spring", stiffness: 500, damping: 30 }}
      disabled={disabled || loading}
      className={clsx("focus-ring inline-flex select-none items-center justify-center rounded-[12px] font-medium transition-[color,box-shadow,background] duration-200 disabled:cursor-not-allowed disabled:opacity-50",
        styles[variant], sizes[size], className)}
      {...rest}>
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </motion.button>
  );
});

/* ── Input ───────────────────────────────────────────────────────────────── */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & {
  label?: string; hint?: ReactNode; leading?: ReactNode; trailing?: ReactNode;
}>(
  function Input({ label, hint, leading, trailing, className, id, ...rest }, ref) {
    const inputId = id || rest.name;
    return (
      <label className="block" htmlFor={inputId}>
        {label && <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>}
        <span className="neu-inset flex h-11 items-center gap-2 rounded-[12px] px-3.5 transition-shadow focus-within:shadow-[0_0_0_2px_var(--primary)]">
          {leading && <span className="text-muted">{leading}</span>}
          <input ref={ref} id={inputId} className={clsx("h-full w-full min-w-0 bg-transparent text-sm text-text outline-none placeholder:text-muted/70", className)} {...rest} />
          {trailing}
        </span>
        {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
      </label>
    );
  });

/* ── Modal ───────────────────────────────────────────────────────────────── */
export function Modal({ open, onClose, title, children, width = 480 }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; width?: number;
}) {
  return (
    <Overlay open={open} onClose={onClose} label={title} z={95}>
      <motion.div role="dialog" aria-modal aria-label={title}
        className="relative max-h-[92dvh] w-full overflow-y-auto rounded-t-[28px] border border-line bg-surface p-6 shadow-2xl sm:rounded-[28px]" style={{ maxWidth: width }}
        initial={{ y: 40, scale: 0.96, opacity: 0 }} animate={{ y: 0, scale: 1, opacity: 1 }}
        exit={{ y: 30, scale: 0.97, opacity: 0 }} transition={{ type: "spring", stiffness: 380, damping: 32 }}>
        <div className="mb-5 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="focus-ring rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </motion.div>
    </Overlay>
  );
}

/* ── Status pill ─────────────────────────────────────────────────────────── */
const STATUS: Record<string, { label: string; color: string; live?: boolean }> = {
  draft: { label: "Draft", color: "var(--text-muted)" },
  running: { label: "Running", color: "var(--primary-2)", live: true },
  waiting: { label: "Waiting for you", color: "var(--warning)", live: true },
  completed: { label: "Completed", color: "var(--success)" },
  failed: { label: "Failed", color: "var(--danger)" },
  paused: { label: "Paused", color: "var(--info)" },
  working: { label: "Working", color: "var(--primary-2)", live: true },
  needs_approval: { label: "Needs approval", color: "var(--warning)", live: true },
  done: { label: "Done", color: "var(--success)" },
  blocked: { label: "Blocked", color: "var(--danger)", live: true },
};
const AGENT_LABEL: Record<string, string> = {
  waiting: "Idle", working: "Working", needs_approval: "Needs you", done: "Done", failed: "Failed", blocked: "Blocked", paused: "Paused",
};
export function StatusPill({ status, agent }: { status: ProjectStatus | AgentStatus; agent?: boolean }) {
  const base = STATUS[status] ?? { label: status, color: "var(--text-muted)" };
  const s = agent ? { ...base, label: AGENT_LABEL[status] ?? base.label, color: status === "waiting" ? "var(--text-muted)" : base.color, live: status !== "waiting" && base.live } : base;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold"
      style={{ color: s.color, background: `color-mix(in srgb, ${s.color} 14%, transparent)`,
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${s.color} 30%, transparent)` }}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", s.live && "pulse-ring")}
        style={{ background: s.color, ["--ring" as string]: s.color }} />
      {s.label}
    </span>
  );
}

/* ── Progress ring ───────────────────────────────────────────────────────── */
export function ProgressRing({ value, size = 44, stroke = 4, color = "var(--primary)" }: {
  value: number; size?: number; stroke?: number; color?: string;
}) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border)" strokeWidth={stroke} />
        <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - Math.min(1, value)) }}
          transition={{ duration: 0.9, ease: "easeOut" }} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
      </svg>
      <span className="absolute inset-0 grid place-items-center text-[10px] font-semibold tabular-nums">
        {Math.round(value * 100)}%
      </span>
    </div>
  );
}

/* ── Animated number ─────────────────────────────────────────────────────── */
export function AnimatedNumber({ value, decimals = 0, prefix = "" }: { value: number; decimals?: number; prefix?: string }) {
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => `${prefix}${v.toFixed(decimals)}`);
  useEffect(() => {
    const controls = animate(mv, value, { duration: 1.1, ease: [0.16, 1, 0.3, 1] });
    return controls.stop;
  }, [mv, value]);
  return <motion.span className="tabular-nums">{text}</motion.span>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}
