import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, ChevronDown, FileText, Loader2, Paperclip, SendHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Captured, FileChips, UserBubble, useTypewriter } from "../../components/chat/ChatParts";
import { Markdown } from "../../components/Markdown";
import { ApiError } from "../../lib/api";
import { intakeApi, type ChatMsg, type IntakeState } from "../../lib/intake";

const OPENER: ChatMsg = {
  role: "echo", ts: "",
  text: "Hi, I'm **Echo**, the business analyst. I capture **what the flow must do for the business**: no technical questions. "
    + "After me, each specialist asks only their own field: Atlas talks to your data analyst about the data, Archie agrees the "
    + "technology with your technical lead, and so on. Three ways to start:\n\n"
    + "- **Upload the requirement document you have** (Word, PDF, text): I read it and ask only what's missing, all in one go.\n"
    + "- **Write it as one message**. Not sure what to cover? **What to include** lists the questions a good requirement answers.\n"
    + "- Or simply tell me, in one or two sentences, **what this flow should do**, and I'll ask the rest in small groups.\n\n"
    + "_e.g. \"Every evening the day's orders from our shop must reach the warehouse team, so they can pack them the next morning.\"_",
  quick_replies: ["Upload my requirement doc", "Show what to include", "Let me describe it", "Show me an example"],
};

/** Echo leads the interview: grouped questions with tap-to-answer chips, documents instead of pasting.
 *  Orion's hand-offs and answers appear as his (never as the user's); Echo's notes to Orion show inline. */
