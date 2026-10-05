import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRight, BookOpen, CheckCircle2, ChevronDown, HelpCircle, Info, ListChecks, ScanSearch, ShieldAlert, XCircle,
} from "lucide-react";
import { useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { AgentLive, asDate, useNow } from "../../components/AgentLive";
import { CodeBlock } from "../../components/Markdown";
import { Skeleton } from "../../components/ui";
import type { Agent } from "../../lib/api";
import { flowApi, type Mapping, type MappingSample, type Proof } from "../../lib/flow";

const STEPS = [
  { key: "read", label: "Reading the requirement & samples" },
  { key: "write", label: "Writing rows, rules & worked examples" },
  { key: "check", label: "Checking examples against the table" },
];

/** Atlas's workspace: live while he works; afterwards the field map, the worked examples, rules and queries. */
export function MappingTab({ projectId, agents, onOpenDocs }: { projectId: string; agents: Agent[]; onOpenDocs: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["mapping", projectId], queryFn: () => flowApi.mapping(projectId),
    refetchInterval: (q) => (q.state.data?.status === "working" ? 2500 : false),
  });
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-96" /></div>;

  const atlas = agents.find((a) => a.key === "ba");
  if (data.status === "working") return <Working projectId={projectId} activity={data.activity} startedAt={atlas?.started_at ?? null} revising={!!data.mapping} />;
  if (!data.mapping) {
    return (
      <div className="flex flex-col items-center rounded-[28px] border border-line bg-surface px-6 py-16 text-center">
        <AgentAvatar agent="ba" accent="emerald" status="waiting" size={64} />
        <h3 className="mt-4 font-display text-xl font-semibold">Atlas starts after you approve Orion's plan</h3>
        <p className="mt-1 max-w-lg text-sm text-muted">Atlas (BA/DA) turns the signed-off requirement into the formal data-mapping document: field-by-field rules, validation rules and <b className="text-text">worked examples</b> (happy path and error cases). He writes no code: Dev does that later and turns the examples into tests.</p>
      </div>
    );
  }
  return <MappingView m={data.mapping} cost={data.cost_usd} onOpenDocs={onOpenDocs} />;
}

function Working({ projectId, activity, startedAt, revising }: { projectId: string; activity: string; startedAt: string | null; revising: boolean }) {
  const now = useNow();
  const secs = startedAt ? (now - asDate(startedAt).getTime()) / 1000 : 0;
  return (
    <AgentLive projectId={projectId} agent="ba" title={revising ? "Atlas is revising the data mapping" : "Atlas is writing the data mapping"}
      activity={activity} startedAt={startedAt} steps={STEPS.map((s) => s.label)}
      step={/checking/i.test(activity) ? 2 : secs > 20 ? 1 : 0}
      hint="Usually 2–6 minutes: writing a full mapping document takes the model a while, so quiet stretches are normal." />
  );
}

