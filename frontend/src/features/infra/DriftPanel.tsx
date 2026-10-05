import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, ChevronDown, Code2, FileText, Hourglass, RefreshCw, RotateCcw, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Button } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type DriftReport, type InfraState } from "../../lib/flow";
import { celebrate } from "../../lib/fx";
import { timeAgo } from "../../lib/time";

/** Terra's drift watch (user, 10-03: "if someone modifies the Lambda code in the console, deletes the Lambda or a layer, or
 *  changes the SQS configuration … Terra must inform the user"). The Terraform state and Dev's packages are the source of
 *  truth. Restore applies them directly (no Dev or Quinn steps); Keep turns the console changes into a change request. */
export function DriftPanel({ projectId, data, onRefresh, onOpenFile }: {
  projectId: string; data: InfraState; onRefresh: () => void; onOpenFile: (p: string) => void;
}) {
  const qc = useQueryClient();
  const { data: approvals } = useQuery({ queryKey: ["approvals", projectId], queryFn: () => flowApi.approvals(projectId) });
  const gate = approvals?.find((a) => a.stage === "drift" && a.status === "pending");
  const [keep, setKeep] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const d = data.drift;
  const every = data.watch_minutes ?? 0;
  const checking = data.busy === "tp.drift" || data.busy === "tp.restore";

  const decide = async (how: "approve" | "changes" | "accept", origin?: Element | null) => {
    if (!gate) return;
    setBusy(how);
    try {
      await flowApi.decide(projectId, gate.id, how, how === "changes" ? (keep ?? "").trim() || "Keep these changes: they're intended." : "");
      if (how === "approve") { celebrate(origin ?? null, false); toast.success("Terra is restoring AWS from the Terraform state", { description: "Only the restore plan, then Dev's code where it changed. No other step." }); }
      else if (how === "changes") toast.success("Keeping the changes: Orion and Terra update the Terraform (and Dev the code)");
      else toast.success("Left as it is. Terra tells you if anything else changes");
      setKeep(null);
      ["infra", "approvals", "project", "crew", "changes", "tickets", "build"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setBusy(null); }
  };

  const watchLine = every > 0 ? `Terra compares AWS with the Terraform state and Dev's packages every ${every} min` : "Terra compares AWS with the Terraform state and Dev's packages when you ask";
  if (!d || d.clean || d.status === "clean" || (d.status !== "found" && !gate)) {
    const restored = d?.status === "restored" || d?.status === "partly";
    return (
      <div className={clsx("flex flex-wrap items-center gap-3 rounded-[18px] border px-4 py-2.5", restored ? "border-success/40 bg-success/[0.06]" : "border-line bg-surface")}>
        <span className="relative grid h-8 w-8 place-items-center rounded-full bg-success/15">
          <ShieldCheck className="h-4 w-4 text-success" />
          {every > 0 && <span className="sonar absolute inset-0 rounded-full border border-success/50" />}
        </span>
        <div className="min-w-0 flex-1 text-sm">
          <p className="font-semibold">{checking ? "Terra is checking AWS…" : restored ? `Restored ${timeAgo(d!.restored?.at)}: ${d!.restored?.line}`
            : d?.status === "keeping" ? `Keeping the console changes: ${d.kept?.as.join(", ") || "in progress"}`
            : d?.status === "left" ? `Changes left in AWS ${timeAgo(d.left_at)} (you chose “Leave it for now”)`
            : d ? `No drift: AWS matches the source of truth (${d.total} resources${d.mode === "terra" ? " and every function's code" : ""})` : "Drift watch is on"}</p>
          <p className="text-xs text-muted">{watchLine}{d ? ` · last check ${timeAgo(d.checked_at)}${d.auto ? " (automatic)" : ""}` : ""}
            {d?.error && <span className="text-danger"> · last check failed: {d.error.slice(0, 120)}</span>}</p>
        </div>
        <Button size="sm" variant="primary" icon={<RefreshCw className={clsx("h-3.5 w-3.5", checking && "animate-spin")} />} disabled={!!data.busy} onClick={onRefresh}
          title="Terra reads every resource and every function's code from AWS and compares them with the source of truth (nothing changes)">{checking ? "Checking…" : "Check for drift now"}</Button>
      </div>
    );
  }
  return <Found d={d} gate={!!gate} busy={busy} keep={keep} setKeep={setKeep} decide={decide} onOpenFile={onOpenFile} watchLine={watchLine} />;
}

function Found({ d, gate, busy, keep, setKeep, decide, onOpenFile, watchLine }: {
  d: DriftReport; gate: boolean; busy: string | null; keep: string | null; setKeep: (v: string | null) => void;
  decide: (how: "approve" | "changes" | "accept", origin?: Element | null) => void; onOpenFile: (p: string) => void; watchLine: string;
}) {
  const c = d.restore?.counts;
  return (
    <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className="beam-border relative overflow-hidden rounded-[24px] border border-warning/50 bg-surface p-5" style={{ ["--beam-1" as string]: "var(--warning)" }}>
      <div className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--warning) 16%, transparent)" }} />
      <div className="relative flex flex-wrap items-center gap-3">
        <span className="relative grid h-11 w-11 place-items-center rounded-full bg-warning/15"><AlertTriangle className="h-5 w-5 text-warning" />
          <span className="pulse-ring absolute inset-0 rounded-full" style={{ ["--ring" as string]: "var(--warning)" }} /></span>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-warning">Drift · changed outside Terraform</p>
          <h3 className="font-display text-lg font-bold tracking-tight">Someone changed this project in AWS</h3>
          <p className="text-xs text-muted">Found {timeAgo(d.checked_at)}{d.auto ? " by the automatic check" : ""} · {watchLine}</p>
        </div>
        <button onClick={() => onOpenFile("reports/drift.md")} className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-xs font-semibold hover:border-primary hover:text-primary">
          <FileText className="h-3.5 w-3.5" />Report</button>
      </div>

      <div className="relative mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="space-y-3">
          {d.settings.length > 0 && (
            <div className="rounded-[16px] border border-line p-3">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Settings changed in AWS</p>
              <div className="md"><div className="md-table"><table>
                <thead><tr><th>Resource</th><th>Setting</th><th>Terraform (source of truth)</th><th>Now in AWS</th></tr></thead>
                <tbody>{d.settings.flatMap((s) => s.fields.map((f) => (
                  <tr key={`${s.address}-${f.key}`}><td><code className="text-[11.5px]">{s.name || s.address}</code></td><td>{f.key.replace(/_/g, " ")}</td>
                    {f.lines?.length ? (  // a long value (a policy, a JSON setting): only the lines that differ
                      <td colSpan={2}>
                        <p className="mb-1 text-[11px] text-muted">Only what differs (<span className="text-success">− Terraform</span> · <span className="text-warning">+ now in AWS</span>):</p>
                        <pre className="max-h-48 overflow-auto rounded-[8px] bg-bg-2/60 px-2 py-1 font-mono text-[11px] leading-snug">{f.lines.map((ln, i) => (
                          <span key={i} className={clsx("block whitespace-pre-wrap break-all", ln.startsWith("-") ? "text-success" : "text-warning")}>{ln}</span>))}</pre>
                      </td>
                    ) : (<>
                      <td><code className="text-success">{f.terraform}</code></td><td><code className="text-warning">{f.aws}</code></td></>)}</tr>
                )))}</tbody>
              </table></div></div>
            </div>
          )}
          {d.deleted.length > 0 && (
            <div className="rounded-[16px] border border-danger/30 bg-danger/[0.04] p-3">
              <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-danger"><Trash2 className="h-3.5 w-3.5" />Deleted in AWS</p>
              <ul className="flex flex-wrap gap-1.5">{d.deleted.map((x) => (
                <li key={x.address} className="rounded-full bg-surface px-2.5 py-1 text-xs"><code>{x.name || x.address}</code> <span className="text-muted">({x.type.replace(/^aws_/, "").replace(/_/g, " ")})</span></li>
              ))}</ul>
            </div>
          )}
          {d.code.map((f) => <CodeDrift key={f.function_name} f={f} />)}
        </div>
        <div className="space-y-3">
          <div className="rounded-[16px] border border-success/30 bg-success/[0.05] p-3 text-sm">
            <p className="flex items-center gap-1.5 font-semibold"><CheckCircle2 className="h-4 w-4 text-success" />Unchanged</p>
            <p className="text-xs text-muted">{d.unchanged} of {d.total} resources match the Terraform state{d.mode === "terra" ? "; every other function runs Dev's package" : ""}.</p>
          </div>
          <div className="rounded-[16px] border border-line p-3 text-sm">
            <p className="flex items-center gap-1.5 font-semibold"><RotateCcw className="h-4 w-4 text-primary" />What Restore does</p>
            <p className="mt-0.5 text-xs text-muted">{c ? <><b className="text-text">terraform apply</b>: +{c.create} ~{c.update + c.replace} −{c.delete}</> : "Nothing for Terraform to change"}
              {d.code.length > 0 && <>, then <b className="text-text">Dev's code goes back</b> into {d.code.map((x) => x.function_name.split("-").pop()).join(", ")}</>}.
              Only these changes: no Dev, Archie or Quinn steps.</p>
          </div>
          <div className="flex items-start gap-2 rounded-[16px] bg-bg-2/50 p-3 text-xs text-muted">
            <AgentAvatar agent="tp" accent="orange" status="needs_approval" size={26} plain />
            <span>A mistake? <b className="text-text">Restore</b>. Intended? <b className="text-text">Keep the changes</b>: Orion and Terra put them in the Terraform{d.code.length ? " and Dev in his code" : ""}, so the source of truth catches up.</span>
          </div>
        </div>
      </div>

      {gate ? (
        <div className="relative mt-4">
          {keep !== null && (
            <textarea autoFocus value={keep} onChange={(e) => setKeep(e.target.value)} rows={2}
              placeholder="Why these changes stay (optional), e.g. we raised the visibility timeout to 45 s on purpose."
              className="mb-2 w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
          )}
          <div className="flex flex-wrap gap-2">
            {keep === null ? (
              <>
                <Button variant="primary" className="shimmer" icon={<RotateCcw className="h-4 w-4" />} loading={busy === "approve"} onClick={(e) => decide("approve", e.currentTarget)}>Restore from Terraform</Button>
                <Button icon={<Code2 className="h-4 w-4" />} onClick={() => setKeep("")}>Keep the changes…</Button>
                <Button variant="ghost" icon={<Hourglass className="h-4 w-4" />} loading={busy === "accept"} onClick={() => decide("accept")}>Leave it for now</Button>
              </>
            ) : (
              <>
                <Button variant="primary" loading={busy === "changes"} onClick={() => decide("changes")}>Keep them: update the source of truth</Button>
                <Button variant="ghost" onClick={() => setKeep(null)}>Cancel</Button>
              </>
            )}
          </div>
        </div>
      ) : <p className="relative mt-3 text-xs text-muted">Terra is working on it…</p>}
    </motion.section>
  );
}

