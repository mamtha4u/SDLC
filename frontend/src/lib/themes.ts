export interface ThemeDef {
  id: string;
  name: string;
  tone: "Dark" | "Light" | "Dual-tone";
  swatch: [string, string, string]; // bg, primary, primary-2 — used for gallery previews
}

export const THEMES: ThemeDef[] = [
  { id: "aurora", name: "Aurora", tone: "Dark", swatch: ["#0b0f1a", "#7c5cff", "#22d3ee"] },
  { id: "midnight", name: "Midnight", tone: "Dark", swatch: ["#05070d", "#6366f1", "#a78bfa"] },
  { id: "sunset", name: "Sunset", tone: "Dual-tone", swatch: ["#170b16", "#fb7185", "#f59e0b"] },
  { id: "ocean", name: "Ocean", tone: "Dual-tone", swatch: ["#04141c", "#06b6d4", "#3b82f6"] },
  { id: "forest", name: "Forest", tone: "Dual-tone", swatch: ["#07130d", "#22c55e", "#a3e635"] },
  { id: "cyberpunk", name: "Cyberpunk", tone: "Dual-tone", swatch: ["#0a0612", "#f472b6", "#facc15"] },
  { id: "daylight", name: "Daylight", tone: "Light", swatch: ["#dfe5ef", "#5b3cf0", "#0e7490"] },
  { id: "mono", name: "Mono", tone: "Light", swatch: ["#e2e2e2", "#111111", "#525252"] },
];

/** The account's theme (Settings). A project with its own theme shows that one inside the project only. */
export const themeState = { account: "aurora", project: null as string | null };

export function applyTheme(id: string, persist = true) {
  const el = document.documentElement;
  el.dataset.theme = id;
  const bg = THEMES.find((t) => t.id === id)?.swatch[0];
  if (bg) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", bg);
  if (persist) {
    themeState.account = id;
    try { localStorage.setItem("orkestra-theme", id); } catch { /* private mode */ }
  }
}

/** Inside a project with its own theme (its Settings tab): show it without changing the account's choice. */
export function showProjectTheme(id: string | null | undefined) {
  themeState.project = id || null;
  const want = id || themeState.account;
  if (document.documentElement.dataset.theme !== want) applyTheme(want, false);
}

export function leaveProjectTheme() {
  themeState.project = null;
  if (document.documentElement.dataset.theme !== themeState.account) applyTheme(themeState.account, false);
}

/** Where the user last pressed: a theme change spreads out from there. */
let lastPress = { x: 0, y: 0 };
if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", (e) => { lastPress = { x: e.clientX, y: e.clientY }; }, { passive: true, capture: true });
}

/** Change theme with a circular reveal from the last click (View Transitions API); a plain swap where unsupported. */
export function switchTheme(id: string) {
  const doc = document as Document & { startViewTransition?: (cb: () => void) => { ready: Promise<void> } };
  if (!doc.startViewTransition || currentTheme() === id || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    applyTheme(id);
    return;
  }
  const { x, y } = lastPress.x || lastPress.y ? lastPress : { x: window.innerWidth - 80, y: 40 };
  const r = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
  const t = doc.startViewTransition(() => applyTheme(id));
  t.ready.then(() => {
    document.documentElement.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
      { duration: 750, easing: "cubic-bezier(.2,.7,.2,1)", pseudoElement: "::view-transition-new(root)" });
  }).catch(() => undefined);
}

export function currentTheme(): string {
  return document.documentElement.dataset.theme || "aurora";
}

/** Accent name → CSS colour, shared by agents and project cards. */
export const ACCENT: Record<string, string> = {
  violet: "#8b5cf6", cyan: "#22d3ee", emerald: "#34d399", amber: "#fbbf24",
  orange: "#fb923c", blue: "#60a5fa", rose: "#fb7185", indigo: "#818cf8",
};
