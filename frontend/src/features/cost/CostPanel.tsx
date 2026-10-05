import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, CircleDollarSign, ExternalLink, Info, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";
import { AnimatedNumber, Skeleton } from "../../components/ui";
import { flowApi, type CostResource, type PriceTier, type Prices } from "../../lib/flow";
import { ACCENT } from "../../lib/themes";

const VOLUMES = [10_000, 100_000, 1_000_000, 10_000_000, 100_000_000];
const ACCENT_OF: Record<string, string> = { lambda: "orange", apigateway: "violet", sqs: "rose", sns: "rose", logs: "emerald", alarms: "amber", iam: "cyan" };
interface Assume { ms: number; logKb: number; msgKb: number; failPct: number }
interface Line { what: string; math: string; usd: number }
interface Row { r: CostResource; usd: number; driver: string; lines: Line[]; note?: string }

const fmtN = (n: number) => n >= 1e9 ? `${+(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${+(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(1)}K` : `${+n.toFixed(2)}`;
const usd = (n: number) => n === 0 ? "$0" : n < 0.01 ? `$${n.toFixed(4)}` : n < 100 ? `$${n.toFixed(2)}` : `$${Math.round(n).toLocaleString()}`;
const perM = (t: PriceTier[] | undefined) => t?.[0] ? `$${+(t[0].usd * 1e6).toFixed(4)}/M` : "?";

function tiered(qty: number, tiers: PriceTier[] | undefined): number {
  if (!tiers) return 0;
  let cost = 0;
  for (const t of tiers) {
    if (qty <= t.from) break;
    cost += (Math.min(qty, t.to ?? Infinity) - t.from) * t.usd;
  }
  return cost;
}

/** One resource's monthly cost at `n` messages, with the lines that make it up (AWS list prices, their own tiers). */
function price(r: CostResource, n: number, a: Assume, p: Prices["prices"]): Row {
  const t = (k: string) => p[k]?.tiers;
  if (r.model === "lambda") {
    const arm = r.params.arch === "arm64";
    const gb = (r.params.memory_mb ?? 128) / 1024;
    const gbs = n * (a.ms / 1000) * gb;
    const req = tiered(n, t(arm ? "lambda_requests_arm" : "lambda_requests"));
    const comp = tiered(gbs, t(arm ? "lambda_gbs_arm" : "lambda_gbs"));
    return { r, usd: req + comp, driver: `${fmtN(n)} runs × ${a.ms} ms × ${r.params.memory_mb ?? 128} MB`, lines: [
      { what: "Requests", math: `${fmtN(n)} × ${perM(t(arm ? "lambda_requests_arm" : "lambda_requests"))}`, usd: req },
      { what: `Compute (${arm ? "Arm" : "x86"})`, math: `${fmtN(n)} × ${a.ms / 1000} s × ${gb} GB = ${fmtN(gbs)} GB-s × $${t(arm ? "lambda_gbs_arm" : "lambda_gbs")?.[0]?.usd}`, usd: comp }] };
  }
  if (r.model === "apigw_rest" || r.model === "apigw_http") {
    const c = tiered(n, t(r.model));
    return { r, usd: c, driver: `${fmtN(n)} API calls`, lines: [{ what: `${r.model === "apigw_rest" ? "REST" : "HTTP"} API requests`, math: `${fmtN(n)} × ${perM(t(r.model))}`, usd: c }] };
  }
  if (r.model === "sqs") {
    const key = r.params.fifo ? "sqs_fifo" : "sqs_standard";
    const msgs = r.params.dlq ? n * (a.failPct / 100) : n;
    const reqs = msgs * 3 * Math.max(1, Math.ceil(a.msgKb / 64));
    const c = tiered(reqs, t(key));
    return { r, usd: c, driver: r.params.dlq ? `${a.failPct}% of messages fail → ${fmtN(msgs)}` : `${fmtN(msgs)} messages`, lines: [
      { what: `${r.params.fifo ? "FIFO" : "Standard"} requests (send + receive + delete${a.msgKb > 64 ? ", per 64 KB" : ""})`, math: `${fmtN(msgs)} × 3 = ${fmtN(reqs)} × ${perM(t(key))}`, usd: c }],
      note: r.params.dlq ? "A dead-letter queue only costs when messages fail." : undefined };
  }
  if (r.model === "logs") {
    const gbIn = (n * a.logKb) / 1024 / 1024;
    const ret = r.params.retention_days || 365;
    const stored = gbIn * Math.min(ret, 365) / 30;
    const ing = gbIn * (t("logs_ingest")?.[0]?.usd ?? 0);
    const sto = stored * (t("logs_storage")?.[0]?.usd ?? 0);
    return { r, usd: ing + sto, driver: `${fmtN(n)} × ${a.logKb} KB of logs`, lines: [
      { what: "Ingestion", math: `${gbIn.toFixed(3)} GB × $${t("logs_ingest")?.[0]?.usd}/GB`, usd: ing },
      { what: `Storage (kept ${r.params.retention_days ? `${r.params.retention_days} days` : "forever, counted 1 year"})`, math: `${stored.toFixed(3)} GB × $${t("logs_storage")?.[0]?.usd}/GB-month`, usd: sto }] };
  }
  if (r.model === "alarm") return { r, usd: t("alarm")?.[0]?.usd ?? 0.1, driver: "1 alarm", lines: [{ what: "Alarm metric", math: `1 × $${t("alarm")?.[0]?.usd}/month`, usd: t("alarm")?.[0]?.usd ?? 0 }] };
  if (r.model === "sns") {
    const c = tiered(n, t("sns_requests"));
    return { r, usd: c, driver: `${fmtN(n)} publishes`, lines: [{ what: "Publishes (first 1M free)", math: `${fmtN(n)} × $0.50/M after 1M`, usd: c }] };
  }
  if (r.model === "free") return { r, usd: 0, driver: "no charge", lines: [], note: "AWS doesn't charge for this resource itself." };
  return { r, usd: 0, driver: "not priced yet", lines: [], note: "Not in the calculator yet; check the AWS pricing page for this service." };
}

