import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Palette } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { THEMES, currentTheme, switchTheme } from "../lib/themes";

export function ThemeSwatch({ id, active, onPick, large }: { id: string; active: boolean; onPick: () => void; large?: boolean }) {
  const t = THEMES.find((x) => x.id === id)!;
  const [bg, p1, p2] = t.swatch;
  return (
    <motion.button whileHover={{ y: -2 }} whileTap={{ scale: 0.96 }} onClick={onPick}
      className={clsx("focus-ring group relative w-full overflow-hidden rounded-[14px] text-left transition-shadow",
        active ? "glow-primary" : "neu-sm", large ? "h-36" : "h-20")}
      aria-pressed={active} aria-label={`${t.name} theme`}>
      <div className="absolute inset-0" style={{ background: bg }} />
      <div className="absolute -left-4 -top-6 h-20 w-20 rounded-full blur-2xl" style={{ background: p1, opacity: 0.8 }} />
      <div className="absolute -right-6 bottom-0 h-20 w-20 rounded-full blur-2xl" style={{ background: p2, opacity: 0.7 }} />
      {large && (
        <div className="absolute inset-x-4 top-4 space-y-2">
          <div className="h-2 w-1/2 rounded-full" style={{ background: p1 }} />
          <div className="h-2 w-3/4 rounded-full opacity-40" style={{ background: t.tone === "Light" ? "#000" : "#fff" }} />
          <div className="h-6 w-20 rounded-lg" style={{ background: `linear-gradient(120deg, ${p1}, ${p2})` }} />
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between px-3 py-2 text-xs font-semibold"
        style={{ color: t.tone === "Light" ? "#111" : "#fff" }}>
        <span>{t.name}<span className="ml-1.5 font-normal opacity-70">{t.tone}</span></span>
        {active && <Check className="h-3.5 w-3.5" />}
      </div>
    </motion.button>
  );
}

export function useThemeChoice() {
  const [theme, setTheme] = useState(currentTheme());
  const choose = (id: string) => {
    switchTheme(id);
    setTheme(id);
    api.setTheme(id).catch(() => undefined); // remembered per user; local choice still applies
  };
  return { theme, choose };
}

export function ThemeSwitcher() {
  const [open, setOpen] = useState(false);
  const { theme, choose } = useThemeChoice();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} aria-label="Change theme"
        className="focus-ring neu-sm grid h-10 w-10 place-items-center rounded-[12px] text-muted hover:text-primary">
        <Palette className="h-[18px] w-[18px]" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -8, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }} transition={{ duration: 0.18 }}
            className="glass absolute right-0 z-40 mt-2 w-[min(88vw,340px)] rounded-[20px] p-3 shadow-2xl">
            <p className="px-1 pb-2 text-xs font-semibold uppercase tracking-wider text-muted">Theme</p>
            <div className="grid grid-cols-2 gap-2">
              {THEMES.map((t) => <ThemeSwatch key={t.id} id={t.id} active={theme === t.id} onPick={() => choose(t.id)} />)}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
