import { useQueryClient } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, type Project } from "../lib/api";
import { flowApi } from "../lib/flow";
import { Button, Input, Modal } from "./ui";

/** Edit a project's spending cap. If an agent stopped at the old cap, it carries on right after you raise it. */
export function BudgetDialog({ project, open, onClose, blockedAgent }: {
  project: Pick<Project, "id" | "name" | "cost_usd" | "budget_usd">; open: boolean; onClose: () => void; blockedAgent?: string | null;
}) {
  const qc = useQueryClient();
  const [value, setValue] = useState(project.budget_usd);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) setValue(Math.max(project.budget_usd, Math.ceil(project.cost_usd + 5))); }, [open, project.budget_usd, project.cost_usd]);
  const pct = Math.min(1, project.cost_usd / Math.max(0.01, project.budget_usd));

  const save = async () => {
    setSaving(true);
    try {
      await api.patchProject(project.id, { budget_usd: value });
      if (blockedAgent && value > project.cost_usd) {
        await flowApi.retryAgent(project.id, blockedAgent).catch(() => undefined);
      }
      toast.success(`Budget set to $${value.toFixed(2)}${blockedAgent ? ". The crew carries on" : ""}`);
      ["project", "projects", "stats", "usage"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      onClose();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save"); }
    finally { setSaving(false); }
  };

  return (
    <Modal open={open} onClose={onClose} title={`Budget · ${project.name}`} width={460}>
      <div className="space-y-4">
        <div>
          <div className="mb-1.5 flex justify-between text-sm">
            <span className="text-muted">Spent so far</span>
            <span className="tabular-nums"><b>${project.cost_usd.toFixed(2)}</b> <span className="text-muted">of ${project.budget_usd.toFixed(2)}</span></span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-bg-2">
            <div className="h-full rounded-full" style={{ width: `${pct * 100}%`, background: pct > 0.9 ? "var(--danger)" : pct > 0.7 ? "var(--warning)" : "var(--success)" }} />
          </div>
          <p className="mt-1.5 text-xs text-muted">Agents stop and ask you before going over this cap. Every AI call is counted (see the Usage tab).</p>
        </div>
        <Input label="Budget cap (USD)" type="number" min={1} max={1000} step={1} value={value}
          onChange={(e) => setValue(Number(e.target.value))} />
        <div className="flex flex-wrap gap-2">
          {[5, 10, 20, 50].map((n) => (
            <button key={n} onClick={() => setValue(Math.min(1000, Math.round((project.budget_usd + n) * 100) / 100))}
              className="rounded-full border border-line px-3 py-1 text-xs font-semibold hover:border-primary hover:text-primary">+${n}</button>
          ))}
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={saving} disabled={!(value >= 1 && value <= 1000)} icon={<Wallet className="h-4 w-4" />} onClick={save}>
            Save budget
          </Button>
        </div>
      </div>
    </Modal>
  );
}
