import { Check, Plus, Search, Sparkles, Trash2 } from "lucide-react";
import { AgentAvatar } from "../components/AgentAvatar";
import { Aurora } from "../components/Aurora";
import { Wordmark } from "../components/Logo";
import { Markdown } from "../components/Markdown";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import { Button, Input, ProgressRing, StatusPill } from "../components/ui";
import { DeploySection } from "../features/build/BuildTab";
import { SAMPLE_ACCESS, SAMPLE_BUILD } from "../features/build/deploySample";
import { AccessView } from "../features/pipeline/AgentAccess";
import { ACCENT } from "../lib/themes";

const SAMPLE_MD = `**Echo's proposal:** here's the JSON I'd expect.

\`\`\`json
{"order_id":"ORD1001","customer_name":"Ravi Kumar","amount":1500,"currency":"INR"}
\`\`\`

\`\`\`xml
<Order><OrderId>ORD1001</OrderId><Customer><Name>Ravi Kumar</Name></Customer><Amount>1500</Amount></Order>
\`\`\`

| Source field | Target field | Rule |
|---|---|---|
| Order/OrderId | order_id | one to one |
| Order/Amount | amount | number, 2 decimals |`;

/** Design gallery: every building block in the current theme. Public (no data) — used for visual QA of all themes. */
export function DesignPage() {
  return (
    <div className="min-h-dvh">
      <Aurora intensity={0.7} />
      <div className="mx-auto max-w-[1280px] space-y-5 px-4 py-6">
        <div className="glass flex items-center justify-between rounded-[20px] px-4 py-3">
          <Wordmark size={34} />
          <span className="text-sm text-muted">Design gallery · {document.documentElement.dataset.theme ?? "aurora"}</span>
          <ThemeSwitcher />
        </div>

        <div className="grid gap-5 lg:grid-cols-3">
          <section className="neu space-y-3 rounded-[22px] p-5">
            <h1 className="font-display text-2xl font-bold">Heading text</h1>
            <p>Body text: the crew turns your requirement into a deployed, tested AWS flow.</p>
            <p className="text-sm text-muted">Muted text: secondary information, hints and timestamps.</p>
            <div className="flex flex-wrap gap-2">
              {(["draft", "running", "waiting", "completed", "failed", "paused"] as const).map((s) => <StatusPill key={s} status={s} />)}
            </div>
            <div className="flex items-center gap-3">
              <ProgressRing value={0.7} /><ProgressRing value={0.35} color="var(--warning)" />
              <span className="text-sm text-muted">Progress rings</span>
            </div>
          </section>

          <section className="glass space-y-3 rounded-[22px] p-5">
            <h2 className="font-display text-lg font-semibold">Form controls</h2>
            <Input label="Project name" placeholder="e.g. Partner orders ingest" leading={<Search className="h-4 w-4" />} />
            <Input label="Filled field" defaultValue="msg-transformation" />
            <textarea rows={2} placeholder="Longer answer…" className="neu-inset w-full rounded-[12px] px-3.5 py-2.5 text-sm outline-none placeholder:text-muted/70" />
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" icon={<Plus className="h-4 w-4" />}>Primary</Button>
              <Button icon={<Sparkles className="h-4 w-4" />}>Neu</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger" icon={<Trash2 className="h-4 w-4" />}>Danger</Button>
            </div>
          </section>

          <section className="rounded-[22px] border border-line bg-surface/70 p-5">
            <h2 className="mb-3 font-display text-lg font-semibold">Agents</h2>
            <div className="grid grid-cols-3 gap-3">
              {([["intake", "cyan", "done"], ["ba", "emerald", "done"], ["ta", "amber", "working"], ["tp", "orange", "needs_approval"], ["de", "blue", "waiting"], ["qa", "rose", "failed"]] as const).map(([k, a, s]) => (
                <div key={k} className="flex flex-col items-center gap-1.5 rounded-[14px] bg-bg-2/70 p-2.5">
                  <AgentAvatar agent={k} accent={a} status={s} size={40} />
                  <span className="text-[11px] text-muted">{s.replace("_", " ")}</span>
                </div>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2 rounded-[14px] border border-success/35 bg-success/[0.08] p-3 text-sm">
              <Check className="h-4 w-4 text-success" />Answered question card
            </div>
            <div className="mt-2 rounded-[14px] border border-warning/45 bg-warning/[0.08] p-3 text-sm">
              <span className="mr-2 rounded-full bg-warning/20 px-2 py-0.5 text-[10px] font-bold uppercase text-warning">blocking</span>Open question card
            </div>
          </section>
        </div>

        <section className="rounded-[22px] border border-line bg-surface/70 p-5">
          <h2 className="mb-2 font-display text-lg font-semibold">Markdown, code & tables</h2>
          <Markdown>{SAMPLE_MD}</Markdown>
        </section>

        <div className="grid gap-5 xl:grid-cols-2">
          <section>
            <h2 className="mb-2 font-display text-lg font-semibold">Deploy to AWS (Build tab)</h2>
            <DeploySection projectId="sample" projectName="Sample" b={SAMPLE_BUILD} onOpenFile={() => undefined} />
          </section>
          <section className="relative h-[760px] overflow-hidden rounded-[22px] border border-line bg-surface">
            <AccessView data={SAMPLE_ACCESS} agent="tp" persona="Terra" accent={ACCENT.orange} />
          </section>
        </div>
      </div>
    </div>
  );
}
