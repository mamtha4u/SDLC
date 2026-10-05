/** Monaco (the editor inside VS Code), bundled with the app instead of loaded from a CDN (the corporate proxy may
 *  block CDNs). Lean build: the core editor, only the features a read-only viewer needs, and syntax colouring for
 *  the languages our projects use. Importing all of `monaco-editor` pulled in ~10 MB of language workers
 *  (TypeScript, CSS, HTML, JSON services) that a viewer never uses. This chunk loads only when the Code view opens. */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/editor/editor.api";
import EditorWorker from "monaco-editor/editor/editor.worker?worker"; // package exports map: ./* → ./esm/vs/*.js

// editor features (the subset of editor.main a read-only viewer uses)
import "monaco-editor/editor/browser/coreCommands";
import "monaco-editor/editor/browser/widget/codeEditor/codeEditorWidget";
import "monaco-editor/editor/browser/widget/diffEditor/diffEditor.contribution";
import "monaco-editor/features/find/register";
import "monaco-editor/editor/contrib/find/browser/findController";
import "monaco-editor/editor/contrib/folding/browser/folding";
import "monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching";
import "monaco-editor/editor/contrib/clipboard/browser/clipboard";
import "monaco-editor/editor/contrib/contextmenu/browser/contextmenu";
import "monaco-editor/editor/contrib/hover/browser/hoverContribution";
import "monaco-editor/editor/contrib/wordHighlighter/browser/wordHighlighter";
import "monaco-editor/editor/contrib/stickyScroll/browser/stickyScrollContribution";
import "monaco-editor/editor/contrib/readOnlyMessage/browser/contribution";
import "monaco-editor/editor/contrib/tokenization/browser/tokenization";
import "monaco-editor/editor/contrib/links/browser/links";
import "monaco-editor/editor/standalone/browser/quickAccess/standaloneGotoLineQuickAccess";
import "monaco-editor/editor/common/standaloneStrings";
import "../../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css";
import "../../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon-modifiers.css";
// syntax colouring (Monarch tokenizers, no workers)
import "monaco-editor/languages/definitions/python/register";
import "monaco-editor/languages/definitions/hcl/register";
import "monaco-editor/languages/definitions/markdown/register";
import "monaco-editor/languages/definitions/xml/register";
import "monaco-editor/languages/definitions/yaml/register";
import "monaco-editor/languages/definitions/shell/register";
import "monaco-editor/languages/definitions/sql/register";
import "monaco-editor/languages/definitions/javascript/register";
import "monaco-editor/languages/definitions/typescript/register";
import "monaco-editor/languages/definitions/java/register";
import "monaco-editor/languages/definitions/ini/register";
import "monaco-editor/languages/definitions/css/register";
import "monaco-editor/languages/definitions/html/register";

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco: monaco as never });

const LANG: Record<string, string> = {
  py: "python", tf: "hcl", hcl: "hcl", tfvars: "hcl", md: "markdown", json: "javascript", xml: "xml", drawio: "xml", xsd: "xml",
  yaml: "yaml", yml: "yaml", sh: "shell", sql: "sql", js: "javascript", ts: "typescript", java: "java", ini: "ini",
  toml: "ini", cfg: "ini", txt: "plaintext", csv: "plaintext", diff: "plaintext", log: "plaintext", html: "html", css: "css",
};
export const languageOf = (path: string) => LANG[path.split(".").pop()?.toLowerCase() ?? ""] ?? "plaintext";

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function hex(v: string, fallback: string) {
  return /^#[0-9a-f]{6}$/i.test(v) ? v : fallback;
}
function dark(h: string) {
  const n = parseInt(h.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128;
}

/** A Monaco theme that follows the app's current theme (8 themes, light and dark). */
export function syncTheme(): string {
  const bg = hex(cssVar("--bg-2"), "#0f1628");
  const surface = hex(cssVar("--surface"), "#161e33");
  const isDark = dark(bg);
  monaco.editor.defineTheme("orkestra", {
    base: isDark ? "vs-dark" : "vs", inherit: true, rules: [],
    colors: {
      "editor.background": bg, "editorGutter.background": bg, "minimap.background": bg,
      "editor.lineHighlightBackground": isDark ? "#ffffff0d" : "#0000000a",
      "editorWidget.background": surface, "diffEditor.insertedTextBackground": "#34d39926", "diffEditor.removedTextBackground": "#f43f5e26",
    },
  });
  return "orkestra";
}
