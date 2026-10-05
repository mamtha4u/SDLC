import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, ChevronDown, Circle, FileText, Loader2, Paperclip, Play, RotateCcw, SendHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Captured, UserBubble, useTypewriter } from "../../components/chat/ChatParts";
import { Markdown } from "../../components/Markdown";
import { Button, Skeleton } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { CREW } from "../../lib/crew";
import { talkApi, type TalkAgent, type TalkState, type TalkSummary } from "../../lib/talk";

const accentOf = (agent: string) => CREW.find((c) => c.key === agent)?.accent ?? "violet";

/** One agent's kickoff conversation with the person who owns its phase (backend agents/talk.py): the agent asks its
 *  field's questions in groups, the person answers in their own words or taps a suggestion, and the agent starts its
 *  work once they confirm (or they press "Start now"). */
export function PhaseTalk({ projectId, agent }: { projectId: string; agent: TalkAgent }) {
  const qc = useQueryClient();
  const { data: st } = useQuery({
    queryKey: ["talk", projectId, agent], queryFn: () => talkApi.get(projectId, agent),
    refetchInterval: (q) => (q.state.data?.busy ? 2500 : 8000),
  });
  // the reply as it's written: pushed live (talk.delta), polled as a fallback behind proxies that buffer the stream
  const pushed = (useQuery({ queryKey: ["talk-live", projectId, agent], queryFn: () => "", enabled: false, initialData: "" }).data ?? "") as string;
  const { data: draft } = useQuery({
    queryKey: ["talk-draft", projectId, agent], queryFn: () => talkApi.draft(projectId, agent),
    enabled: !!st?.busy, refetchInterval: 700,
  });
  const chatLen = st?.chat.length ?? 0;
  useEffect(() => {
    if (draft && (draft.chat_len !== chatLen || draft.status !== st?.status || (!draft.busy && st?.busy))) {
      qc.invalidateQueries({ queryKey: ["talk", projectId, agent] });
      qc.invalidateQueries({ queryKey: ["talks", projectId] });
    }
  }, [draft, chatLen, st?.status, st?.busy, qc, projectId, agent]);
  if (!st) return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-96" /></div>;
  const live = st.busy === "thinking" ? (draft?.draft || pushed || "") : "";
  const onState = (s: TalkState) => { qc.setQueryData(["talk", projectId, agent], s); qc.invalidateQueries({ queryKey: ["talks", projectId] }); };
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="sheen elev flex min-h-[560px] flex-col overflow-hidden rounded-[28px] border border-line bg-surface">
        <Header st={st} projectId={projectId} onState={onState} />
        <div className="flex min-h-0 flex-1 flex-col px-4 pb-4 sm:px-6">
          <Thread st={st} projectId={projectId} live={live} onState={onState} />
        </div>
      </section>
      <Knows st={st} />
    </div>
  );
}

