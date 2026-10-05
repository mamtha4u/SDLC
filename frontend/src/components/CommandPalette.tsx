import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import {
  BarChart3, Cloud, Code2, CornerDownLeft, FilePlus2, FlaskConical, FolderKanban, GitCompareArrows, Hammer, MessagesSquare, Network, Palette, Plus,
  Search, Settings, Sparkles, Ticket, Users, Workflow,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { switchTheme, THEMES } from "../lib/themes";
import { Overlay } from "./Overlay";

/** App-wide events the palette (and others) send; the page that owns the thing listens. */
export const ORK = { tab: "ork:tab", change: "ork:change", newProject: "ork:new-project", sage: "ork:sage", workbench: "ork:workbench" } as const;
export const send = (name: string, detail?: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

interface Item { id: string; group: string; label: string; hint?: string; icon: React.ReactNode; run: () => void; keywords?: string }

const TAB_ITEMS: [string, string, React.ReactNode][] = [
  ["requirement", "Requirement", <MessagesSquare key="r" className="h-4 w-4" />], ["mapping", "Mapping", <GitCompareArrows key="m" className="h-4 w-4" />],
  ["design", "Design", <Network key="d" className="h-4 w-4" />], ["build", "Build: infrastructure, code & deploy", <Hammer key="b" className="h-4 w-4" />],
  ["aws", "AWS: resources, settings & names", <Cloud key="a" className="h-4 w-4" />],
  ["testing", "Testing: test plan, live tests, sign-off", <FlaskConical key="q" className="h-4 w-4" />], ["tickets", "Tickets (bugs & tasks)", <Ticket key="t" className="h-4 w-4" />],
  ["code", "Code", <Code2 key="c" className="h-4 w-4" />], ["pipeline", "Pipeline", <Workflow key="p" className="h-4 w-4" />],
  ["crew", "Crew room", <Users key="cr" className="h-4 w-4" />], ["usage", "Usage", <BarChart3 key="u" className="h-4 w-4" />],
];

/** Fuzzy match: every typed character in order; earlier and consecutive hits score higher. */
function score(q: string, text: string): number {
  if (!q) return 1;
  const t = text.toLowerCase();
  let i = 0, s = 0, run = 0;
  for (const ch of q.toLowerCase()) {
    const j = t.indexOf(ch, i);
    if (j < 0) return 0;
    run = j === i ? run + 1 : 0;
    s += 10 - Math.min(9, j - i) + run * 4;
    i = j + 1;
  }
  return s + (t.startsWith(q.toLowerCase()) ? 25 : 0);
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest?.(".monaco-editor")) return; // the code editor's own Ctrl+K chords
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("ork:palette", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("ork:palette", onOpen); };
  }, []);
  return (
    <Overlay open={open} onClose={close} label="Command palette" z={120}>
      {open && <PaletteBody onClose={close} />}
    </Overlay>
  );
}

