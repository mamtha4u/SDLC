import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  Archive, ArchiveRestore, Clock, DollarSign, MoreHorizontal, Pencil, Plus, Search, Sparkles, Trash2, Wallet, Workflow,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AgentAvatar } from "../components/AgentAvatar";
import { BudgetDialog } from "../components/BudgetDialog";
import { ORK } from "../components/CommandPalette";
import { Sparkline } from "../components/Sparkline";
import { AnimatedNumber, Button, Input, Modal, ProgressRing, Skeleton, StatusPill } from "../components/ui";
import { api, ApiError, type Project } from "../lib/api";
import { ACCENT } from "../lib/themes";
import { usageApi } from "../lib/usage";
import { useEvents } from "../lib/useEvents";

const AGENT_ACCENT: Record<string, string> = {
  cto: "violet", intake: "cyan", ba: "emerald", ta: "amber", tp: "orange", de: "blue", qa: "rose",
};
const AGENT_NAME: Record<string, string> = {
  cto: "Orion", intake: "Echo", ba: "Atlas", ta: "Archie", tp: "Terra", de: "Dev", qa: "Quinn",
};

function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function StatTile({ label, value, decimals = 0, prefix = "", color, delay, hint }: {
  label: string; value: number; decimals?: number; prefix?: string; color: string; delay: number; hint?: string;
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay }}
      className="spotlight sheen elev relative overflow-hidden rounded-[22px] border border-line bg-surface p-4">
      <div className="absolute -right-6 -top-6 h-24 w-24 rounded-full blur-2xl" style={{ background: color, opacity: 0.28 }} />
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 font-display text-4xl font-bold" style={{ color }}>
        <AnimatedNumber value={value} decimals={decimals} prefix={prefix} />
      </p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </motion.div>
  );
}

/** The crew, live: every agent, glowing when it's working on one of your projects (hover to see where). */
function CrewNow({ projects, onOpen }: { projects: Project[]; onOpen: (id: string) => void }) {
  const order = ["cto", "intake", "ba", "ta", "tp", "de", "qa"];
  const busy = Object.fromEntries(projects.filter((p) => p.status === "running" && p.current_agent).map((p) => [p.current_agent!, p]));
  return (
    <div className="glass flex items-center gap-1 rounded-full px-2 py-1.5">
      <span className="px-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Crew</span>
      {order.map((k, i) => {
        const p = busy[k];
        return (
          <motion.button key={k} initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.2 + i * 0.05 }}
            whileHover={{ y: -3 }} onClick={() => p && onOpen(p.id)} disabled={!p}
            title={p ? `${AGENT_NAME[k]} is working on ${p.name}` : `${AGENT_NAME[k]} is free`} className="rounded-full">
            <AgentAvatar agent={k} accent={AGENT_ACCENT[k]} status={p ? "working" : "waiting"} size={28} />
          </motion.button>
        );
      })}
    </div>
  );
}