function Header({ st, projectId, onState }: { st: TalkState; projectId: string; onState: (s: TalkState) => void }) {
  const [starting, setStarting] = useState(false);
  const asked = st.chat.some((m) => m.role === "agent");
  const start = async () => {
    setStarting(true);
    try { onState(await talkApi.start(projectId, st.agent)); toast.success(`${st.name} is starting ${st.doing}`); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't start"); }
    finally { setStarting(false); }
  };
  return (
    <div className="relative flex flex-wrap items-start gap-4 border-b border-line px-5 py-4 sm:px-6">
      <div className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full opacity-15"
        style={{ background: "radial-gradient(closest-side, var(--primary-2), transparent)" }} />
      <AgentAvatar agent={st.agent} accent={accentOf(st.agent)} status={st.busy ? "working" : "needs_approval"} size={48} />
      <div className="relative min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Kickoff · before {st.doing}</p>
        <h2 className="font-display text-xl font-bold leading-tight">{st.name} has a few questions for {st.person}</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Only {st.person.replace(/^the /, "")} questions: answer in your own words, attach files, or tap a suggestion. "You decide" is fine.
          When {st.name} understands, it plays back the plan; say go and it starts.
        </p>
      </div>
      {asked && st.status === "talking" && (
        <Button variant="ghost" icon={<Play className="h-4 w-4" />} loading={starting || st.busy === "starting"} disabled={st.busy === "thinking"} onClick={start}
          title={`${st.name} uses its recommendations for anything still open and starts now`}>
          That's all, start now
        </Button>
      )}
    </div>
  );
}

function Thread({ st, projectId, live, onState }: { st: TalkState; projectId: string; live: string; onState: (s: TalkState) => void }) {
  const typed = useTypewriter(live);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string[]>([]);
  const [uploading, setUploading] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastTyped = useRef("");
  const busy = !!st.busy;
  const msgs = st.chat;
  const lastAgent = [...msgs].reverse().find((m) => m.role === "agent");
  const labels = Object.fromEntries(st.sections.flatMap((s) => s.questions.map((q) => [q.id, q.label])));
  const inputId = `talk-input-${st.agent}`;
  if (typed) lastTyped.current = typed;

  useEffect(() => { end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [msgs.length, busy, typed.length > 0, pending.length]);

  const send = async (msg: string, attachments: string[] = pending) => {
    if ((!msg.trim() && !attachments.length) || busy) return;
    const keep = { text: msg, pending: attachments };
    setText(""); setPending([]);
    try { onState(await talkApi.message(projectId, st.agent, msg.trim(), attachments)); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); setText(keep.text); setPending(keep.pending); }
  };
  const attach = async (files: FileList | null) => {
    if (!files?.length) return;
    try {
      for (const f of Array.from(files)) {
        setUploading(f.name);
        const r = await talkApi.upload(projectId, st.agent, f);
        onState(r.state);
        setPending((p) => (p.includes(r.name) ? p : [...p, r.name]));
      }
      document.getElementById(inputId)?.focus();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setUploading(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const unattach = async (name: string) => {
    setPending((p) => p.filter((x) => x !== name));
    try { onState(await talkApi.removeUpload(projectId, st.agent, name)); } catch { /* the file simply stays available */ }
  };
  const retry = async () => {
    setRetrying(true);
    try { onState(await talkApi.retry(projectId, st.agent)); } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't retry"); }
    finally { setRetrying(false); }
  };
  const tap = (r: string) => (/attach|upload/i.test(r) ? fileRef.current?.click() : send(r, []));

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="no-scrollbar max-h-[64vh] min-h-[320px] flex-1 space-y-5 overflow-y-auto px-1 pb-4 pt-4">
        <AnimatePresence initial={false}>
          {msgs.map((m, i) => {
            const wasTyped = m === lastAgent && !!lastTyped.current && m.text.startsWith(lastTyped.current.slice(0, 60));
            return (
              <motion.div key={`${i}-${m.ts}`} initial={wasTyped ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 30 }}>
                {m.role === "agent" ? (
                  <AgentBubble st={st} status={m === lastAgent && !busy ? "needs_approval" : "done"} highlight={m === lastAgent}>
                    <Markdown>{m.text}</Markdown>
                    {!!m.filled?.length && <Captured ids={m.filled} labels={labels} answers={st.answers} title={`Noted by ${st.name}`} />}
                    {m === lastAgent && !busy && !!m.quick_replies?.length && st.status === "talking" && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {m.quick_replies.map((r, k) => (
                          <motion.button key={r} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 + k * 0.05 }}
                            whileHover={{ y: -2 }} whileTap={{ scale: 0.96 }} onClick={() => tap(r)}
                            className="rounded-full border border-primary/35 bg-primary/8 px-3.5 py-1.5 text-[13px] font-medium text-text transition-colors hover:border-primary hover:bg-primary/15">
                            {r}
                          </motion.button>
                        ))}
                      </div>
                    )}
                  </AgentBubble>
                ) : m.role === "note" ? (
                  <div className="flex items-center justify-center gap-2 text-[12px] text-muted">
                    <span className="h-px w-6 bg-line" /><span className="rounded-full border border-line bg-bg-2/70 px-3 py-1">{m.text}</span><span className="h-px w-6 bg-line" />
                  </div>
                ) : (
                  <div className="flex justify-end"><div className="max-w-[88%] min-w-0"><UserBubble text={m.text} attachments={m.attachments} projectId={projectId} /></div></div>
                )}
              </motion.div>
            );
          })}
          {busy && typed && (
            <motion.div key="typing" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
              <AgentBubble st={st} status="working" highlight><Markdown>{typed + " ▍"}</Markdown></AgentBubble>
            </motion.div>
          )}
          {((busy && !typed) || uploading) && (
            <motion.div key="thinking" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex items-center gap-3">
              <AgentAvatar agent={st.agent} accent={accentOf(st.agent)} status="working" size={36} />
              <div className="flex items-center gap-2.5 rounded-[18px] border border-line bg-surface px-4 py-3 text-sm">
                <span className="flex gap-1">
                  {[0, 1, 2].map((d) => (
                    <motion.span key={d} className="h-2 w-2 rounded-full bg-primary-2" animate={{ opacity: [0.25, 1, 0.25], y: [0, -3, 0] }}
                      transition={{ repeat: Infinity, duration: 1, delay: d * 0.15 }} />
                  ))}
                </span>
                <span className="text-muted">
                  {uploading ? `Uploading ${uploading}…` : st.busy === "starting" ? `${st.name} is starting ${st.doing}…`
                    : msgs.length ? `${st.name} is thinking… usually 10–30 s` : `${st.name} is reading the earlier phases… usually under a minute`}
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        {st.last_error && !busy && (
          <div className="flex flex-wrap items-center gap-3 rounded-[16px] border border-danger/40 bg-danger/[0.06] px-4 py-3 text-sm">
            <span className="min-w-0 flex-1">{st.last_error.message}</span>
            <Button variant="primary" icon={<RotateCcw className="h-4 w-4" />} loading={retrying} onClick={retry}>Try again</Button>
          </div>
        )}
        <div ref={end} />
      </div>

      {st.status === "talking" && (
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
            <button type="button" onClick={() => fileRef.current?.click()} disabled={busy || !!uploading} title="Attach files (samples, mapping sheets, standards, code…)"
              className="focus-ring grid h-11 w-11 shrink-0 place-items-center rounded-[16px] text-muted transition-colors hover:bg-surface-2 hover:text-primary disabled:opacity-40">
              {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Paperclip className="h-5 w-5" />}
            </button>
            <textarea id={inputId} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              placeholder={`Reply to ${st.name}, or attach a file… (Shift+Enter = new line)`}
              className="max-h-48 min-h-[44px] flex-1 resize-none bg-transparent px-1 py-3 text-[15px] outline-none placeholder:text-muted/70" />
            <button type="submit" disabled={(!text.trim() && !pending.length) || busy || !!uploading} aria-label="Send"
              className="focus-ring grid h-11 w-11 shrink-0 place-items-center rounded-[16px] bg-primary text-on-primary shadow-[0_6px_18px_-6px_var(--primary)] transition-opacity disabled:opacity-35">
              <SendHorizontal className="h-5 w-5" />
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function AgentBubble({ st, status, highlight, children }: { st: TalkState; status: "working" | "needs_approval" | "done"; highlight?: boolean; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <AgentAvatar agent={st.agent} accent={accentOf(st.agent)} status={status} size={36} />
      <div className="min-w-0 flex-1">
        <p className="mb-1 text-xs font-semibold text-muted">{st.name}</p>
        <div className={clsx("rounded-[20px] rounded-tl-md border bg-surface px-4 py-3 shadow-sm", highlight ? "border-primary-2/40" : "border-line")}>{children}</div>
      </div>
    </div>
  );
}

/** What the agent has understood so far, topic by topic: the person sees at a glance what's still open. */
function Knows({ st }: { st: TalkState }) {
  const all = st.sections.flatMap((s) => s.questions);
  const done = all.filter((q) => (st.answers[q.id] ?? "").trim()).length;
  return (
    <aside className="sheen elev h-max rounded-[24px] border border-line bg-surface p-4 lg:sticky lg:top-28">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">What {st.name} knows</p>
      <div className="mt-2 flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-bg-2">
          <motion.div className="h-full rounded-full bg-success" initial={false} animate={{ width: `${all.length ? (done / all.length) * 100 : 0}%` }} />
        </div>
        <span className="font-mono text-xs text-muted">{done}/{all.length}</span>
      </div>
      <div className="mt-3 space-y-3">
        {st.sections.map((s) => (
          <div key={s.id}>
            <p className="text-xs font-semibold">{s.title}</p>
            <ul className="mt-1 space-y-1">
              {s.questions.map((q) => {
                const v = (st.answers[q.id] ?? "").trim();
                return (
                  <li key={q.id} className="flex gap-2 text-[12.5px]" title={v || q.example || ""}>
                    {v ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" /> : <Circle className={clsx("mt-0.5 h-3.5 w-3.5 shrink-0", q.required ? "text-warning" : "text-muted/60")} />}
                    <span className={clsx("min-w-0", v ? "text-text" : "text-muted")}>
                      {q.label}{q.required && !v && <span className="text-warning"> · needed</span>}
                      {v && <span className="block truncate text-[11.5px] text-muted">{v}</span>}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </aside>
  );
}

/** A finished kickoff, folded under the agent's work: what the person agreed with it. */
export function KickoffRecord({ projectId, agent, summary }: { projectId: string; agent: TalkAgent; summary?: TalkSummary }) {
  const [open, setOpen] = useState(false);
  const { data: st } = useQuery({ queryKey: ["talk", projectId, agent], queryFn: () => talkApi.get(projectId, agent), enabled: open });
  const name = CREW.find((c) => c.key === agent)?.persona ?? agent;
  if (!summary || summary.status !== "done") return null;
  const agreed = st ? st.sections.flatMap((s) => s.questions).filter((q) => (st.answers[q.id] ?? "").trim()) : [];
  return (
    <section className="mt-4 rounded-[20px] border border-line bg-surface/70">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 px-4 py-3 text-left">
        <AgentAvatar agent={agent} accent={accentOf(agent)} status="done" size={28} plain />
        <span className="min-w-0 flex-1 text-sm font-semibold">{name}'s kickoff conversation <span className="font-normal text-muted">· what was agreed before the work started ({summary.messages} messages)</span></span>
        <ChevronDown className={clsx("h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-line px-4 py-3">
          {!st ? <Skeleton className="h-24" /> : (
            <>
              <dl className="grid gap-2 sm:grid-cols-2">
                {agreed.map((q) => (
                  <div key={q.id} className="rounded-[12px] bg-bg-2/70 px-3 py-2">
                    <dt className="text-[11px] text-muted">{q.label}</dt>
                    <dd className="whitespace-pre-wrap text-[13px]">{st.answers[q.id]}</dd>
                  </div>
                ))}
                {!agreed.length && <p className="text-sm text-muted">Nothing beyond the earlier documents: {name} used its recommendations.</p>}
              </dl>
              <p className="mt-2 text-xs text-muted">The whole conversation is in <code>talks/{agent}.md</code> (Code tab), and every later agent reads what was agreed.</p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
