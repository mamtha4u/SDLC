import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import {
  ArrowRight, Bug, CheckCircle2, Cloud, Code2, MessageSquare, Plus, RotateCcw, Search, Send, Sparkles, Wrench, X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Overlay } from "../../components/Overlay";
import { Button, Input, Modal, Skeleton } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { ASSIGNEES, flowApi, type NewTicket, type Ticket, type TicketComment, type TicketStatus } from "../../lib/flow";
import { ACCENT } from "../../lib/themes";
import { timeAgo } from "../../lib/time";

const WHO = Object.fromEntries(ASSIGNEES.map((a) => [a.key, a]));
const SEV: Record<Ticket["severity"], { color: string; label: string }> = {
  critical: { color: "var(--danger)", label: "Critical" }, major: { color: "var(--warning)", label: "Major" }, minor: { color: "var(--info)", label: "Minor" },
};
const COLUMNS: { key: string; title: string; hint: string; statuses: TicketStatus[]; tone: string; icon: typeof Bug }[] = [
  { key: "open", title: "With the fixer", hint: "Dev or Terra picks it up next", statuses: ["open", "reopened"], tone: "var(--danger)", icon: Bug },
  { key: "fixing", title: "Being fixed", hint: "Fix, test, deploy", statuses: ["in_progress"], tone: "var(--primary-2)", icon: Wrench },
  { key: "retest", title: "Back with Quinn", hint: "Waiting for the live retest", statuses: ["resolved"], tone: "var(--warning)", icon: RotateCcw },
  { key: "closed", title: "Closed", hint: "Fixed and verified live", statuses: ["closed"], tone: "var(--success)", icon: CheckCircle2 },
];
const STATUS_LABEL: Record<TicketStatus, string> = { open: "Open", reopened: "Reopened", in_progress: "Being fixed", resolved: "Waiting for retest", closed: "Closed" };
const STATUS_TONE: Record<TicketStatus, string> = { open: "var(--danger)", reopened: "var(--danger)", in_progress: "var(--primary-2)", resolved: "var(--warning)", closed: "var(--success)" };

/** The crew's bug board: Quinn opens a ticket for every failed live check; Dev (code) or Terra (infrastructure) fixes,
 *  deploys and hands it back; Quinn retests and closes or reopens it. You can open, comment on and assign any ticket. */
