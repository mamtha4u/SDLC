import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import {
  Activity, CheckCircle2, Cloud, Eye, Keyboard, KeyRound, LogOut, PartyPopper, Rocket, Sparkles, UserRound, Users, Wind, XCircle,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../components/AgentAvatar";
import { ThemeSwatch, useThemeChoice } from "../components/ThemeSwitcher";
import { Button } from "../components/ui";
import { api, ApiError, type UserPrefs } from "../lib/api";
import { THEMES } from "../lib/themes";

/** Who keeps an eye on whom while they work (backend agents/watch.py). */
const WATCHED_BY: Record<string, string> = { tp: "Archie", de: "Archie", cto: "Archie", intake: "Orion", ba: "Orion", ta: "Orion", qa: "Orion" };

/** Your account, how Orkestra looks and feels, defaults for new projects, and the platform (user, 10-05: "nothing is in
 *  this settings page… please have some useful features"). Each project also has its own Settings tab. */
export function SettingsPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
  const info = useQuery({ queryKey: ["info"], queryFn: api.info });
  const { theme, choose } = useThemeChoice();
  const prefs: UserPrefs = { motion: "full", celebrate: true, default_budget_usd: 20, ...(me.data?.prefs ?? {}) };

  const save = async (patch: Parameters<typeof api.updateMe>[0], ok: string) => {
    try { const u = await api.updateMe(patch); qc.setQueryData(["me"], u); toast.success(ok); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save"); }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-16">
      <div>
        <h1 className="font-display font-bold tracking-tight" style={{ fontSize: "var(--fs-h1)" }}>Settings</h1>
        <p className="text-sm text-muted">Your account, how Orkestra looks and feels, and the defaults for new projects. Each project also has its own
          <b className="text-text"> Settings</b> tab (its own theme, budget, drift watch).</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Profile name={me.data?.display_name ?? ""} username={me.data?.username ?? ""} since={me.data?.created_at}
          onSave={(display_name) => save({ display_name }, "Name saved")} />
        <Card icon={<Sparkles className="h-4 w-4" />} title="Experience" sub="How lively the screens are.">
          <Row label="Animations" hint="Calm keeps fades but stops the moving extras (beams, confetti, travelling chips).">
            <Segmented value={prefs.motion} options={[["full", "Full", <Sparkles key="f" className="h-3.5 w-3.5" />], ["calm", "Calm", <Wind key="c" className="h-3.5 w-3.5" />]]}
              onChange={(v) => save({ prefs: { motion: v as UserPrefs["motion"] } }, v === "calm" ? "Calm animations" : "Full animations")} />
          </Row>
          <Row label="Celebrations" hint="A burst of confetti when you approve a step or sign off.">
            <Toggle on={prefs.celebrate} icon={<PartyPopper className="h-3.5 w-3.5" />}
              onChange={(v) => save({ prefs: { celebrate: v } }, v ? "Celebrations on" : "Celebrations off")} />
          </Row>
        </Card>
      </div>

      <Card icon={<Eye className="h-4 w-4" />} title="Theme" sub="Eight looks. Your pick follows your account; a project can have its own in its Settings tab.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {THEMES.map((t, i) => (
            <motion.div key={t.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}>
              <ThemeSwatch id={t.id} large active={theme === t.id} onPick={() => choose(t.id)} />
            </motion.div>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card icon={<Rocket className="h-4 w-4" />} title="New projects" sub="Defaults when you create one. Change them per project later.">
          <BudgetDefault value={prefs.default_budget_usd} onSave={(v) => save({ prefs: { default_budget_usd: v } }, `New projects start with $${v}`)} />
          <Row label="Region" hint="Fixed for this sandbox: the account allows only Ireland."><code className="rounded-full bg-bg-2 px-2.5 py-1 text-xs">eu-west-1</code></Row>
          <Row label="Resource names" hint="Required here so colleagues' resources stay untouchable; your own convention follows it (AWS tab)."><code className="rounded-full bg-bg-2 px-2.5 py-1 text-xs">orkestra-…</code></Row>
        </Card>
        <Password />
      </div>

      <Card icon={<Users className="h-4 w-4" />} title="The crew" sub="Who does what, on which model, and who keeps an eye on them while they work (they ask a silent teammate if all is OK and restart a stuck step).">
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          {(info.data?.crew ?? []).map((a) => (
            <div key={a.key} className="flex items-center gap-3 rounded-[16px] border border-line bg-surface p-3">
              <AgentAvatar agent={a.key} accent={a.accent} status="done" size={36} plain />
              <div className="min-w-0 text-sm">
                <p className="font-semibold">{a.persona} <span className="font-normal text-muted">· {a.role}</span></p>
                <p className="truncate text-xs text-muted">{a.model.replace(/^eu\.anthropic\./, "").replace(/-/g, " ")}{WATCHED_BY[a.key] ? ` · watched by ${WATCHED_BY[a.key]}` : ""}</p>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <CloudConnection info={info.data} />
        <Card icon={<Keyboard className="h-4 w-4" />} title="Shortcuts" sub="Faster around Orkestra.">
          {[["Ctrl K", "Search and jump anywhere"], ["Esc", "Close a popup"], ["Enter", "Send to Echo or Archie"], ["Shift Enter", "New line in a message"]].map(([k, d]) => (
            <div key={k} className="flex items-center justify-between gap-3 py-1 text-sm"><span className="text-muted">{d}</span>
              <kbd className="rounded-md border border-line bg-bg-2 px-2 py-0.5 font-mono text-[11px]">{k}</kbd></div>
          ))}
        </Card>
      </div>
    </div>
  );
}

function Card({ icon, title, sub, children }: { icon: ReactNode; title: string; sub: string; children: ReactNode }) {
  return (
    <section className="glass rounded-[24px] p-5">
      <div className="mb-3 flex items-start gap-2.5">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary/12 text-primary">{icon}</span>
        <div><h2 className="font-display text-lg font-semibold">{title}</h2><p className="text-sm text-muted">{sub}</p></div>
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

export function Segmented({ value, options, onChange }: { value: string; options: [string, string, ReactNode?][]; onChange: (v: string) => void }) {
  return (
    <div className="flex gap-1 rounded-[12px] bg-bg-2 p-1">
      {options.map(([v, label, icon]) => (
        <button key={v} onClick={() => v !== value && onChange(v)}
          className={clsx("press relative inline-flex items-center gap-1.5 rounded-[9px] px-3 py-1.5 text-xs font-semibold", value === v ? "text-text" : "text-muted hover:text-text")}>
          {value === v && <motion.span layoutId={`seg-${options.map((o) => o[0]).join("")}`} className="neu-sm absolute inset-0 -z-10 rounded-[9px]" />}
          {icon}{label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ on, onChange, icon }: { on: boolean; onChange: (v: boolean) => void; icon?: ReactNode }) {
  return (
    <button role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={clsx("press relative inline-flex h-8 w-[66px] items-center rounded-full border px-1 transition-colors", on ? "border-primary/50 bg-primary/20" : "border-line bg-bg-2")}>
      <motion.span layout className={clsx("grid h-6 w-6 place-items-center rounded-full shadow", on ? "bg-primary text-bg" : "bg-surface text-muted")}
        style={{ marginLeft: on ? "auto" : 0 }}>{icon}</motion.span>
    </button>
  );
}

function Profile({ name, username, since, onSave }: { name: string; username: string; since?: string | null; onSave: (n: string) => void }) {
  const [draft, setDraft] = useState(name);
  useEffect(() => setDraft(name), [name]);
  const logout = async () => { await api.logout().catch(() => undefined); window.location.href = "/login"; };
  return (
    <Card icon={<UserRound className="h-4 w-4" />} title="Profile" sub="How the crew addresses you.">
      <div className="flex items-center gap-4">
        <span className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] font-display text-2xl font-bold text-bg">
          {(name || username || "?").slice(0, 1).toUpperCase()}</span>
        <div className="min-w-0 text-sm">
          <p className="font-semibold">@{username}</p>
          <p className="text-xs text-muted">{since ? `Member since ${new Date(since).toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" })}` : ""}</p>
        </div>
        <Button size="sm" variant="ghost" className="ml-auto" icon={<LogOut className="h-4 w-4" />} onClick={logout}>Sign out</Button>
      </div>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (draft.trim() && draft.trim() !== name) onSave(draft.trim()); }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={120} aria-label="Display name"
          className="min-w-0 flex-1 rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary" />
        <Button type="submit" variant="primary" disabled={!draft.trim() || draft.trim() === name}>Save name</Button>
      </form>
    </Card>
  );
}

function BudgetDefault({ value, onSave }: { value: number; onSave: (v: number) => void }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const n = Number(draft);
  const ok = Number.isFinite(n) && n >= 1 && n <= 1000 && n !== value;
  return (
    <Row label="Budget per project" hint="The crew pauses when a project reaches it; raise it any time.">
      <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (ok) onSave(n); }}>
        <span className="text-sm text-muted">$</span>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} inputMode="decimal" aria-label="Default budget"
          className="w-20 rounded-[10px] border border-line bg-surface px-2.5 py-1.5 text-sm tabular-nums outline-none focus:border-primary" />
        <Button size="sm" type="submit" variant="primary" disabled={!ok}>Save</Button>
      </form>
    </Row>
  );
}

function Password() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const problem = next && next.length < 8 ? "At least 8 characters" : again && again !== next ? "The two new passwords differ" : "";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { await api.changePassword(cur, next); toast.success("Password changed"); setCur(""); setNext(""); setAgain(""); }
    catch (err) { toast.error(err instanceof ApiError ? err.message : "Couldn't change it"); }
    finally { setBusy(false); }
  };
  const field = "w-full rounded-[12px] border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary";
  return (
    <Card icon={<KeyRound className="h-4 w-4" />} title="Security" sub="Change your password.">
      <form className="space-y-2" onSubmit={submit}>
        <input type="password" autoComplete="current-password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} className={field} />
        <input type="password" autoComplete="new-password" placeholder="New password (8+ characters)" value={next} onChange={(e) => setNext(e.target.value)} className={field} />
        <input type="password" autoComplete="new-password" placeholder="New password again" value={again} onChange={(e) => setAgain(e.target.value)} className={field} />
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-danger">{problem}</span>
          <Button type="submit" variant="primary" loading={busy} disabled={!cur || !next || next !== again || !!problem}>Change password</Button>
        </div>
      </form>
    </Card>
  );
}

function CloudConnection({ info }: { info?: Awaited<ReturnType<typeof api.info>> }) {
  const [checks, setChecks] = useState<Awaited<ReturnType<typeof api.bedrockCheck>> | null>(null);
  const [checking, setChecking] = useState(false);
  const runCheck = async () => {
    setChecking(true);
    try { setChecks(await api.bedrockCheck()); } finally { setChecking(false); }
  };
  return (
    <section className="glass rounded-[24px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 grid h-8 w-8 place-items-center rounded-full bg-primary/12 text-primary"><Cloud className="h-4 w-4" /></span>
          <div><h2 className="font-display text-lg font-semibold">Cloud connection</h2>
            <p className="text-sm text-muted">The identity the crew uses, and a live Claude-on-Bedrock check.</p></div>
        </div>
        <Button variant="primary" loading={checking} icon={<Activity className="h-4 w-4" />} onClick={runCheck}>Check Bedrock</Button>
      </div>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-4">
        {[["Environment", info?.environment], ["Account", info?.account], ["Region", info?.region], ["Role", info?.role ?? "—"]].map(([k, v]) => (
          <div key={k} className="neu-inset rounded-[14px] p-3"><dt className="text-xs text-muted">{k}</dt><dd className="mt-0.5 truncate font-mono">{v ?? "…"}</dd></div>
        ))}
      </dl>
      {checks && (
        <ul className="mt-4 space-y-2">
          {checks.map((c, i) => (
            <motion.li key={c.model} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }}
              className="neu-sm flex items-center gap-3 rounded-[14px] px-4 py-3 text-sm">
              {c.ok ? <CheckCircle2 className="h-5 w-5 text-success" /> : <XCircle className="h-5 w-5 text-danger" />}
              <span className="font-mono">{c.model}</span>
              <span className="ml-auto truncate text-muted">{c.ok ? `“${c.reply}” · ${c.ms} ms` : c.error}</span>
            </motion.li>
          ))}
        </ul>
      )}
    </section>
  );
}