function CodeDrift({ f }: { f: DriftReport["code"][number] }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-[16px] border border-warning/40 p-3">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 text-left">
        <Code2 className="h-4 w-4 text-warning" />
        <span className="min-w-0 flex-1 text-sm"><b>Code changed in AWS:</b> <code className="text-[12px]">{f.function_name}</code>
          <span className="block text-xs text-muted">should run Dev's {f.expected_version ?? "package"} · changed {f.last_modified ? timeAgo(f.last_modified) : "recently"} · {f.files.length} file(s) differ</span></span>
        <ChevronDown className={clsx("h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="mt-2 space-y-2">{f.files.map((x, i) => (
              <div key={i} className="overflow-hidden rounded-[10px] border border-line">
                {x.file && <p className="flex items-center gap-2 bg-bg-2/60 px-2.5 py-1 text-xs"><code>{x.file}</code><span className="text-muted">{x.change}</span></p>}
                <pre className="max-h-64 overflow-auto px-2.5 py-1.5 font-mono text-[11px] leading-snug">{x.diff.split("\n").map((line, j) => (
                  <span key={j} className={clsx("block whitespace-pre-wrap break-all", line.startsWith("+") && !line.startsWith("+++") ? "bg-success/10 text-success"
                    : line.startsWith("-") && !line.startsWith("---") ? "bg-danger/10 text-danger" : line.startsWith("@@") ? "text-primary" : "text-muted")}>{line || " "}</span>
                ))}</pre>
              </div>
            ))}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