export function TicketsTab({ projectId }: { projectId: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["tickets", projectId], queryFn: () => flowApi.tickets(projectId), refetchInterval: 8000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [who, setWho] = useState("all");
  const [q, setQ] = useState("");

  const tickets = useMemo(() => (data?.tickets ?? []).filter((t) => (who === "all" || t.assignee === who)
    && (!q || `${t.label} ${t.title} ${t.description} ${t.check_id ?? ""}`.toLowerCase().includes(q.toLowerCase()))), [data, who, q]);
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-36" /><Skeleton className="h-[420px]" /></div>;
  const all = data.tickets;
  const counts = Object.fromEntries(COLUMNS.map((c) => [c.key, all.filter((t) => c.statuses.includes(t.status)).length]));
  const holders = ASSIGNEES.filter((a) => all.some((t) => t.assignee === a.key));

  return (
    <div className="space-y-4">
      <Journey counts={counts} total={all.length} onNew={() => setCreating(true)} />

      <div className="flex flex-wrap items-center gap-2">
        <Chip active={who === "all"} onClick={() => setWho("all")}>Everyone <b>{all.length}</b></Chip>
        {holders.map((a) => (
          <Chip key={a.key} active={who === a.key} onClick={() => setWho(a.key)}>
            {a.key === "user" ? <span className="grid h-5 w-5 place-items-center rounded-full bg-primary/20 text-[10px]">You</span>
              : <AgentAvatar agent={a.key} accent={a.accent} status="done" size={20} plain />}
            {a.name} <b>{all.filter((t) => t.assignee === a.key && t.status !== "closed").length}</b>
          </Chip>
        ))}
        <label className="neu-inset ml-auto flex h-9 min-w-[200px] items-center gap-2 rounded-[12px] px-3 text-sm">
          <Search className="h-4 w-4 text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tickets" className="w-full bg-transparent outline-none placeholder:text-muted/70" aria-label="Search tickets" />
        </label>
      </div>

      {!all.length ? <Empty onNew={() => setCreating(true)} /> : (
        <LayoutGroup>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {COLUMNS.map((col) => {
              const items = tickets.filter((t) => col.statuses.includes(t.status));
              const Icon = col.icon;
              return (
                <section key={col.key} className="flex min-h-[220px] flex-col rounded-[22px] border border-line bg-bg-2/40 p-2.5">
                  <header className="flex items-center gap-2 px-1.5 pb-2.5 pt-1">
                    <span className="grid h-7 w-7 place-items-center rounded-[10px]" style={{ background: `color-mix(in srgb, ${col.tone} 16%, transparent)`, color: col.tone }}>
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1"><p className="font-display text-sm font-semibold">{col.title}</p><p className="truncate text-[11px] text-muted">{col.hint}</p></div>
                    <span className="rounded-full px-2 py-0.5 font-mono text-xs font-semibold" style={{ background: `color-mix(in srgb, ${col.tone} 14%, transparent)`, color: col.tone }}>{items.length}</span>
                  </header>
                  <div className="flex flex-1 flex-col gap-2">
                    <AnimatePresence initial={false}>
                      {items.map((t) => <Card key={t.id} t={t} onOpen={() => setSelected(t.id)} />)}
                    </AnimatePresence>
                    {!items.length && <p className="m-auto px-4 py-6 text-center text-xs text-muted">Nothing here</p>}
                  </div>
                </section>
              );
            })}
          </div>
        </LayoutGroup>
      )}

      <Drawer projectId={projectId} id={selected} onClose={() => setSelected(null)} />
      <NewTicketModal projectId={projectId} open={creating} onClose={() => setCreating(false)} onCreated={(id) => setSelected(id)} />
    </div>
  );
}

function Journey({ counts, total, onNew }: { counts: Record<string, number>; total: number; onNew: () => void }) {
  const steps = [
    { key: "open", label: "Quinn finds it", sub: "a failed live check", agent: "qa", accent: "rose", icon: Bug },
    { key: "fixing", label: "Dev or Terra fixes", sub: "test, deploy, comment", agent: "de", accent: "blue", icon: Wrench },
    { key: "retest", label: "Quinn retests", sub: "live, with evidence", agent: "qa", accent: "rose", icon: RotateCcw },
    { key: "closed", label: "Closed", sub: "or reopened to the fixer", agent: "", accent: "emerald", icon: CheckCircle2 },
  ];
  return (
    <div className="spotlight sheen elev relative overflow-hidden rounded-[24px] border border-line bg-surface p-5">
      <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--primary-2) 22%, transparent)" }} />
      <div className="relative flex flex-wrap items-start gap-4">
        <div className="min-w-[220px] flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">Tickets</p>
          <h2 className="font-display text-2xl font-bold tracking-tight">How the crew fixes things</h2>
          <p className="mt-1 max-w-xl text-sm text-muted">Every failed live check becomes a ticket for whoever owns the fix. They fix it, deploy it, comment and hand it back;
            Quinn retests it in AWS. You can open a ticket for any agent, comment, or move it.</p>
        </div>
        <Button variant="primary" className="shimmer" icon={<Plus className="h-4 w-4" />} onClick={onNew}>New ticket</Button>
      </div>
      <ol className="relative mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        {steps.map((s, i) => {
          const Icon = s.icon;
          const n = counts[s.key] ?? 0;
          return (
            <motion.li key={s.key} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.06 * i }}
              className="relative flex items-center gap-3 rounded-[18px] border border-line bg-bg-2/50 p-3">
              {i < steps.length - 1 && (
                <span className="absolute -right-3 top-1/2 z-10 hidden h-[3px] w-3 -translate-y-1/2 overflow-hidden rounded-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))] md:block">
                  <span className="rail-flow block h-full w-full opacity-80" />
                </span>
              )}
              <span className="relative grid h-10 w-10 shrink-0 place-items-center rounded-[14px] text-on-primary"
                style={{ background: `linear-gradient(135deg, ${ACCENT[s.accent]}, color-mix(in srgb, ${ACCENT[s.accent]} 55%, var(--primary-2)))` }}>
                <Icon className="h-5 w-5" />
                {n > 0 && s.key !== "closed" && <span className="sonar absolute inset-0 rounded-[14px] border" style={{ borderColor: ACCENT[s.accent] }} />}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{s.label}</p>
                <p className="truncate text-[11px] text-muted">{s.sub}</p>
              </div>
              <motion.span key={n} initial={{ scale: 1.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="font-display text-2xl font-bold tabular-nums">{n}</motion.span>
            </motion.li>
          );
        })}
      </ol>
      {total > 0 && (
        <div className="relative mt-4 h-2 overflow-hidden rounded-full bg-bg-2" title={`${counts.closed ?? 0} of ${total} closed`}>
          <motion.div className="h-full rounded-full bg-[linear-gradient(90deg,var(--success),color-mix(in_srgb,var(--success)_60%,var(--primary-2)))]"
            initial={{ width: 0 }} animate={{ width: `${((counts.closed ?? 0) / total) * 100}%` }} transition={{ duration: 0.9, ease: [0.2, 0.7, 0.2, 1] }} />
        </div>
      )}
    </div>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={clsx("press focus-ring inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-sm transition-colors",
      active ? "border-primary bg-primary/12 text-text" : "border-line text-muted hover:text-text")}>{children}</button>
  );
}