function PaletteBody({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const loc = useLocation();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const { data: projects } = useQuery({ queryKey: ["projects", "palette"], queryFn: () => api.projects("all", "", "updated"), staleTime: 15_000 });
  const inProject = /^\/projects\/[^/]+/.test(loc.pathname);
  useEffect(() => { input.current?.focus(); }, []);

  const items = useMemo<Item[]>(() => {
    const go = (fn: () => void) => () => { onClose(); fn(); };
    const out: Item[] = [];
    if (inProject) {
      for (const [id, label, icon] of TAB_ITEMS) out.push({ id: `tab-${id}`, group: "This project", label, icon, run: go(() => send(ORK.tab, id)), keywords: "tab open" });
      out.push({ id: "cr", group: "This project", label: "New change request", hint: "Orion routes it", icon: <FilePlus2 className="h-4 w-4" />, run: go(() => send(ORK.change)), keywords: "change requirement add fix" });
      out.push({ id: "sage", group: "This project", label: "Ask Sage about this project", icon: <Sparkles className="h-4 w-4" />, run: go(() => send(ORK.sage)), keywords: "question help assistant" });
    }
    for (const p of projects ?? []) {
      out.push({ id: `p-${p.id}`, group: "Projects", label: p.name, hint: `${Math.round(p.progress * 100)}% · ${p.status}`,
        icon: <FolderKanban className="h-4 w-4" />, run: go(() => nav(`/projects/${p.id}`)), keywords: p.description });
    }
    out.push({ id: "new", group: "Actions", label: "New project", icon: <Plus className="h-4 w-4" />,
      run: go(() => { nav("/projects"); window.setTimeout(() => send(ORK.newProject), 120); }), keywords: "create start" });
    out.push({ id: "nav-projects", group: "Go to", label: "Projects", icon: <FolderKanban className="h-4 w-4" />, run: go(() => nav("/projects")) });
    out.push({ id: "nav-usage", group: "Go to", label: "Usage & cost", icon: <BarChart3 className="h-4 w-4" />, run: go(() => nav("/usage")), keywords: "spend tokens money" });
    out.push({ id: "nav-settings", group: "Go to", label: "Settings", icon: <Settings className="h-4 w-4" />, run: go(() => nav("/settings")) });
    for (const t of THEMES) {
      out.push({ id: `theme-${t.id}`, group: "Theme", label: `Theme: ${t.name}`, icon: <Palette className="h-4 w-4" />,
        run: go(() => { switchTheme(t.id); api.setTheme(t.id).catch(() => undefined); }), keywords: "colour color look" });
    }
    return out;
  }, [projects, inProject, nav, onClose]);

  const shown = useMemo(() => {
    const ranked = items.map((it) => ({ it, s: Math.max(score(q, it.label), score(q, `${it.group} ${it.keywords ?? ""}`) * 0.6) }))
      .filter((x) => x.s > 0);
    if (q) ranked.sort((a, b) => b.s - a.s);
    return ranked.map((x) => x.it).slice(0, 40);
  }, [items, q]);
  useEffect(() => setSel(0), [q]);
  useEffect(() => { list.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" }); }, [sel]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(shown.length - 1, s + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    if (e.key === "Enter") { e.preventDefault(); shown[sel]?.run(); }
  };
  let lastGroup = "";
  return (
    <motion.div role="dialog" aria-modal aria-label="Command palette" onKeyDown={onKey}
      initial={{ opacity: 0, y: -14, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8, scale: 0.98 }}
      transition={{ type: "spring", stiffness: 420, damping: 32 }}
      className="relative mb-auto mt-[12vh] w-full max-w-[640px] overflow-hidden rounded-[22px] border border-line bg-surface elev">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--primary-2),transparent)]" />
      <div className="flex items-center gap-3 border-b border-line px-4">
        <Search className="h-5 w-5 text-primary" />
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to a project, a tab, a theme…"
          className="h-14 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted/80" aria-label="Search commands" />
        <span className="kbd">Esc</span>
      </div>
      <div ref={list} className="no-scrollbar max-h-[52vh] overflow-y-auto p-2">
        {!shown.length && <p className="px-3 py-10 text-center text-sm text-muted">Nothing matches “{q}”.</p>}
        {shown.map((it, i) => {
          const header = it.group !== lastGroup ? (lastGroup = it.group) : null;
          return (
            <div key={it.id}>
              {header && <p className="px-3 pb-1 pt-2.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted">{header}</p>}
              <button data-i={i} onMouseMove={() => setSel(i)} onClick={it.run}
                className={clsx("relative flex w-full items-center gap-3 rounded-[12px] px-3 py-2.5 text-left text-sm", i === sel ? "text-text" : "text-text/85")}>
                {i === sel && <motion.span layoutId="palette-sel" className="absolute inset-0 rounded-[12px] bg-primary/12 ring-1 ring-primary/35"
                  transition={{ type: "spring", stiffness: 600, damping: 42 }} />}
                <span className={clsx("relative grid h-8 w-8 place-items-center rounded-[10px]", i === sel ? "bg-primary text-on-primary" : "bg-bg-2 text-muted")}>{it.icon}</span>
                <span className="relative min-w-0 flex-1 truncate font-medium">{it.label}</span>
                {it.hint && <span className="relative shrink-0 text-xs text-muted">{it.hint}</span>}
                {i === sel && <CornerDownLeft className="relative h-4 w-4 shrink-0 text-primary" />}
              </button>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-[11px] text-muted">
        <span className="flex items-center gap-1"><span className="kbd">↑</span><span className="kbd">↓</span> move</span>
        <span className="flex items-center gap-1"><span className="kbd">Enter</span> open</span>
        <span className="ml-auto flex items-center gap-1"><span className="kbd">Ctrl</span><span className="kbd">K</span> anywhere</span>
      </div>
    </motion.div>
  );
}
