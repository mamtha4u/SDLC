import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { FolderKanban } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { BarList, DailyBars, KpiRow, PriceTable, agentIcon, agentName } from "../components/UsageKit";
import { Skeleton } from "../components/ui";
import { usageApi } from "../lib/usage";

const RANGES = [7, 30, 90];

/** Global usage across all of the user's projects: totals, by project / agent / model, spend per day. */
export function UsagePage() {
  const [days, setDays] = useState(30);
  const nav = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["usage-all", days], queryFn: () => usageApi.overall(days), refetchInterval: 30000 });

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display font-bold tracking-tight" style={{ fontSize: "var(--fs-h1)" }}>Usage & cost</h1>
          <p className="mt-1 text-sm text-muted">Every token the crew used across all your projects, at exact prices.</p>
        </div>
        <div className="neu-inset flex rounded-[14px] p-1" role="group" aria-label="Time range">
          {RANGES.map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={clsx("relative h-9 rounded-[10px] px-4 text-sm font-medium", days === d ? "text-text" : "text-muted")}>
              {days === d && <motion.span layoutId="usage-range" className="neu-sm absolute inset-0 rounded-[10px]" />}
              <span className="relative">{d} days</span>
            </button>
          ))}
        </div>
      </div>

      {isLoading || !data ? (
        <div className="space-y-3"><Skeleton className="h-28" /><Skeleton className="h-56" /><Skeleton className="h-72" /></div>
      ) : (
        <>
          <KpiRow t={data.totals} />
          <DailyBars daily={data.daily} days={days} />
          <div className="grid gap-4 xl:grid-cols-3">
            <BarList title="Cost by project" rows={data.by_project} label={(r) => r.name}
              icon={() => <FolderKanban className="h-4 w-4 text-muted" />} />
            <BarList title="Cost by agent" rows={data.by_agent} label={(r) => agentName(r.agent)} icon={(r) => agentIcon(r.agent)} />
            <BarList title="Cost by model" rows={data.by_model} label={(r) => data.models[r.model_key]?.label ?? r.model_key} />
          </div>
          {data.by_project.length > 0 && (
            <div className="sheen elev rounded-[20px] border border-line bg-surface/60 p-4">
              <h3 className="mb-3 font-display font-semibold">Projects</h3>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead><tr className="text-left text-xs text-muted">
                    {["Project", "Calls", "Input", "Cached", "Output", "Cost"].map((h, i) => <th key={h} className={`pb-2 font-medium ${i ? "text-right" : ""}`}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {data.by_project.map((p) => (
                      <tr key={p.project_id} onClick={() => nav(`/projects/${p.project_id}`)} className="cursor-pointer border-t border-line tabular-nums hover:bg-surface-2/50">
                        <td className="py-2 font-medium">{p.name}</td><td className="py-2 text-right">{p.calls}</td>
                        <td className="py-2 text-right">{p.input_tokens.toLocaleString()}</td><td className="py-2 text-right">{p.cache_read_tokens.toLocaleString()}</td>
                        <td className="py-2 text-right">{p.output_tokens.toLocaleString()}</td><td className="py-2 text-right font-semibold">${p.cost_usd.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <PriceTable models={data.models} />
        </>
      )}
    </div>
  );
}