export function EchoChat({ projectId, state, onState, live = "", onGuide }: {
  projectId: string; state: IntakeState; onState: (s: IntakeState) => void; live?: string; onGuide?: () => void;
}) {
  const typed = useTypewriter(live);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string[]>([]); // uploaded, waiting for the user to press send
  const [uploading, setUploading] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastTyped = useRef("");
  const busy = state.busy === "chatting";
  const locked = !!state.busy && !busy;
  const msgs = state.chat.length ? state.chat : [OPENER];
  const lastEcho = [...msgs].reverse().find((m) => m.role === "echo");
  const labels = Object.fromEntries(state.questionnaire.sections.flatMap((s) => s.questions.map((q) => [q.id, q.label])));
  if (typed) lastTyped.current = typed;

  useEffect(() => { end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [msgs.length, busy, typed.length > 0, pending.length]);

  const send = async (msg: string, attachments: string[] = pending) => {
    if ((!msg.trim() && !attachments.length) || busy) return;
    const keep = { text: msg, pending: attachments };
    setText(""); setPending([]);
    try { onState(await intakeApi.chat(projectId, msg.trim(), attachments)); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); setText(keep.text); setPending(keep.pending); }
  };

  const attach = async (files: FileList | null) => {
    if (!files?.length) return;
    try {
      for (const f of Array.from(files)) {
        setUploading(f.name);
        const r = await intakeApi.upload(projectId, f);
        onState(r.state);
        setPending((p) => (p.includes(r.name) ? p : [...p, r.name]));
        if (r.kind === "template") toast.success(`${f.name}: filled ${r.filled} topic${r.filled === 1 ? "" : "s"}`);
        else if (!state.chat.length) setText((t) => t || "Here's my requirement document: please read it and ask me only what's still missing, in one go.");
      }
      document.getElementById("echo-input")?.focus();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setUploading(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const unattach = async (name: string) => {
    setPending((p) => p.filter((x) => x !== name));
    try { onState(await intakeApi.removeUpload(projectId, name)); } catch { /* the file simply stays available */ }
  };

  const tapReply = (r: string) => {
    if (/what to include/i.test(r) && onGuide) return onGuide();
    if (/attach|upload/i.test(r)) return fileRef.current?.click();
    if (/example/i.test(r) && !state.chat.length) {
      return setText("Every evening our online shop exports the day's orders. The warehouse team needs them by 6 AM to pack them, and support must hear about any order that can't be passed on.");
    }
    if (/describe it/i.test(r)) return document.getElementById("echo-input")?.focus();
    send(r, []);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="no-scrollbar min-h-0 flex-1 space-y-5 overflow-y-auto px-1 pb-4 pt-1">
        <AnimatePresence initial={false}>
          {msgs.map((m, i) => {
            // the reply that was just typed out live must not animate in a second time
            const wasTyped = m === lastEcho && !!lastTyped.current && m.text.startsWith(lastTyped.current.slice(0, 60));
            return (
              <motion.div key={`${i}-${m.ts}`} initial={wasTyped ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 30 }}>
                {m.role === "echo" ? (
                  <div className="flex gap-3">
                    <AgentAvatar agent="intake" accent="cyan" status={m === lastEcho && !busy ? "needs_approval" : "done"} size={36} />
                    <div className="min-w-0 flex-1">
                      <p className="mb-1 text-xs font-semibold text-muted">Echo</p>
                      <div className={clsx("rounded-[20px] rounded-tl-md border bg-surface px-4 py-3 shadow-sm",
                        m === lastEcho ? "border-primary-2/40" : "border-line")}>
                        <Markdown>{m.text}</Markdown>
                        {!!m.filled?.length && <Captured ids={m.filled} labels={labels} answers={state.answers} />}
                      </div>
                      {m === lastEcho && !busy && !!m.quick_replies?.length && (
                        <div className="mt-2.5 flex flex-wrap gap-2">
                          {m.quick_replies.map((r, k) => (
                            <motion.button key={r} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 + k * 0.05 }}
                              whileHover={{ y: -2 }} whileTap={{ scale: 0.96 }} onClick={() => tapReply(r)} disabled={locked}
                              className="rounded-full border border-primary/35 bg-primary/8 px-3.5 py-1.5 text-[13px] font-medium text-text transition-colors hover:border-primary hover:bg-primary/15 disabled:opacity-50">
                              {r}
                            </motion.button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ) : m.role === "orion" || m.cr ? (
                  <OrionMessage m={m} projectId={projectId} />
                ) : m.role === "note" ? (
                  <div className="flex items-center justify-center gap-2 text-[12px] text-muted">
                    <span className="h-px w-6 bg-line" />
                    <span className="max-w-[85%] rounded-full border border-line bg-bg-2/70 px-3 py-1">
                      <b className="text-text">Echo</b> <ArrowRight className="inline h-3 w-3" /> <b className="text-text">Orion</b>: {m.text}
                    </span>
                    <span className="h-px w-6 bg-line" />
                  </div>
                ) : (
                  <div className="flex justify-end">
                    <div className="max-w-[88%] min-w-0">
                      <UserBubble text={m.text} attachments={m.attachments} projectId={projectId} />
                    </div>
                  </div>
                )}
              </motion.div>
            );
          })}
          {busy && typed && (
            <motion.div key="typing" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="flex gap-3">
              <AgentAvatar agent="intake" accent="cyan" status="working" size={36} />
              <div className="min-w-0 flex-1">
                <p className="mb-1 text-xs font-semibold text-muted">Echo</p>
                <div className="rounded-[20px] rounded-tl-md border border-primary-2/40 bg-surface px-4 py-3 shadow-sm">
                  <Markdown>{typed + " ▍"}</Markdown>
                </div>
              </div>
            </motion.div>
          )}
          {((busy && !typed) || uploading) && (
            <motion.div key="thinking" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex items-center gap-3">
              <AgentAvatar agent="intake" accent="cyan" status="working" size={36} />
              <div className="flex items-center gap-2.5 rounded-[18px] border border-line bg-surface px-4 py-3 text-sm">
                <span className="flex gap-1">
                  {[0, 1, 2].map((d) => (
                    <motion.span key={d} className="h-2 w-2 rounded-full bg-primary-2" animate={{ opacity: [0.25, 1, 0.25], y: [0, -3, 0] }}
                      transition={{ repeat: Infinity, duration: 1, delay: d * 0.15 }} />
                  ))}
                </span>
                <span className="text-muted">{uploading ? `Uploading ${uploading}…` : "Echo is thinking… usually 10–30 s"}</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <div ref={end} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(text); }}
        className="rounded-[22px] border border-line bg-surface p-2 shadow-[0_10px_30px_-20px_rgba(0,0,0,0.5)] focus-within:border-primary/60">
        {pending.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1 pb-2 pt-1">
            {pending.map((n) => (
              <span key={n} className="inline-flex items-center gap-1.5 rounded-full border border-primary/35 bg-primary/8 px-2.5 py-1 text-xs">
                <FileText className="h-3.5 w-3.5 text-primary-2" />{n}
                <button type="button" onClick={() => unattach(n)} aria-label={`Remove ${n}`} className="text-muted hover:text-danger"><X className="h-3.5 w-3.5" /></button>
              </span>
            ))}
            <span className="self-center text-[11px] text-muted">Add a note if you like, then press send</span>
          </div>
        )}
        <div className="flex items-end gap-2">
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => attach(e.target.files)} />
          <button type="button" onClick={() => fileRef.current?.click()} disabled={busy || !!uploading || locked} title="Attach files (XML, JSON, Word, PDF, code, mapping sheets…)"
            className="focus-ring grid h-11 w-11 shrink-0 place-items-center rounded-[16px] text-muted transition-colors hover:bg-surface-2 hover:text-primary disabled:opacity-40">
            {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Paperclip className="h-5 w-5" />}
          </button>
          <textarea id="echo-input" value={text} onChange={(e) => setText(e.target.value)} rows={1} disabled={locked}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
            placeholder="Reply to Echo, or attach a file… (Shift+Enter = new line)"
            className="max-h-48 min-h-[44px] flex-1 resize-none bg-transparent px-1 py-3 text-[15px] outline-none placeholder:text-muted/70" />
          <button type="submit" disabled={(!text.trim() && !pending.length) || busy || locked || !!uploading} aria-label="Send"
            className="focus-ring grid h-11 w-11 shrink-0 place-items-center rounded-[16px] bg-primary text-on-primary shadow-[0_6px_18px_-6px_var(--primary)] transition-opacity disabled:opacity-35">
            <SendHorizontal className="h-5 w-5" />
          </button>
        </div>
      </form>
    </div>
  );
}

/** Orion's hand-off of a change request, or his answer to Echo: clearly his, never shown as the user's. */
function OrionMessage({ m, projectId }: { m: ChatMsg; projectId: string }) {
  const [briefOpen, setBriefOpen] = useState(false);
  const reply = m.kind === "reply";
  // older hand-offs were stored as "📝 Change request CR-001: <text>\n\n📎 Attached: a, b"
  const legacy = m.role !== "orion";
  const body = legacy ? m.text.replace(/^📝 Change request [^:]+:\s*/, "").split("\n\n📎 Attached: ")[0] : m.text;
  const label = m.label ?? m.text.match(/CR-\d+/)?.[0] ?? "Change request";
  return (
    <div className="flex gap-3">
      <AgentAvatar agent="cto" accent="violet" status="done" size={36} />
      <div className="min-w-0 flex-1">
        <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-muted">
          Orion <span className="rounded bg-primary/15 px-1 font-mono text-[10px] text-primary">CTO</span><ArrowRight className="h-3 w-3" />Echo
        </p>
        <div className="rounded-[20px] rounded-tl-md border border-primary/40 bg-primary/[0.06] px-4 py-3">
          {reply ? <Markdown>{m.text}</Markdown> : (
            <>
              <p className="text-[11px] font-semibold uppercase tracking-wider text-primary">{label} forwarded to Echo{m.version ? ` · becomes ${m.version}` : ""}</p>
              {m.summary && <p className="mt-1 font-medium">{m.summary}</p>}
              <p className="mt-1.5 text-[13px] text-muted">The user's request:</p>
              <blockquote className="mt-0.5 whitespace-pre-wrap border-l-2 border-primary/50 pl-3 text-[14px]">{body}</blockquote>
              {!!m.attachments?.length && <FileChips names={m.attachments} projectId={projectId} align="start" />}
              {m.brief && (
                <div className="mt-2">
                  <button onClick={() => setBriefOpen(!briefOpen)} className="inline-flex items-center gap-1 text-xs font-semibold text-primary">
                    <ChevronDown className={clsx("h-3.5 w-3.5 transition-transform", briefOpen && "rotate-180")} />Orion's brief to Echo
                  </button>
                  {briefOpen && <div className="mt-1.5 rounded-[12px] bg-surface/70 px-3 py-2 text-[13px]"><Markdown>{m.brief}</Markdown></div>}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