function Card({ t, onOpen }: { t: Ticket; onOpen: () => void }) {
  const sev = SEV[t.severity];
  const a = WHO[t.assignee];
  const comments = (t.comments ?? []).filter((c) => c.kind === "comment").length;
  return (
    <motion.button layout layoutId={t.id} onClick={onOpen}
      initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.94 }}
      transition={{ type: "spring", stiffness: 420, damping: 34 }}
      className="lift press focus-ring group relative w-full overflow-hidden rounded-[16px] border border-line bg-surface p-3 pl-4 text-left">
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: sev.color }} />
      <div className="flex items-center gap-1.5 text-[11px]">
        <span className="font-mono font-semibold">{t.label}</span>
        <span className="inline-flex items-center gap-1 rounded-full bg-bg-2 px-1.5 py-0.5 text-muted">
          {t.area === "infra" ? <Cloud className="h-3 w-3" /> : <Code2 className="h-3 w-3" />}{t.area}</span>
        {t.status === "reopened" && <span className="rounded-full bg-danger/15 px-1.5 py-0.5 font-semibold text-danger">reopened</span>}
        <span className="ml-auto text-muted">{timeAgo(t.updated_at)}</span>
      </div>
      <p className="mt-1.5 line-clamp-2 text-sm font-medium leading-snug">{t.title}</p>
      <div className="mt-2.5 flex items-center gap-2 text-[11px] text-muted">
        {a && a.key !== "user" ? <AgentAvatar agent={a.key} accent={a.accent} status={t.status === "in_progress" ? "working" : "done"} size={22} plain />
          : <span className="grid h-[22px] w-[22px] place-items-center rounded-full bg-primary/20 text-[9px] text-text">You</span>}
        <span className="font-medium text-text/85">{a?.name ?? t.assignee}</span>
        <span className="rounded-full px-1.5 py-0.5 font-semibold" style={{ color: sev.color, background: `color-mix(in srgb, ${sev.color} 12%, transparent)` }}>{sev.label}</span>
        {t.check_id && <span className="font-mono">{t.check_id}</span>}
        {comments > 0 && <span className="ml-auto inline-flex items-center gap-0.5"><MessageSquare className="h-3 w-3" />{comments}</span>}
      </div>
    </motion.button>
  );
}

function Empty({ onNew }: { onNew: () => void }) {
  return (
    <div className="grid place-items-center rounded-[24px] border border-dashed border-line bg-surface/60 px-6 py-14 text-center">
      <motion.div animate={{ y: [0, -6, 0] }} transition={{ duration: 3.2, repeat: Infinity, ease: "easeInOut" }}
        className="grid h-16 w-16 place-items-center rounded-[20px] bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-on-primary shadow-[0_18px_40px_-18px_var(--primary)]">
        <Sparkles className="h-7 w-7" />
      </motion.div>
      <p className="mt-4 font-display text-lg font-semibold">No tickets yet</p>
      <p className="mt-1 max-w-md text-sm text-muted">Once Dev's code is live, Quinn tests every scenario in AWS and opens a ticket for each failure. You can open one yourself too, for any agent.</p>
      <Button className="mt-4" icon={<Plus className="h-4 w-4" />} onClick={onNew}>Open a ticket</Button>
    </div>
  );
}

const KIND_ICON: Record<TicketComment["kind"], typeof Bug> = { created: Bug, comment: MessageSquare, status: ArrowRight, assign: ArrowRight };

