import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { Archive, ArchiveRestore, Gauge, MessagesSquare, Palette, ScanSearch, ShieldAlert, Tag, Trash2, Wallet } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ThemeSwatch } from "../../components/ThemeSwitcher";
import { Button } from "../../components/ui";
import { api, ApiError, type ProjectDetail } from "../../lib/api";
import { ACCENT, showProjectTheme, THEMES, themeState } from "../../lib/themes";
import { Toggle } from "../../pages/SettingsPage";

const ACCENTS = ["violet", "cyan", "emerald", "amber", "rose", "blue", "orange"];

/** This project's own settings (user, 10-05: "have a settings page for each project too… even each project can have its
 *  own theme selection"). Its theme applies only while you're inside it. */
export function ProjectSettings({ project, onGo }: { project: ProjectDetail; onGo: (tab: "aws" | "design" | "build") => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(project.name);
  const [desc, setDesc] = useState(project.description);
  const [budget, setBudget] = useState(String(project.budget_usd));
  const [confirm, setConfirm] = useState("");
  useEffect(() => { setName(project.name); setDesc(project.description); setBudget(String(project.budget_usd)); }, [project.name, project.description, project.budget_usd]);

  const patch = async (p: Parameters<typeof api.patchProject>[1], ok: string) => {
    try {
      const out = await api.patchProject(project.id, p);
      qc.setQueryData<ProjectDetail>(["project", project.id], (old) => old && { ...old, ...out });
      qc.invalidateQueries({ queryKey: ["projects"] });
      toast.success(ok);
      return out;
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save"); }
  };
  const pickTheme = async (id: string | null) => {
    showProjectTheme(id);
    await patch({ theme: id ?? "" }, id ? `This project now uses ${THEMES.find((t) => t.id === id)?.name}` : "Back to your account's theme");
  };
  const remove = async () => {
    try { await api.deleteProject(project.id, confirm); toast.success(`Deleted ${project.name}`); navigate("/projects"); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't delete"); }
  };
  const n = Number(budget);
  const watch = project.settings?.drift_watch !== false;
  const talks = !!project.settings?.talks;

  return (
    <div className="space-y-5 pb-16">
      <div>
        <h2 className="font-display text-xl font-bold tracking-tight">Project settings</h2>
        <p className="text-sm text-muted">Only for <b className="text-text">{project.name}</b>. Your account settings stay as they are.</p>
      </div>

      <Card icon={<Palette className="h-4 w-4" />} title="Look" sub="Its own theme while you're inside this project, and its colour on the Projects page.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <button onClick={() => pickTheme(null)}
            className={clsx("press grid h-36 place-items-center rounded-[14px] border-2 border-dashed p-3 text-center text-sm",
              !project.theme ? "border-primary text-text" : "border-line text-muted hover:border-primary/60")}>
            <span><b className="block">Account theme</b><span className="text-xs">({THEMES.find((t) => t.id === themeState.account)?.name ?? "yours"})</span></span>
          </button>
          {THEMES.map((t, i) => (
            <motion.div key={t.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}>
              <ThemeSwatch id={t.id} large active={project.theme === t.id} onPick={() => pickTheme(t.id)} />
            </motion.div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">Colour</span>
          {ACCENTS.map((a) => (
            <button key={a} aria-label={a} onClick={() => a !== project.accent && patch({ accent: a }, "Colour saved")}
              className={clsx("press h-7 w-7 rounded-full ring-offset-2 ring-offset-[var(--surface)]", project.accent === a && "ring-2 ring-[var(--text)]")}
              style={{ background: ACCENT[a] }} />
          ))}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card icon={<Tag className="h-4 w-4" />} title="Details" sub="The name and description everyone sees.">
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} aria-label="Project name"
            className="w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
          <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} maxLength={2000} aria-label="Description" placeholder="What this flow does"
            className="w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
          <div className="flex justify-end">
            <Button variant="primary" disabled={name.trim().length < 2 || (name === project.name && desc === project.description)}
              onClick={() => patch({ name: name.trim(), description: desc.trim() }, "Details saved")}>Save details</Button>
          </div>
        </Card>
        <Card icon={<Wallet className="h-4 w-4" />} title="Budget" sub={`Spent $${project.cost_usd.toFixed(2)} so far. The crew pauses at the budget; raise it any time.`}>
          <div className="h-2 overflow-hidden rounded-full bg-bg-2">
            <div className="h-full rounded-full bg-[linear-gradient(90deg,var(--primary),var(--warning))]" style={{ width: `${Math.min(100, (project.cost_usd / project.budget_usd) * 100)}%` }} />
          </div>
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (n >= 1 && n <= 1000) patch({ budget_usd: n }, `Budget set to $${n}`); }}>
            <span className="text-sm text-muted">$</span>
            <input value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" aria-label="Budget"
              className="w-24 rounded-[10px] border border-line bg-surface px-2.5 py-1.5 text-sm tabular-nums outline-none focus:border-primary" />
            <Button type="submit" variant="primary" disabled={!(n >= 1 && n <= 1000) || n === project.budget_usd}>Save budget</Button>
          </form>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card icon={<ScanSearch className="h-4 w-4" />} title="Drift watch" sub="Terra compares AWS with the Terraform state and Dev's packages every 30 minutes once the project is live.">
          <Row label="Automatic check" hint="Off: only when you press Check for drift (header or AWS tab).">
            <Toggle on={watch} onChange={(v) => patch({ settings: { drift_watch: v } }, v ? "Drift watch on" : "Drift watch off: check by hand")} />
          </Row>
        </Card>
        <Card icon={<MessagesSquare className="h-4 w-4" />} title="Kickoff conversations" sub="Before its work, each agent asks the person who owns its phase: Atlas your data analyst, Archie your technical lead, then Terra, Dev and Quinn.">
          <Row label="Ask before working" hint="Off: each agent starts straight away from the documents, and you steer it at its approval gate.">
            <Toggle on={talks} onChange={(v) => patch({ settings: { talks: v } }, v ? "Kickoff conversations on" : "Kickoff conversations off: agents start straight away")} />
          </Row>
        </Card>
        <Card icon={<Gauge className="h-4 w-4" />} title="Elsewhere in this project" sub="Settings that live next to their work.">
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => onGo("aws")}>Naming convention (AWS)</Button>
            <Button size="sm" onClick={() => onGo("design")}>Coverage gate (Design)</Button>
            <Button size="sm" onClick={() => onGo("build")}>Tear down AWS (Build)</Button>
          </div>
        </Card>
      </div>

      <Card icon={<ShieldAlert className="h-4 w-4 text-danger" />} title="Danger zone" sub="Archive hides it from the main list; delete removes its files (only once nothing is left in AWS).">
        <Row label={project.archived ? "Archived" : "Archive"} hint={project.archived ? "Bring it back to the main list." : "Keeps everything; you find it under Archived."}>
          <Button size="sm" icon={project.archived ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}
            onClick={() => patch({ archived: !project.archived }, project.archived ? "Restored" : "Archived")}>{project.archived ? "Restore" : "Archive"}</Button>
        </Row>
        <Row label="Delete" hint={`Type ${project.name} to confirm. Refused while the project has resources or roles in AWS.`}>
          <div className="flex gap-2">
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={project.name} aria-label="Confirm the name"
              className="w-44 rounded-[10px] border border-line bg-surface px-2.5 py-1.5 text-sm outline-none focus:border-danger" />
            <Button size="sm" variant="danger" icon={<Trash2 className="h-4 w-4" />} disabled={confirm !== project.name} onClick={remove}>Delete</Button>
          </div>
        </Row>
      </Card>
    </div>
  );
}

function Card({ icon, title, sub, children }: { icon: ReactNode; title: string; sub: string; children: ReactNode }) {
  return (
    <section className="glass rounded-[24px] p-5">
      <div className="mb-3 flex items-start gap-2.5">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary/12 text-primary">{icon}</span>
        <div><h3 className="font-display text-lg font-semibold">{title}</h3><p className="text-sm text-muted">{sub}</p></div>
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-[16px] border border-line bg-surface/60 px-4 py-3">
      <div className="min-w-0 flex-1"><p className="text-sm font-semibold">{label}</p><p className="text-xs text-muted">{hint}</p></div>
      {children}
    </div>
  );
}
