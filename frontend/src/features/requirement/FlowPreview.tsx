import clsx from "clsx";
import { motion } from "framer-motion";
import { ArrowDownToLine, Cog, Send } from "lucide-react";

/** Live sketch of the flow, drawn from the answers: Source → your flow → Target, with data moving along it. */
export function FlowPreview({ answers }: { answers: Record<string, string> }) {
  const clean = (v?: string) => (v && v !== "__suggest__" ? v : "");
  // flow.* since 10-05 (Echo asks the business only); systems.* in older requirements
  const src = clean(answers["flow.source"] ?? answers["systems.source"]);
  const tgt = clean(answers["flow.destination"] ?? answers["systems.target"]);
  const inbound = clean(answers["flow.trigger"] ?? answers["systems.inbound"]);
  const outbound = clean(answers["systems.outbound"]);
  const name = clean(answers["overview.name"]) || "Your flow";
  const live = !!(src || tgt);

  return (
    <div className="relative overflow-hidden rounded-[18px] border border-line bg-bg-2/70 p-4">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">The flow so far</p>
      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
        <Node icon={<ArrowDownToLine className="h-4 w-4" />} label="From" value={src} hint="Where does it come from? (if anywhere)" />
        <Link label={inbound} live={live} />
        <Node icon={<Cog className="h-4 w-4" />} label="Orkestra builds" value={name} hint="" center />
        <Link label={outbound} live={live} />
        <Node icon={<Send className="h-4 w-4" />} label="To" value={tgt} hint="Where should it end up? (if anywhere)" />
      </div>
    </div>
  );
}

function Node({ icon, label, value, hint, center }: { icon: React.ReactNode; label: string; value: string; hint: string; center?: boolean }) {
  const empty = !value;
  return (
    <motion.div layout className={clsx("min-w-0 flex-1 rounded-[14px] px-3 py-2.5",
      empty ? "border border-dashed border-line" : center ? "bg-primary text-on-primary shadow-[0_8px_24px_-10px_var(--primary)]" : "border border-line bg-surface")}>
      <p className={clsx("flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider", center && !empty ? "text-on-primary/80" : "text-muted")}>
        {icon}{label}
      </p>
      <p className={clsx("mt-0.5 line-clamp-2 text-[13px] font-semibold leading-snug", empty && "font-normal italic text-muted")}>
        {value || hint}
      </p>
    </motion.div>
  );
}

function Link({ label, live }: { label: string; live: boolean }) {
  return (
    <div className="flex shrink-0 flex-col items-center justify-center px-1 sm:w-24">
      <svg viewBox="0 0 96 12" className="hidden h-3 w-24 sm:block" aria-hidden>
        <line x1="2" y1="6" x2="88" y2="6" stroke="var(--border)" strokeWidth="2" strokeDasharray="4 4" />
        <path d="M86 2 L93 6 L86 10" fill="none" stroke="var(--text-muted)" strokeWidth="2" strokeLinecap="round" />
        {live && [0, 0.6, 1.2].map((d) => (
          <circle key={d} r="3" fill="var(--primary-2)">
            <animate attributeName="cx" from="4" to="86" dur="1.8s" begin={`${d}s`} repeatCount="indefinite" />
            <animate attributeName="cy" values="6;6" dur="1.8s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0;1;1;0" dur="1.8s" begin={`${d}s`} repeatCount="indefinite" />
          </circle>
        ))}
      </svg>
      <span className="text-muted sm:hidden">↓</span>
      <span className="mt-0.5 max-w-[6rem] truncate text-center text-[10px] text-muted" title={label}>{label || " "}</span>
    </div>
  );
}
