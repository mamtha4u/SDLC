import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle, Check, ChevronDown, CircleHelp, Cloud, Lightbulb, Scale, ThumbsDown, ThumbsUp, Wand2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Markdown } from "../../components/Markdown";
import { ProgressRing } from "../../components/ui";
import type { IntakeState, Round } from "../../lib/intake";

const IMPACT: Record<string, string> = { high: "var(--danger)", medium: "var(--warning)", low: "var(--info)" };

/** Echo's review of one round: playback, questions to answer inline, suggestions to accept or reject. */
export function ReviewPanel({ state, onDecide, locked }: {
  state: IntakeState;
  onDecide: (decisions: Record<string, string>, gapAnswers: Record<string, string>) => void;
  locked: boolean;
}) {
  const rounds = state.rounds;
  const [sel, setSel] = useState(rounds.length - 1);
  useEffect(() => setSel(rounds.length - 1), [rounds.length]);
  const r: Round | undefined = rounds[sel];
  if (!r) return null;
  const latest = sel === rounds.length - 1;

  return (
    <div className="space-y-4">
      {/* round switcher */}
      <div className="flex flex-wrap items-center gap-2">
        {rounds.map((x, i) => (
          <button key={x.round} onClick={() => setSel(i)}
            className={clsx("focus-ring rounded-full px-3 py-1.5 text-xs font-semibold transition-colors",
              i === sel ? "bg-primary text-on-primary" : "neu-sm text-muted hover:text-text")}>
            Round {x.round}{x.mode === "reflect" ? " · reflect" : ""}
          </button>
        ))}
      </div>

      <motion.div key={r.round} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
        className="flex items-center gap-4 rounded-[20px] border border-line bg-surface/60 p-4">
        <ProgressRing value={r.completeness / 100} size={56} stroke={5}
          color={r.completeness >= 80 ? "var(--success)" : r.completeness >= 50 ? "var(--warning)" : "var(--danger)"} />
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Echo's verdict · round {r.round}</p>
          <p className="mt-0.5 font-medium leading-snug">{r.headline}</p>
          {r.ready_for_signoff && <p className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-success"><Check className="h-3.5 w-3.5" />Echo thinks this is ready to sign off</p>}
        </div>
      </motion.div>

      <Collapsible title="What Echo understood" icon={<Scale className="h-4 w-4" />} defaultOpen>
        <Markdown>{r.understanding}</Markdown>
        {r.services.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
            {r.services.map((s) => (
              <span key={s.service} title={s.purpose} className="inline-flex items-center gap-1.5 rounded-full bg-primary-2/10 px-2.5 py-1 text-xs">
                <Cloud className="h-3.5 w-3.5 text-primary-2" />{s.service}
              </span>
            ))}
          </div>
        )}
      </Collapsible>

      {r.gaps.length > 0 && (
        <Section icon={<CircleHelp className="h-4 w-4" />} title={`Echo's questions (${r.gaps.length})`}
          hint={latest ? "Answer here. Echo uses your answers in the next round." : undefined}>
          {[...r.gaps].sort((a, b) => Number(!!state.gap_answers[a.id]) - Number(!!state.gap_answers[b.id]) || Number(b.blocking) - Number(a.blocking)).map((g, i) => (
            <GapCard key={g.id} i={i} gap={g} answer={state.gap_answers[g.id] ?? ""} editable={latest && !locked}
              onSave={(v) => onDecide({}, { [g.id]: v })} />
          ))}
        </Section>
      )}

      {r.suggestions.length > 0 && (
        <Section icon={<Lightbulb className="h-4 w-4" />} title={`Echo's suggestions (${r.suggestions.length})`}
          hint={latest ? "Accept what you want included. Rejected ones won't come back." : undefined}>
          {r.suggestions.map((s, i) => {
            const d = state.decisions[s.id];
            return (
              <motion.div key={s.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
                className={clsx("rounded-[16px] border p-3.5 transition-colors",
                  d === "accepted" ? "border-success/40 bg-success/[0.06]" : d === "rejected" ? "border-line opacity-55" : "border-line bg-surface/60")}>
                <div className="flex items-start gap-3">
                  <span className="mt-1 h-2 w-2 shrink-0 rounded-full" style={{ background: IMPACT[s.impact] }} title={`${s.impact} impact`} />
                  <div className="min-w-0 flex-1">
                    <p className="font-medium leading-snug">{s.title}</p>
                    <p className="mt-0.5 text-sm text-muted">{s.detail}</p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px]">
                      <span className="rounded bg-bg-2 px-1.5 py-0.5 capitalize">{s.category}</span>
                      <span className="rounded bg-bg-2 px-1.5 py-0.5">{s.impact} impact</span>
                      {s.proposed_value && <span className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-primary"><Wand2 className="h-3 w-3" />fills an answer</span>}
                    </div>
                  </div>
                  {latest && !locked ? (
                    <div className="flex shrink-0 gap-1">
                      <IconToggle on={d === "accepted"} color="var(--success)" label="Accept" onClick={() => onDecide({ [s.id]: "accepted" }, {})}><ThumbsUp className="h-4 w-4" /></IconToggle>
                      <IconToggle on={d === "rejected"} color="var(--danger)" label="Reject" onClick={() => onDecide({ [s.id]: "rejected" }, {})}><ThumbsDown className="h-4 w-4" /></IconToggle>
                    </div>
                  ) : d && <span className={clsx("text-xs font-semibold", d === "accepted" ? "text-success" : "text-muted")}>{d}</span>}
                </div>
              </motion.div>
            );
          })}
        </Section>
      )}

      {(r.assumptions.length > 0 || r.conflicts.length > 0) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {r.assumptions.length > 0 && (
            <Collapsible title={`Assumptions (${r.assumptions.length})`} icon={<Scale className="h-4 w-4" />}>
              <ul className="list-disc space-y-1 pl-4 text-sm">{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            </Collapsible>
          )}
          {r.conflicts.length > 0 && (
            <Collapsible title={`Conflicts (${r.conflicts.length})`} icon={<AlertTriangle className="h-4 w-4 text-warning" />} defaultOpen>
              <ul className="list-disc space-y-1 pl-4 text-sm">{r.conflicts.map((a) => <li key={a}>{a}</li>)}</ul>
            </Collapsible>
          )}
        </div>
      )}
    </div>
  );
}

function GapCard({ gap, i, answer, editable, onSave }: {
  gap: IntakeState["rounds"][number]["gaps"][number]; i: number; answer: string; editable: boolean; onSave: (v: string) => void;
}) {
  const [v, setV] = useState(answer);
  useEffect(() => setV(answer), [answer]);
  const dirty = v.trim() !== answer;
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
      className={clsx("rounded-[16px] border p-3.5", answer ? "border-success/35 bg-success/[0.05]"
        : gap.blocking ? "border-warning/45 bg-warning/[0.05]" : "border-line bg-surface/60")}>
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 font-medium leading-snug">{gap.question}</p>
        {gap.blocking && !answer && <span className="shrink-0 rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-bold uppercase text-warning">blocking</span>}
        {answer && <Check className="h-4 w-4 shrink-0 text-success" />}
      </div>
      <p className="mt-0.5 text-xs text-muted">{gap.why} · <span className="italic">{gap.section}</span></p>
      {editable ? (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <textarea value={v} onChange={(e) => setV(e.target.value)} rows={1} placeholder="Your answer…"
            className="neu-inset focus-ring min-h-[40px] flex-1 resize-y rounded-[10px] px-3 py-2 text-sm outline-none" />
          <div className="flex gap-2">
            {gap.suggested_answer && !v && (
              <button onClick={() => setV(gap.suggested_answer)} className="neu-sm rounded-[10px] px-3 text-xs hover:text-primary" title={gap.suggested_answer}>
                Use Echo's suggestion
              </button>
            )}
            <button disabled={!dirty || !v.trim()} onClick={() => onSave(v.trim())}
              className="rounded-[10px] bg-primary px-4 py-2 text-xs font-semibold text-on-primary disabled:opacity-40">Save</button>
          </div>
        </div>
      ) : answer && <p className="mt-2 rounded-[10px] bg-bg-2 px-3 py-2 text-sm">{answer}</p>}
    </motion.div>
  );
}

function IconToggle({ on, color, label, onClick, children }: { on: boolean; color: string; label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <motion.button whileTap={{ scale: 0.88 }} onClick={onClick} aria-label={label} aria-pressed={on} title={label}
      className="grid h-9 w-9 place-items-center rounded-[10px] transition-colors"
      style={on ? { background: color, color: "#fff" } : { background: "var(--bg-2)", color: "var(--text-muted)" }}>
      {children}
    </motion.button>
  );
}

function Section({ icon, title, hint, children }: { icon: React.ReactNode; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="flex items-center gap-1.5 font-display font-semibold">{icon}{title}</h4>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Collapsible({ title, icon, defaultOpen, children }: { title: string; icon: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className="rounded-[18px] border border-line bg-surface/60">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-3 text-left font-display font-semibold">
        {icon}<span className="flex-1">{title}</span>
        <ChevronDown className={clsx("h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="px-4 pb-4">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