/** What the flow costs per month at any volume, per resource, from AWS's own list prices for eu-west-1. */
export function CostPanel({ projectId, compact }: { projectId: string; compact?: boolean }) {
  const { data, isLoading } = useQuery({ queryKey: ["cost", projectId], queryFn: () => flowApi.cost(projectId), staleTime: 60_000 });
  const [n, setN] = useState(1_000_000);
  const [a, setA] = useState<Assume>({ ms: 200, logKb: 1, msgKb: 4, failPct: 1 });
  const [open, setOpen] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [tune, setTune] = useState(false);
  const rows = useMemo(() => (data ? data.resources.map((r) => price(r, n, a, data.prices.prices)) : []), [data, n, a]);
  const at = (v: number) => (data ? data.resources.reduce((s, r) => s + price(r, v, a, data.prices.prices).usd, 0) : 0);
  if (isLoading || !data) return <Skeleton className="h-64" />;
  const total = rows.reduce((s, x) => s + x.usd, 0);
  const paid = rows.filter((x) => x.usd > 0 || x.r.model === "unpriced").sort((x, y) => y.usd - x.usd);
  const free = rows.filter((x) => x.usd === 0 && x.r.model !== "unpriced");
  const max = Math.max(...paid.map((x) => x.usd), 0.000001);
  const logN = Math.log10(n);
  const published = Object.entries(data.prices.sources).map(([s, v]) => `${s.replace(/^AWS|^Amazon/, "")} ${v.published?.slice(0, 10)}`).join(" · ");

  return (
    <section className={clsx("relative overflow-hidden rounded-[24px] border border-line bg-surface", !compact && "sheen elev")}>
      <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--success) 16%, transparent)" }} />
      <div className="relative flex flex-wrap items-end gap-4 border-b border-line p-5">
        <div className="min-w-[220px] flex-1">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-success"><CircleDollarSign className="h-3.5 w-3.5" />What it costs · eu-west-1</p>
          <p className="mt-1 font-display text-4xl font-bold tracking-tight"><AnimatedNumber value={total} decimals={total < 10 ? 2 : 0} prefix="$" /><span className="ml-1.5 text-base font-medium text-muted">/ month</span></p>
          <p className="text-sm text-muted">at <b className="text-text">{fmtN(n)}</b> messages a month · {n ? usd(total / n * 1e6) : "$0"} per million · {n ? `$${(total / n).toFixed(7)}` : "$0"} per message</p>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {[100_000, 1_000_000, 10_000_000].map((v) => (
            <button key={v} onClick={() => setN(v)} className={clsx("press rounded-[14px] border px-3 py-2 text-left", n === v ? "border-success bg-success/10" : "border-line bg-bg-2/40 hover:border-success/50")}>
              <p className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">{fmtN(v)} / month</p>
              <p className="font-display text-lg font-bold tabular-nums">{usd(at(v))}</p>
            </button>
          ))}
        </div>
      </div>

      <div className="relative space-y-3 border-b border-line p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">Messages per month</span>
          {VOLUMES.map((v) => (
            <button key={v} onClick={() => setN(v)} className={clsx("press rounded-full border px-3 py-1 text-xs font-semibold", n === v ? "border-success bg-success/15 text-text" : "border-line text-muted hover:text-text")}>{fmtN(v)}</button>
          ))}
          <input type="number" min={0} value={n} onChange={(e) => setN(Math.max(0, Number(e.target.value) || 0))} aria-label="Messages per month"
            className="neu-inset h-8 w-36 rounded-[10px] px-2.5 font-mono text-sm outline-none" />
          <button onClick={() => setTune(!tune)} className={clsx("press ml-auto inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold", tune ? "border-primary text-text" : "border-line text-muted")}>
            <SlidersHorizontal className="h-3.5 w-3.5" />Assumptions</button>
        </div>
        <input type="range" min={3} max={9} step={0.01} value={logN} onChange={(e) => setN(Math.round(10 ** Number(e.target.value)))}
          aria-label="Volume" className="w-full accent-[var(--success)]" />
        <AnimatePresence initial={false}>
          {tune && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
              <div className="grid gap-2 pt-1 sm:grid-cols-4">
                <Assumption label="Lambda run time" unit="ms" value={a.ms} onChange={(v) => setA({ ...a, ms: v })} hint="Average per message. After Quinn's live tests, the logs show the real number." />
                <Assumption label="Logs per message" unit="KB" value={a.logKb} onChange={(v) => setA({ ...a, logKb: v })} hint="What the function writes to CloudWatch per message." />
                <Assumption label="Message size" unit="KB" value={a.msgKb} onChange={(v) => setA({ ...a, msgKb: v })} hint="SQS bills every 64 KB as one request." />
                <Assumption label="Failures to the DLQ" unit="%" value={a.failPct} onChange={(v) => setA({ ...a, failPct: v })} hint="Share of messages that end in the dead-letter queue." />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <ul className="relative divide-y divide-line/70">
        {paid.map((x) => {
          const accent = ACCENT[ACCENT_OF[x.r.service] ?? "indigo"];
          const isOpen = open === x.r.address;
          return (
            <li key={x.r.address}>
              <button onClick={() => setOpen(isOpen ? null : x.r.address)} className="grid w-full grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_110px_20px] items-center gap-3 px-5 py-3 text-left hover:bg-bg-2/40">
                <span className="flex min-w-0 items-center gap-2.5">
                  <span className="h-8 w-1.5 shrink-0 rounded-full" style={{ background: accent }} />
                  <span className="min-w-0"><span className="block truncate font-mono text-[12.5px] font-semibold" title={x.r.name}>{x.r.name}</span>
                    <span className="block text-[11px] text-muted">{x.r.kind}</span></span>
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-[11.5px] text-muted">{x.driver}</span>
                  <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-bg-2">
                    <motion.span className="block h-full rounded-full" initial={{ width: 0 }} animate={{ width: `${(x.usd / max) * 100}%` }} transition={{ duration: 0.6 }}
                      style={{ background: `linear-gradient(90deg, ${accent}, color-mix(in srgb, ${accent} 50%, var(--primary-2)))` }} />
                  </span>
                </span>
                <span className="text-right font-display text-base font-bold tabular-nums">{x.r.model === "unpriced" ? "–" : usd(x.usd)}
                  {x.r.terra_usd != null && <span className="block text-[10px] font-normal text-muted" title="Terra's own estimate at the requirement's volume">Terra: ${x.r.terra_usd}</span>}</span>
                <ChevronDown className={clsx("h-4 w-4 text-muted transition-transform", isOpen && "rotate-180")} />
              </button>
              <AnimatePresence initial={false}>
                {isOpen && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <div className="mx-5 mb-3 rounded-[14px] border border-line bg-bg-2/50 p-3">
                      {x.lines.map((l) => (
                        <div key={l.what} className="flex flex-wrap items-baseline gap-x-3 py-1 text-[12.5px]">
                          <span className="w-44 shrink-0 font-semibold">{l.what}</span>
                          <span className="min-w-0 flex-1 font-mono text-[11.5px] text-muted">{l.math}</span>
                          <b className="tabular-nums">{usd(l.usd)}</b>
                        </div>
                      ))}
                      {x.note && <p className="mt-1 flex items-start gap-1.5 text-[11.5px] text-muted"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />{x.note}</p>}
                      {x.lines.length > 0 && <p className="mt-1 text-[11px] text-muted">AWS list price for eu-west-1, with AWS's volume tiers. {x.lines.length > 1 ? `Total ${usd(x.usd)}.` : ""}</p>}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </li>
          );
        })}
      </ul>
      {free.length > 0 && (
        <div className="border-t border-line px-5 py-2.5">
          <button onClick={() => setShowAll(!showAll)} className="text-xs font-semibold text-muted hover:text-text">
            {free.length} resources cost nothing on their own (IAM, API routes and stages, permissions, triggers) {showAll ? "▴" : "▾"}</button>
          {showAll && <p className="mt-1.5 flex flex-wrap gap-1.5">{free.map((x) => <span key={x.r.address} className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[11px] text-muted">{x.r.name}</span>)}</p>}
        </div>
      )}
      <div className="border-t border-line bg-bg-2/40 px-5 py-3 text-[11.5px] text-muted">
        <p>Prices: <a className="inline-flex items-center gap-0.5 text-primary hover:underline" href={Object.values(data.prices.sources)[0]?.url} target="_blank" rel="noreferrer">
          AWS Price List<ExternalLink className="h-3 w-3" /></a>, eu-west-1, published {published}.
          {data.prices.stale && <span className="text-warning"> {data.prices.stale}.</span>}</p>
        <p className="mt-0.5">Before the AWS free tier (it's account-wide and shared with colleagues in this sandbox). Not included: data transfer out of AWS, X-Ray, Dev's layer storage (tiny).
          {data.terra_total > 0 && <> Terra's own estimate at the requirement's volume: <b className="text-text">${data.terra_total.toFixed(2)}/month</b>.</>}</p>
      </div>
    </section>
  );
}

function Assumption({ label, unit, value, onChange, hint }: { label: string; unit: string; value: number; onChange: (v: number) => void; hint: string }) {
  return (
    <label className="block rounded-[12px] border border-line bg-bg-2/40 p-2.5" title={hint}>
      <span className="block text-[11px] font-semibold text-muted">{label}</span>
      <span className="mt-1 flex items-center gap-1.5">
        <input type="number" min={0} step="any" value={value} onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
          className="neu-inset h-8 w-full rounded-[8px] px-2 font-mono text-sm outline-none" />
        <span className="text-xs text-muted">{unit}</span>
      </span>
      <span className="mt-1 block text-[10.5px] leading-tight text-muted">{hint}</span>
    </label>
  );
}
