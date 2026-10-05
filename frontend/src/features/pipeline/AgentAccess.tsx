import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { Check, ChevronDown, FilePen, KeyRound, Lock, ShieldCheck, ShieldOff, SlidersHorizontal, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { CodeBlock } from "../../components/Markdown";
import { Button, Modal, Skeleton } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { flowApi, type AccessInfo, type AccessPlan, type AccessRole, type AwsCall, type PolicyExtra, type PolicyStatement } from "../../lib/flow";

/** Agents that never touch AWS, and what they do instead. */
const NO_AWS: Record<string, string> = {
  intake: "Echo talks with you and writes the requirement. No AWS access at all.",
  ba: "Atlas writes the data mapping, on paper only. No AWS access at all.",
  ta: "Archie designs the HLD, LLD and diagram. No AWS access at all: Terra builds what Archie designs.",
  guide: "Sage only reads this project's files to answer your questions. No AWS access at all.",
};
const STATUS: Record<string, { label: string; cls: string }> = {
  proposed: { label: "Waiting for your approval", cls: "bg-warning/15 text-warning" },
  active: { label: "Active", cls: "bg-success/15 text-success" },
  removed: { label: "Removed", cls: "bg-bg-2 text-muted" },
  blocked: { label: "Blocked", cls: "bg-danger/15 text-danger" },
};

/** "AWS access" in an agent's popup: exactly what this agent may and may not do in AWS, the policy itself, and what it did. */
export function AgentAccess({ projectId, agent, persona, accent }: { projectId: string; agent: string; persona: string; accent: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["access", projectId], queryFn: () => flowApi.access(projectId), refetchInterval: 15000,
    enabled: !(agent in NO_AWS) });
  if (!(agent in NO_AWS) && (isLoading || !data)) return <div className="space-y-3 p-6"><Skeleton className="h-28" /><Skeleton className="h-40" /></div>;
  return <AccessView data={data} agent={agent} persona={persona} accent={accent} projectId={projectId} />;
}