function MappingView({ m, cost, onOpenDocs }: { m: Mapping; cost: number; onOpenDocs: () => void }) {
  const passed = m.proof.filter((p) => p.pass).length;
  const allPass = passed === m.proof.length && m.proof.length > 0;
  const forDev = m.proof.filter((p) => p.pass && p.verified === false).length;
  const pii = m.rows.filter((r) => r.pii).length;
  const legacy = !!m.transform_code;
  return (
    <div className="space-y-4">
      {/* hero */}
      <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="spotlight sheen elev relative overflow-hidden rounded-[28px] border border-line bg-surface p-5 sm:p-7">
        <div className="pointer-events-none absolute -right-24 -top-28 h-80 w-80 rounded-full opacity-20" style={{ background: "radial-gradient(closest-side, var(--success), transparent)" }} />
        <div className="relative flex flex-wrap items-start gap-4">
          <AgentAvatar agent="ba" accent="emerald" status="done" size={52} />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Atlas · BA/DA · data mapping</p>
            <h2 className="font-display text-2xl font-bold leading-tight">{m.title}</h2>
            <p className="mt-1.5 max-w-3xl text-sm text-muted">{m.summary}</p>
          </div>
          <button onClick={onOpenDocs} className="inline-flex items-center gap-1.5 rounded-[12px] border border-line px-3 py-2 text-sm hover:border-primary hover:text-primary">
            <BookOpen className="h-4 w-4" />Open 01_data_mapping.md
          </button>
        </div>
        <div className="relative mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Flow" value={`${m.source.format} → ${m.target.format}`} sub={`${m.direction} · ${m.source.message_name} → ${m.target.message_name}`} />
          <Stat label="Fields" value={String(m.rows.length)} sub={`${m.rows.filter((r) => r.target_moc === "M").length} mandatory · ${pii} personal data`} />
          <Stat label="Worked examples" value={`${passed}/${m.proof.length}`}
            sub={allPass ? (forDev ? `agree with the table · ${forDev} for Dev/Quinn` : "all agree with the mapping table") : "some disagree"}
            tone={allPass ? "var(--success)" : "var(--danger)"} />
          <Stat label="Cost" value={`$${cost.toFixed(3)}`} sub={legacy ? "earlier Atlas version" : `${m.check_attempts ?? 1} check${(m.check_attempts ?? 1) === 1 ? "" : "s"} · no code written`} />
        </div>
        {m.changes && <p className="relative mt-4 rounded-[12px] bg-primary/10 px-3 py-2 text-sm"><b>What changed:</b> {m.changes}</p>}
        {legacy && (
          <p className="relative mt-4 flex items-start gap-2 rounded-[12px] border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <span>This mapping was written by the earlier Atlas, who also wrote and ran code to test it. That code is <b>not</b> part of the mapping and is hidden here: writing code is Dev's job. The rows, rules and examples are unaffected. To have Atlas redo it the new way, use <b>Change request</b>.</span>
          </p>
        )}
      </motion.section>

      {/* field map */}
      <Section icon={<ListChecks className="h-4 w-4" />} title="Field mapping" hint="Source → target, with the exact rule for each field">
        <div className="divide-y divide-line">
          {m.rows.map((r, i) => (
            <motion.div key={r.target} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
              className="grid gap-3 py-3.5 md:grid-cols-[minmax(0,1fr)_28px_minmax(0,1fr)_minmax(0,1.6fr)] md:items-start">
              <Endpoint path={r.source_path} sample={r.source_sample} moc={r.source_moc} type={r.source_type} />
              <ArrowRight className="hidden h-5 w-5 self-center text-primary-2 md:block" />
              <Endpoint path={r.target} sample={r.target_sample} moc={r.target_moc} type={r.target_type} pii={r.pii} strong />
              <div className="text-sm">
                <p className="leading-snug">
                  {r.rule_kind && <span className="mr-1.5 rounded bg-bg-2 px-1.5 py-0.5 align-middle font-mono text-[10px] uppercase text-muted">{r.rule_kind}</span>}
                  {r.logic}
                </p>
                {r.comments && <p className="mt-1 text-xs text-muted">{r.comments}</p>}
              </div>
            </motion.div>
          ))}
        </div>
      </Section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        {/* proof */}
        <Section icon={<ScanSearch className="h-4 w-4" />} title="Worked examples" hint="Each one checked against the mapping table. Dev turns them into unit tests; Quinn into test cases">
          <div className="space-y-2">
            {m.samples.map((s) => <SampleRow key={s.name} s={s} p={m.proof.find((x) => x.name === s.name)} />)}
          </div>
        </Section>

        <div className="space-y-4">
          <Section icon={<ShieldAlert className="h-4 w-4" />} title="Validation & filter rules">
            <ul className="space-y-2.5">
              {m.rules.map((r) => (
                <li key={r.rule} className="rounded-[12px] bg-bg-2/70 p-3 text-sm">
                  <p className="font-semibold">{r.rule}</p>
                  <p className="text-muted">When {r.condition}</p>
                  <p className="mt-0.5"><span className="text-muted">→</span> {r.action}</p>
                </li>
              ))}
            </ul>
          </Section>
          {(m.queries.length > 0 || m.assumptions.length > 0) && (
            <Section icon={<HelpCircle className="h-4 w-4" />} title="Assumptions & queries">
              {m.queries.length > 0 && (
                <ul className="mb-3 space-y-1.5 text-sm">
                  {m.queries.map((q) => <li key={q.question} className="rounded-[10px] border border-warning/35 bg-warning/[0.07] px-3 py-2">❓ {q.question} <span className="text-xs text-muted">· {q.owner}</span></li>)}
                </ul>
              )}
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted">{m.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            </Section>
          )}
        </div>
      </div>

    </div>
  );
}

