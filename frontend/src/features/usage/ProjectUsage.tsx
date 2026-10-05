import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { BarList, KpiRow, LedgerTable, PriceTable, agentIcon, agentName } from "../../components/UsageKit";
import { Skeleton } from "../../components/ui";
import { usageApi, usd } from "../../lib/usage";

/** Project → Usage: tokens and exact cost by agent and by model, budget, and every call. */
export function ProjectUsage({ projectId }: { projectId: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["usage", projectId], queryFn: () => usageApi.project(projectId), refetchInterval: 15000 });
  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-28" /><Skeleton className="h-72" /></div>;
  const pct = Math.min(1, data.totals.cost_usd / data.budget_usd);
  return (
    <div className="space-y-4">
      <KpiRow t={data.totals} />
      <div className="sheen elev rounded-[20px] border border-line bg-surface/60 p-4">
        <div className="mb-2 flex items-baseline justify-between text-sm">
          <span className="font-display font-semibold">Budget</span>
          <span className="tabular-nums"><b>{usd(data.totals.cost_usd)}</b> <span className="text-muted">of ${data.budget_usd.toFixed(2)} · {Math.round(pct * 100)}% used</span></span>
        </div>
        <div className="h-2.5 rounded-full bg-bg-2">
          <motion.div className="h-full rounded-full" initial={{ width: 0 }} animate={{ width: `${Math.max(1, pct * 100)}%` }}
            style={{ background: pct > 0.9 ? "var(--danger)" : pct > 0.7 ? "var(--warning)" : "var(--primary)" }} />
        </div>
        <p className="mt-1.5 text-[11px] text-muted">Agents pause and ask you before going over budget.</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <BarList title="Cost by agent" rows={data.by_agent} label={(r) => agentName(r.agent)} icon={(r) => agentIcon(r.agent)} />
        <BarList title="Cost by model" rows={data.by_model} label={(r) => data.models[r.model_key]?.label ?? r.model_key} />
      </div>
      <LedgerTable calls={data.calls} models={data.models} />
      <PriceTable models={data.models} />
    </div>
  );
}
