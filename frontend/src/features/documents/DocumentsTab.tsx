import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { Download, FileCode2, FileText, FolderOpen, Image as ImageIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CodeBlock, Markdown } from "../../components/Markdown";
import { Skeleton } from "../../components/ui";
import { apiFetch } from "../../lib/api";

interface FileRow { path: string; size: number; modified: string }
interface FilesResp { versions: string[]; current: string; files: FileRow[] }

const GROUPS: { title: string; test: (p: string) => boolean }[] = [
  { title: "Requirement & plan", test: (p) => p.startsWith("00_") || p === "plan.md" || p === "access_plan.md" },
  { title: "Data mapping", test: (p) => p.startsWith("01_") },
  { title: "Design (HLD / LLD)", test: (p) => p.startsWith("02_") || p.startsWith("03_") || p.startsWith("diagrams/") },
  { title: "Infrastructure", test: (p) => p.startsWith("infra/") },
  { title: "Code & tests", test: (p) => p.startsWith("src/") || p.startsWith("tests/") },
  { title: "Reports & bugs", test: (p) => p.startsWith("reports/") || p.startsWith("bugs/") },
  { title: "Your uploads", test: (p) => p.startsWith("inputs/") },
];
const TITLES: Record<string, string> = {
  "00_requirement.md": "Requirement", "plan.md": "Delivery plan (Orion)", "01_data_mapping.md": "Data mapping",
  "02_hld.md": "High-level design", "03_lld.md": "Low-level design", "access_plan.md": "Access plan",
};
const kindOf = (p: string) => (p.endsWith(".md") ? "md" : /\.(png|jpe?g|svg|gif)$/i.test(p) ? "img" : /\.(docx|pdf|zip)$/i.test(p) ? "bin" : "code");

/** Every file the crew produced, rendered — the same files the agents read. Versions + download. */
export function DocumentsTab({ projectId }: { projectId: string }) {
  const [version, setVersion] = useState<string | undefined>();
  const { data, isLoading } = useQuery({
    queryKey: ["files", projectId, version],
    queryFn: async (): Promise<FilesResp> => {
      const r = await apiFetch(`/api/projects/${projectId}/files${version ? `?version=${version}` : ""}`);
      return r.json();
    },
  });
  const files = useMemo(() => (data?.files ?? []).filter((f) => !f.path.endsWith(".gitkeep")), [data]);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    if (!files.length) return setSel(null);
    if (!sel || !files.some((f) => f.path === sel)) setSel((files.find((f) => f.path === "00_requirement.md") ?? files[0]).path);
  }, [files, sel]);
  const v = version ?? data?.current;

  const { data: content, isFetching } = useQuery({
    queryKey: ["file", projectId, v, sel], enabled: !!sel && kindOf(sel ?? "") !== "bin" && kindOf(sel ?? "") !== "img",
    queryFn: async () => (await apiFetch(`/api/projects/${projectId}/files/content?path=${encodeURIComponent(sel!)}${v ? `&version=${v}` : ""}`)).text(),
  });
  const url = (p: string, dl = false) => `/api/projects/${projectId}/files/content?path=${encodeURIComponent(p)}${v ? `&version=${v}` : ""}${dl ? "&download=1" : ""}`;

  if (isLoading) return <div className="grid gap-4 lg:grid-cols-[300px_1fr]"><Skeleton className="h-96" /><Skeleton className="h-96" /></div>;
  if (!files.length) {
    return (
      <div className="flex flex-col items-center rounded-[28px] border border-line bg-surface px-6 py-16 text-center">
        <div className="grid h-16 w-16 place-items-center rounded-[20px] bg-primary/12 text-primary"><FolderOpen className="h-8 w-8" /></div>
        <h3 className="mt-4 font-display text-xl font-semibold">No documents yet</h3>
        <p className="mt-1 max-w-md text-sm text-muted">Echo writes <b className="text-text">00_requirement.md</b> when you sign off, and every agent after adds its own files here: mapping, designs, infrastructure, code and reports.</p>
      </div>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="rounded-[24px] border border-line bg-surface p-3">
        <div className="mb-2 flex items-center justify-between px-1">
          <p className="text-sm font-semibold">Files</p>
          {(data?.versions.length ?? 0) > 0 && (
            <select value={v} onChange={(e) => setVersion(e.target.value)} aria-label="Version"
              className="rounded-[10px] border border-line bg-bg-2 px-2 py-1 font-mono text-xs">
              {data!.versions.map((x) => <option key={x} value={x}>{x}{x === data!.current ? " (current)" : ""}</option>)}
            </select>
          )}
        </div>
        {GROUPS.map((g) => {
          const list = files.filter((f) => g.test(f.path));
          if (!list.length) return null;
          return (
            <div key={g.title} className="mb-2">
              <p className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{g.title}</p>
              {list.map((f) => {
                const k = kindOf(f.path);
                const Icon = k === "md" ? FileText : k === "img" ? ImageIcon : FileCode2;
                return (
                  <button key={f.path} onClick={() => setSel(f.path)}
                    className={clsx("relative flex w-full items-center gap-2.5 rounded-[12px] px-2.5 py-2 text-left text-sm", sel === f.path ? "text-text" : "text-muted hover:bg-bg-2/70 hover:text-text")}>
                    {sel === f.path && <motion.span layoutId="doc-sel" className="absolute inset-0 rounded-[12px] bg-primary/12 ring-1 ring-primary/30" />}
                    <Icon className="relative h-4 w-4 shrink-0" />
                    <span className="relative min-w-0 flex-1">
                      <span className="block truncate font-medium">{TITLES[f.path] ?? f.path.split("/").pop()}</span>
                      <span className="block truncate font-mono text-[10px] text-muted">{f.path}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </aside>

      <section className="min-w-0 rounded-[24px] border border-line bg-surface">
        {sel && (
          <>
            <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate font-display font-semibold">{TITLES[sel] ?? sel.split("/").pop()}</p>
                <p className="font-mono text-[11px] text-muted">{v} / {sel}</p>
              </div>
              <a href={url(sel, true)} className="inline-flex items-center gap-1.5 rounded-[10px] border border-line px-3 py-1.5 text-sm hover:border-primary hover:text-primary">
                <Download className="h-4 w-4" />Download
              </a>
            </div>
            <motion.div key={sel + v} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="px-5 py-5 sm:px-8 sm:py-7">
              {kindOf(sel) === "img" ? <img src={url(sel)} alt={sel} className="mx-auto max-w-full rounded-[12px] border border-line" />
                : kindOf(sel) === "bin" ? <p className="text-sm text-muted">Preview isn't available for this file type. Use Download.</p>
                  : isFetching && !content ? <div className="space-y-2"><Skeleton className="h-5 w-2/3" /><Skeleton className="h-4" /><Skeleton className="h-4 w-5/6" /></div>
                    : kindOf(sel) === "md" ? <article className="mx-auto max-w-3xl"><Markdown className="doc">{content ?? ""}</Markdown></article>
                      : <CodeBlock raw={content ?? ""} />}
            </motion.div>
          </>
        )}
      </section>
    </div>
  );
}
