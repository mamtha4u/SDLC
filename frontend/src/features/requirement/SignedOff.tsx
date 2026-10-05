import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, ChevronDown, Cloud, Download, GitPullRequestArrow, Paperclip, SearchCheck, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { DiffView } from "../../components/DiffView";
import { Markdown } from "../../components/Markdown";
import { Button, Skeleton } from "../../components/ui";
import { api } from "../../lib/api";
import { CREW } from "../../lib/crew";
import { fileUrl, flowApi, type ChangeRequest } from "../../lib/flow";
import type { IntakeState } from "../../lib/intake";

const byKey = Object.fromEntries(CREW.map((c) => [c.key, c]));
const VERDICT: Record<string, string> = {
  confirmed: "bg-success/15 text-success", refuted: "bg-danger/15 text-danger", partly: "bg-warning/15 text-warning", unverified: "bg-bg-2 text-muted",
};
const CR_CHIP: Record<ChangeRequest["status"], { label: string; cls: string }> = {
  triage: { label: "Orion triaging", cls: "bg-primary/15 text-primary" },
  clarifying: { label: "With Echo", cls: "bg-warning/15 text-warning" },
  planning: { label: "Awaiting plan approval", cls: "bg-primary-2/15 text-primary-2" },
  in_progress: { label: "In progress", cls: "bg-primary-2/15 text-primary-2" },
  reviewing: { label: "Orion asks you to confirm", cls: "bg-warning/15 text-warning" },
  done: { label: "Done", cls: "bg-success/15 text-success" },
};

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

