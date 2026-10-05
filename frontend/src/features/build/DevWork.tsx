import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Braces, CheckCircle2, ChevronDown, Download, ExternalLink, FileCode2, FlaskConical, Gauge, Layers, ListChecks, TriangleAlert, Wrench, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import type { BuildState } from "../../lib/flow";

type CodeData = NonNullable<BuildState["code"]["data"]>;

/** Dev's code and unit tests at a glance (user, 10-03: "the Dev area looks clumsy; do something beautiful; the coverage
 *  report download must be obvious"): four tiles, the reports front and centre, coverage per file, tests per module. */
export function DevWork({ projectId, data, onOpenFile }: { projectId: string; data: CodeData; onOpenFile: (p: string) => void }) {
  const t = data.tests;
  const covOk = data.coverage >= data.coverage_gate;
  const src = data.files.filter((f) => f.startsWith("src/"));
  const tests = data.files.filter((f) => f.startsWith("tests/") && !f.startsWith("tests/fixtures"));
  const layers = data.files.filter((f) => f.startsWith("layers/"));
  const reports = data.reports ?? [];
  const dl = (p: string) => `/api/projects/${projectId}/files/content?path=${encodeURIComponent(p)}&download=1`;
  const failed = (t?.failed ?? 0) + (t?.error ?? 0);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">{data.summary}</p>

      {/* the four numbers */}
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <Tile icon={<FlaskConical className="h-4 w-4" />} label="Unit tests" tone={failed ? "var(--danger)" : "var(--success)"}
          value={t ? `${t.passed}/${t.total}` : "–"} sub={t ? (failed ? `${failed} failing` : "all passing") : "not run"} ring={t?.total ? t.passed / t.total : 0} />
        <Tile icon={<Gauge className="h-4 w-4" />} label="Coverage" tone={covOk ? "var(--success)" : "var(--danger)"}
          value={`${data.coverage}%`} sub={`gate ${data.coverage_gate}% ${covOk ? "✓ met" : "✗ not met"}`} ring={data.coverage / 100} mark={data.coverage_gate / 100} />
        <Tile icon={<Braces className="h-4 w-4" />} label="Code" tone="var(--primary)" value={String(src.length)}
          sub={`file${src.length === 1 ? "" : "s"} in src/${layers.length ? ` · ${layers.length} in layers/` : ""}`} />
        <Tile icon={<Wrench className="h-4 w-4" />} label="Version" tone="var(--primary-2)" value={data.version}
          sub={`${data.test_runs} test run${data.test_runs === 1 ? "" : "s"} in the sandbox`} />
      </div>

      {/* the reports, impossible to miss */}
      <div className="grid gap-2.5 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="relative overflow-hidden rounded-[18px] border border-primary/40 bg-primary/[0.06] p-4">
          <div className="pointer-events-none absolute -right-10 -top-12 h-36 w-36 rounded-full blur-2xl" style={{ background: "color-mix(in srgb, var(--primary) 22%, transparent)" }} />
          <p className="relative flex items-center gap-2 font-display font-semibold"><Gauge className="h-4 w-4 text-primary" />Coverage report</p>
          <p className="relative mt-0.5 text-xs text-muted">Every file with its tested and untested lines highlighted, plus every test: the pytest-cov HTML report.</p>
          {reports.includes("reports/coverage.html") ? (
            <div className="relative mt-3 flex flex-wrap gap-2">
              <a href={dl("reports/coverage.html")} className="press inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-sm font-semibold text-on-primary shadow-[0_8px_20px_-10px_var(--primary)] hover:opacity-90">
                <Download className="h-4 w-4" />Download (.html)</a>
              <button onClick={() => onOpenFile("reports/coverage.html")} className="press inline-flex items-center gap-1.5 rounded-full border border-primary/50 px-3.5 py-1.5 text-sm font-semibold text-primary hover:bg-primary/10">
                <ExternalLink className="h-4 w-4" />Open it here</button>
            </div>
          ) : <p className="relative mt-3 text-xs text-muted">It appears after Dev's next test run.</p>}
        </div>
        <div className="rounded-[18px] border border-line p-4">
          <p className="flex items-center gap-2 font-semibold"><ListChecks className="h-4 w-4 text-primary" />More reports</p>
          <ul className="mt-2 space-y-1.5">
            {([["reports/pytest.md", "Test results", "every test and its outcome"], ["reports/code_review.md", "Archie's code review", "verdict, findings, recommendations"],
              ["reports/code_deploy.md", "Deploy & full-flow test", "what's live, what Dev's test sent and received"],
              ["reports/lambda_test_event.json", "Lambda test event", "paste into the Lambda console's Test tab"]] as const).filter(([p]) => reports.includes(p) || p === "reports/code_review.md").map(([p, label, hint]) => (
              <li key={p} className="flex items-center gap-2 text-sm">
                <button onClick={() => onOpenFile(p)} className="min-w-0 flex-1 truncate text-left hover:text-primary"><b className="font-medium">{label}</b> <span className="text-xs text-muted">· {hint}</span></button>
                <a href={dl(p)} aria-label={`Download ${label}`} title="Download" className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted hover:bg-bg-2 hover:text-primary"><Download className="h-3.5 w-3.5" /></a>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {(data.changes || data.fixed_bugs?.length > 0) && (
        <div className="grid gap-2 md:grid-cols-2">
          {data.fixed_bugs?.length > 0 && <p className="rounded-[14px] bg-success/10 px-3.5 py-2.5 text-sm"><b>Fixed:</b> {data.fixed_bugs.join(", ")}</p>}
          {data.changes && <p className="rounded-[14px] bg-primary/10 px-3.5 py-2.5 text-sm"><b>What changed:</b> {data.changes}</p>}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {t?.coverage && <CoverageByFile files={t.coverage.files} gate={data.coverage_gate} onOpen={onOpenFile} />}
        {t && <TestsByModule tests={t.tests} />}
      </div>

      {data.notes.length > 0 && (
        <Section title={`Dev's notes (${data.notes.length})`} icon={<FileCode2 className="h-4 w-4" />}>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted">{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </Section>
      )}

      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="mr-1 font-semibold text-muted">Open:</span>
        {[...src, ...layers.slice(0, 6), ...tests.slice(0, 8)].map((f) => (
          <button key={f} onClick={() => onOpenFile(f)} className="inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-0.5 font-mono text-[11.5px] hover:border-primary hover:text-primary">
            {f.startsWith("layers/") ? <Layers className="h-3 w-3" /> : f.startsWith("tests/") ? <FlaskConical className="h-3 w-3" /> : <Braces className="h-3 w-3" />}{f.replace(/^(src|tests|layers)\//, "")}</button>
        ))}
      </div>
    </div>
  );
}

function Tile({ icon, label, value, sub, tone, ring, mark }: { icon: React.ReactNode; label: string; value: string; sub: string; tone: string; ring?: number; mark?: number }) {
  const r = 19, c = 2 * Math.PI * r;
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-3 rounded-[18px] border border-line bg-bg-2/40 p-3">
      {ring !== undefined ? (
        <svg viewBox="0 0 48 48" className="h-12 w-12 shrink-0 -rotate-90" aria-hidden>
          <circle cx="24" cy="24" r={r} fill="none" stroke="var(--border)" strokeWidth="5" />
          <motion.circle cx="24" cy="24" r={r} fill="none" stroke={tone} strokeWidth="5" strokeLinecap="round" strokeDasharray={c}
            initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - Math.min(1, ring)) }} transition={{ duration: 1, ease: [0.2, 0.7, 0.2, 1] }} />
          {mark !== undefined && <line x1="24" y1="2" x2="24" y2="9" stroke="var(--text)" strokeWidth="2" transform={`rotate(${mark * 360} 24 24)`} />}
        </svg>
      ) : <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full" style={{ color: tone, background: `color-mix(in srgb, ${tone} 14%, transparent)` }}>{icon}</span>}
      <div className="min-w-0">
        <p className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-muted">{ring !== undefined && icon}{label}</p>
        <p className="font-display text-xl font-bold leading-tight">{value}</p>
        <p className="truncate text-[11.5px]" style={{ color: tone === "var(--danger)" ? tone : "var(--text-muted)" }}>{sub}</p>
      </div>
    </motion.div>
  );
}

/** One thin bar per file, sorted lowest first; the gate is a tick on every bar. Hover shows the untested lines. */
function CoverageByFile({ files, gate, onOpen }: { files: Record<string, { percent: number; missing: number[] }>; gate: number; onOpen: (p: string) => void }) {
  const rows = useMemo(() => Object.entries(files).sort((a, b) => a[1].percent - b[1].percent), [files]);
  const [hover, setHover] = useState<string | null>(null);
  return (
    <Section title={`Coverage by file (${rows.length})`} icon={<Gauge className="h-4 w-4" />} open>
      <ul className="space-y-2">
        {rows.map(([p, f]) => (
          <li key={p} onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)} className="text-xs">
            <div className="flex items-center gap-2">
              <button onClick={() => onOpen(p)} className="min-w-0 flex-1 truncate text-left font-mono text-[11.5px] hover:text-primary" title={p}>{p.replace(/^src\//, "")}</button>
              <span className={clsx("w-11 shrink-0 text-right font-semibold", f.percent < gate ? "text-danger" : "text-text")}>{f.percent}%</span>
            </div>
            <div className="relative mt-1 h-1.5 rounded-full bg-bg-2">
              <motion.div className="h-full rounded-full" initial={{ width: 0 }} animate={{ width: `${Math.min(100, f.percent)}%` }} transition={{ duration: 0.7 }}
                style={{ background: f.percent >= gate ? "var(--success)" : "var(--danger)" }} />
              <span className="absolute -top-0.5 h-2.5 w-0.5 rounded bg-text/60" style={{ left: `${gate}%` }} title={`gate ${gate}%`} />
            </div>
            <AnimatePresence>
              {hover === p && f.missing.length > 0 && (
                <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mt-1 text-[11px] text-muted">
                  Untested lines: {f.missing.slice(0, 24).join(", ")}{f.missing.length > 24 ? "…" : ""}</motion.p>
              )}
            </AnimatePresence>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** Tests grouped by their module (tests/test_x.py), pass/fail at a glance; failures open by default. */
function TestsByModule({ tests }: { tests: { id: string; outcome: string; message: string }[] }) {
  const groups = useMemo(() => {
    const m: Record<string, typeof tests> = {};
    for (const t of tests) (m[t.id.split("::")[0]] ??= []).push(t);
    return Object.entries(m).sort((a, b) => a[0].localeCompare(b[0]));
  }, [tests]);
  return (
    <Section title={`Tests by module (${tests.length})`} icon={<FlaskConical className="h-4 w-4" />} open>
      <ul className="space-y-1.5">{groups.map(([mod, ts]) => <Module key={mod} mod={mod} tests={ts} />)}</ul>
    </Section>
  );
}

function Module({ mod, tests }: { mod: string; tests: { id: string; outcome: string; message: string }[] }) {
  const bad = tests.filter((t) => t.outcome !== "passed" && t.outcome !== "skipped").length;
  const [open, setOpen] = useState(bad > 0);
  return (
    <li className="rounded-[12px] border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs">
        {bad ? <XCircle className="h-3.5 w-3.5 shrink-0 text-danger" /> : <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />}
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{mod.replace(/^tests\//, "")}</span>
        <span className="flex shrink-0 gap-0.5">{tests.slice(0, 24).map((t) => (
          <span key={t.id} className={clsx("h-2.5 w-1.5 rounded-sm", t.outcome === "passed" ? "bg-success/70" : t.outcome === "skipped" ? "bg-muted/40" : "bg-danger")} title={t.id.split("::").pop()} />
        ))}</span>
        <span className="w-10 shrink-0 text-right text-muted">{tests.length - bad}/{tests.length}</span>
        <ChevronDown className={clsx("h-3.5 w-3.5 shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul className="space-y-0.5 border-t border-line px-2.5 py-1.5">{tests.map((t) => (
          <li key={t.id} className="flex items-start gap-1.5 text-[11.5px]">
            {t.outcome === "passed" ? <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-success" /> : t.outcome === "skipped" ? <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-muted" /> : <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-danger" />}
            <span className="min-w-0"><span className="break-all font-mono">{t.id.split("::").slice(1).join("::")}</span>
              {t.message && t.outcome !== "passed" && <span className="block break-words text-danger">{t.message.slice(0, 300)}</span>}</span>
          </li>
        ))}</ul>
      )}
    </li>
  );
}

function Section({ title, icon, open: initial = false, children }: { title: string; icon: React.ReactNode; open?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="rounded-[16px] border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm font-semibold">
        <span className="text-primary">{icon}</span>{title}<ChevronDown className={clsx("ml-auto h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="max-h-[420px] overflow-y-auto border-t border-line p-3.5">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
