import Editor, { DiffEditor } from "@monaco-editor/react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import {
  BarChart3, BookOpen, Braces, ChevronRight, ClipboardCheck, Cloud, Code2, Download, Eye, FileCheck2, FileCode2, FileJson, FileText,
  FlaskConical, Folder, GitBranch, GitCompareArrows, Image as ImageIcon, Layers, Lock, Network, Search, Table2, Upload, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { DrawioViewer } from "../../components/DrawioViewer";
import { Markdown } from "../../components/Markdown";
import { Skeleton } from "../../components/ui";
import { CREW, ORION } from "../../lib/crew";
import { flowApi, ownerOf } from "../../lib/flow";
import { ACCENT } from "../../lib/themes";
import { languageOf, syncTheme } from "./monaco";

const META = Object.fromEntries([ORION, ...CREW].map((m) => [m.key, m]));
const OWNER_LABEL = (k: string) => (k === "user" ? "You (uploads)" : k === "platform" ? "Orkestra" : `${META[k]?.persona} · ${META[k]?.abbr}`);
const TEXT = /\.(md|txt|json|xml|drawio|xsd|ya?ml|py|tf|hcl|tfvars|sql|sh|csv|diff|log|js|ts|java|ini|toml|cfg|html|css)$/i;

/** The codebase in sections, grouped by phase (user, 10-02: "everything is in one place, organize them"; 10-03: "still
 *  cluttered: show them in a well organised way"). A file goes to the first section that matches. */