/** After sign-off: the frozen requirement document and Orion's delivery plan. */
export function SignedOff({ projectId, state }: { projectId: string; state: IntakeState }) {
  const plan = state.plan;
  const { data: project } = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId) });
  const orion = project?.agents.find((a) => a.key === "cto");
  const orionBusy = orion?.status === "working";
  const { data: changes } = useQuery({
    queryKey: ["changes", projectId], queryFn: () => flowApi.changes(projectId), refetchInterval: orionBusy ? 3000 : 15000,
  });
  const version = project?.current_version ?? "v1";
  return (
    <div className="space-y-5">
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
        className="relative overflow-hidden rounded-[24px] border border-success/35 bg-success/[0.07] p-5">
        <div className="flex flex-wrap items-center gap-4">
          <motion.div initial={{ scale: 0, rotate: -30 }} animate={{ scale: 1, rotate: 0 }} transition={{ type: "spring", stiffness: 260, damping: 14 }}>
            <CheckCircle2 className="h-12 w-12 text-success" />
          </motion.div>
          <div className="min-w-0 flex-1">
            <h2 className="flex flex-wrap items-center gap-2 font-display text-xl font-bold">
              Requirement signed off<span className="rounded-full bg-success/15 px-2 py-0.5 font-mono text-xs text-success">{version}</span>
            </h2>
            <p className="text-sm text-muted">
              Frozen as <b className="text-text">00_requirement.md</b> after {state.rounds.length} review rounds
              {state.signed_off_at ? ` · ${new Date(state.signed_off_at + (state.signed_off_at.endsWith("Z") ? "" : "Z")).toLocaleString()}` : ""}.
              {changes?.length ? ` ${changes.length} change request${changes.length === 1 ? "" : "s"} since.` : " Anything new or extra: use Change request at the top."}
            </p>
          </div>
          <Button icon={<Download className="h-4 w-4" />} onClick={() => download(`00_requirement_${version}.md`, state.requirement_md ?? "")}>Download .md</Button>
        </div>
      </motion.div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <section className="sheen elev rounded-[22px] border border-line bg-surface/70 p-5 sm:p-6">
          <Markdown>{state.requirement_md ?? ""}</Markdown>
        </section>

        <section className="space-y-4">
          {!!changes?.length && (
            <div className="glass rounded-[22px] p-5">
              <h3 className="mb-3 flex items-center gap-2 font-display text-lg font-semibold"><GitPullRequestArrow className="h-5 w-5 text-primary" />Change requests</h3>
              <ol className="space-y-2.5">{changes.map((c) => <ChangeRow key={c.id} c={c} projectId={projectId} />)}</ol>
            </div>
          )}
          <div className="glass rounded-[22px] p-5">
            <div className="flex items-center gap-3">
              <AgentAvatar agent="cto" accent="violet" status={orionBusy || !plan ? "working" : "done"} size={44} />
              <div>
                <h3 className="font-display text-lg font-semibold">Orion's delivery plan</h3>
                <p className="text-xs text-muted">{plan ? "The crew will follow this, with your approval at every step." : "Orion is reading the requirement…"}</p>
              </div>
            </div>
            {orionBusy && (
              <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                className="mt-3 flex items-center gap-2.5 rounded-[14px] border border-primary/40 bg-primary/10 px-3 py-2.5 text-sm">
                <span className="h-2 w-2 shrink-0 rounded-full bg-primary pulse-ring" style={{ ["--ring" as string]: "var(--primary)" }} />
                <span className="min-w-0 flex-1"><b>Orion, live:</b> {orion?.activity || "Working…"}</span>
              </motion.div>
            )}
            {!plan ? (
              <div className="mt-4 space-y-2"><Skeleton className="h-4" /><Skeleton className="h-4 w-4/5" /><Skeleton className="h-24" /></div>
            ) : (
              <div className="mt-4 space-y-4">
                <p className="text-sm">{plan.summary}</p>
                <div className="flex flex-wrap gap-2">
                  {plan.services.map((s) => (
                    <span key={s.service} title={s.purpose} className="inline-flex items-center gap-1.5 rounded-full bg-primary-2/10 px-2.5 py-1 text-xs">
                      <Cloud className="h-3.5 w-3.5 text-primary-2" />{s.service}
                    </span>
                  ))}
                </div>
                <ol className="space-y-2.5">
                  {plan.steps.map((st, i) => {
                    const m = byKey[st.agent];
                    return (
                      <motion.li key={i} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.06 }}
                        className="flex gap-3 rounded-[14px] bg-bg-2/60 p-3">
                        {m && <AgentAvatar agent={m.key} accent={m.accent} status="waiting" size={34} />}
                        <div className="min-w-0 text-sm">
                          <p className="font-semibold">{m ? `${m.persona} · ${m.abbr}` : st.agent}</p>
                          <p className="text-muted">{st.task}</p>
                          <p className="mt-1 inline-flex items-center gap-1 text-xs text-warning"><ShieldCheck className="h-3.5 w-3.5" />{st.approval}</p>
                        </div>
                      </motion.li>
                    );
                  })}
                </ol>
                {plan.feedback_addressed && (
                  <p className="rounded-[12px] bg-primary/10 px-3 py-2 text-sm"><b>Your feedback:</b> {plan.feedback_addressed}</p>
                )}
                {!!plan.research?.length && (
                  <div>
                    <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted"><SearchCheck className="h-3.5 w-3.5" />Verified by research</p>
                    <ul className="space-y-2">
                      {plan.research.map((r) => (
                        <li key={r.claim} className="rounded-[12px] border border-line bg-bg-2/60 p-3 text-sm">
                          <div className="flex items-start gap-2">
                            <span className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${VERDICT[r.verdict]}`}>{r.verdict}</span>
                            <p className="font-medium">{r.claim}</p>
                          </div>
                          <p className="mt-1 text-muted">{r.finding}</p>
                          <p className="mt-1 truncate text-xs">
                            {/^https?:/.test(r.source)
                              ? <a href={r.source} target="_blank" rel="noreferrer" className="text-primary-2 underline">{r.source}</a>
                              : <span className="text-muted">{r.source}</span>}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {plan.risks.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Risks (evidence-backed)</p>
                    <ul className="space-y-2 text-sm">
                      {plan.risks.map((r) => (
                        <li key={r.risk} className="flex gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                          <span><b>{r.risk}</b> <span className="text-muted">→ {r.mitigation}</span>
                            {r.evidence && <span className="mt-0.5 block text-xs italic text-muted">Evidence: {r.evidence}</span>}</span></li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function ChangeRow({ c, projectId }: { c: ChangeRequest; projectId: string }) {
  const [open, setOpen] = useState(c.status !== "done");
  const chip = CR_CHIP[c.status];
  return (
    <li className="rounded-[14px] border border-line bg-bg-2/60">
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 p-3 text-left">
        <span className="mt-0.5 font-mono text-xs font-bold text-primary">{c.label}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium leading-snug">{c.triage?.summary ?? c.text}</span>
          <span className="mt-1 flex flex-wrap items-center gap-2 text-xs">
            <span className={clsx("rounded-full px-2 py-0.5 font-semibold", chip.cls)}>{chip.label}</span>
            {c.version_to && <span className="font-mono text-muted">{c.version_from} → {c.version_to}</span>}
            {c.route && <span className="text-muted">route: {c.route}</span>}
          </span>
        </span>
        <ChevronDown className={clsx("mt-1 h-4 w-4 shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-3 pb-3 pt-2 text-sm">
          <p className="whitespace-pre-wrap text-muted">“{c.text}”</p>
          {!!c.attachments.length && (
            <div className="flex flex-wrap gap-1.5">
              {c.attachments.map((a) => (
                <a key={a} href={fileUrl(projectId, a)} title={`Download ${a}`}
                  className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-0.5 text-xs hover:border-primary hover:text-primary">
                  <Paperclip className="h-3 w-3" />{a}
                </a>
              ))}
            </div>
          )}
          {c.triage?.reason && <p><b>Why this route:</b> <span className="text-muted">{c.triage.reason}</span></p>}
          {!!c.triage?.affected_agents.length && (
            <ul className="space-y-1">
              {c.triage.affected_agents.map((a) => {
                const m = byKey[a.agent];
                return (
                  <li key={a.agent} className="flex items-start gap-2 text-[13px]">
                    {m && <AgentAvatar agent={m.key} accent={m.accent} status="waiting" size={22} />}
                    <span><b>{m ? m.persona : a.agent}</b> <span className="text-muted">informed: {a.why}</span></span>
                  </li>
                );
              })}
            </ul>
          )}
          {c.diff && <DiffView diff={c.diff} />}
        </div>
      )}
    </li>
  );
}
