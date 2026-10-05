import { AnimatePresence, motion } from "framer-motion";
import { ArrowDownToLine, ChevronLeft, ChevronRight, FileText, ShieldCheck, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { CREW, ORION, type CrewMember } from "../lib/crew";
import { ACCENT } from "../lib/themes";
import { AgentAvatar } from "./AgentAvatar";

export interface DetailOrigin { x: number; y: number }

const member = (i: number) => (i === -1 ? ORION : CREW[i]);

/** Focused explainer for one crew member. Flies out of the avatar that was clicked; the page behind is
 *  blurred and all background motion is paused (cheap blur, full focus). ←/→ walk the hand-off chain. */
export function AgentDetail({ index, origin, onClose, onNavigate }: {
  index: number | null; origin: DetailOrigin; onClose: () => void; onNavigate: (i: number) => void;
}) {
  const [dir, setDir] = useState(1);
  const open = index !== null;
  const go = (i: number) => { setDir(i > (index ?? 0) ? 1 : -1); onNavigate(i); };

  useEffect(() => {
    document.body.classList.toggle("modal-open", open);
    return () => document.body.classList.remove("modal-open");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && index! < CREW.length - 1) go(index! + 1);
      if (e.key === "ArrowLeft" && index! > -1) go(index! - 1);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

  const dx = origin.x - window.innerWidth / 2;
  const dy = origin.y - window.innerHeight / 2;
  const m = index === null ? null : member(index);
  const accent = m ? ACCENT[m.accent] ?? "var(--primary)" : "var(--primary)";

  return (
    <AnimatePresence>
      {open && m && (
        <motion.div className="modal-layer fixed inset-0 z-[80] flex items-end justify-center p-2 sm:items-center sm:p-4"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/40 backdrop-blur-md" onClick={onClose} />
          <motion.div role="dialog" aria-modal aria-label={`${m.persona} details`}
            className="relative flex max-h-[96dvh] w-full max-w-[680px] flex-col overflow-hidden rounded-[28px] border border-line bg-surface"
            style={{ boxShadow: `0 0 0 1px color-mix(in srgb, ${accent} 35%, transparent), 0 30px 90px -25px color-mix(in srgb, ${accent} 55%, black)` }}
            initial={{ x: dx, y: dy, scale: 0.15, opacity: 0 }} animate={{ x: 0, y: 0, scale: 1, opacity: 1 }}
            exit={{ x: dx, y: dy, scale: 0.15, opacity: 0 }} transition={{ type: "spring", stiffness: 260, damping: 28 }}>
            <button onClick={onClose} aria-label="Close"
              className="focus-ring absolute right-3 top-3 z-10 grid h-9 w-9 place-items-center rounded-full bg-black/20 text-text/80 hover:bg-black/35 hover:text-text">
              <X className="h-4 w-4" />
            </button>

            <div className="no-scrollbar min-h-0 overflow-y-auto">
              <AnimatePresence mode="wait" initial={false}>
                <motion.div key={index} initial={{ opacity: 0, x: dir * 36 }} animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: dir * -36 }} transition={{ duration: 0.2 }}>
                  <Hero m={m} accent={accent} index={index!} />
                  <Body m={m} accent={accent} />
                </motion.div>
              </AnimatePresence>
            </div>

            <Footer index={index!} go={go} />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Hero({ m, accent, index }: { m: CrewMember; accent: string; index: number }) {
  const prev = index > 0 ? CREW[index - 1] : null;
  const next = index >= 0 && index < CREW.length - 1 ? CREW[index + 1] : null;
  return (
    <div className="relative overflow-hidden px-6 pb-5 pt-6 sm:px-7"
      style={{ background: `linear-gradient(160deg, color-mix(in srgb, ${accent} 26%, var(--surface)) 0%, var(--surface) 70%)` }}>
      <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full opacity-40"
        style={{ background: `radial-gradient(circle, ${accent}, transparent 65%)` }} />
      <div className="relative flex items-center gap-4 pr-10">
        <AgentAvatar agent={m.key} accent={m.accent} status="working" size={68} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-display text-[28px] font-bold leading-none">{m.persona}</h3>
            <span className="rounded-md px-2 py-0.5 font-mono text-xs font-bold tracking-wider"
              style={{ color: accent, background: `color-mix(in srgb, ${accent} 18%, transparent)` }}>{m.abbr}</span>
          </div>
          <p className="mt-1.5 text-[15px] text-text/90">{m.role}</p>
          <span className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-black/20 px-2.5 py-1 font-mono text-[11px] text-text/80">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: accent }} />{m.model}
          </span>
        </div>
      </div>

      {index >= 0 && (
        <div className="relative mt-5 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
          <ChainPill label={prev ? prev.persona : "You"} sub={prev ? prev.abbr : "input"} m={prev} />
          <Flow accent={accent} />
          <ChainPill label={m.persona} sub={m.abbr} m={m} active accent={accent} />
          <Flow accent={accent} />
          <ChainPill label={next ? next.persona : "Done"} sub={next ? next.abbr : "delivered"} m={next} />
        </div>
      )}
    </div>
  );
}

function ChainPill({ label, sub, m, active, accent }: {
  label: string; sub: string; m: CrewMember | null; active?: boolean; accent?: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-[14px] px-2.5 py-2"
      style={active ? { background: `color-mix(in srgb, ${accent} 16%, var(--surface))`, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${accent} 45%, transparent)` }
        : { background: "color-mix(in srgb, var(--bg-2) 70%, transparent)" }}>
      {m ? <AgentAvatar agent={m.key} accent={m.accent} status={active ? "working" : "done"} size={28} />
        : <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-2 text-[9px] font-bold">{label === "You" ? "YOU" : "✓"}</span>}
      <div className="min-w-0 leading-tight">
        <p className={active ? "truncate text-[13px] font-semibold" : "truncate text-[13px] text-text/85"}>{label}</p>
        <p className="truncate font-mono text-[10px] text-muted">{sub}</p>
      </div>
    </div>
  );
}

function Flow({ accent }: { accent: string }) {
  return (
    <svg width="26" height="10" viewBox="0 0 26 10" className="shrink-0" aria-hidden>
      <line x1="0" y1="5" x2="20" y2="5" stroke={accent} strokeWidth="2" strokeDasharray="3 4" style={{ animation: "dash-flow 1s linear infinite" }} />
      <path d="M19 1.5 L25 5 L19 8.5" fill="none" stroke={accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Body({ m, accent }: { m: CrewMember; accent: string }) {
  return (
    <div className="grid gap-4 px-6 pb-5 pt-4 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] sm:px-7">
      <section>
        <Label>What {m.persona} does</Label>
        <ol className="mt-2 space-y-2.5">
          {m.does.map((d, i) => (
            <motion.li key={d} className="flex gap-3 text-[14px] leading-snug"
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.07 }}>
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full font-mono text-[11px] font-bold"
                style={{ color: accent, background: `color-mix(in srgb, ${accent} 16%, transparent)` }}>{i + 1}</span>
              <span className="pt-0.5">{d}</span>
            </motion.li>
          ))}
        </ol>
      </section>

      <section className="space-y-3">
        <Card icon={<ArrowDownToLine className="h-4 w-4" />} title="Receives">
          <p className="text-[13px] font-semibold">{m.receives.from}</p>
          <p className="text-[13px] text-muted">{m.receives.what}</p>
        </Card>
        <Card icon={<FileText className="h-4 w-4" />} title="Produces">
          <div className="flex flex-wrap gap-1.5">
            {m.produces.map((f) => <span key={f} className="rounded-lg bg-bg-2 px-2 py-1 font-mono text-[12px]">{f}</span>)}
          </div>
        </Card>
        {m.gate && (
          <div className="flex gap-2.5 rounded-[14px] border border-warning/35 bg-warning/10 p-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div className="text-[13px] leading-snug">
              <p className="font-semibold text-warning">Your approval</p>
              <p>{m.gate}</p>
            </div>
          </div>
        )}
      </section>

      <p className="text-[13px] text-muted sm:col-span-2">
        Hands over to <span className="font-semibold text-text">{m.handsTo}</span>
      </p>
    </div>
  );
}

function Label({ children }: { children: ReactNode }) {
  return <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{children}</p>;
}

function Card({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="rounded-[14px] bg-bg-2/70 p-3">
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{icon}{title}</p>
      {children}
    </div>
  );
}

function Footer({ index, go }: { index: number; go: (i: number) => void }) {
  const all = [-1, ...CREW.map((_, i) => i)];
  const prev = index > -1 ? member(index - 1) : null;
  const next = index < CREW.length - 1 ? member(index + 1) : null;
  const btn = "focus-ring group inline-flex min-w-0 items-center gap-1.5 rounded-xl px-3 py-2 text-sm hover:bg-surface-2 disabled:invisible";
  return (
    <div className="flex items-center justify-between gap-2 border-t border-line bg-surface px-3 py-2.5">
      <button disabled={!prev} onClick={() => go(index - 1)} className={btn}>
        <ChevronLeft className="h-4 w-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
        {prev && <span className="truncate"><span className="text-muted">Prev · </span>{prev.persona} <span className="font-mono text-[11px] text-muted">{prev.abbr}</span></span>}
      </button>
      <div className="hidden items-center gap-1.5 sm:flex">
        {all.map((i) => (
          <button key={i} onClick={() => go(i)} aria-label={member(i).persona}
            className="h-2 rounded-full transition-all duration-300"
            style={{ width: i === index ? 22 : 8, background: i === index ? "var(--primary)" : "var(--border)" }} />
        ))}
      </div>
      <button disabled={!next} onClick={() => go(index + 1)} className={btn}>
        {next && <span className="truncate"><span className="text-muted">Next · </span>{next.persona} <span className="font-mono text-[11px] text-muted">{next.abbr}</span></span>}
        <ChevronRight className="h-4 w-4 shrink-0 transition-transform group-hover:translate-x-0.5" />
      </button>
    </div>
  );
}
