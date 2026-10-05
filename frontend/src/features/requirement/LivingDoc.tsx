import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Pencil, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CodeBlock } from "../../components/Markdown";
import { ProgressRing } from "../../components/ui";
import type { IntakeState, Question } from "../../lib/intake";
import { FlowPreview } from "./FlowPreview";

/** The requirement as a living document: fills itself from the conversation, every line editable in place. */
export function LivingDoc({ state, answers, setAnswer, locked }: {
  state: IntakeState; answers: Record<string, string>; setAnswer: (id: string, v: string) => void; locked: boolean;
}) {
  const c = state.completeness;
  const left = c.required_total - c.required_done;
  const has = (id: string) => !!(answers[id] ?? "").trim();

  // Briefly highlight answers that just arrived (from Echo or an upload).
  const prev = useRef(answers);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    const changed = Object.keys(answers).filter((k) => answers[k] && answers[k] !== prev.current[k]);
    prev.current = answers;
    if (changed.length) {
      setFresh(new Set(changed));
      const t = setTimeout(() => setFresh(new Set()), 2600);
      return () => clearTimeout(t);
    }
  }, [answers]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <ProgressRing value={c.total ? c.answered / c.total : 0} size={54} stroke={5} />
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold">Your requirement</h3>
          <p className="text-sm text-muted">
            {left > 0 ? <><b className="text-text">{left}</b> must-have{left === 1 ? "" : "s"} left · </> : <span className="text-success">All must-haves covered · </span>}
            {c.answered} of {c.total} topics filled. Click anything to edit.
          </p>
        </div>
      </div>

      <FlowPreview answers={answers} />

      {state.questionnaire.sections.map((s) => {
        const done = s.questions.filter((q) => has(q.id)).length;
        const status = done === s.questions.length ? "done" : done > 0 ? "partial" : "empty";
        return (
          <section key={s.id} className="rounded-[18px] border border-line bg-surface p-4">
            <div className="mb-2 flex items-center gap-2">
              <span className={clsx("h-2.5 w-2.5 rounded-full", status === "done" ? "bg-success" : status === "partial" ? "bg-warning" : "bg-line")} />
              <h4 className="flex-1 font-display font-semibold">{s.title}</h4>
              <span className="text-[11px] text-muted">{done}/{s.questions.length}</span>
            </div>
            <dl className="divide-y divide-line">
              {s.questions.map((q) => (
                <Item key={q.id} q={q} value={answers[q.id] ?? ""} fresh={fresh.has(q.id)} locked={locked}
                  suggest={state.suggest_token} onSave={(v) => setAnswer(q.id, v)} />
              ))}
            </dl>
          </section>
        );
      })}
    </div>
  );
}

function Item({ q, value, fresh, locked, suggest, onSave }: {
  q: Question; value: string; fresh: boolean; locked: boolean; suggest: string; onSave: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => { if (!editing) setDraft(value); }, [value, editing]);
  const isSuggest = value === suggest;
  const isCode = q.type === "code" && value && !isSuggest;
  const save = () => { onSave(draft.trim()); setEditing(false); };

  return (
    <div className={clsx("relative -mx-2 rounded-[10px] px-2 py-2.5", fresh && "fresh-flash")}>
      <dt className="flex items-center gap-1.5 text-[12px] text-muted">
        {q.label}{q.required && !value && <span className="text-warning">· must-have</span>}
        {fresh && <span className="inline-flex items-center gap-1 rounded-full bg-primary-2/15 px-1.5 text-[10px] font-semibold text-primary-2"><Sparkles className="h-3 w-3" />just added</span>}
      </dt>
      <AnimatePresence mode="wait" initial={false}>
        {editing ? (
          <motion.dd key="edit" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-1.5">
            {q.type === "select" ? (
              <div className="flex flex-wrap gap-1.5">
                {q.options!.map((o) => (
                  <button key={o} onClick={() => { onSave(o); setEditing(false); }}
                    className={clsx("rounded-full border px-3 py-1 text-[13px]", value === o ? "border-primary bg-primary text-on-primary" : "border-line hover:border-primary")}>{o}</button>
                ))}
                <button onClick={() => setEditing(false)} className="px-2 text-xs text-muted">Cancel</button>
              </div>
            ) : (
              <div className="space-y-2">
                <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} rows={q.type === "code" ? 6 : q.type === "longtext" ? 3 : 1}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save(); if (e.key === "Escape") setEditing(false); }}
                  placeholder={q.example ? `e.g. ${q.example}` : ""}
                  className={clsx("w-full resize-y rounded-[10px] border border-line bg-bg-2 px-3 py-2 text-sm outline-none focus:border-primary", q.type === "code" && "font-mono text-[12.5px]")} />
                <div className="flex gap-2">
                  <button onClick={save} className="inline-flex items-center gap-1 rounded-[8px] bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary"><Check className="h-3.5 w-3.5" />Save</button>
                  <button onClick={() => setEditing(false)} className="inline-flex items-center gap-1 rounded-[8px] px-3 py-1.5 text-xs text-muted hover:text-text"><X className="h-3.5 w-3.5" />Cancel</button>
                  <span className="self-center text-[11px] text-muted">Ctrl+Enter to save</span>
                </div>
              </div>
            )}
          </motion.dd>
        ) : (
          <motion.dd key="view" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            onClick={() => !locked && !isCode && setEditing(true)}
            className={clsx("group mt-0.5 text-[14px]", !locked && !isCode && "cursor-text")}>
            {isCode ? (
              <div className="relative">
                <CodeBlock raw={value} />
                {!locked && <button onClick={() => setEditing(true)} className="absolute right-2 top-9 rounded-md bg-surface px-2 py-0.5 text-[11px] text-muted shadow hover:text-text">Edit</button>}
              </div>
            ) : value ? (
              <span className="flex items-start gap-2">
                <span className="min-w-0 flex-1 whitespace-pre-wrap">{isSuggest ? <i className="text-primary">Echo will suggest a value</i> : value}</span>
                {!locked && <Pencil className="mt-1 h-3.5 w-3.5 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />}
              </span>
            ) : (
              <span className="text-muted/70 italic group-hover:text-muted">{locked ? "—" : "Not yet · click to add"}</span>
            )}
          </motion.dd>
        )}
      </AnimatePresence>
    </div>
  );
}
