import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, Eye } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { CREW, ORION } from "../lib/crew";
import { flowApi } from "../lib/flow";
import { ACCENT } from "../lib/themes";
import { AgentAvatar } from "./AgentAvatar";
import { WorkProgress } from "./WorkProgress";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));

/** "Is this agent actually doing something?" Steps, a running clock, and its own latest lines from the crew room. */
export function AgentLive({ projectId, agent, title, activity, startedAt, steps, step, hint }: {
  projectId: string; agent: string; title: string; activity: string; startedAt: string | null;
  steps: string[]; step: number; hint: string;
}) {
  const m = META[agent];
  const accent = m ? ACCENT[m.accent] : "var(--primary-2)";
  const now = useNow();
  const started = startedAt ? asDate(startedAt).getTime() : now;
  const secs = Math.max(0, Math.round((now - started) / 1000));
  const { data: crew } = useQuery({ queryKey: ["crew", projectId], queryFn: () => flowApi.crew(projectId), refetchInterval: 3000 });
  const { data: project } = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId) });
  const me = project?.agents.find((a) => a.key === agent);
  const expected = me?.expected_s;
  const trail = (crew ?? []).filter((c) => c.sender === agent && asDate(c.created_at).getTime() >= started - 5000).slice(-8);
  return (
    <div id={`live-${agent}`} className="relative scroll-mt-48 overflow-hidden rounded-[28px] border bg-surface p-6 sm:p-8" style={{ borderColor: `color-mix(in srgb, ${accent} 40%, var(--border))` }}>
      <div className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full opacity-25" style={{ background: `radial-gradient(closest-side, ${accent}, transparent)` }} />
      <div className="relative flex items-center gap-4">
        <AgentAvatar agent={agent} accent={m?.accent ?? "violet"} status="working" size={64} />
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: accent }}>Working now</p>
          <h3 className="font-display text-2xl font-bold">{title}</h3>
          <p className="truncate text-sm text-muted">{activity}</p>
        </div>
        <WorkProgress startedAt={me?.job_started_at ?? startedAt} expectedS={expected} tone={accent} className="ml-auto hidden w-56 shrink-0 sm:block" />
      </div>
      <ol className="relative mt-6 grid gap-3" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(180px, 1fr))` }}>
        {steps.map((s, i) => (
          <li key={s} className={clsx("flex items-center gap-3 rounded-[16px] border p-3.5 text-sm",
            i < step ? "border-success/40 bg-success/[0.07]" : i === step ? "bg-primary-2/[0.08]" : "border-line opacity-60")}
            style={i === step ? { borderColor: `color-mix(in srgb, ${accent} 55%, transparent)` } : undefined}>
            <span className={clsx("grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-bold",
              i < step ? "bg-success text-bg" : i === step ? "text-bg" : "bg-bg-2 text-muted")}
              style={i === step ? { background: accent } : undefined}>
              {i < step ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
            </span>
            <span className="font-medium">{s}</span>
            {i === step && <span className="ml-auto h-2 w-2 rounded-full pulse-ring" style={{ background: accent, ["--ring" as string]: accent }} />}
          </li>
        ))}
      </ol>
      <div className="relative mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-success pulse-ring" style={{ ["--ring" as string]: "var(--success)" }} />
          Working for <b className="tabular-nums text-text">{Math.floor(secs / 60)}m {String(secs % 60).padStart(2, "0")}s</b></span>
        <span className="min-w-0 flex-1">{hint}</span>
        <button onClick={() => window.dispatchEvent(new CustomEvent("ork:workbench", { detail: agent }))}
          className="press inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] font-semibold text-text hover:border-primary"
          style={{ borderColor: `color-mix(in srgb, ${accent} 50%, var(--border))`, background: `color-mix(in srgb, ${accent} 10%, transparent)` }}>
          <Eye className="h-3.5 w-3.5" style={{ color: accent }} />Watch behind the scenes</button>
      </div>
      {trail.length > 0 && (
        <ul className="relative mt-4 space-y-1.5 border-t border-line pt-3">
          {trail.map((c) => (
            <li key={c.id} className="flex gap-2 text-[13px]">
              <span className="w-12 shrink-0 font-mono text-[11px] text-muted">{asDate(c.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              <span className={clsx("min-w-0 line-clamp-2", c.kind === "issue" ? "text-danger" : c.kind === "fix" ? "text-success" : c.kind === "think" ? "italic text-muted" : "")}>
                {c.kind === "think" ? "💭 " : ""}{c.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function useNow(active = true) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}
export const asDate = (iso: string) => new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