function CardMenu({ project, onRename, onBudget, onDelete }: { project: Project; onRename: () => void; onBudget: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const toggleArchive = async () => {
    setOpen(false);
    await api.patchProject(project.id, { archived: !project.archived });
    toast.success(project.archived ? "Project restored" : "Project archived");
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const item = "flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-sm hover:bg-surface-2";
  return (
    <div ref={ref} className="relative" onClick={(e) => e.stopPropagation()}>
      <button onClick={() => setOpen(!open)} aria-label="Project actions"
        className="focus-ring grid h-8 w-8 place-items-center rounded-[10px] text-muted hover:bg-surface-2 hover:text-text">
        <MoreHorizontal className="h-4 w-4" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, scale: 0.95, y: -4 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }}
            className="glass absolute right-0 z-30 mt-1 w-44 rounded-[14px] p-1.5 shadow-xl">
            <button className={item} onClick={() => { setOpen(false); onRename(); }}><Pencil className="h-4 w-4" />Rename</button>
            <button className={item} onClick={() => { setOpen(false); onBudget(); }}><Wallet className="h-4 w-4" />Edit budget</button>
            <button className={item} onClick={toggleArchive}>
              {project.archived ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}
              {project.archived ? "Restore" : "Archive"}
            </button>
            <button className={clsx(item, "text-danger")} onClick={() => { setOpen(false); onDelete(); }}>
              <Trash2 className="h-4 w-4" />Delete
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ProjectCard({ p, index, onRename, onBudget, onDelete }: { p: Project; index: number; onRename: () => void; onBudget: () => void; onDelete: () => void }) {
  const nav = useNavigate();
  const accent = ACCENT[p.accent] ?? "var(--primary)";
  const live = p.status === "running" || p.status === "waiting";
  return (
    <motion.article layout initial={{ opacity: 0, y: 24, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.94 }} transition={{ delay: Math.min(index * 0.05, 0.4), type: "spring", stiffness: 260, damping: 26 }}
      whileHover={{ y: -4 }} onClick={() => nav(`/projects/${p.id}`)}
      onPointerMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
        e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
      }}
      className={clsx("neu group relative cursor-pointer overflow-hidden rounded-[24px] p-5 transition-shadow", live && "glow-primary beam-border")}>
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: `linear-gradient(90deg, ${accent}, transparent)` }} />
      {/* cursor spotlight */}
      <div className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{ background: `radial-gradient(300px circle at var(--mx) var(--my), color-mix(in srgb, ${accent} 16%, transparent), transparent 65%)` }} />
      <div className="absolute -right-10 -top-10 h-32 w-32 rounded-full opacity-0 blur-3xl transition-opacity duration-500 group-hover:opacity-40"
        style={{ background: accent }} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-display text-lg font-semibold">{p.name}</h3>
          <p className="mt-0.5 line-clamp-2 min-h-[2.5rem] text-sm text-muted">{p.description || "No description yet"}</p>
        </div>
        <CardMenu project={p} onRename={onRename} onBudget={onBudget} onDelete={onDelete} />
      </div>

      <div className="mt-4 flex items-center gap-3">
        <ProgressRing value={p.progress} color={accent} />
        <div className="min-w-0 flex-1">
          <StatusPill status={p.status} />
          <p className="mt-1.5 truncate text-xs text-muted">{p.last_activity}</p>
        </div>
        {p.current_agent && live && (
          <div className="flex flex-col items-center gap-1">
            <AgentAvatar agent={p.current_agent} accent={AGENT_ACCENT[p.current_agent]} status="working" size={34} />
            <span className="text-[10px] font-medium text-muted">{AGENT_NAME[p.current_agent]}</span>
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-line pt-3 text-xs text-muted">
        <span className="inline-flex items-center gap-1"><Clock className="h-3.5 w-3.5" />{timeAgo(p.updated_at)}</span>
        <span className="font-mono">{p.current_version}</span>
        <span className="inline-flex items-center gap-0.5"><DollarSign className="h-3.5 w-3.5" />{p.cost_usd.toFixed(2)}
          <span className="opacity-60">/ {p.budget_usd.toFixed(0)}</span></span>
      </div>
    </motion.article>
  );
}

function EmptyState({ onCreate, filtered }: { onCreate: () => void; filtered: boolean }) {
  return (
    <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
      className="glass mx-auto mt-6 flex max-w-lg flex-col items-center rounded-[28px] px-8 py-12 text-center">
      <div className="relative mb-5">
        <div className="absolute inset-0 animate-pulse rounded-full bg-primary/30 blur-2xl" />
        <div className="neu relative grid h-20 w-20 place-items-center rounded-[24px] text-primary"><Workflow className="h-9 w-9" /></div>
      </div>
      <h3 className="font-display text-xl font-semibold">{filtered ? "Nothing matches" : "Your stage is empty"}</h3>
      <p className="mt-2 text-sm text-muted">
        {filtered ? "Try another filter or search term." : "Create a project, describe what you need, and watch the crew design, build, deploy and test it."}
      </p>
      {!filtered && <Button variant="primary" className="mt-6" icon={<Sparkles className="h-4 w-4" />} onClick={onCreate}>Start your first project</Button>}
    </motion.div>
  );
}

