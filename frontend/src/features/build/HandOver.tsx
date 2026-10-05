import clsx from "clsx";
import { LayoutGroup, motion } from "framer-motion";
import { ArrowRight, CheckCircle2, Cloud, FileText, Play, ShieldCheck, UserCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import type { Approval, BuildState, HandOverPackage } from "../../lib/flow";
import { timeAgo } from "../../lib/time";

/** Terra deploys everything (user, 10-03: "we need to see the movement in the website: Dev giving the codebase and
 *  layers to Terra, and Terra removing the fake code and deploying the real code"). Dev packs → Archie reviewed → Terra
 *  plans and applies (your approval) → live in AWS. The package chips travel along the lane as the work moves on. */
type Stop = 0 | 1 | 2 | 3;
const STOPS: { label: string; agent: string; accent: string }[] = [
  { label: "Dev packs", agent: "de", accent: "blue" },
  { label: "Archie reviewed", agent: "ta", accent: "amber" },
  { label: "Terra deploys", agent: "tp", accent: "orange" },
  { label: "Live in AWS", agent: "aws", accent: "emerald" },
];

export function HandOver({ b, approvals, onOpenFile }: { b: BuildState; approvals: Approval[]; onOpenFile: (p: string) => void }) {
  const ho = b.handover;
  const [replay, setReplay] = useState<Stop | null>(null);
  useEffect(() => {
    if (replay === null) return;
    const t = window.setTimeout(() => setReplay(replay < 3 ? ((replay + 1) as Stop) : null), replay === 3 ? 1400 : 900);
    return () => window.clearTimeout(t);
  }, [replay]);
  if (!ho || ho.mode !== "terra") return null;
  const images = ho.images ?? [];
  const all = [...ho.code, ...ho.layers, ...images.map((p) => ({ ...p, image: true }))];
  const tp = b.infra.state, de = b.code.state;
  const reviewed = b.code.review?.verdict === "approve" || approvals.some((a) => a.stage === "code_review" && a.status === "approved");
  const gate = approvals.find((a) => a.stage === "deploy" && a.status === "pending") && ho.planned;
  const terraBusy = tp?.status === "working" && ho.pending;
  const packing = de?.status === "working" && /📦|packing/i.test(de.activity);
  const withTerra = all.some((p) => p.status === "with_terra");
  const now: Stop = withTerra || ho.pending ? 2 : all.length && all.every((p) => p.status === "live") ? 3 : reviewed ? 1 : 0;
  const shown: Stop = replay ?? now;
  const caption = replay !== null ? ["Dev zips each function's src/ folder (runtime code only) and builds the layers",
    "Archie reviewed the code and you approved it", "Terra plans the swap and runs terraform apply after your go",
    "Dev's code replaced the placeholder: one Terraform state for code, layers and infrastructure"][replay]
    : packing ? "Dev is packing his code and layers for Terra"
    : gate ? "Terra's plan is ready: approve it in the bar above and Terra swaps Dev's code in"
    : terraBusy ? (/apply|🚀/i.test(tp?.activity ?? "") ? "Terra is running terraform apply: the placeholder is being replaced" : "Terra is planning the deploy of Dev's packages")
    : now === 3 ? "Dev's packages are live. Code, layers and infrastructure share one Terraform state: a console edit shows up as drift"
    : now === 1 ? "Reviewed: Dev hands his packages to Terra next" : "Dev hands his packages to Terra after Archie's review and yours";
  return (
    <div className="relative overflow-hidden rounded-[20px] border border-line bg-surface p-4">
      <div className="pointer-events-none absolute -left-16 -top-20 h-52 w-52 rounded-full blur-3xl" style={{ background: "color-mix(in srgb, var(--primary) 14%, transparent)" }} />
      <div className="relative flex flex-wrap items-center gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">The hand-over</p>
        <p className="text-sm font-semibold">Dev's code and layers, deployed by Terra</p>
        <div className="ml-auto flex items-center gap-1.5">
          {ho.plan && <button onClick={() => onOpenFile("reports/deploy_plan.md")}
            className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-[11px] font-semibold hover:border-primary hover:text-primary">
            <FileText className="h-3 w-3" />Terra's plan</button>}
          <button onClick={() => setReplay(0)} disabled={replay !== null} title="Watch the packages travel from Dev to AWS"
            className="press inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-[11px] font-semibold text-muted hover:border-primary hover:text-primary disabled:opacity-50">
            <Play className="h-3 w-3" />Replay</button>
        </div>
      </div>

      <LayoutGroup id="handover">
        <div className="no-scrollbar relative mt-3 overflow-x-auto pb-1">
          <ol className="relative grid min-w-[640px] grid-cols-4">
            <span className="absolute left-[12.5%] right-[12.5%] top-[22px] h-[3px] rounded-full bg-bg-2" />
            <span className="absolute left-[12.5%] top-[22px] h-[3px] overflow-hidden rounded-full bg-[linear-gradient(90deg,var(--primary),var(--success))]"
              style={{ width: `calc(75% * ${shown / 3})`, transition: "width 0.8s cubic-bezier(.2,.7,.2,1)" }}>
              <span className="rail-flow block h-full w-full opacity-80" />
            </span>
            {STOPS.map((s, i) => {
              const reached = i <= shown;
              const here = i === shown;
              const needsYou = i === 2 && gate && replay === null;
              const busy = replay === null && ((i === 2 && terraBusy) || (i === 0 && packing));
              return (
                <li key={s.label} className="relative flex flex-col items-center text-center">
                  <span className={clsx("relative grid h-11 w-11 place-items-center rounded-full border-2 bg-surface transition-colors", (busy || needsYou) && "pulse-ring")}
                    style={{ borderColor: needsYou ? "var(--warning)" : reached ? (i === 3 ? "var(--success)" : "var(--primary)") : "var(--border)",
                      ["--ring" as string]: needsYou ? "var(--warning)" : "var(--primary-2)" }}>
                    {s.agent === "aws" ? <Cloud className={clsx("h-5 w-5", reached ? "text-success" : "text-muted")} />
                      : <AgentAvatar agent={s.agent} accent={s.accent} status={busy ? "working" : reached ? "done" : "waiting"} size={34} plain />}
                    {i === 1 && reviewed && <ShieldCheck className="absolute -bottom-1 -right-1 h-4 w-4 rounded-full bg-surface text-success" />}
                  </span>
                  <span className={clsx("mt-1.5 text-[12px] font-semibold", reached ? "text-text" : "text-muted")}>{s.label}</span>
                  <span className="text-[10.5px] text-muted">{needsYou ? <b className="text-warning">needs your approval</b> : i === 2 && ho.plan ? planLine(ho.plan.counts) : i === 3 && now === 3 ? "one Terraform state" : " "}</span>
                  <div className="mt-2 flex min-h-[30px] flex-wrap justify-center gap-1">
                    {here && all.map((p) => <Chip key={`${"image" in p ? "i" : p.function_name ? "c" : "l"}-${p.key}`} p={p} live={i === 3} />)}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      </LayoutGroup>

      <motion.p key={caption} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
        className={clsx("relative mt-1 flex items-center gap-1.5 rounded-[12px] px-3 py-2 text-xs", gate && replay === null ? "bg-warning/10" : "bg-bg-2/60")}>
        {gate && replay === null ? <UserCheck className="h-3.5 w-3.5 shrink-0 text-warning" /> : <ArrowRight className="h-3.5 w-3.5 shrink-0 text-primary" />}{caption}</motion.p>

      {(ho.code.length > 0 || images.length > 0) && (
        <ul className="relative mt-3 space-y-1.5">{[...ho.code, ...images].map((p) => <Swap key={`${p.uri ? "i" : "c"}-${p.key}`} p={p} />)}</ul>
      )}
      {ho.layers.length > 0 && (
        <p className="relative mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted">
          <span className="font-semibold text-text">Layers:</span>
          {ho.layers.map((p) => (
            <span key={p.key} className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5", p.status === "live" ? "border-success/40 text-text" : "border-line")}>
              📦 <code>{p.name ?? p.key}</code> · {p.kb} KB · from <code>layers/{p.key}/</code>
              {p.status === "live" ? <CheckCircle2 className="h-3 w-3 text-success" /> : <span className="text-warning">with Terra</span>}</span>
          ))}
        </p>
      )}
    </div>
  );
}

function planLine(c: Record<string, number>) {
  return `plan: +${c.create ?? 0} ~${(c.update ?? 0) + (c.replace ?? 0)} −${c.delete ?? 0}`;
}

function Chip({ p, live }: { p: HandOverPackage & { image?: boolean }; live: boolean }) {
  const image = !!p.image;
  const code = !image && !!p.function_name;
  return (
    <motion.span layoutId={`pkg-${image ? "i" : code ? "c" : "l"}-${p.key}`} transition={{ type: "spring", stiffness: 140, damping: 20 }}
      className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold shadow-sm",
        live ? "border-success/40 bg-success/10 text-success" : "border-primary/40 bg-primary/10 text-primary")}
      title={image ? `${p.source_dir}/Dockerfile → ${p.function_name ?? p.key} (ECR image)` : code ? `${p.source_dir}/ → ${p.function_name}` : `layers/${p.key}/`}>
      {image ? "🐳" : code ? "⚡" : "📦"} {code || image ? p.source_dir?.split("/").pop() ?? p.key : p.key}
      {!image && <span className="font-normal opacity-70">{p.kb} KB</span>}
    </motion.span>
  );
}

/** One function: what runs in it now, and what Terra's apply puts there. */
function Swap({ p }: { p: HandOverPackage }) {
  const live = p.status === "live";
  const before = live ? null : p.applied_version ?? "placeholder";
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-[12px] border border-line bg-bg-2/30 px-3 py-2 text-xs">
      <code className="min-w-0 break-all font-semibold">{p.function_name}</code>
      <span className="ml-auto flex flex-wrap items-center gap-1.5">
        {before && <span className={clsx("rounded-full px-2 py-0.5", before === "placeholder" ? "bg-danger/10 text-danger line-through decoration-2" : "bg-bg-2 text-muted line-through")}
          title={before === "placeholder" ? "Terra's placeholder: answers 503 'Code not deployed yet'" : "the code that's live now"}>
          {before === "placeholder" ? "placeholder (503)" : `Dev's ${before}`}</span>}
        {before && <ArrowRight className="h-3.5 w-3.5 text-muted" />}
        <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold", live ? "bg-success/15 text-success" : "bg-primary/10 text-primary")}>
          {p.uri ? "🐳" : "⚡"} Dev's {live ? p.applied_version ?? p.version : p.version}{" "}
          <span className="font-normal opacity-75">{p.uri ? `image from ${p.source_dir}/Dockerfile` : `from ${p.source_dir}/ · ${p.kb} KB`}</span></span>
        <span className="text-muted">{live ? (p.adopted ? "taken over from the live code" : `live ${timeAgo(p.applied_at)}`) : "after Terra's apply"}</span>
      </span>
    </li>
  );
}