/** The panel itself, from data (also rendered with sample data in the /design gallery, without projectId: read-only). */
export function AccessView({ data, agent, persona, accent, projectId }: { data: AccessInfo | undefined; agent: string; persona: string; accent: string; projectId?: string }) {
  if (agent in NO_AWS || !data) {
    return (
      <Scroll>
        <Hero accent="var(--text-muted)" icon={<ShieldOff className="h-6 w-6" />} kicker="AWS access" title="None" chip={null}>
          <p className="text-sm text-muted">{NO_AWS[agent]}</p>
        </Hero>
        <p className="text-sm text-muted">Only Orion (who creates the roles), Terra (builds), Dev (reads live logs) and Quinn (tests the live flow) ever get AWS access, each with its own role for this project only.</p>
      </Scroll>
    );
  }
  const calls = data.calls.filter((c) => c.agent === agent).reverse();

  if (agent === "cto") {
    const plan = data.plan;
    return (
      <Scroll>
        <Hero accent={accent} icon={<KeyRound className="h-6 w-6" />} kicker="Platform role · Orion holds the higher permission" title={data.platform.role} chip={STATUS.active}>
          <p className="text-sm text-muted">Orion uses it to create one role per agent for this project, with only what each task needs. It can never create a role without the crew boundary.</p>
        </Hero>
        <CanCannot can={data.platform.can} cannot={data.platform.cannot} />
        <Fold title="Platform policy (JSON)"><CodeBlock raw={JSON.stringify(data.platform.policy, null, 2)} /></Fold>
        <Boundary arn={data.boundary.arn} policy={data.boundary.policy} />
        <div>
          <Label>Roles Orion made for this project</Label>
          {plan?.roles.length ? (
            <ul className="space-y-1.5">{plan.roles.map((r) => (
              <li key={r.role} className="flex flex-wrap items-center gap-2 rounded-[12px] bg-bg-2/60 px-3 py-2 text-sm">
                <b className="w-14">{r.persona}</b><code className="min-w-0 flex-1 truncate text-[12.5px]">{r.role}</code><Chip s={plan.status} />
              </li>
            ))}</ul>
          ) : <p className="text-sm text-muted">None yet. Orion drafts them after QA passes, from Terra's Terraform; you approve before any exist.</p>}
          {plan?.problem && <p className="mt-2 rounded-[12px] bg-danger/10 px-3 py-2 text-sm text-danger">{plan.problem}</p>}
        </div>
        <Calls calls={calls} />
      </Scroll>
    );
  }

  const plan = data.plan;
  const role = plan?.roles.find((r) => r.agent === agent);
  if (!plan || !role) {
    return (
      <Scroll>
        <Hero accent={accent} icon={<Lock className="h-6 w-6" />} kicker="AWS access" title="Not created yet" chip={null}>
          <p className="text-sm text-muted">After Quinn's QA passes, Orion drafts {persona}'s own role from Terra's Terraform: only the services this project uses, only names with the project's prefix, only eu-west-1. You see the exact policy here and approve it before it exists.</p>
        </Hero>
        <Boundary arn={data.boundary.arn} policy={data.boundary.policy} />
      </Scroll>
    );
  }
  return (
    <Scroll>
      <Hero accent={accent} icon={<KeyRound className="h-6 w-6" />} kicker={`${persona}'s role for this project`} title={role.role} chip={STATUS[plan.status] ?? null}>
        <p className="text-sm text-muted">{role.purpose}.</p>
        <Facts plan={plan} role={role} />
      </Hero>
      <CanCannot can={role.can} cannot={role.cannot} />
      <YourChanges projectId={projectId} agent={agent} persona={persona} plan={plan} role={role} extra={data.extras?.[agent]} />
      <Fold title="Permissions policy (JSON)" open><CodeBlock raw={JSON.stringify(
        data.extras?.[agent]?.statements.length ? { ...(role.policy as object), Statement: [...((role.policy as { Statement: unknown[] }).Statement), ...data.extras[agent].statements] } : role.policy,
        null, 2)} /></Fold>
      <Boundary arn={data.boundary.arn} policy={data.boundary.policy} />
      <Calls calls={calls} />
    </Scroll>
  );
}

/** Your own changes to this agent's policy: a request Orion checks against the project's rules, shown, then applied. */
function YourChanges({ projectId, agent, persona, plan, role, extra }: {
  projectId?: string; agent: string; persona: string; plan: AccessPlan; role: AccessRole; extra?: PolicyExtra;
}) {
  const [open, setOpen] = useState(false);
  const mine = extra?.statements ?? [];
  return (
    <div className="rounded-[18px] border border-primary/30 bg-primary/[0.04] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <SlidersHorizontal className="h-4 w-4 text-primary" />
        <p className="text-sm font-semibold">Your changes to {persona}'s access</p>
        <span className="text-xs text-muted">{mine.length ? `${mine.length} statement(s) on top of Orion's` : "none: Orion's policy as generated"}</span>
        {projectId && plan.status === "active" && (
          <Button size="sm" variant="primary" className="ml-auto" icon={<FilePen className="h-3.5 w-3.5" />} onClick={() => setOpen(true)}>Request a policy change</Button>
        )}
      </div>
      {mine.length > 0 && (
        <ul className="mt-2.5 space-y-1.5">{mine.map((s, i) => (
          <li key={i} className="rounded-[12px] bg-surface px-3 py-2 text-[12.5px]">
            <span className={clsx("mr-2 rounded-full px-2 py-0.5 text-[10.5px] font-bold", s.Effect === "Allow" ? "bg-success/15 text-success" : "bg-danger/15 text-danger")}>{s.Effect}</span>
            <code>{([] as string[]).concat(s.Action).join(", ")}</code>
            <span className="block break-all pl-1 pt-0.5 font-mono text-[11px] text-muted">on {([] as string[]).concat(s.Resource).join(", ")}</span>
          </li>
        ))}</ul>
      )}
      {extra && <p className="mt-2 text-xs text-muted">Last change by {extra.by}, {new Date(extra.at).toLocaleString()}: “{extra.reason}”{extra.history.length > 1 ? ` · ${extra.history.length} changes in total` : ""}</p>}
      {projectId && <PolicyChangeDialog open={open} onClose={() => setOpen(false)} projectId={projectId} agent={agent} persona={persona} plan={plan} role={role} current={mine} />}
    </div>
  );
}

function PolicyChangeDialog({ open, onClose, projectId, agent, persona, plan, role, current }: {
  open: boolean; onClose: () => void; projectId: string; agent: string; persona: string; plan: AccessPlan; role: AccessRole; current: PolicyStatement[];
}) {
  const qc = useQueryClient();
  const sample: PolicyStatement[] = [{ Effect: "Allow", Action: ["sqs:PurgeQueue"], Resource: [`arn:aws:sqs:${plan.region}:144831534428:${plan.prefix}*`] }];
  const [text, setText] = useState(() => JSON.stringify(current.length ? current : sample, null, 2));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<{ ok: boolean; problems?: string[]; statements?: PolicyStatement[]; policy?: unknown } | null>(null);
  const parse = (): unknown[] | null => {
    try { const v = JSON.parse(text); return Array.isArray(v) ? v : [v]; } catch { setCheck({ ok: false, problems: ["That isn't valid JSON: a list of statements like the example"] }); return null; }
  };
  const run = async (apply: boolean, statements?: unknown[]) => {
    const st = statements ?? parse();
    if (!st) return;
    setBusy(true);
    try {
      const r = await flowApi.policyChange(projectId, agent, st, reason, apply);
      setCheck(r);
      if (r.ok && apply) {
        toast.success(`Applied to ${role.role}: Orion checked it and wrote it`);
        qc.invalidateQueries({ queryKey: ["access", projectId] });
        onClose();
      }
    } catch (e) { setCheck({ ok: false, problems: [e instanceof ApiError ? e.message : "Couldn't reach Orion"] }); }
    finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title={`Change ${persona}'s AWS access`} width={720}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Add permissions (<code>Allow</code>) or take some away (<code>Deny</code>). Orion checks your change first: only this project's
          resources (<code>{plan.prefix}*</code>), only the crew's services, and the crew boundary still caps everything. Then you see the result and confirm.
          Orion's own access is the platform's and can't be changed.</p>
        <label className="block text-sm font-semibold">Why
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={`e.g. ${persona === "Quinn" ? "Quinn needs to purge the test queues between runs" : "needed for …"}`}
            className="mt-1 w-full rounded-[12px] border border-line bg-bg-2 px-3 py-2 text-sm font-normal outline-none focus:border-primary" />
        </label>
        <label className="block text-sm font-semibold">Your statements (JSON list; they replace your earlier ones)
          <textarea value={text} onChange={(e) => { setText(e.target.value); setCheck(null); }} rows={10} spellCheck={false}
            className="mt-1 w-full rounded-[12px] border border-line bg-bg-2 px-3 py-2 font-mono text-[12.5px] font-normal outline-none focus:border-primary" />
        </label>
        {check && !check.ok && (
          <ul className="space-y-1 rounded-[12px] border border-danger/40 bg-danger/[0.06] px-3 py-2 text-sm">
            <li className="font-semibold text-danger">Orion can't apply this yet:</li>
            {check.problems?.map((p) => <li key={p} className="flex gap-2"><X className="mt-0.5 h-4 w-4 shrink-0 text-danger" />{p}</li>)}
          </ul>
        )}
        {check?.ok && (
          <div className="rounded-[12px] border border-success/40 bg-success/[0.06] px-3 py-2 text-sm">
            <p className="flex items-center gap-2 font-semibold text-success"><ShieldCheck className="h-4 w-4" />Orion: OK. {check.statements?.length ?? 0} statement(s) of yours on top of his policy.</p>
            <Fold title="The policy as it will be written (JSON)"><CodeBlock raw={JSON.stringify(check.policy, null, 2)} /></Fold>
          </div>
        )}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {current.length > 0 && <Button variant="ghost" loading={busy} onClick={() => { setText("[]"); run(false, []); }}>Remove all my changes</Button>}
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button loading={busy} onClick={() => run(false)}>Check with Orion</Button>
          <Button variant="primary" loading={busy} disabled={!check?.ok || !reason.trim()} title={!reason.trim() ? "Say why first" : undefined} onClick={() => run(true, check?.statements)}>Apply to {persona}'s role</Button>
        </div>
      </div>
    </Modal>
  );
}

function Scroll({ children }: { children: React.ReactNode }) {
  return <div className="no-scrollbar h-full space-y-4 overflow-y-auto p-5 sm:p-7">{children}</div>;
}

function Hero({ accent, icon, kicker, title, chip, children }: {
  accent: string; icon: React.ReactNode; kicker: string; title: string; chip: { label: string; cls: string } | null; children?: React.ReactNode;
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="relative overflow-hidden rounded-[22px] border border-line p-5"
      style={{ background: `linear-gradient(140deg, color-mix(in srgb, ${accent} 14%, var(--surface)), var(--surface) 70%)` }}>
      <div className="flex items-start gap-4">
        <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[16px]" style={{ color: accent, background: `color-mix(in srgb, ${accent} 16%, transparent)` }}>{icon}</span>
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">{kicker}</span>
            {chip && <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", chip.cls)}>{chip.label}</span>}
          </div>
          <p className="break-all font-mono text-[15px] font-semibold">{title}</p>
          {children}
        </div>
      </div>
    </motion.div>
  );
}

function Facts({ plan, role }: { plan: AccessPlan; role: AccessRole }) {
  const facts = [["Names", `${plan.prefix}*`], ["Region", plan.region], ["Services", plan.services.join(", ")], ["Drafted for", plan.version]];
  return (
    <div className="flex flex-wrap gap-1.5 pt-1">
      {facts.map(([k, v]) => <span key={k} className="rounded-full bg-bg-2 px-2.5 py-0.5 text-xs"><span className="text-muted">{k} </span><span className="font-mono">{v}</span></span>)}
      <span className="rounded-full bg-bg-2 px-2.5 py-0.5 text-xs" title={role.arn}><span className="text-muted">Credentials </span>short-lived, only while working</span>
    </div>
  );
}

function CanCannot({ can, cannot }: { can: string[]; cannot: string[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="rounded-[18px] border border-success/30 bg-success/[0.05] p-4">
        <Label>Can</Label>
        <ul className="space-y-1.5 text-sm">{can.map((c) => <li key={c} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-success" />{c}</li>)}</ul>
      </div>
      <div className="rounded-[18px] border border-danger/25 bg-danger/[0.04] p-4">
        <Label>Cannot</Label>
        <ul className="space-y-1.5 text-sm">{cannot.map((c) => <li key={c} className="flex gap-2"><X className="mt-0.5 h-4 w-4 shrink-0 text-danger" />{c}</li>)}</ul>
      </div>
    </div>
  );
}

function Boundary({ arn, policy }: { arn: string; policy: unknown }) {
  return (
    <div className="rounded-[18px] border border-line bg-bg-2/40 p-4">
      <p className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="h-4 w-4 text-primary" />Capped by the crew boundary</p>
      <p className="mt-1 text-sm text-muted"><code>{arn.split("/").pop()}</code> is the ceiling for every agent role and every role the flow creates: even a wrong policy can't reach past it (only orkestra-* names, only eu-west-1, never the platform role).</p>
      <Fold title="Crew boundary (JSON)"><CodeBlock raw={JSON.stringify(policy, null, 2)} /></Fold>
    </div>
  );
}

function Calls({ calls }: { calls: AwsCall[] }) {
  return (
    <div>
      <Label>What it did in AWS</Label>
      {calls.length ? (
        <ul className="max-h-72 space-y-1 overflow-y-auto">{calls.slice(0, 60).map((c, i) => (
          <li key={`${c.ts}-${i}`} className="flex items-start gap-2 rounded-[10px] px-2 py-1 text-[13px] hover:bg-bg-2/60">
            {c.ok ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" /> : <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />}
            <span className="shrink-0 font-mono text-[12px] text-muted">{new Date(c.ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
            <span className="font-semibold">{c.action}</span>
            <span className="min-w-0 flex-1 break-all font-mono text-[12px] text-muted">{c.target}{c.detail ? ` · ${c.detail}` : ""}</span>
          </li>
        ))}</ul>
      ) : <p className="text-sm text-muted">Nothing yet. Every AWS action appears here, with the role that did it.</p>}
    </div>
  );
}

function Chip({ s }: { s: string }) {
  const c = STATUS[s];
  return c ? <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", c.cls)}>{c.label}</span> : null;
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{children}</p>;
}

function Fold({ title, children, open: initial = false }: { title: string; children: React.ReactNode; open?: boolean }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="mt-2 rounded-[14px] border border-line bg-surface">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-semibold">
        {title}<ChevronDown className={clsx("ml-auto h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-line p-3">{children}</div>}
    </div>
  );
}