type Phase = "Plan" | "Design" | "Build" | "Test & sign-off" | "More";
interface Group { id: string; label: string; hint: string; icon: typeof FileText; phase: Phase; owner?: string; root?: string; match: (p: string) => boolean }
const ALSO_IN_TESTS = ["reports/coverage.html", "reports/pytest.md"];
const GROUPS: Group[] = [
  { id: "docs", label: "Documents", hint: "Requirement, plan, mapping, HLD and LLD: the agreed source of truth", icon: BookOpen, phase: "Plan",
    match: (p) => !p.includes("/") && /\.md$/i.test(p) },
  { id: "mapping", label: "Mapping data", hint: "Atlas's worked examples and sample messages", icon: Table2, phase: "Plan", owner: "ba", root: "mapping/", match: (p) => p.startsWith("mapping/") },
  { id: "inputs", label: "Your uploads", hint: "Files you attached for Echo and the crew", icon: Upload, phase: "Plan", root: "inputs/", match: (p) => p.startsWith("inputs/") },
  { id: "changes", label: "Change requests", hint: "The requirement diff of each change request", icon: GitCompareArrows, phase: "Plan", owner: "cto", root: "changes/", match: (p) => p.startsWith("changes/") },
  { id: "diagrams", label: "Diagrams", hint: "The architecture diagram (draw.io) and its data", icon: Network, phase: "Design", owner: "ta", root: "diagrams/", match: (p) => p.startsWith("diagrams/") },
  { id: "infra", label: "Infrastructure", hint: "Terra's Terraform: every AWS resource, its settings and names", icon: Cloud, phase: "Build", owner: "tp", root: "infra/", match: (p) => p.startsWith("infra/") },
  { id: "src", label: "Lambda code", hint: "What runs in AWS: one folder per function", icon: Braces, phase: "Build", owner: "de", root: "src/", match: (p) => p.startsWith("src/") },
  { id: "layers", label: "Layers", hint: "Shared code and libraries the functions load", icon: Layers, phase: "Build", owner: "de", root: "layers/", match: (p) => p.startsWith("layers/") },
  { id: "tests", label: "Unit tests", hint: "pytest: every worked example is a test · coverage report", icon: FlaskConical, phase: "Build", owner: "de", root: "tests/", match: (p) => p.startsWith("tests/") },
  { id: "testing", label: "Testing", hint: "Quinn's test plan, live results and bugs", icon: ClipboardCheck, phase: "Test & sign-off", owner: "qa",
    match: (p) => p.startsWith("qa/") || p.startsWith("bugs/") || /^reports\/(test_plan|live_qa|qa)/.test(p) },
  { id: "reports", label: "Reports", hint: "Coverage, tests, code review, deploy, AWS, drift", icon: BarChart3, phase: "Test & sign-off", root: "reports/", match: (p) => p.startsWith("reports/") },
  { id: "signoff", label: "Sign-offs", hint: "One per approved stage", icon: FileCheck2, phase: "Test & sign-off", root: "signoff/", match: (p) => p.startsWith("signoff/") },
  { id: "other", label: "Other", hint: "Everything else", icon: Folder, phase: "More", match: () => true },
];
const PHASES: Phase[] = ["Plan", "Design", "Build", "Test & sign-off", "More"];
/** "What this file is", in plain words, for the file cards. */
const HINTS: [RegExp, string | ((m: RegExpMatchArray) => string)][] = [
  [/^00_requirement\.md$/, "The signed-off requirement"], [/^plan\.md$/, "Orion's delivery plan"],
  [/^01_data_mapping\.md$/, "Field-by-field mapping, rules and examples"], [/^02_hld\.md$/, "High-level design"],
  [/^03_lld\.md$/, "Low-level design: every resource and setting"], [/^CHANGELOG\.md$/, "What changed in each version"],
  [/\.drawio$/, "The diagram: opens drawn, or as XML"], [/^diagrams\/.*\.json$/, "The data Archie's drawing is built from"],
  [/^infra\/names\.tf$/, "Every resource name, in one place"], [/^infra\/names\.auto\.tfvars\.json$/, "Your renames (they win over names.tf)"],
  [/^infra\/packages\.tf$/, "Orkestra's: deploys Dev's code and layer packages"], [/^infra\/layers\.tf$/, "Orkestra's: publishes Dev's layer packages"],
  [/^infra\/(versions|providers|backend)\.tf$/, "Terraform setup: providers, versions, state"], [/^infra\/variables\.tf$/, "Inputs: prefix, project id, boundary…"],
  [/^infra\/outputs\.tf$/, "What the deploy hands to Dev and Quinn (URLs, names)"], [/^infra\/locals\.tf$/, "Shared values used across the files"],
  [/^infra\/plan_preview\.json$/, "Terra's resource list for the preview"], [/^infra\/README\.md$/, "How it's built, deployed and destroyed"],
  [/^infra\/(\w+)\.tf$/, (m) => `Terraform: ${m[1].replace(/_/g, " ")}`],
  [/^src\/([^/]+)\/handler\.py$/, (m) => `The ${m[1]} function's entry point`], [/^src\/.*requirements\.txt$/, "Python packages the function needs"],
  [/^src\/.*\.py$/, "Lambda code"], [/^tests\/fixtures\//, "Test data: Atlas's worked examples"], [/^tests\/conftest\.py$/, "Shared pytest fixtures"],
  [/^tests\/.*test_(\w+)\.py$/, (m) => `Tests: ${m[1].replace(/_/g, " ")}`],
  [/^layers\/([^/]+)\/requirements\.txt$/, (m) => `Libraries in the ${m[1]} layer`], [/^layers\/([^/]+)\/python\//, (m) => `Code in the ${m[1]} layer`],
  [/^reports\/coverage\.html$/, "Coverage: every line tested or not (opens rendered)"], [/^reports\/pytest\.md$/, "Every unit test and its result"],
  [/^reports\/code_review\.md$/, "Archie's code review"], [/^reports\/code_deploy\.md$/, "What's live, and Dev's full-flow test"],
  [/^reports\/deploy_plan\.md$/, "Terra's plan before terraform apply"], [/^reports\/aws_inventory\.md$/, "Everything in AWS, with console links"],
  [/^reports\/aws_access\.md$/, "Orion's AWS role for each agent"], [/^reports\/drift\.md$/, "Changes made in AWS outside Terraform"],
  [/^reports\/test_plan\.md$/, "Quinn's test plan"], [/^reports\/lambda_test_event\.json$/, "Paste it into the Lambda console's Test tab"],
  [/^reports\/infra_validate\.md$/, "terraform validate results"], [/^signoff\/\d+-(.+)\.md$/, (m) => `Sign-off: ${m[1].replace(/-/g, " ")}`],
  [/^changes\/(CR-\d+)/, (m) => `Requirement diff for ${m[1]}`], [/^inputs\//, "A file you attached"],
];
const hintOf = (p: string) => { for (const [re, h] of HINTS) { const m = p.match(re); if (m) return typeof h === "string" ? h : h(m); } return ""; };
const PART_KEY = "ork-code-section";
const DOC_LABEL: Record<string, string> = {
  "00_requirement.md": "Requirement · Echo", "plan.md": "Delivery plan · Orion", "01_data_mapping.md": "Data mapping · Atlas",
  "02_hld.md": "High-level design · Archie", "03_lld.md": "Low-level design · Archie", "CHANGELOG.md": "What changed per version",
};
const groupOf = (p: string) => GROUPS.find((g) => g.match(p))!;
const DRAW_KEY = "ork-drawio-open";

/** The project's codebase as VS Code shows it: explorer with owners, tabs, the Monaco editor, versions and diffs.
 *  Read-only on purpose: the crew writes, you review (changes go through Echo or a change request). */
export default function CodeTab({ projectId, projectName, openPath }: { projectId: string; projectName: string; openPath?: string | null }) {
  const [version, setVersion] = useState<string | undefined>();
  const { data, isLoading } = useQuery({ queryKey: ["files", projectId, version ?? "current"], queryFn: () => flowApi.files(projectId, version) });
  const [tabs, setTabs] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState(true);
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [section, setSectionRaw] = useState<string>(() => { try { return localStorage.getItem(PART_KEY) || "docs"; } catch { return "docs"; } });
  const setSection = (id: string) => { setSectionRaw(id); setQuery(""); try { localStorage.setItem(PART_KEY, id); } catch { /* private window */ } };
  const shownVersion = version ?? data?.current;

  const open = (path: string) => {
    setTabs((t) => (t.includes(path) ? t : [...t, path]));
    setActive(path);
    setCompareWith(null);
    setSectionRaw(groupOf(path).id);
  };
  const close = (path: string) => {
    setTabs((t) => {
      const next = t.filter((x) => x !== path);
      if (active === path) setActive(next[next.length - 1] ?? null);
      return next;
    });
  };
  useEffect(() => { if (openPath) open(openPath); }, [openPath]); // eslint-disable-line react-hooks/exhaustive-deps

  const sections = useMemo(() => {
    const by: Record<string, { path: string; size: number }[]> = {};
    (data?.files ?? []).forEach((f) => { (by[groupOf(f.path).id] ??= []).push(f); });
    // the unit tests' own results live in reports/: show them with the tests too (user, 10-05: "the coverage report isn't here")
    if (by.tests?.length) by.tests.push(...(data?.files ?? []).filter((f) => ALSO_IN_TESTS.includes(f.path)));
    return GROUPS.filter((g) => by[g.id]?.length).map((g) => ({ g, files: by[g.id], size: by[g.id].reduce((n, f) => n + f.size, 0) }));
  }, [data]);
  const byOwner = useMemo(() => {
    const m: Record<string, number> = {};
    (data?.files ?? []).forEach((f) => { const o = ownerOf(f.path); m[o] = (m[o] ?? 0) + 1; });
    return m;
  }, [data]);
  const activeFile = data?.files.find((f) => f.path === active);

  if (isLoading || !data) return <Skeleton className="h-[640px]" />;
  const current = sections.find((s) => s.g.id === section) ?? sections[0];
  const searching = query.trim().length > 0;
  const listed = searching ? data.files.filter((f) => f.path.toLowerCase().includes(query.trim().toLowerCase())) : current?.files ?? [];
  const zipUrl = (g: Group, files: { path: string }[]) => `/api/projects/${projectId}/files/zip?paths=${encodeURIComponent(g.root ? g.root : files.map((f) => f.path).join(","))}`
    + `&name=${g.id}${version ? `&version=${version}` : ""}`;
  const everything = `/api/projects/${projectId}/files/zip?paths=${encodeURIComponent(sections.flatMap(({ g, files }) => (g.root ? [g.root] : files.map((f) => f.path))).join(","))}`
    + `&name=everything${version ? `&version=${version}` : ""}`;

  return (
    <div className="flex h-[calc(100dvh-13rem)] min-h-[560px] flex-col overflow-hidden rounded-[22px] border border-line bg-bg-2 shadow-[0_20px_60px_-40px_rgba(0,0,0,0.7)]">
      <div className="flex min-h-0 flex-1">
        {/* 1 · sections, grouped by phase */}
        <nav className="hidden w-[214px] shrink-0 flex-col border-r border-line bg-surface md:flex" aria-label="Sections">
          <div className="space-y-2 px-3 pb-2 pt-3">
            <div className="flex items-center gap-2">
              <p className="min-w-0 flex-1 truncate font-display text-sm font-semibold" title={projectName}>{projectName}</p>
              <select value={shownVersion} onChange={(e) => { setVersion(e.target.value === data.current ? undefined : e.target.value); setTabs([]); setActive(null); }}
                title="Project version" className="rounded-[8px] border border-line bg-bg-2 px-1.5 py-0.5 font-mono text-[11px]">
                {data.versions.map((v) => <option key={v} value={v}>{v}{v === data.current ? " ·now" : ""}</option>)}
              </select>
            </div>
            <label className="flex items-center gap-1.5 rounded-[10px] border border-line bg-bg-2 px-2 focus-within:border-primary">
              <Search className="h-3.5 w-3.5 text-muted" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a file…" aria-label="Find a file"
                className="min-w-0 flex-1 bg-transparent py-1.5 text-[13px] outline-none" />
              {searching && <button onClick={() => setQuery("")} aria-label="Clear"><X className="h-3.5 w-3.5 text-muted" /></button>}
            </label>
          </div>
          <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {PHASES.map((ph) => {
              const items = sections.filter((s) => s.g.phase === ph);
              if (!items.length) return null;
              return (
                <div key={ph} className="mb-2">
                  <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">{ph}</p>
                  {items.map(({ g, files }) => {
                    const Icon = g.icon;
                    const on = !searching && current?.g.id === g.id;
                    const tone = g.owner ? ACCENT[META[g.owner].accent] : "var(--primary)";
                    return (
                      <button key={g.id} onClick={() => setSection(g.id)} aria-current={on}
                        className={clsx("relative flex w-full items-center gap-2 rounded-[10px] px-2 py-1.5 text-left", on ? "text-text" : "text-muted hover:text-text")}>
                        {on && <motion.span layoutId="code-section" className="absolute inset-0 rounded-[10px] bg-bg-2 ring-1 ring-line" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
                        <span className="relative grid h-7 w-7 shrink-0 place-items-center rounded-[8px]" style={{ color: tone, background: `color-mix(in srgb, ${tone} 13%, transparent)` }}>
                          <Icon className="h-4 w-4" /></span>
                        <span className="relative min-w-0 flex-1 truncate text-[13px] font-medium">{g.label}</span>
                        <span className="relative rounded-full bg-bg-2 px-1.5 text-[10.5px] text-muted">{files.length}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
          <a href={everything} className="m-2 inline-flex items-center justify-center gap-1.5 rounded-[10px] border border-line px-2 py-1.5 text-[12px] font-semibold text-muted hover:border-primary hover:text-primary"
            title="Every file of this version as one zip"><Download className="h-3.5 w-3.5" />Download everything</a>
        </nav>

        {/* 2 · the section's files, as cards */}
        <aside className="hidden w-[300px] shrink-0 flex-col border-r border-line bg-surface/50 lg:flex">
          {searching ? (
            <div className="border-b border-line px-3.5 py-3"><p className="text-sm font-semibold">Files matching “{query.trim()}”</p><p className="text-[11.5px] text-muted">{listed.length} found</p></div>
          ) : current && (
            <div className="border-b border-line px-3.5 py-3">
              <div className="flex items-center gap-2">
                {current.g.owner && <AgentAvatar agent={current.g.owner} accent={META[current.g.owner].accent} status="done" size={22} />}
                <p className="min-w-0 flex-1 truncate font-display text-[15px] font-semibold">{current.g.label}</p>
                <a href={zipUrl(current.g, current.files)} title={`Download every file in ${current.g.label} as a zip`}
                  className="press inline-flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-[11px] font-semibold text-primary hover:bg-primary/20">
                  <Download className="h-3.5 w-3.5" />.zip</a>
              </div>
              <p className="mt-1 text-[11.5px] leading-snug text-muted">{current.g.hint}{current.g.owner ? ` · ${META[current.g.owner].persona}'s` : ""} · {current.files.length} file{current.files.length === 1 ? "" : "s"} · {fmtSize(current.size)}</p>
            </div>
          )}
          <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto p-2">
            <FileCards files={listed} base={searching ? "" : current?.g.root ?? ""} active={active} onOpen={open} showPath={searching} />
          </div>
        </aside>

        {/* 3 · the viewer */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="no-scrollbar flex shrink-0 gap-1.5 overflow-x-auto border-b border-line bg-surface px-2 py-1.5 md:hidden">
            {sections.map(({ g, files }) => (
              <button key={g.id} onClick={() => { setSection(g.id); setActive(null); }}
                className={clsx("shrink-0 rounded-full px-3 py-1 text-xs font-semibold", current?.g.id === g.id ? "bg-primary text-on-primary" : "bg-bg-2 text-muted")}>
                {g.label} <span className="opacity-70">{files.length}</span></button>
            ))}
          </div>
          {tabs.length > 0 && (
            <div className="no-scrollbar flex shrink-0 overflow-x-auto border-b border-line bg-surface/60">
              {tabs.map((t) => (
                <div key={t} onMouseDown={(e) => e.button === 1 && close(t)}
                  className={clsx("group flex shrink-0 cursor-pointer items-center gap-2 border-r border-line px-3 py-2 text-[13px]",
                    t === active ? "bg-bg-2 text-text shadow-[inset_0_2px_0_var(--primary)]" : "text-muted hover:text-text")}
                  onClick={() => { setActive(t); setCompareWith(null); }}>
                  <FileIcon path={t} /><span className="max-w-[180px] truncate">{t.split("/").pop()}</span>
                  <button onClick={(e) => { e.stopPropagation(); close(t); }} aria-label={`Close ${t}`}
                    className="rounded p-0.5 opacity-60 hover:bg-surface-2 hover:opacity-100"><X className="h-3.5 w-3.5" /></button>
                </div>
              ))}
            </div>
          )}
          {active && activeFile ? (
            <FileView projectId={projectId} path={active} version={shownVersion!} versions={data.versions} isCurrent={!version}
              preview={preview} setPreview={setPreview} compareWith={compareWith} setCompareWith={setCompareWith} />
          ) : (
            <SectionHome projectName={projectName} byOwner={byOwner} total={data.files.length} version={shownVersion!} current={current}
              listed={listed} searching={searching} zip={current ? zipUrl(current.g, current.files) : ""} active={active} onOpen={open} />
          )}
        </main>
      </div>

      {/* status bar */}
      <footer className="flex shrink-0 items-center gap-4 overflow-hidden whitespace-nowrap bg-primary px-3 py-1 text-[11.5px] text-on-primary">
        <span className="inline-flex items-center gap-1"><GitBranch className="h-3.5 w-3.5" />{shownVersion}{version ? " (older version)" : ""}</span>
        <span className="inline-flex items-center gap-1"><Lock className="h-3.5 w-3.5" />Read-only · the crew writes, you review</span>
        <span className="ml-auto">{data.files.length} files</span>
        {active && <span>{languageOf(active)}</span>}
        {active && <span>{OWNER_LABEL(ownerOf(active))}</span>}
        {activeFile && <span>{fmtSize(activeFile.size)}</span>}
      </footer>
    </div>
  );
}

function FileView({ projectId, path, version, versions, isCurrent, preview, setPreview, compareWith, setCompareWith }: {
  projectId: string; path: string; version: string; versions: string[]; isCurrent: boolean; preview: boolean;
  setPreview: (v: boolean) => void; compareWith: string | null; setCompareWith: (v: string | null) => void;
}) {
  const textual = TEXT.test(path);
  const { data: text, isLoading, isError, refetch } = useQuery({
    queryKey: ["file", projectId, version, path], queryFn: () => flowApi.fileAt(projectId, path, isCurrent ? undefined : version), enabled: textual,
  });
  const { data: older } = useQuery({
    queryKey: ["file", projectId, compareWith, path], queryFn: () => flowApi.fileAt(projectId, path, compareWith!), enabled: !!compareWith,
  });
  const owner = ownerOf(path);
  const md = path.endsWith(".md");
  const html = /\.html?$/i.test(path);
  const drawio = path.endsWith(".drawio");
  const theme = useMemo(() => syncTheme(), []);
  const others = versions.filter((v) => v !== version);
  // a .drawio file: ask once whether to show the diagram or its XML (or remember the answer)
  const stored = () => { try { return localStorage.getItem(DRAW_KEY) as "diagram" | "xml" | null; } catch { return null; } };
  const [draw, setDraw] = useState<"ask" | "diagram" | "xml">(() => stored() ?? "ask");
  const [remember, setRemember] = useState(false);
  useEffect(() => { setDraw(stored() ?? "ask"); }, [path]);
  const choose = (m: "diagram" | "xml") => { setDraw(m); if (remember) { try { localStorage.setItem(DRAW_KEY, m); } catch { /* private window */ } } };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-3 py-1.5 text-[12px] text-muted">
        <span className="font-mono">{version}</span>
        {path.split("/").map((seg, i, all) => (
          <span key={i} className="inline-flex items-center gap-1"><ChevronRight className="h-3 w-3" />
            <span className={i === all.length - 1 ? "text-text" : ""}>{seg}</span></span>
        ))}
        <span className="ml-2 inline-flex items-center gap-1.5 rounded-full bg-surface px-2 py-0.5" title="Who owns this file">
          {owner in META ? <AgentAvatar agent={owner} accent={META[owner].accent} status="done" size={16} /> : null}{OWNER_LABEL(owner)}
        </span>
        <span className="flex-1" />
        {(md || html) && !compareWith && (
          <button onClick={() => setPreview(!preview)} className={clsx("inline-flex items-center gap-1 rounded-[8px] px-2 py-1", preview ? "bg-primary/15 text-primary" : "hover:bg-surface")}
            title={preview ? "Showing it rendered: click for the source" : "Showing the source: click to render it"}>
            <Eye className="h-3.5 w-3.5" />{preview ? "Preview" : "Source"}
          </button>
        )}
        {drawio && !compareWith && draw !== "ask" && (
          <span className="inline-flex rounded-[8px] bg-surface p-0.5">
            {(["diagram", "xml"] as const).map((m) => (
              <button key={m} onClick={() => setDraw(m)} className={clsx("inline-flex items-center gap-1 rounded-[6px] px-2 py-0.5", draw === m ? "bg-primary/15 text-primary" : "hover:text-text")}>
                {m === "diagram" ? <Network className="h-3.5 w-3.5" /> : <Code2 className="h-3.5 w-3.5" />}{m === "diagram" ? "Diagram" : "XML"}
              </button>
            ))}
          </span>
        )}
        {textual && others.length > 0 && (
          <label className="inline-flex items-center gap-1 rounded-[8px] px-1.5 py-0.5 hover:bg-surface" title="Compare with another version">
            <GitCompareArrows className="h-3.5 w-3.5" />
            <select value={compareWith ?? ""} onChange={(e) => setCompareWith(e.target.value || null)} className="bg-transparent text-[12px] outline-none">
              <option value="">Compare…</option>
              {others.map((v) => <option key={v} value={v}>with {v}</option>)}
            </select>
          </label>
        )}
        <a href={`/api/projects/${projectId}/files/content?path=${encodeURIComponent(path)}${isCurrent ? "" : `&version=${version}`}&download=1`}
          className="inline-flex items-center gap-1 rounded-[8px] px-2 py-1 hover:bg-surface hover:text-text"><Download className="h-3.5 w-3.5" />Download</a>
      </div>
      <div className="min-h-0 flex-1">
        {!textual ? <p className="p-6 text-sm text-muted">Binary file: use Download.</p>
          : isError ? (
            <div className="grid h-full place-items-center p-6 text-center text-sm">
              <div><p className="font-semibold">Couldn't load {path.split("/").pop()}.</p>
                <button onClick={() => refetch()} className="mt-2 rounded-full border border-line px-3 py-1 text-xs font-semibold hover:border-primary hover:text-primary">Try again</button></div>
            </div>
          ) : isLoading || text === undefined ? <Skeleton className="m-4 h-64" />
          : text.trim() === "" ? (  // an empty file used to look like a grey box (10-03)
            <div className="grid h-full place-items-center p-6 text-center">
              <div><FileText className="mx-auto h-8 w-8 text-muted" /><p className="mt-2 font-semibold">{path.split("/").pop()} is empty in {version}</p>
                <p className="text-sm text-muted">The file exists but has no content{owner in META ? ` (${META[owner].persona}'s file)` : ""}.</p></div>
            </div>
          )
            : compareWith ? (
              older === undefined ? <Skeleton className="m-4 h-64" /> : (
                <DiffEditor original={older} modified={text} language={languageOf(path)} theme={theme} height="100%"
                  options={{ readOnly: true, renderSideBySide: true, fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: 13, minimap: { enabled: false } }} />
              )
            ) : drawio && draw === "ask" ? (
              <div className="grid h-full place-items-center overflow-y-auto p-6">
                <div className="w-full max-w-lg rounded-[22px] border border-line bg-surface p-6 text-center shadow-[0_20px_50px_-30px_rgba(0,0,0,0.6)]">
                  <Network className="mx-auto h-9 w-9 text-primary" />
                  <p className="mt-2 font-display text-lg font-semibold">How do you want to open {path.split("/").pop()}?</p>
                  <p className="mt-1 text-sm text-muted">A .drawio file is a diagram saved as XML.</p>
                  <div className="mt-4 grid gap-2.5 sm:grid-cols-2">
                    <button onClick={() => choose("diagram")} className="press rounded-[16px] border-2 border-primary/50 bg-primary/[0.07] p-4 text-left hover:border-primary">
                      <Network className="h-5 w-5 text-primary" /><b className="mt-1 block">Show the diagram</b><span className="text-xs text-muted">Drawn with the draw.io viewer: zoom, pan, layers</span>
                    </button>
                    <button onClick={() => choose("xml")} className="press rounded-[16px] border-2 border-line p-4 text-left hover:border-primary">
                      <Code2 className="h-5 w-5 text-muted" /><b className="mt-1 block">Show the XML</b><span className="text-xs text-muted">The raw file, as draw.io saves it</span>
                    </button>
                  </div>
                  <label className="mt-3 inline-flex cursor-pointer items-center gap-2 text-xs text-muted">
                    <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-[var(--primary)]" />Remember my choice for .drawio files</label>
                </div>
              </div>
            ) : drawio && draw === "diagram" ? (
              <div className="no-scrollbar h-full overflow-y-auto"><DrawioViewer xml={text} /></div>
            ) : html && preview ? (
              // a report Orkestra wrote (e.g. the coverage report): rendered with scripts and forms disabled
              <iframe title={path} sandbox="" srcDoc={text} className="h-full w-full border-0 bg-white" />
            ) : md && preview ? (
              <div className="no-scrollbar h-full overflow-y-auto px-6 py-5"><div className="mx-auto max-w-4xl"><Markdown>{text}</Markdown></div></div>
            ) : (
              <Editor value={text} language={languageOf(path)} theme={theme} height="100%"
                options={{ readOnly: true, domReadOnly: true, fontFamily: "JetBrains Mono, Consolas, monospace", fontSize: 13, wordWrap: md ? "on" : "off",
                  minimap: { enabled: true }, scrollBeyondLastLine: false, renderWhitespace: "selection", smoothScrolling: true }} />
            )}
      </div>
    </div>
  );
}

/** No file open: the section at a glance (its files as cards, the zip), and who owns what. */
function SectionHome({ projectName, byOwner, total, version, current, listed, searching, zip, active, onOpen }: {
  projectName: string; byOwner: Record<string, number>; total: number; version: string;
  current?: { g: Group; files: { path: string; size: number }[]; size: number }; listed: { path: string; size: number }[];
  searching: boolean; zip: string; active: string | null; onOpen: (p: string) => void;
}) {
  const owners = ["intake", "cto", "ba", "ta", "tp", "de", "qa"];
  const g = current?.g;
  const Icon = g?.icon ?? FileText;
  const tone = g?.owner ? ACCENT[META[g.owner].accent] : "var(--primary)";
  return (
    <div className="no-scrollbar h-full overflow-y-auto p-5 sm:p-8">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{projectName} · {version} · {total} files</p>
      {searching ? <h2 className="mt-1 font-display text-2xl font-bold">{listed.length} matching file{listed.length === 1 ? "" : "s"}</h2> : g && (
        <motion.div key={g.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-2 flex flex-wrap items-center gap-3">
          <span className="grid h-12 w-12 place-items-center rounded-[15px]" style={{ color: tone, background: `color-mix(in srgb, ${tone} 14%, transparent)` }}><Icon className="h-6 w-6" /></span>
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-2xl font-bold tracking-tight">{g.label}</h2>
            <p className="text-sm text-muted">{g.hint}{g.owner ? ` · written by ${META[g.owner].persona}` : ""} · {current!.files.length} file{current!.files.length === 1 ? "" : "s"} · {fmtSize(current!.size)}</p>
          </div>
          <a href={zip} className="press inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-2 text-sm font-semibold text-on-primary shadow-[0_8px_20px_-10px_var(--primary)] hover:opacity-90">
            <Download className="h-4 w-4" />Download all (.zip)</a>
        </motion.div>
      )}
      <p className="mt-5 text-[11px] font-semibold uppercase tracking-wider text-muted">Open a file</p>
      <div className="mt-2"><FileCards files={listed} base={searching ? "" : g?.root ?? ""} active={active} onOpen={onOpen} showPath={searching} grid /></div>
      <p className="mt-7 text-[11px] font-semibold uppercase tracking-wider text-muted">Who owns what · nobody edits another agent's files</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {owners.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface/70 py-1 pl-1 pr-2.5 text-xs">
            <AgentAvatar agent={k} accent={META[k].accent} status={byOwner[k] ? "done" : "waiting"} size={20} />
            <b style={{ color: ACCENT[META[k].accent] }}>{META[k].persona}</b><span className="text-muted">{byOwner[k] ?? 0} files</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** A section's files as cards: a friendly name, what the file is, its folder (one function per folder in src/). */
function FileCards({ files, base, active, onOpen, showPath, grid }: {
  files: { path: string; size: number }[]; base: string; active: string | null; onOpen: (p: string) => void; showPath?: boolean; grid?: boolean;
}) {
  const folders: Record<string, { path: string; size: number }[]> = {};
  for (const f of files) {
    const outside = !showPath && !!base && !f.path.startsWith(base);  // e.g. the coverage report shown with the unit tests
    const rel = showPath ? f.path : outside ? f.path : f.path.slice(base.length);
    const cut = rel.lastIndexOf("/");
    (folders[showPath ? "" : (outside ? "@" : "") + (cut >= 0 ? rel.slice(0, cut + 1) : "")] ??= []).push(f);
  }
  const keys = Object.keys(folders).sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b, undefined, { numeric: true })));
  if (!files.length) return <p className="px-2 py-6 text-center text-sm text-muted">No files here yet.</p>;
  return (
    <div className="space-y-3">
      {keys.map((k) => (
        <div key={k}>
          {k && <p className="mb-1 flex items-center gap-1 px-1 font-mono text-[11px] text-muted"><Folder className="h-3.5 w-3.5 text-primary-2" />
            {k.startsWith("@") ? `${k.slice(1)} · test results` : `${base}${k}`}</p>}
          <div className={clsx(grid ? "grid gap-2 sm:grid-cols-2 xl:grid-cols-3" : "space-y-1")}>
            {folders[k].sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true })).map((f) => {
              const name = f.path.split("/").pop()!;
              const label = DOC_LABEL[f.path]?.split(" · ")[0];
              const owner = ownerOf(f.path);
              const on = active === f.path;
              return (
                <button key={f.path} onClick={() => onOpen(f.path)} title={f.path}
                  className={clsx("group flex w-full items-start gap-2.5 rounded-[12px] border px-2.5 py-2 text-left transition-colors",
                    on ? "border-primary/60 bg-primary/10" : "border-transparent hover:border-line hover:bg-surface")}>
                  <span className="mt-0.5"><FileIcon path={f.path} /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-semibold group-hover:text-primary">{label ?? name}</span>
                    <span className="block truncate text-[11.5px] text-muted">{showPath ? f.path : hintOf(f.path) || (label ? name : fmtSize(f.size))}</span>
                  </span>
                  {owner in META && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: ACCENT[META[owner].accent] }} title={OWNER_LABEL(owner)} />}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function FileIcon({ path }: { path: string }) {
  const ext = path.split(".").pop()?.toLowerCase();
  const map: Record<string, [typeof FileText, string]> = {
    md: [FileText, "#519aba"], py: [FileCode2, "#3b82f6"], tf: [FileCode2, "#844fba"], hcl: [FileCode2, "#844fba"], json: [FileJson, "#cbcb41"],
    xml: [FileCode2, "#e37933"], drawio: [Network, "#f08705"], yaml: [FileCode2, "#cb171e"], yml: [FileCode2, "#cb171e"],
    png: [ImageIcon, "#a074c4"], diff: [GitCompareArrows, "#41b883"],
  };
  const [Icon, color] = map[ext ?? ""] ?? [FileText, "var(--text-muted)"];
  return <Icon className="h-4 w-4 shrink-0" style={{ color }} />;
}

const fmtSize = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`);