function Drawer({ projectId, id, onClose }: { projectId: string; id: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: t } = useQuery({ queryKey: ["ticket", projectId, id], queryFn: () => flowApi.ticket(projectId, id!), enabled: !!id, refetchInterval: 6000 });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const refresh = (next?: Ticket) => {
    if (next) qc.setQueryData(["ticket", projectId, id], next);
    qc.invalidateQueries({ queryKey: ["tickets", projectId] });
    qc.invalidateQueries({ queryKey: ["crew", projectId] });
  };
  const run = async (key: string, fn: () => Promise<Ticket>, ok: (r: Ticket) => string) => {
    setBusy(key);
    try { const r = await fn(); refresh(r); toast.success(ok(r)); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't update the ticket"); }
    finally { setBusy(null); }
  };
  const send = () => text.trim() && run("comment", () => flowApi.commentTicket(projectId, id!, text.trim()), () => { setText(""); return "Comment added"; });
  const routed = (r: Ticket) => ({ dispatched: "Orion handed it over", queued: "Queued: Orion hands it over at the next step", waiting: "Waiting for the requirement" }[r.routed ?? ""]
    ?? (r.routed?.startsWith("change request") ? `Turned into ${r.routed.replace("change request ", "")}` : "Updated"));

  return (
    <Overlay open={!!id} onClose={onClose} label="Ticket" z={90}>
      <motion.aside role="dialog" aria-modal aria-label={t ? `${t.label} ${t.title}` : "Ticket"}
        initial={{ x: 60, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 60, opacity: 0 }} transition={{ type: "spring", stiffness: 360, damping: 34 }}
        className="absolute inset-y-0 right-0 flex w-full max-w-[620px] flex-col border-l border-line bg-surface shadow-2xl">
        {!t ? <div className="space-y-3 p-6"><Skeleton className="h-10" /><Skeleton className="h-64" /></div> : (
          <>
            <header className="relative border-b border-line p-5">
              <span className="absolute inset-x-0 top-0 h-1" style={{ background: SEV[t.severity].color }} />
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm font-semibold">{t.label}</span>
                <span className="rounded-full px-2.5 py-0.5 text-[11px] font-semibold" style={{ color: STATUS_TONE[t.status], background: `color-mix(in srgb, ${STATUS_TONE[t.status]} 14%, transparent)` }}>{STATUS_LABEL[t.status]}</span>
                <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold" style={{ color: SEV[t.severity].color, background: `color-mix(in srgb, ${SEV[t.severity].color} 12%, transparent)` }}>{SEV[t.severity].label}</span>
                <span className="inline-flex items-center gap-1 rounded-full bg-bg-2 px-2 py-0.5 text-[11px] text-muted">{t.area === "infra" ? <Cloud className="h-3 w-3" /> : <Code2 className="h-3 w-3" />}{t.area}</span>
                <button onClick={onClose} className="focus-ring ml-auto rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text" aria-label="Close"><X className="h-4 w-4" /></button>
              </div>
              <h2 className="mt-2 font-display text-xl font-semibold leading-snug">{t.title}</h2>
              <p className="mt-1 text-xs text-muted">Opened by {WHO[t.reporter]?.name ?? t.reporter} {timeAgo(t.created_at)} · found in {t.version_found ?? "–"}
                {t.version_fixed && <> · fixed in <b className="text-text">{t.version_fixed}</b></>}{t.check_id && <> · check <span className="font-mono">{t.check_id}</span></>}</p>
            </header>

            <div className="flex-1 space-y-5 overflow-y-auto p-5">
              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Assigned to</p>
                <div className="flex flex-wrap gap-1.5">
                  {ASSIGNEES.filter((a) => a.key !== "user").map((a) => {
                    const on = t.assignee === a.key;
                    return (
                      <button key={a.key} disabled={!!busy || on} title={`${a.name}${a.role ? ` · ${a.role}` : ""}`}
                        onClick={() => run(`a-${a.key}`, () => flowApi.updateTicket(projectId, t.id, { assignee: a.key }), (r) => `${a.name} has it. ${routed(r)}`)}
                        className={clsx("press focus-ring flex items-center gap-1.5 rounded-full border py-1 pl-1 pr-2.5 text-xs transition-colors",
                          on ? "border-transparent text-on-primary" : "border-line text-muted hover:border-primary hover:text-text")}
                        style={on ? { background: `linear-gradient(120deg, ${ACCENT[a.accent]}, color-mix(in srgb, ${ACCENT[a.accent]} 55%, var(--primary-2)))` } : undefined}>
                        <AgentAvatar agent={a.key} accent={a.accent} status={on ? "working" : "done"} size={22} plain />{a.name}
                      </button>
                    );
                  })}
                </div>
                <p className="mt-1.5 text-[11px] text-muted">Dev fixes code, Terra infrastructure, Quinn retests. Atlas, Archie, Echo or Orion get it as a change request.</p>
              </div>

              <div className="flex flex-wrap gap-2">
                {t.status !== "closed" && <Button size="sm" variant="neu" loading={busy === "close"} icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                  onClick={() => run("close", () => flowApi.updateTicket(projectId, t.id, { status: "closed" }), () => "Ticket closed")}>Close</Button>}
                {t.status === "closed" && <Button size="sm" variant="neu" loading={busy === "reopen"} icon={<RotateCcw className="h-3.5 w-3.5" />}
                  onClick={() => run("reopen", () => flowApi.updateTicket(projectId, t.id, { status: "reopened" }), (r) => `Reopened. ${routed(r)}`)}>Reopen</Button>}
              </div>

              {t.description && <Block title="What's wrong">{t.description}</Block>}
              {t.steps && <Block title="Steps to reproduce" mono>{t.steps}</Block>}
              {(t.expected || t.actual) && (
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="rounded-[14px] border border-success/30 bg-success/[0.06] p-3"><p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-success">Expected</p><p className="whitespace-pre-wrap break-words text-sm">{t.expected || "–"}</p></div>
                  <div className="rounded-[14px] border border-danger/30 bg-danger/[0.06] p-3"><p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-danger">Actual</p><p className="whitespace-pre-wrap break-words text-sm">{t.actual || "–"}</p></div>
                </div>
              )}

              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">History</p>
                <ol className="relative space-y-3 pl-1">
                  <span className="absolute bottom-2 left-[15px] top-2 w-px bg-line" />
                  {(t.comments ?? []).map((c, i) => {
                    const who = WHO[c.author];
                    const Icon = KIND_ICON[c.kind];
                    return (
                      <motion.li key={c.id} initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i, 8) * 0.03 }} className="relative flex gap-3">
                        {who && who.key !== "user" ? <AgentAvatar agent={who.key} accent={who.accent} status="done" size={30} plain />
                          : <span className="relative z-[1] grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-primary text-[10px] font-semibold text-on-primary">You</span>}
                        <div className={clsx("min-w-0 flex-1 rounded-[14px] px-3 py-2", c.kind === "comment" ? "border border-line bg-bg-2/50" : "bg-transparent")}>
                          <p className="flex items-center gap-1.5 text-[11px] text-muted"><b className="text-text">{who?.name ?? c.author}</b><Icon className="h-3 w-3" />{c.kind === "comment" ? "commented" : c.kind === "created" ? "opened it" : "updated it"} · {timeAgo(c.created_at)}</p>
                          <p className="mt-0.5 whitespace-pre-wrap break-words text-sm">{c.text}</p>
                        </div>
                      </motion.li>
                    );
                  })}
                </ol>
              </div>
            </div>

            <footer className="border-t border-line p-4">
              <div className="neu-inset flex items-end gap-2 rounded-[14px] p-2">
                <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="Add a comment for the crew…"
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } }}
                  className="min-h-[44px] flex-1 resize-none bg-transparent px-1.5 py-1 text-sm outline-none placeholder:text-muted/70" aria-label="Comment" />
                <Button size="sm" variant="primary" disabled={!text.trim()} loading={busy === "comment"} icon={<Send className="h-3.5 w-3.5" />} onClick={send}>Send</Button>
              </div>
              <p className="mt-1 text-[10.5px] text-muted"><span className="kbd">Ctrl</span> <span className="kbd">Enter</span> to send. The assignee reads your comments when they work on it.</p>
            </footer>
          </>
        )}
      </motion.aside>
    </Overlay>
  );
}

