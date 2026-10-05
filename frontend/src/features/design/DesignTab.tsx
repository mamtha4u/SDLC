import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { BookOpen, Boxes, Download, ExternalLink, FileUp, GitCompareArrows, Lightbulb, Network, Send, Server, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { AgentLive, asDate, useNow } from "../../components/AgentLive";
import { DrawioViewer } from "../../components/DrawioViewer";
import { Markdown } from "../../components/Markdown";
import { Button, Modal, Skeleton } from "../../components/ui";
import { ApiError, type Agent } from "../../lib/api";
import { flowApi, helpingWho, type Design } from "../../lib/flow";
import { TechStack } from "./TechStack";

const STEPS = ["Reading requirement & mapping", "Writing the HLD & LLD", "Drawing the architecture"];
type Doc = "hld" | "lld" | "decisions" | "resources";

/** Archie's workspace: the architecture diagram (editable in draw.io), the HLD, the LLD, decisions and resources. */
export function DesignTab({ projectId, agents, onOpenFile }: { projectId: string; agents: Agent[]; onOpenFile: (path: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["design", projectId], queryFn: () => flowApi.design(projectId),
    refetchInterval: (q) => (q.state.data?.status === "working" ? 3000 : false),
  });
  const now = useNow(data?.status === "working");
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-[520px]" /></div>;
  const archie = agents.find((a) => a.key === "ta");
  // Archie also helps others (unblocking Dev or Terra, advice, code reviews): only design work shows the design progress
  if (data.status === "working" && !/helping|shoulder|review|question|answer/i.test(data.activity ?? "")) {
    const secs = data.started_at ? (now - asDate(data.started_at).getTime()) / 1000 : 0;
    return (
      <AgentLive projectId={projectId} agent="ta" title={data.design ? "Archie is revising the design" : "Archie is designing the solution"}
        activity={data.activity} startedAt={data.started_at ?? archie?.started_at ?? null} steps={STEPS}
        step={/submit|draw/i.test(data.activity) ? 2 : secs > 25 ? 1 : 0}
        hint="Usually 3–8 minutes: the HLD and LLD are long documents, written in one go." />
    );
  }
  if (!data.design || !data.drawio) {
    return (
      <div className="flex flex-col items-center rounded-[28px] border border-line bg-surface px-6 py-16 text-center">
        <AgentAvatar agent="ta" accent="amber" status="waiting" size={64} />
        <h3 className="mt-4 font-display text-xl font-semibold">Archie starts after you approve Atlas's mapping</h3>
        <p className="mt-1 max-w-lg text-sm text-muted">Archie (TA) writes the <b className="text-text">HLD</b> and <b className="text-text">LLD</b> in your team's format and draws the architecture with real AWS icons. You can edit the diagram in draw.io and upload it back; Archie updates the documents to match.</p>
      </div>
    );
  }
  const helps = archie ? helpingWho(archie) : null;
  return (
    <div className="space-y-4">
      {helps && (  // he's unblocking a teammate, not changing this design (10-05)
        <div className="flex items-center gap-3 rounded-[18px] border border-primary-2/40 bg-surface px-4 py-3 text-sm">
          <AgentAvatar agent="ta" accent="amber" status="working" size={32} />
          <p className="min-w-0 flex-1"><b>Archie isn't changing the design.</b> <span className="text-muted">He's helping {helps === "de" ? "Dev" : "Terra"} fix a
            problem ({archie!.activity.replace(/^\W+\s*/, "")}); the design below stays as you approved it.</span></p>
        </div>
      )}
      <DesignView projectId={projectId} d={data.design} xml={data.drawio} editorUrl={data.editor_url} cost={data.cost_usd} onOpenFile={onOpenFile} />
    </div>
  );
}

function DesignView({ projectId, d, xml, editorUrl, cost, onOpenFile }: {
  projectId: string; d: Design; xml: string; editorUrl: string | null; cost: number; onOpenFile: (p: string) => void;
}) {
  const [doc, setDoc] = useState<Doc>("hld");
  const [upload, setUpload] = useState(false);
  const a = d.architecture;
  const shapes = a.sources.length + a.path.length + a.destinations.length + a.support.length;
  const download = () => {
    const url = URL.createObjectURL(new Blob([xml], { type: "application/xml" }));
    Object.assign(document.createElement("a"), { href: url, download: "architecture.drawio" }).click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="space-y-4">
      <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="spotlight sheen elev relative overflow-hidden rounded-[28px] border border-line bg-surface p-5 sm:p-7">
        <div className="pointer-events-none absolute -right-24 -top-28 h-80 w-80 rounded-full opacity-20" style={{ background: "radial-gradient(closest-side, var(--warning), transparent)" }} />
        <div className="relative flex flex-wrap items-start gap-4">
          <AgentAvatar agent="ta" accent="amber" status="done" size={52} />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Archie · TA · design</p>
            <h2 className="font-display text-2xl font-bold leading-tight">{a.title}</h2>
            <p className="mt-1.5 max-w-3xl text-sm text-muted">{d.summary}</p>
          </div>
        </div>
        <div className="relative mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Components" value={String(shapes)} sub={`${a.edges.length} connections`} />
          <Stat label="AWS resources" value={String(d.resources.length)} sub="for Terra to build" />
          <Stat label="Decisions" value={String(d.decisions.length)} sub={`${d.open_points.length} open point(s)`} />
          <Stat label="Cost" value={`$${cost.toFixed(3)}`} sub="Archie so far" />
        </div>
        {d.changes && <p className="relative mt-4 rounded-[12px] bg-primary/10 px-3 py-2 text-sm"><b>What changed:</b> {d.changes}</p>}
      </motion.section>

      <section className="sheen elev overflow-hidden rounded-[24px] border border-line bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <h3 className="mr-auto flex items-center gap-2 font-display text-lg font-semibold"><Network className="h-4 w-4" />Architecture diagram</h3>
          <Button size="sm" icon={<Download className="h-3.5 w-3.5" />} onClick={download}>.drawio</Button>
          {editorUrl && (
            <a href={editorUrl} target="_blank" rel="noreferrer"
              className="inline-flex h-8 items-center gap-1.5 rounded-[12px] border border-line px-3 text-xs font-medium hover:border-primary hover:text-primary">
              <ExternalLink className="h-3.5 w-3.5" />Edit in diagrams.net
            </a>
          )}
          <Button size="sm" variant="primary" icon={<FileUp className="h-3.5 w-3.5" />} onClick={() => setUpload(true)}>Upload edited diagram</Button>
        </div>
        <DrawioViewer xml={xml} />
        <p className="border-t border-line px-4 py-2 text-xs text-muted">
          Edit it in draw.io (or open the .drawio in the desktop app), save, then <b className="text-text">Upload edited diagram</b>: Archie sees exactly what you changed and updates the HLD and LLD to match. Your drawing is kept as you drew it.
        </p>
      </section>

      <QualityGates projectId={projectId} gates={d.quality_gates} />
      <TechStack projectId={projectId} />

      <section className="sheen elev rounded-[24px] border border-line bg-surface">
        <div className="no-scrollbar flex gap-1 overflow-x-auto border-b border-line p-2">
          {([["hld", "02_hld.md", BookOpen], ["lld", "03_lld.md", Server], ["decisions", `Decisions (${d.decisions.length})`, Lightbulb],
            ["resources", `Resources (${d.resources.length})`, Boxes]] as const).map(([k, l, Icon]) => (
            <button key={k} onClick={() => setDoc(k)}
              className={clsx("relative flex shrink-0 items-center gap-1.5 rounded-[10px] px-3.5 py-2 text-sm font-medium", doc === k ? "text-text" : "text-muted hover:text-text")}>
              {doc === k && <motion.span layoutId="design-doc" className="absolute inset-0 rounded-[10px] bg-bg-2" />}
              <Icon className="relative h-4 w-4" /><span className="relative">{l}</span>
            </button>
          ))}
          <button onClick={() => onOpenFile(doc === "lld" ? "03_lld.md" : "02_hld.md")}
            className="ml-auto shrink-0 rounded-[10px] px-3 py-2 text-xs text-muted hover:text-primary">Open in Code view ›</button>
        </div>
        <div className="p-5 sm:p-6">
          {doc === "hld" && <Markdown>{d.hld_markdown}</Markdown>}
          {doc === "lld" && <Markdown>{d.lld_markdown}</Markdown>}
          {doc === "decisions" && (
            <ul className="space-y-2.5">
              {d.decisions.map((x) => (
                <li key={x.decision} className="rounded-[14px] border border-line bg-bg-2/50 p-3.5 text-sm">
                  <p className="font-semibold">{x.decision}</p>
                  <p className="mt-0.5 text-muted">{x.why}</p>
                  {x.alternatives && <p className="mt-1 text-xs text-muted"><b>Considered:</b> {x.alternatives}</p>}
                </li>
              ))}
              {d.open_points.length > 0 && (
                <li className="rounded-[14px] border border-warning/40 bg-warning/[0.07] p-3.5 text-sm">
                  <p className="font-semibold">Open points</p>
                  <ul className="mt-1 list-disc pl-5">{d.open_points.map((o) => <li key={o}>{o}</li>)}</ul>
                </li>
              )}
            </ul>
          )}
          {doc === "resources" && (
            <div className="md"><div className="md-table"><table>
              <thead><tr><th>Name</th><th>Type</th><th>Purpose</th><th>Key settings</th></tr></thead>
              <tbody>{d.resources.map((r) => (
                <tr key={r.name}><td><code>{r.name}</code></td><td><code>{r.type}</code></td><td>{r.purpose}</td><td>{r.key_settings}</td></tr>
              ))}</tbody>
            </table></div></div>
          )}
        </div>
      </section>
      <UploadDiagram projectId={projectId} open={upload} onClose={() => setUpload(false)} />
    </div>
  );
}

function UploadDiagram({ projectId, open, onClose }: { projectId: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [summary, setSummary] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try { setSummary((await flowApi.uploadDiagram(projectId, f)).summary); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Upload failed"); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  const apply = async () => {
    setBusy(true);
    try {
      await flowApi.applyDiagram(projectId, note);
      toast.success("Sent to Archie. He's updating the HLD and LLD to match your drawing");
      ["design", "approvals", "project", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
      setSummary(null); setNote(""); onClose();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title="Upload your edited diagram" width={560}>
      <input ref={input} type="file" hidden accept=".drawio,.xml" onChange={(e) => pick(e.target.files?.[0])} />
      {!summary ? (
        <button onClick={() => input.current?.click()} disabled={busy}
          className="flex w-full flex-col items-center gap-2 rounded-[16px] border-2 border-dashed border-line px-4 py-10 text-sm hover:border-primary">
          <FileUp className="h-8 w-8 text-primary" />
          <b>{busy ? "Reading…" : "Choose the .drawio you saved"}</b>
          <span className="text-muted">We compare it with Archie's version and show you what changed first.</span>
        </button>
      ) : (
        <div className="space-y-3">
          <p className="flex items-start gap-2 rounded-[12px] bg-bg-2 p-3 text-sm"><GitCompareArrows className="mt-0.5 h-4 w-4 shrink-0 text-primary" />{summary}</p>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Anything Archie should know about these changes? (optional)"
            className="w-full rounded-[12px] border border-line bg-bg-2 px-3 py-2 text-sm outline-none focus:border-primary" />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setSummary(null)}>Choose another file</Button>
            <Button variant="primary" loading={busy} icon={<Send className="h-4 w-4" />} onClick={apply}>Send to Archie</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Archie's quality gates: the platform enforces them on Dev's code. Editable here directly (no AI call). */
function QualityGates({ projectId, gates }: { projectId: string; gates?: Design["quality_gates"] }) {
  const qc = useQueryClient();
  const current = gates?.min_coverage_percent ?? 70;
  const [value, setValue] = useState(current);
  const [busy, setBusy] = useState(false);
  useEffect(() => setValue(current), [current]);
  const save = async () => {
    setBusy(true);
    try {
      await flowApi.setGates(projectId, value);
      toast.success(`Coverage gate set to ${value}%. It applies to Dev's next submission`);
      ["design", "build", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save"); }
    finally { setBusy(false); }
  };
  const [checklist, setChecklist] = useState(false);
  const rules = gates?.rules ?? [];
  const soon = [
    { name: "CodeScene", what: "Code health score must not drop" },
    { name: "Snyk", what: "No high or critical vulnerabilities in dependencies" },
    { name: "SonarQube", what: "Quality gate: bugs, code smells, duplication" },
  ];
  return (
    <section className="sheen elev rounded-[24px] border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-2 font-display text-lg font-semibold"><ShieldCheck className="h-4 w-4 text-success" />Quality gates</h3>
        <span className="text-sm text-muted">Dev's code must pass every active gate before it reaches you; otherwise Dev reworks it.</span>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {/* the one the user owns: test coverage */}
        <div className="relative overflow-hidden rounded-[18px] border border-success/40 bg-success/[0.06] p-4 xl:col-span-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-success">Active · enforced</span>
            {gates?.set_by === "user" && <span className="rounded-full bg-bg-2 px-2 py-0.5 text-[10.5px] text-muted">set by you</span>}
          </div>
          <p className="mt-1 font-display text-lg font-semibold">Test coverage</p>
          <p className="text-xs text-muted">Share of code lines the unit tests run.</p>
          <div className="mt-3 flex items-center gap-2">
            <span className="text-sm">at least</span>
            <input type="number" min={0} max={100} value={value} onChange={(e) => setValue(Number(e.target.value))} aria-label="Minimum test coverage"
              className="w-20 rounded-[10px] border border-line bg-surface px-2 py-1.5 text-right font-mono text-lg font-semibold outline-none focus:border-primary" />
            <span className="text-lg font-semibold">%</span>
            <Button size="sm" variant="primary" className="ml-auto" loading={busy} disabled={value === current || value < 0 || value > 100} onClick={save}>Save</Button>
          </div>
          <div className="relative mt-3 h-2 rounded-full bg-bg-2">
            <div className="h-full rounded-full bg-[linear-gradient(90deg,var(--success),var(--primary-2))] transition-[width] duration-500" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
          </div>
        </div>
        {soon.map((g) => (
          <div key={g.name} className="rounded-[18px] border border-dashed border-line bg-bg-2/40 p-4 opacity-80">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">Coming soon</span>
            <p className="mt-1 font-display text-lg font-semibold">{g.name}</p>
            <p className="text-xs text-muted">{g.what}</p>
            <span className="mt-3 inline-flex items-center gap-2 text-xs text-muted">
              <span className="relative inline-block h-5 w-9 rounded-full bg-bg-2"><span className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-surface shadow" /></span>off
            </span>
          </div>
        ))}
      </div>
      {rules.length > 0 && (
        <div className="mt-3 rounded-[14px] border border-line">
          <button onClick={() => setChecklist(!checklist)} className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm">
            <b>Archie's test checklist</b><span className="text-muted">· {rules.length} items from the requirement, checked by Dev's and Quinn's tests</span>
            <span className="ml-auto text-xs text-primary-2">{checklist ? "Hide" : "Show"}</span>
          </button>
          {checklist && <ol className="list-decimal space-y-1 border-t border-line py-3 pl-9 pr-4 text-sm">{rules.map((r) => <li key={r}>{r}</li>)}</ol>}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-[16px] border border-line bg-bg-2/60 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-0.5 truncate font-display text-xl font-bold">{value}</p>
      <p className="truncate text-xs text-muted">{sub}</p>
    </div>
  );
}