export function ProjectsPage() {
  const [view, setView] = useState<"all" | "running" | "archived">("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("updated");
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [budgeting, setBudgeting] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const qc = useQueryClient();
  const nav = useNavigate();

  const projects = useQuery({ queryKey: ["projects", view, q, sort], queryFn: () => api.projects(view, q, sort) });
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });

  // Live: any event on any of my projects refreshes the dashboard (throttled).
  const pending = useRef<number | null>(null);
  useEvents(() => {
    if (pending.current) return;
    pending.current = window.setTimeout(() => {
      pending.current = null;
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    }, 600);
  });

  const s = stats.data;
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
  const all = useQuery({ queryKey: ["projects", "all", "", "updated"], queryFn: () => api.projects("all", "", "updated") });
  const spend = useQuery({ queryKey: ["usage-overall", 30], queryFn: () => usageApi.overall(30), staleTime: 60_000 });
  const needsYou = (all.data ?? []).filter((p) => p.status === "waiting");
  const inFlight = (s?.running ?? 0) + (s?.waiting ?? 0);
  const daily = spend.data?.daily.map((d) => d.cost_usd) ?? [];
  const hour = new Date().getHours();
  const hello = hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  useEffect(() => {
    const open = () => setCreating(true);
    window.addEventListener(ORK.newProject, open);
    return () => window.removeEventListener(ORK.newProject, open);
  }, []);
  return (
    <div className="mx-auto max-w-[1600px]">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <motion.p initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="text-sm font-medium text-muted">
            {hello}{me.data ? `, ${me.data.display_name.split(" ")[0]}` : ""} 👋
          </motion.p>
          <motion.h1 initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.05 }}
            className="font-display font-bold tracking-tight" style={{ fontSize: "var(--fs-hero)", lineHeight: 1.05 }}>
            Your <span className="text-gradient-anim">crew</span> at work
          </motion.h1>
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 }} className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted">{s?.total ?? 0} projects · {inFlight} in flight</span>
            {needsYou.slice(0, 3).map((p) => (
              <button key={p.id} onClick={() => nav(`/projects/${p.id}`)}
                className="press inline-flex max-w-[260px] items-center gap-2 rounded-full border border-warning/45 bg-warning/10 px-3 py-1 text-[13px] font-medium hover:border-warning">
                <span className="h-2 w-2 shrink-0 rounded-full bg-warning pulse-ring" style={{ ["--ring" as string]: "var(--warning)" }} />
                <span className="truncate">{p.name}</span><span className="shrink-0 text-warning">needs you</span>
              </button>
            ))}
          </motion.div>
        </div>
        <div className="flex flex-col items-stretch gap-3 sm:items-end">
          <CrewNow projects={all.data ?? []} onOpen={(id) => nav(`/projects/${id}`)} />
          <Button variant="primary" size="lg" className="shimmer press w-full sm:w-auto" icon={<Plus className="h-5 w-5" />} onClick={() => setCreating(true)}>New project</Button>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}
          className="spotlight sheen elev col-span-2 overflow-hidden rounded-[22px] border border-line bg-surface p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-muted">Spend · last 30 days</p>
              <p className="mt-1 font-display text-3xl font-bold text-warning"><AnimatedNumber value={spend.data?.totals.cost_usd ?? s?.cost_usd ?? 0} decimals={2} prefix="$" /></p>
            </div>
            <p className="text-right text-xs text-muted">{spend.data ? `${spend.data.totals.calls} model calls` : ""}<br />all-time ${(s?.cost_usd ?? 0).toFixed(2)}</p>
          </div>
          <Sparkline values={daily} color="var(--warning)" height={52} className="mt-2" />
        </motion.div>
        <StatTile label="In flight" value={inFlight} color="var(--primary-2)" delay={0.1} hint={needsYou.length ? `${needsYou.length} waiting for you` : "nothing waiting for you"} />
        <StatTile label="Completed" value={s?.completed ?? 0} color="var(--success)" delay={0.15} hint="live in AWS and tested" />
      </div>

      <div className="mt-6 flex flex-col gap-3 md:flex-row md:items-center">
        <div className="neu-inset relative flex rounded-[14px] p-1">
          {(["all", "running", "archived"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={clsx("focus-ring relative z-10 h-9 rounded-[10px] px-4 text-sm font-medium transition-colors", view === v ? "text-text" : "text-muted")}>
              {view === v && <motion.span layoutId="view-tab" className="neu-sm absolute inset-0 -z-10 rounded-[10px]"
                transition={{ type: "spring", stiffness: 500, damping: 35 }} />}
              {v === "all" ? "All" : v === "running" ? "Running now" : "Archived"}
            </button>
          ))}
        </div>
        <div className="flex-1"><Input placeholder="Search projects…" value={q} onChange={(e) => setQ(e.target.value)} leading={<Search className="h-4 w-4" />} /></div>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort"
          className="neu-sm focus-ring h-11 rounded-[12px] bg-surface px-3 text-sm text-text">
          <option value="updated">Last activity</option><option value="created">Newest</option>
          <option value="name">Name</option><option value="cost">Cost</option>
        </select>
      </div>

      <div className="mt-6">
        {projects.isLoading ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[228px] !rounded-[24px]" />)}
          </div>
        ) : projects.data?.length ? (
          <motion.div layout className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            <AnimatePresence>
              {projects.data.map((p, i) => (
                <ProjectCard key={p.id} p={p} index={i} onRename={() => setRenaming(p)} onBudget={() => setBudgeting(p)} onDelete={() => setDeleting(p)} />
              ))}
            </AnimatePresence>
          </motion.div>
        ) : (
          <EmptyState onCreate={() => setCreating(true)} filtered={view !== "all" || !!q} />
        )}
      </div>

      <CreateModal open={creating} onClose={() => setCreating(false)} onCreated={(id) => nav(`/projects/${id}`)} />
      <RenameModal project={renaming} onClose={() => setRenaming(null)} />
      {budgeting && <BudgetDialog project={budgeting} open onClose={() => setBudgeting(null)} />}
      <DeleteModal project={deleting} onClose={() => setDeleting(null)} />
    </div>
  );
}

function CreateModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [budget, setBudget] = useState(20);
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => api.createProject(name, description, budget),
    onSuccess: (p) => {
      toast.success(`“${p.name}” is ready — the crew is standing by`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      onClose();
      setName(""); setDescription("");
      onCreated(p.id);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not create project"),
  });
  return (
    <Modal open={open} onClose={onClose} title="New project">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <Input label="Project name" placeholder="e.g. Partner orders ingest" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} autoFocus />
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-muted">One-line description (optional)</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
            placeholder="What should this flow do? You'll give the full requirement to Echo next."
            className="neu-inset focus-ring w-full resize-none rounded-[12px] p-3.5 text-sm outline-none placeholder:text-muted/70" />
        </label>
        <Input label="Budget cap (USD)" type="number" min={1} max={1000} value={budget}
          onChange={(e) => setBudget(Number(e.target.value))} hint="Agents pause and ask you before exceeding this." />
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={m.isPending} icon={<Sparkles className="h-4 w-4" />}>Create project</Button>
        </div>
      </form>
    </Modal>
  );
}

function RenameModal({ project, onClose }: { project: Project | null; onClose: () => void }) {
  const [name, setName] = useState("");
  const qc = useQueryClient();
  useEffect(() => { if (project) setName(project.name); }, [project]);
  const save = async () => {
    try {
      await api.patchProject(project!.id, { name });
      toast.success("Renamed");
      qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Rename failed"); }
  };
  return (
    <Modal open={!!project} onClose={onClose} title="Rename project" width={420}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus minLength={2} required />
        <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary">Save</Button></div>
      </form>
    </Modal>
  );
}

function DeleteModal({ project, onClose }: { project: Project | null; onClose: () => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  useEffect(() => setTyped(""), [project]);
  const del = async () => {
    setBusy(true);
    try {
      await api.deleteProject(project!.id, typed);
      toast.success("Project deleted");
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      onClose();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Delete failed"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open={!!project} onClose={onClose} title="Delete project" width={440}>
      <div className="rounded-[14px] border border-danger/30 bg-danger/10 p-3.5 text-sm">
        This permanently deletes <b>{project?.name}</b>: every version, document, log and secret.
        AWS resources are torn down separately, with their own confirmation.
      </div>
      <div className="mt-4"><Input label={`Type “${project?.name}” to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus /></div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="danger" disabled={typed !== project?.name} loading={busy} onClick={del} icon={<Trash2 className="h-4 w-4" />}>Delete forever</Button>
      </div>
    </Modal>
  );
}
