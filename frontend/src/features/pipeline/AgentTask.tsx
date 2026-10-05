import { useQueryClient } from "@tanstack/react-query";
import { ClipboardPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button, Modal } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type Ticket } from "../../lib/flow";

/** What happens to a task you give each agent (user, 10-02: "hey TA, change this in the LLD" from the agent's own tab). */
const HOW: Record<string, { area: Ticket["area"]; steps: string[]; example: string }> = {
  ta: { area: "design", example: "Add a section to the LLD on how the DLQ is monitored, with the alarm threshold.",
    steps: ["A ticket for Archie, and a change request Orion triages", "Archie updates the HLD / LLD / diagram (documents only? nothing downstream is redone)", "You approve the result: the ticket gets his comment and is closed"] },
  ba: { area: "design", example: "Map the partner's <Notes> field to notes (optional, max 500 chars).",
    steps: ["A ticket for Atlas, and a change request Orion triages", "Atlas revises the mapping and its worked examples", "You approve it: the ticket gets his comment and is closed"] },
  intake: { area: "other", example: "The partner now also sends a <Priority> element: add it to the requirement.",
    steps: ["A ticket for Echo, and a change request Orion triages", "Echo updates the requirement with you (a new version)", "When it's signed off and done, the ticket is commented and closed"] },
  cto: { area: "other", example: "Re-check the plan: can we use Python 3.12 instead of 3.14?",
    steps: ["A ticket for Orion, and a change request", "Orion researches and revises the plan (with sources)", "You approve it: the ticket is commented and closed"] },
  de: { area: "code", example: "Add docstrings to every public function in src/transform/handler.py.",
    steps: ["A ticket for Dev: Orion hands it over when the crew is free", "Dev changes the code, Archie reviews it with you, Terra deploys it and Dev tests it", "Quinn retests it live and closes the ticket"] },
  tp: { area: "infra", example: "Set the orders queue's retention to 7 days.",
    steps: ["A ticket for Terra: Orion hands it over when the crew is free", "Terra changes the Terraform; you approve the plan, then the apply", "Quinn retests it live and closes the ticket"] },
  qa: { area: "other", example: "Also test an order with 200 line items.",
    steps: ["A ticket for Quinn", "He checks it on the live flow, with evidence", "He comments and closes it, or raises a bug for the fixer"] },
};

export function AgentTask({ projectId, agent, persona, open, onClose }: { projectId: string; agent: string; persona: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const how = HOW[agent];
  if (!how) return null;
  const send = async () => {
    const t = text.trim();
    setBusy(true);
    try {
      const first = t.split("\n")[0];
      const r = await flowApi.newTicket(projectId, { title: first.length > 120 ? `${first.slice(0, 117)}…` : first, description: t, steps: "", expected: "",
        actual: "", severity: "minor", area: how.area, assignee: agent });
      toast.success(`${r.label} for ${persona}` + (r.routed?.startsWith("change request") ? `: ${r.routed} raised` : r.routed === "dispatched" ? ": handed over" : ""));
      ["tickets", "changes", "crew", "agent"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
      setText(""); onClose();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't create the task"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title={`Give ${persona} a task`} width={600}>
      <div className="space-y-3">
        <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={5} placeholder={`e.g. ${how.example}`}
          className="w-full resize-y rounded-[14px] border border-line bg-bg-2 px-3.5 py-2.5 text-sm outline-none focus:border-primary" />
        <ol className="grid gap-2 sm:grid-cols-3">
          {how.steps.map((s, i) => (
            <li key={s} className="flex gap-2 rounded-[12px] bg-bg-2/70 px-2.5 py-2 text-[12px]"><span className="font-bold text-primary">{i + 1}</span><span className="text-muted">{s}</span></li>
          ))}
        </ol>
        <p className="text-xs text-muted">It's tracked as a ticket on the Tickets tab, with every step in its history.</p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<ClipboardPlus className="h-4 w-4" />} loading={busy} disabled={text.trim().length < 3} onClick={send}>Give it to {persona}</Button>
        </div>
      </div>
    </Modal>
  );
}
