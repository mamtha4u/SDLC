import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Loader2, ScanSearch } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type InfraState } from "../../lib/flow";
import { timeAgo } from "../../lib/time";

/** "Check for drift" in the project header (user, 10-04: "besides the 30-minute watch, one button the user can click to
 *  check whether there's any drift; if there is, let him put the original back or keep it through a change request").
 *  Shown once the project is in AWS. Terra's check changes nothing in AWS; the result and the choices (Restore / Keep /
 *  Leave) open on the AWS tab. */
export function DriftButton({ projectId, onOpen, compact = false }: { projectId: string; onOpen: () => void; compact?: boolean }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["infra", projectId], queryFn: () => flowApi.infra(projectId),
    refetchInterval: (q) => ((q.state.data as InfraState | undefined)?.busy ? 4000 : 30000),
  });
  if (!data || (data.status !== "deployed" && data.status !== "partial")) return null;
  const checking = data.busy === "tp.drift" || data.busy === "tp.restore";
  const found = data.drift?.status === "found" && !data.drift.clean;

  const check = async () => {
    if (found) return onOpen();
    try {
      await flowApi.refreshInfra(projectId);
      toast.success("Terra is checking for drift", {
        description: "Every resource, setting and function's code in AWS against the Terraform state and Dev's packages. Nothing changes in AWS; about a minute. The result opens on the AWS tab.",
      });
      qc.invalidateQueries({ queryKey: ["infra", projectId] });
      onOpen();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't start the check"); }
  };
  const title = found ? "Something was changed in AWS outside Orkestra: see it and choose Restore or Keep"
    : `Compare AWS with the source of truth now${data.drift?.checked_at ? ` (last check ${timeAgo(data.drift.checked_at)})` : ""}`;
  return (
    <Button size={compact ? "sm" : undefined} variant="neu" onClick={check} disabled={checking || (!!data.busy && !found)} title={title}
      className={clsx(found && "border-warning/60 bg-warning/10 text-warning", compact && "hidden md:inline-flex")}
      icon={checking ? <Loader2 className="h-4 w-4 animate-spin" /> : found ? <AlertTriangle className="h-4 w-4" /> : <ScanSearch className="h-4 w-4" />}>
      {checking ? "Checking AWS…" : found ? "Drift found" : "Check for drift"}
    </Button>
  );
}
