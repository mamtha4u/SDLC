import clsx from "clsx";
import { CheckCheck, ChevronDown, Download, FileText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fileUrl } from "../../lib/flow";
import { CodeBlock, prettyJson, prettyXml } from "../Markdown";

/** Shared by Echo's interview and every agent's kickoff conversation (features/talk). */

/** User messages: pasted XML/JSON is shown formatted (with the Indented/Original toggle); files as download chips. */
export function UserBubble({ text, attachments, projectId }: { text: string; attachments?: string[]; projectId: string }) {
  const [body, att] = text.split("\n\n📎 Attached: ");
  const names = attachments?.length ? attachments : att ? att.split(", ") : [];
  const trimmed = body.startsWith("📎 Attached: ") ? "" : body.trim();
  const codeStart = trimmed.search(/[<{[]/);
  const prose = codeStart > 0 ? trimmed.slice(0, codeStart).trim() : codeStart === 0 ? "" : trimmed;
  const code = codeStart >= 0 ? trimmed.slice(codeStart) : "";
  const isCode = !!code && !!(prettyXml(code) || prettyJson(code));
  return (
    <div className="space-y-2">
      {(prose || (!isCode && trimmed)) && (
        <div className="rounded-[20px] rounded-tr-md bg-primary px-4 py-2.5 text-[14px] text-on-primary">
          <p className="whitespace-pre-wrap">{isCode ? prose : trimmed}</p>
        </div>
      )}
      {isCode && <CodeBlock raw={code} />}
      {names.length > 0 && <FileChips names={names} projectId={projectId} align="end" />}
    </div>
  );
}

export function FileChips({ names, projectId, align }: { names: string[]; projectId: string; align: "start" | "end" }) {
  return (
    <div className={clsx("mt-2 flex flex-wrap gap-1.5", align === "end" && "justify-end")}>
      {names.map((n) => (
        <a key={n} href={fileUrl(projectId, n)} title={`Download ${n}`}
          className="group inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs transition-colors hover:border-primary hover:text-primary">
          <FileText className="h-3.5 w-3.5 text-primary-2" />{n}
          <Download className="h-3 w-3 opacity-50 group-hover:opacity-100" />
        </a>
      ))}
    </div>
  );
}

/** Smoothly reveals `target` as it grows (catches up fast when far behind), for a live typing feel. */
export function useTypewriter(target: string) {
  const [shown, setShown] = useState("");
  const shownRef = useRef("");
  useEffect(() => {
    if (!target) { shownRef.current = ""; setShown(""); return; }
    if (!target.startsWith(shownRef.current)) { shownRef.current = ""; }
    let raf = 0;
    const tick = () => {
      const cur = shownRef.current;
      if (cur.length >= target.length) return;
      const step = Math.max(2, Math.ceil((target.length - cur.length) / 12));
      shownRef.current = target.slice(0, cur.length + step);
      setShown(shownRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return shown;
}

/** "Added to …": tap to see exactly what the agent saved for each topic. */
export function Captured({ ids, labels, answers, title = "Added to your requirement" }: {
  ids: string[]; labels: Record<string, string>; answers: Record<string, string>; title?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2.5 border-t border-line pt-2.5">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-1.5 text-left text-[12px] font-semibold text-success">
        <CheckCheck className="h-3.5 w-3.5" />{title} ({ids.length})
        <span className="ml-auto text-[11px] font-medium text-muted">{open ? "Hide" : "Show what I saved"}</span>
        <ChevronDown className={clsx("h-3.5 w-3.5 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {!open ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {ids.map((q) => <button key={q} onClick={() => setOpen(true)} className="rounded-md bg-success/12 px-1.5 py-0.5 text-[11px] text-text hover:bg-success/20">{labels[q] ?? q}</button>)}
        </div>
      ) : (
        <dl className="mt-2 space-y-1.5">
          {ids.map((q) => (
            <div key={q} className="rounded-[10px] bg-bg-2/70 px-3 py-2">
              <dt className="text-[11px] text-muted">{labels[q] ?? q}</dt>
              <dd className="whitespace-pre-wrap text-[13px]">{answers[q] || <i className="text-muted">(changed later)</i>}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
