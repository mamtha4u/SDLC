import clsx from "clsx";
import { motion } from "framer-motion";
import { CheckCircle2, Code2, Lightbulb, MessageCircleQuestion, Send, Sparkles, Wand2 } from "lucide-react";
import { useMemo, useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Button } from "../../components/ui";
import type { CodeReview } from "../../lib/flow";

/** Archie walks you through Dev's code before it's deployed (user, 10-05: "after the review TA should show the code in a
 *  proper way (redirect to the Code area, like VS Code) and ask the user: is the code fine, function-based or class-based…
 *  and give suggestions, e.g. for a Lambda, globals at the top so the cold start loads them once"). Your picks default to
 *  what the code does now: nothing changes unless you choose it. */
export function ArchieAsks({ r, gate, busy, onOpenFile, onApprove, onSend }: {
  r: CodeReview; gate: boolean; busy: boolean; onOpenFile: (path: string) => void;
  onApprove: (origin?: Element | null) => void; onSend: (text: string) => void;
}) {
  const choices = r.choices ?? [];
  const suggestions = r.suggestions ?? [];
  const [picked, setPicked] = useState<Record<number, string>>(() => Object.fromEntries(choices.map((c, i) => [i, c.current])));
  const [apply, setApply] = useState<Record<number, boolean>>({});
  const [note, setNote] = useState("");
  const main = useMemo(() => r.files.find((f) => /handler\.py$/.test(f)) ?? r.files.find((f) => f.startsWith("src/")) ?? r.files[0], [r.files]);

  const changes = [
    ...choices.flatMap((c, i) => (picked[i] && picked[i] !== c.current ? [`${c.question} → ${picked[i]} (now: ${c.current})`] : [])),
    ...suggestions.flatMap((s, i) => (apply[i] ? [`Apply: ${s.title}. ${s.detail}${s.example ? `\nExample:\n${s.example}` : ""}`] : [])),
    ...(note.trim() ? [note.trim()] : []),
  ];
  const takeAll = () => {
    setPicked(Object.fromEntries(choices.map((c, i) => [i, c.recommended])));
    setApply(Object.fromEntries(suggestions.map((_, i) => [i, true])));
  };
  const text = "Your choices after Archie's walkthrough (change only these, keep everything else as it is):\n" + changes.map((c) => `- ${c}`).join("\n");

  return (
    <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className="relative overflow-hidden rounded-[22px] border border-warning/40 bg-surface p-4 sm:p-5">
      <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--warning) 14%, transparent)" }} />
      <div className="relative flex flex-wrap items-center gap-3">
        <AgentAvatar agent="ta" accent="amber" status="needs_approval" size={44} />
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-warning">Archie walks you through the code</p>
          <h3 className="font-display text-lg font-bold">Is the code how you want it?</h3>
          <p className="text-xs text-muted">Open it in the code view, answer a few questions (your picks start at what the code does now), and take the suggestions you like.</p>
        </div>
        {main && <Button variant="primary" icon={<Code2 className="h-4 w-4" />} onClick={() => onOpenFile(main)}>Open the code</Button>}
      </div>

      {choices.length > 0 && (
        <div className="relative mt-4 space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted"><MessageCircleQuestion className="h-3.5 w-3.5" />Archie asks</p>
            <button onClick={takeAll} className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary">
              <Wand2 className="h-3.5 w-3.5" />Take all Archie's picks</button>
          </div>
          {choices.map((c, i) => (
            <div key={i} className="rounded-[16px] border border-line bg-bg-2/40 p-3">
              <p className="text-sm font-semibold">{c.question}</p>
              <p className="text-xs text-muted">{c.why}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {c.options.map((o) => {
                  const on = picked[i] === o;
                  return (
                    <button key={o} onClick={() => setPicked((p) => ({ ...p, [i]: o }))}
                      className={clsx("press relative rounded-full border px-3 py-1.5 text-left text-xs font-semibold transition-colors",
                        on ? "border-primary bg-primary/12 text-text" : "border-line text-muted hover:border-primary/50 hover:text-text")}>
                      {on && <CheckCircle2 className="mr-1 inline h-3.5 w-3.5 text-primary" />}{o}
                      {o === c.current && <span className="ml-1.5 rounded-full bg-bg-2 px-1.5 py-0.5 text-[10px] font-medium text-muted">now</span>}
                      {o === c.recommended && <span className="ml-1.5 rounded-full bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning">Archie</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="relative mt-4 space-y-2.5">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted"><Lightbulb className="h-3.5 w-3.5" />Archie suggests</p>
          <div className="grid gap-2.5 lg:grid-cols-2">
            {suggestions.map((s, i) => (
              <div key={i} className={clsx("rounded-[16px] border p-3 transition-colors", apply[i] ? "border-success/50 bg-success/[0.06]" : "border-line")}>
                <div className="flex items-start gap-2">
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold">{s.title}</p>
                    <p className="text-xs text-muted">{s.detail}</p>
                    <p className="mt-0.5 text-xs"><span className="text-muted">Why: </span>{s.benefit}</p>
                  </div>
                  <button onClick={() => setApply((a) => ({ ...a, [i]: !a[i] }))}
                    className={clsx("press shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold", apply[i] ? "border-success bg-success/15 text-success" : "border-line text-muted hover:text-text")}>
                    {apply[i] ? "✓ Apply" : "Apply"}</button>
                </div>
                {s.example && <pre className="mt-2 max-h-40 overflow-auto rounded-[10px] bg-bg-2 px-3 py-2 font-mono text-[11.5px] leading-snug">{s.example}</pre>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="relative mt-4 space-y-2">
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Anything else for Dev? (optional)"
          className="w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
        <div className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 flex-1 text-xs text-muted">{changes.length ? `${changes.length} change(s) for Dev: he applies them, Archie checks again with you, then it's deployed.`
            : gate ? "No changes picked: the code goes to AWS as it is (Terra's plan still waits for your go)." : "No changes picked."}</p>
          {changes.length > 0 ? (
            <Button variant="primary" icon={<Send className="h-4 w-4" />} loading={busy} onClick={() => onSend(text)}>Send my choices to Dev</Button>
          ) : gate ? (
            <Button variant="primary" className="shimmer" icon={<CheckCircle2 className="h-4 w-4" />} loading={busy} onClick={(e) => onApprove(e.currentTarget)}>Code is fine: deploy it</Button>
          ) : null}
        </div>
      </div>
    </motion.section>
  );
}