function Block({ title, children, mono }: { title: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</p>
      <p className={clsx("whitespace-pre-wrap break-words rounded-[14px] bg-bg-2/50 px-3 py-2 text-sm", mono && "font-mono text-[12.5px]")}>{children}</p>
    </div>
  );
}

const WHAT_HAPPENS: Record<string, string> = {
  de: "Dev fixes the code with a regression test, deploys it into AWS and hands it back to Quinn to retest.",
  tp: "Terra fixes the infrastructure; you approve the plan before anything changes in AWS; then Quinn retests.",
  qa: "Quinn checks it live in AWS, then closes it or sends it to the fixer with the evidence.",
  ta: "Orion turns it into a change request: Archie's design changes through a re-plan.",
  ba: "Orion turns it into a change request: Atlas's mapping changes through a re-plan.",
  intake: "Orion turns it into a change request: Echo updates the requirement with you.",
  cto: "Orion triages it like a change request and routes it to whoever it affects.",
};
const AREA_OF: Record<string, NewTicket["area"]> = { de: "code", tp: "infra", qa: "code", ta: "design", ba: "design", intake: "other", cto: "other" };

function NewTicketModal({ projectId, open, onClose, onCreated }: { projectId: string; open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const blank: NewTicket = { title: "", description: "", steps: "", expected: "", actual: "", severity: "major", area: "code", assignee: "de" };
  const [f, setF] = useState<NewTicket>(blank);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof NewTicket, v: string) => setF((o) => ({ ...o, [k]: v }));
  const submit = async () => {
    setBusy(true);
    try {
      const t = await flowApi.newTicket(projectId, { ...f, area: AREA_OF[f.assignee] ?? "other" });
      toast.success(`${t.label} opened for ${WHO[t.assignee]?.name}`, { description: t.routed === "dispatched" ? "Orion handed it over." : t.routed === "queued" ? "Orion hands it over at the next step." : t.routed });
      qc.invalidateQueries({ queryKey: ["tickets", projectId] });
      setF(blank);
      onClose();
      onCreated(t.id);
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't open the ticket"); }
    finally { setBusy(false); }
  };
  const area = (k: keyof NewTicket, label: string, rows = 3, placeholder = "") => (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>
      <textarea value={f[k]} rows={rows} placeholder={placeholder} onChange={(e) => set(k, e.target.value)}
        className="neu-inset w-full resize-y rounded-[12px] px-3.5 py-2.5 text-sm outline-none placeholder:text-muted/70 focus:shadow-[0_0_0_2px_var(--primary)]" />
    </label>
  );
  return (
    <Modal open={open} onClose={onClose} title="New ticket" width={640}>
      <div className="space-y-4">
        <Input label="What's wrong, in one line" value={f.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Orders with an empty SKU are accepted" autoFocus />
        {area("description", "Details (optional)", 3, "What you saw, where, why it matters")}
        <div className="grid gap-3 sm:grid-cols-2">
          {area("expected", "Expected (optional)", 2)}
          {area("actual", "Actual (optional)", 2)}
        </div>
        <div>
          <span className="mb-1.5 block text-xs font-medium text-muted">Severity</span>
          <div className="flex gap-1.5">
            {(Object.keys(SEV) as Ticket["severity"][]).map((s) => (
              <button key={s} onClick={() => set("severity", s)} className={clsx("press flex-1 rounded-[12px] border py-2 text-sm font-semibold", f.severity === s ? "text-text" : "border-line text-muted")}
                style={f.severity === s ? { borderColor: SEV[s].color, background: `color-mix(in srgb, ${SEV[s].color} 14%, transparent)` } : undefined}>{SEV[s].label}</button>
            ))}
          </div>
        </div>
        <div>
          <span className="mb-1.5 block text-xs font-medium text-muted">Who should take it?</span>
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {ASSIGNEES.filter((a) => a.key !== "user").map((a) => (
              <button key={a.key} onClick={() => set("assignee", a.key)}
                className={clsx("press flex items-center gap-2 rounded-[14px] border p-2 text-left text-sm", f.assignee === a.key ? "text-text" : "border-line text-muted hover:text-text")}
                style={f.assignee === a.key ? { borderColor: ACCENT[a.accent], background: `color-mix(in srgb, ${ACCENT[a.accent]} 12%, transparent)` } : undefined}>
                <AgentAvatar agent={a.key} accent={a.accent} status={f.assignee === a.key ? "working" : "done"} size={26} plain />
                <span className="min-w-0"><b className="block text-[13px]">{a.name}</b><span className="block truncate text-[10.5px] text-muted">{a.role.replace(" (change request)", "")}</span></span>
              </button>
            ))}
          </div>
          <p className="mt-2 rounded-[12px] bg-primary/10 px-3 py-2 text-xs">{WHAT_HAPPENS[f.assignee]}</p>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={f.title.trim().length < 3} icon={<Send className="h-4 w-4" />} onClick={submit}>Open ticket</Button>
        </div>
      </div>
    </Modal>
  );
}
