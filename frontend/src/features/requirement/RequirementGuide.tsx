import clsx from "clsx";
import { motion } from "framer-motion";
import { CheckCircle2, Circle, ClipboardCopy, FileUp, Loader2, SendHorizontal, Star, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { intakeApi, type IntakeState } from "../../lib/intake";

/** "What a good requirement answers" (user, 10-03: "if the user doesn't know how to give a requirement in an organised way,
 *  Echo must show the list of the most common questions; with it the user can write a master requirement prompt").
 *  Write it here as one message, or upload a document you already have: Echo reads it and asks only what's missing. */
export function RequirementGuide({ projectId, state, onState, onDone, locked }: {
  projectId: string; state: IntakeState; onState: (s: IntakeState) => void; onDone: () => void; locked: boolean;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"upload" | "send" | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const sections = state.questionnaire.sections;
  const total = sections.reduce((n, s) => n + s.questions.length, 0);
  const answered = sections.reduce((n, s) => n + s.questions.filter((q) => state.answers[q.id]).length, 0);

  const outline = () => ["# My requirement", "", ...sections.flatMap((s) => [`## ${s.title}`,
    ...s.questions.map((q) => `- ${q.label}${q.required ? " (must)" : ""}: ${q.example ? `e.g. ${q.example}` : "…"}`), ""])].join("\n");
  const copy = () => navigator.clipboard.writeText(outline()).then(() => toast.success("Outline copied: fill it in, then paste it here or in the chat"),
    () => toast.error("Couldn't copy"));
  const start = () => setText(outline());

  const send = async () => {
    if (!text.trim()) return;
    setBusy("send");
    try {
      onState(await intakeApi.chat(projectId, text.trim(), []));
      toast.success("Sent to Echo: she captures everything and asks what's missing, grouped");
      setText(""); onDone();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setBusy(null); }
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy("upload");
    try {
      const names: string[] = [];
      for (const f of Array.from(files)) {
        const r = await intakeApi.upload(projectId, f);
        onState(r.state);
        names.push(r.name);
      }
      onState(await intakeApi.chat(projectId, "Here is my requirement document. Please read it, capture everything it answers, and ask me only what's still missing, in one go.", names));
      toast.success(`Echo is reading ${names.join(", ")}`, { description: "She fills in everything the document answers and asks the rest in one grouped message." });
      onDone();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Upload failed"); }
    finally { setBusy(null); if (input.current) input.current.value = ""; }
  };
  const remove = async (name: string) => onState(await intakeApi.removeUpload(projectId, name));

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        <motion.label onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); if (!locked) upload(e.dataTransfer.files); }} animate={{ scale: drag ? 1.02 : 1 }}
          className={clsx("flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-[20px] border-2 border-dashed p-5 text-center transition-colors",
            drag ? "border-primary bg-primary/10" : "border-line hover:border-primary/60 hover:bg-surface-2/40", locked && "pointer-events-none opacity-50")}>
          <input ref={input} type="file" multiple hidden onChange={(e) => upload(e.target.files)} disabled={locked} />
          {busy === "upload" ? <Loader2 className="h-7 w-7 animate-spin text-primary" /> : <UploadCloud className="h-7 w-7 text-primary" />}
          <p className="font-semibold">Already have a requirement document?</p>
          <p className="text-xs text-muted">Drop it here (Word, PDF, text, Markdown…). Echo reads it and asks only what's missing, in one go.</p>
        </motion.label>
        <div className="flex flex-col rounded-[20px] border border-line bg-surface/60 p-4">
          <p className="font-semibold">Or write it as one message (a master prompt)</p>
          <p className="text-xs text-muted">Answer the questions below in your own words; skip what you don't know. Start from the outline if it helps.</p>
          <div className="mt-auto flex flex-wrap gap-2 pt-3">
            <Button size="sm" icon={<ClipboardCopy className="h-3.5 w-3.5" />} onClick={copy}>Copy the outline</Button>
            <Button size="sm" variant="primary" onClick={start}>Write it here</Button>
          </div>
        </div>
      </div>

      {text !== "" && (
        <div className="rounded-[18px] border border-primary/40 bg-primary/[0.04] p-3">
          <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={12}
            className="w-full resize-y rounded-[12px] border border-line bg-surface px-3 py-2 font-mono text-[13px] outline-none focus:border-primary" />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="mr-auto text-xs text-muted">Leave "…" where you don't know: Echo suggests those.</span>
            <Button size="sm" variant="ghost" onClick={() => setText("")}>Cancel</Button>
            <Button size="sm" variant="primary" loading={busy === "send"} disabled={locked} icon={<SendHorizontal className="h-3.5 w-3.5" />} onClick={send}>Send to Echo</Button>
          </div>
        </div>
      )}

      <div>
        <div className="mb-2 flex items-center gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">What a good requirement answers</p>
          <span className="ml-auto rounded-full bg-bg-2 px-2.5 py-0.5 text-xs"><b>{answered}</b>/{total} covered so far · <Star className="inline h-3 w-3 text-warning" /> = needed</span>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {sections.map((s, i) => (
            <motion.div key={s.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
              className="rounded-[16px] border border-line bg-surface/60 p-3">
              <p className="mb-1.5 text-sm font-semibold">{s.title}</p>
              <ul className="space-y-1.5">{s.questions.map((q) => {
                const done = !!state.answers[q.id];
                return (
                  <li key={q.id} className="flex items-start gap-2 text-[13px]">
                    {done ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" /> : <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted/50" />}
                    <span className="min-w-0">
                      <span className={clsx(done && "text-muted")}>{q.label}</span>{q.required && <Star className="ml-1 inline h-3 w-3 text-warning" />}
                      {q.example && <span className="block text-[11.5px] text-muted">e.g. {q.example}</span>}
                    </span>
                  </li>
                );
              })}</ul>
            </motion.div>
          ))}
        </div>
      </div>

      {state.uploads.length > 0 && (
        <div className="rounded-[16px] border border-line bg-surface/60 p-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Documents Echo has read</p>
          <ul className="space-y-1.5">{state.uploads.map((u) => (
            <li key={u.name} className="flex items-center gap-2 rounded-[10px] bg-bg-2/60 px-3 py-2 text-sm">
              <FileUp className="h-4 w-4 shrink-0 text-primary-2" />
              <span className="min-w-0 flex-1 truncate">{u.name}</span>
              <span className="text-xs text-muted">{Math.max(1, Math.round(u.chars / 1000))}k chars</span>
              {!locked && <button onClick={() => remove(u.name)} aria-label={`Remove ${u.name}`} className="text-muted hover:text-danger"><Trash2 className="h-4 w-4" /></button>}
            </li>
          ))}</ul>
        </div>
      )}
    </div>
  );
}
