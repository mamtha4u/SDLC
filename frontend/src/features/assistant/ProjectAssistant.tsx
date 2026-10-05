import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { FileText, SendHorizontal, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Markdown } from "../../components/Markdown";
import { ApiError } from "../../lib/api";
import { flowApi } from "../../lib/flow";

const SUGGESTIONS = [
  "Are we using SQS? Which kind?", "Which Python packages does the code use?", "What's waiting for me right now?",
  "What did the last change request change?", "How much has this project cost so far?",
];

/** Sage: ask anything about this project, any time. Answers come from the project's real files and history and cite
 *  where they came from. Read-only. Lives inside the project only; doesn't block the page while open. */
export function ProjectAssistant({ projectId, projectName, onOpenFile }: { projectId: string; projectName: string; onOpenFile: (p: string) => void }) {
  const qc = useQueryClient();
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [unread, setUnread] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const { data: msgs = [] } = useQuery({
    queryKey: ["assistant", projectId], queryFn: () => flowApi.assistant(projectId),
    refetchInterval: (q) => (q.state.data?.some((m) => m.status === "streaming") ? 800 : false),
  });
  const streaming = msgs.some((m) => m.status === "streaming");
  const lastDone = [...msgs].reverse().find((m) => m.role === "assistant" && m.status !== "streaming");
  const seen = useRef<number | null>(null);

  useEffect(() => { // a new answer arrived while the bubble was closed → dot on the button
    if (lastDone && seen.current !== null && lastDone.id !== seen.current && !open) setUnread(true);
    if (lastDone) seen.current = lastDone.id;
  }, [lastDone, open]);
  useEffect(() => { if (open) { setUnread(false); end.current?.scrollIntoView({ block: "end" }); } }, [open, msgs]);
  useEffect(() => { // Ctrl+K → "Ask Sage"
    const show = () => setOpen(true);
    window.addEventListener("ork:sage", show);
    return () => window.removeEventListener("ork:sage", show);
  }, []);

  const ask = async (q: string) => {
    if (!q.trim() || streaming) return;
    setSending(true);
    setText("");
    try { qc.setQueryData(["assistant", projectId], await flowApi.ask(projectId, q.trim())); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't ask"); setText(q); }
    finally { setSending(false); }
  };
  const clear = async () => {
    await flowApi.clearAssistant(projectId);
    qc.setQueryData(["assistant", projectId], []);
  };

  return createPortal(
    <>
      <motion.button onClick={() => setOpen(!open)} aria-label={open ? "Close Sage" : "Ask Sage about this project"} title="Ask Sage about this project"
        whileHover={reduce ? undefined : { scale: 1.06 }} whileTap={{ scale: 0.95 }}
        className="fixed bottom-5 right-5 z-[60] grid h-14 w-14 place-items-center rounded-full text-white shadow-[0_14px_34px_-10px_#818cf8]"
        style={{ background: "linear-gradient(135deg, #818cf8, var(--primary))" }}>
        {open ? <X className="h-6 w-6" /> : <Sparkles className="h-6 w-6" />}
        {unread && !open && <span className="absolute right-0.5 top-0.5 h-3.5 w-3.5 rounded-full border-2 border-white bg-danger" />}
        {streaming && !open && <span className="absolute inset-0 rounded-full border-2 border-white/70 pulse-ring" style={{ ["--ring" as string]: "#818cf8" }} />}
      </motion.button>
      <AnimatePresence>
        {open && (
          <motion.section role="dialog" aria-label="Sage, project guide"
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }} transition={{ type: "spring", stiffness: 380, damping: 32 }}
            className="fixed inset-x-2 bottom-24 z-[60] flex h-[min(640px,75dvh)] flex-col overflow-hidden rounded-[24px] border border-line bg-surface shadow-2xl sm:inset-x-auto sm:right-5 sm:w-[430px]">
            <header className="flex items-center gap-3 border-b border-line px-4 py-3" style={{ background: "linear-gradient(150deg, color-mix(in srgb, #818cf8 20%, var(--surface)), var(--surface) 75%)" }}>
              <AgentAvatar agent="guide" accent="indigo" status={streaming ? "working" : "done"} size={38} />
              <div className="min-w-0 flex-1">
                <p className="font-display font-semibold leading-tight">Sage · project guide</p>
                <p className="truncate text-[11.5px] text-muted">Answers from {projectName}'s files & history · read-only · Sonnet 5</p>
              </div>
              {msgs.length > 0 && (
                <button onClick={clear} title="Clear this conversation" className="grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-bg-2 hover:text-danger"><Trash2 className="h-4 w-4" /></button>
              )}
              <button onClick={() => setOpen(false)} aria-label="Close" className="grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-bg-2"><X className="h-4 w-4" /></button>
            </header>
            <div className="no-scrollbar min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {msgs.length === 0 && (
                <div className="space-y-3">
                  <p className="text-sm text-muted">Ask anything about this project: what it uses, why a decision was made, what's left, what changed. I look it up in the requirement, mapping, design, Terraform, code, tests and the crew's conversation, and tell you where I found it.</p>
                  <div className="flex flex-wrap gap-1.5">
                    {SUGGESTIONS.map((s) => (
                      <button key={s} onClick={() => ask(s)} className="rounded-full border border-line px-3 py-1.5 text-left text-[12.5px] hover:border-primary hover:text-primary">{s}</button>
                    ))}
                  </div>
                </div>
              )}
              {msgs.map((m) => m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <p className="max-w-[85%] whitespace-pre-wrap rounded-[16px] rounded-tr-[6px] bg-primary px-3.5 py-2 text-[13.5px] text-on-primary">{m.text}</p>
                </div>
              ) : (
                <div key={m.id} className="flex gap-2.5">
                  <AgentAvatar agent="guide" accent="indigo" status="done" size={26} />
                  <div className={clsx("min-w-0 flex-1 rounded-[16px] rounded-tl-[6px] border bg-bg-2/60 px-3.5 py-2.5 text-[13.5px]",
                    m.status === "error" ? "border-danger/40" : "border-line")}>
                    {m.status === "streaming" && !m.text ? (
                      <span className="flex items-center gap-2 text-muted">
                        <span className="flex gap-1">{[0, 1, 2].map((d) => (
                          <motion.span key={d} className="h-1.5 w-1.5 rounded-full" style={{ background: "#818cf8" }}
                            animate={reduce ? undefined : { opacity: [0.25, 1, 0.25] }} transition={{ repeat: Infinity, duration: 1, delay: d * 0.15 }} />
                        ))}</span>
                        Looking through the project…
                      </span>
                    ) : <Markdown className="crew-md">{m.text + (m.status === "streaming" ? " ▍" : "")}</Markdown>}
                    {m.refs.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1 border-t border-line pt-2">
                        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">Sources</span>
                        {m.refs.map((r) => (
                          <button key={r} onClick={() => onOpenFile(r.split("@")[0])} title="Open in the Code view"
                            className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[11px] hover:border-primary hover:text-primary">
                            <FileText className="h-3 w-3" />{r}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={end} />
            </div>
            <form onSubmit={(e) => { e.preventDefault(); ask(text); }} className="flex items-end gap-2 border-t border-line p-2.5">
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} placeholder="Ask about this project…"
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(text); } }}
                className="max-h-32 min-h-[42px] flex-1 resize-none rounded-[14px] bg-bg-2 px-3 py-2.5 text-sm outline-none focus:ring-1 focus:ring-primary" />
              <button type="submit" disabled={!text.trim() || streaming || sending} aria-label="Ask"
                className="grid h-[42px] w-[42px] shrink-0 place-items-center rounded-[14px] bg-primary text-on-primary disabled:opacity-35">
                <SendHorizontal className="h-4 w-4" />
              </button>
            </form>
          </motion.section>
        )}
      </AnimatePresence>
    </>,
    document.body,
  );
}