function Endpoint({ path, sample, moc, type, pii, strong }: { path: string; sample: string; moc: string; type: string; pii?: boolean; strong?: boolean }) {
  const none = (v: string) => !v || v.trim() === "-" || v.trim() === "—";
  if (!strong && none(path)) {  // a constant or a generated value: nothing comes from the source message
    return (
      <div className="grid min-h-[58px] place-items-center rounded-[12px] border border-dashed border-line px-3 py-2 text-center text-[12px] text-muted">
        <span><span className="mr-1">✦</span>set by the flow, not from the source</span>
      </div>
    );
  }
  return (
    <div className={clsx("min-w-0 rounded-[12px] border px-3 py-2", strong ? "border-primary/35 bg-primary/[0.06]" : "border-line bg-bg-2/60")}>
      <p className="truncate font-mono text-[13px] font-semibold" title={path}>{path}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
        <span className="rounded bg-surface px-1.5 font-mono" title="Mandatory / Conditional / Optional">{moc}</span>
        <span>{type}</span>
        {sample && <span className="truncate">· e.g. <span className="font-mono text-text">{sample}</span></span>}
        {pii && <span className="rounded bg-warning/15 px-1.5 font-semibold text-warning">PII</span>}
      </div>
    </div>
  );
}

function SampleRow({ s, p }: { s: MappingSample; p?: Proof }) {
  const [open, setOpen] = useState(false);
  const forDev = p?.pass && p.verified === false;
  return (
    <div className={clsx("rounded-[14px] border", p?.pass ? "border-line" : "border-danger/45")}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left"
        title={p?.actual}>
        {forDev ? <ScanSearch className="h-5 w-5 shrink-0 text-muted" /> : p?.pass ? <CheckCircle2 className="h-5 w-5 shrink-0 text-success" /> : <XCircle className="h-5 w-5 shrink-0 text-danger" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px] font-semibold">{s.name}</span>
          <span className="block truncate text-xs text-muted">{s.description}</span>
        </span>
        <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", s.expect === "output" ? "bg-success/12 text-success" : "bg-warning/15 text-warning")}>
          {s.expect === "output" ? "transforms" : "rejects"}
        </span>
        <ChevronDown className={clsx("h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="grid gap-3 border-t border-line p-3 lg:grid-cols-2">
              <div><p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Input</p><CodeBlock raw={s.input} /></div>
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">{s.expect === "output" ? "Expected output" : "Rejected with"}</p>
                {s.expect === "output" ? <CodeBlock raw={s.expected} /> : <p className="rounded-[10px] bg-warning/10 px-3 py-2 font-mono text-sm">{s.expected}</p>}
                {p && (
                  <p className={clsx("mt-2 rounded-[10px] px-3 py-2 text-[13px]", !p.pass ? "bg-danger/10 text-danger" : forDev ? "bg-bg-2 text-muted" : "bg-success/10")}>
                    <b>{!p.pass ? "Disagrees: " : forDev ? "For Dev & Quinn: " : "Check: "}</b>{p.actual}
                  </p>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: string }) {
  return (
    <div className="rounded-[16px] border border-line bg-bg-2/60 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-0.5 truncate font-display text-xl font-bold" style={tone ? { color: tone } : undefined}>{value}</p>
      <p className="truncate text-xs text-muted">{sub}</p>
    </div>
  );
}

function Section({ icon, title, hint, children }: { icon: React.ReactNode; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="sheen elev rounded-[24px] border border-line bg-surface p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h3 className="flex items-center gap-2 font-display text-lg font-semibold">{icon}{title}</h3>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      {children}
    </section>
  );
}
