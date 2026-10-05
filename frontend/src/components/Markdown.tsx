import clsx from "clsx";
import { Check, Copy, WrapText } from "lucide-react";
import { Children, isValidElement, memo, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const PLUGINS = [remarkGfm];
const COMPONENTS = {
  pre: ({ children: c }: { children?: ReactNode }) => <CodeBlock>{c}</CodeBlock>,
  table: ({ children: c }: { children?: ReactNode }) => <div className="md-table"><table>{c}</table></div>,
};

/** Rendered markdown with tables, task lists and code — styled by the `.md` rules in index.css.
 *  Code blocks holding JSON or XML are pretty-printed, with an Indented/Original toggle and Copy.
 *  Memoised: polling re-renders the page often, and re-parsing a long document each time made the UI sluggish. */
export const Markdown = memo(function Markdown({ children, className = "" }: { children: string; className?: string }) {
  return (
    <div className={`md ${className}`}>
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
});

function textOf(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

export function prettyJson(src: string): string | null {
  const t = src.trim();
  if (!/^[[{]/.test(t)) return null;
  try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return null; }
}

export function prettyXml(src: string): string | null {
  const t = src.trim();
  if (!t.startsWith("<") || !t.endsWith(">")) return null;
  const tokens = t.replace(/>\s+</g, "><").split(/(?=<)|(?<=>)/).filter((x) => x.trim());
  let depth = 0;
  const out: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i].trim();
    if (tok.startsWith("</")) {
      depth = Math.max(0, depth - 1);
      out.push("  ".repeat(depth) + tok);
    } else if (tok.startsWith("<") && !tok.startsWith("<?") && !tok.startsWith("<!") && !tok.endsWith("/>")) {
      // keep <Tag>text</Tag> on one line
      const text = tokens[i + 1], close = tokens[i + 2];
      if (text && !text.startsWith("<") && close?.startsWith("</")) {
        out.push("  ".repeat(depth) + tok + text.trim() + close.trim());
        i += 2;
      } else {
        out.push("  ".repeat(depth) + tok);
        depth++;
      }
    } else {
      out.push("  ".repeat(depth) + tok);
    }
  }
  return out.join("\n");
}

export function CodeBlock({ children, raw }: { children?: ReactNode; raw?: string }) {
  const code = useMemo(() => (raw ?? textOf(Children.toArray(children))).replace(/\n$/, ""), [children, raw]);
  const pretty = useMemo(() => prettyJson(code) ?? prettyXml(code), [code]);
  const kind = prettyJson(code) ? "JSON" : pretty ? "XML" : null;
  const [indented, setIndented] = useState(true);
  const [copied, setCopied] = useState(false);
  const shown = indented && pretty ? pretty : code;

  const copy = async () => {
    try { await navigator.clipboard.writeText(shown); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch { /* ignore */ }
  };

  return (
    <div className="group relative my-3 overflow-hidden rounded-[12px] border border-line bg-bg-2">
      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[11px] text-muted">
        <span className="font-mono font-semibold uppercase tracking-wider">{kind ?? "code"}</span>
        <span className="flex-1" />
        {pretty && (
          <button onClick={() => setIndented(!indented)} aria-pressed={indented}
            className={clsx("inline-flex items-center gap-1 rounded-md px-2 py-0.5 transition-colors",
              indented ? "bg-primary/15 text-primary" : "hover:bg-surface-2 hover:text-text")}>
            <WrapText className="h-3.5 w-3.5" />{indented ? "Indented" : "Original"}
          </button>
        )}
        <button onClick={copy} className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 hover:bg-surface-2 hover:text-text">
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}{copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="m-0 max-h-[420px] overflow-auto border-0 bg-transparent px-3.5 py-3"><code>{shown}</code></pre>
    </div>
  );
}
