import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Markdown } from "../../components/Markdown";
import { Button } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type ReviewChat } from "../../lib/flow";

/** Questions people ask a lead before a code review is signed off (user, 10-02). */
const SUGGESTED = [
  "Review everything once more: is it fine to deploy?",
  "Any security vulnerabilities?",
  "Classes or plain functions: which fits this code?",
  "Comments or docstrings: what should we use here?",
  "Are the data structures and algorithms efficient for our volumes?",
  "What would you improve before production?",
];

/** You and Archie, about the code under review: he answers from the code, his LLD and his review. */
export function AskArchie({ projectId, chat, busy }: { projectId: string; chat: ReviewChat[]; busy: boolean }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const waiting = busy || (chat.length > 0 && chat[chat.length - 1].role === "user");
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [chat.length, waiting]);
  const ask = async (q: string) => {
    if (!q.trim()) return;
    setSending(true);
    try {
      await flowApi.askArchie(projectId, q.trim());
      setText("");
      qc.invalidateQueries({ queryKey: ["code-review", projectId] });
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't ask Archie"); }
    finally { setSending(false); }
  };
  return (
    <div className="rounded-[18px] border border-line bg-bg-2/40">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <AgentAvatar agent="ta" accent="amber" status={waiting ? "working" : "done"} size={26} plain />
        <p className="text-sm font-semibold">Ask Archie about the code</p>
        <span className="text-xs text-muted">He answers from the code itself, his LLD and his review. Nothing changes until you decide.</span>
      </div>
      <div className="max-h-[460px] space-y-3 overflow-y-auto px-4 py-3">
        {chat.length === 0 && <p className="text-sm text-muted">Ask for a deeper review, a security check, or his opinion on the code's style. A few ideas:</p>}
        {chat.map((m, i) => (
          <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className={clsx("flex gap-2.5", m.role === "user" && "flex-row-reverse")}>
            {m.role === "archie" && <AgentAvatar agent="ta" accent="amber" status="done" size={28} plain />}
            <div className={clsx("max-w-[85%] rounded-[16px] px-3.5 py-2.5 text-sm",
              m.role === "user" ? "bg-primary text-on-primary" : m.error ? "border border-danger/40 bg-danger/[0.06]" : "border border-line bg-surface")}>
              {m.role === "user" ? <p className="whitespace-pre-wrap">{m.text}</p> : <Markdown>{m.text}</Markdown>}
              <p className={clsx("mt-1 text-[10.5px]", m.role === "user" ? "text-on-primary/70" : "text-muted")}>{m.role === "user" ? "You" : "Archie"} · {m.at}</p>
            </div>
          </motion.div>
        ))}
        {waiting && (
          <div className="flex items-center gap-2.5 text-sm text-muted">
            <AgentAvatar agent="ta" accent="amber" status="working" size={28} plain />
            <span className="inline-flex gap-1">{[0, 1, 2].map((d) => <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: `${d * 0.15}s` }} />)}</span>
            Archie is reading the code…
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="border-t border-line px-4 py-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {SUGGESTED.map((s) => (
            <button key={s} disabled={waiting || sending} onClick={() => ask(s)}
              className="press rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-muted hover:border-primary hover:text-text disabled:opacity-50">{s}</button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} disabled={waiting}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(text); } }}
            placeholder={waiting ? "Archie is answering…" : "Ask Archie anything about this code (Enter to send)"}
            className="min-w-0 flex-1 resize-none rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary disabled:opacity-60" />
          <Button variant="primary" icon={<Send className="h-4 w-4" />} loading={sending} disabled={!text.trim() || waiting} onClick={() => ask(text)}>Ask</Button>
        </div>
      </div>
    </div>
  );
}
