import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Boxes, Cloud, Cpu, FlaskConical, Gauge, Layers3, Package, TriangleAlert } from "lucide-react";
import { Skeleton } from "../../components/ui";
import { flowApi } from "../../lib/flow";

/** The tech stack, read from the project's files (user, 10-03: "the language, runtime, packages used, test tools… show
 *  them somewhere; better on TA's page"). Each block says where it came from; nothing is guessed. */
export function TechStack({ projectId }: { projectId: string }) {
  const { data: s, isLoading } = useQuery({ queryKey: ["stack", projectId], queryFn: () => flowApi.stack(projectId) });
  if (isLoading || !s) return <Skeleton className="h-48" />;
  const blocks: { icon: typeof Cpu; title: string; from?: string; items: { k: string; v?: string }[]; empty: string }[] = [
    { icon: Cpu, title: "Language & runtime", from: s.runtime?.from ?? s.language?.from,
      items: [...(s.language ? [{ k: s.language.name, v: s.language.version }] : []),
        ...(s.runtime ? [{ k: "AWS Lambda", v: s.runtime.lambda.join(", ") }, ...(s.runtime.arch ? [{ k: "Architecture", v: s.runtime.arch }] : []),
          ...(s.runtime.memory_mb.length ? [{ k: "Memory", v: s.runtime.memory_mb.map((m) => `${m} MB`).join(", ") }] : []),
          ...(s.runtime.timeout_s.length ? [{ k: "Timeout", v: s.runtime.timeout_s.map((t) => `${t} s`).join(", ") }] : [])] : [])],
      empty: "Known once Terra writes the infrastructure" },
    { icon: Package, title: "Packages", from: "requirements.txt", items: [
      ...s.packages.map((p) => ({ k: p.name, v: `${p.version || "latest"} · ${p.where}` })),
      ...s.imports.aws_sdk.map((n) => ({ k: n, v: "in the Lambda runtime" }))], empty: "Known once Dev writes the code" },
    { icon: Layers3, title: "Your own modules", from: "src/, layers/", items: s.imports.own.map((n) => ({ k: n })), empty: "Known once Dev writes the code" },
    { icon: FlaskConical, title: "Test tools", from: "tests/", items: s.tests.map((t) => ({ k: t.name, v: t.what })), empty: "Known once Dev writes the tests" },
    { icon: Cloud, title: "AWS services", from: "infra/*.tf", items: s.services.map((n) => ({ k: n })), empty: "Known once Terra writes the infrastructure" },
    { icon: Boxes, title: "Infrastructure as code", from: s.iac?.from, items: s.iac ? [{ k: s.iac.tool, v: s.iac.version ?? "" },
      ...s.iac.providers.map((p) => ({ k: p.name, v: p.version }))] : [], empty: "Known once Terra writes the infrastructure" },
    { icon: Gauge, title: "Quality gates", from: "Archie's gates", items: s.gates.coverage ? [{ k: "Coverage", v: `≥ ${s.gates.coverage}%` }, { k: "pytest", v: "every test passes" }] : [],
      empty: "Set on this page" },
  ];
  return (
    <section className="sheen elev rounded-[24px] border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-2 font-display text-lg font-semibold"><Cpu className="h-4 w-4" />Tech stack</h3>
        <span className="text-xs text-muted">read from the project's files: what Terra and Dev actually use, not a plan</span>
      </div>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
        {blocks.map((b, i) => {
          const Icon = b.icon;
          return (
            <motion.div key={b.title} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
              className="rounded-[16px] border border-line bg-bg-2/40 p-3">
              <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted"><Icon className="h-3.5 w-3.5 text-primary" />{b.title}</p>
              {b.items.length ? (
                <ul className="mt-1.5 space-y-1">{b.items.map((x) => (
                  <li key={x.k + (x.v ?? "")} className="flex items-baseline gap-2 text-[13px]">
                    <b className="font-semibold">{x.k}</b>{x.v && <span className="min-w-0 truncate text-xs text-muted" title={x.v}>{x.v}</span>}
                  </li>
                ))}</ul>
              ) : <p className="mt-1.5 text-xs text-muted">{b.empty}</p>}
              {b.from && b.items.length > 0 && <p className="mt-2 font-mono text-[10.5px] text-muted/80">from {b.from}</p>}
            </motion.div>
          );
        })}
      </div>
      {s.imports.undeclared.length > 0 && (
        <p className="mt-3 flex items-start gap-1.5 rounded-[12px] bg-warning/10 px-3 py-2 text-xs">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <span>Imported in the code but in no requirements.txt: <b>{s.imports.undeclared.join(", ")}</b>. Ask Archie in the code review whether a layer should provide it.</span>
        </p>
      )}
    </section>
  );
}
