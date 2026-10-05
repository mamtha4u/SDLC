import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { BarChart3, ChevronDown, Cloud, FolderKanban, LogOut, Search, Settings } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api, type User } from "../lib/api";
import { Aurora } from "./Aurora";
import { CommandPalette } from "./CommandPalette";
import { Constellation } from "./Constellation";
import { Wordmark } from "./Logo";
import { ThemeSwitcher } from "./ThemeSwitcher";

const NAV = [
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/usage", label: "Usage", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function EnvBadge() {
  const { data } = useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: 300_000 });
  if (!data) return null;
  return (
    <div className="neu-sm hidden items-center gap-2 rounded-full px-3 py-1.5 text-xs xl:flex" title={data.role ?? ""}>
      <Cloud className="h-3.5 w-3.5 text-primary-2" />
      <span className="font-semibold uppercase tracking-wide text-warning">{data.environment}</span>
      <span className="text-muted">·</span><span className="font-mono">{data.account}</span>
      <span className="text-muted">·</span><span className="font-mono">{data.region}</span>
      <span className={clsx("ml-0.5 h-1.5 w-1.5 rounded-full", data.identity_ok ? "bg-success" : "bg-danger")} />
    </div>
  );
}

function UserMenu({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const nav = useNavigate();
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const logout = async () => { await api.logout(); qc.clear(); nav("/login"); };
  const initials = user.display_name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} className="focus-ring neu-sm flex h-10 items-center gap-2 rounded-[12px] pl-1.5 pr-2 sm:pr-3" aria-label="Account menu">
        <span className="grid h-7 w-7 place-items-center rounded-[9px] bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-[11px] font-bold text-on-primary">{initials}</span>
        <span className="hidden max-w-[140px] truncate text-sm font-medium md:inline">{user.display_name}</span>
        <ChevronDown className={clsx("hidden h-4 w-4 text-muted transition-transform sm:block", open && "rotate-180")} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4 }}
            className="absolute right-0 z-40 mt-2 w-56 rounded-[16px] border border-line bg-surface p-1.5 shadow-2xl">
            <div className="px-3 py-2">
              <p className="text-sm font-semibold">{user.display_name}</p>
              <p className="text-xs text-muted">@{user.username}</p>
            </div>
            <div className="my-1 h-px bg-line" />
            <button onClick={logout} className="flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-sm text-danger hover:bg-surface-2">
              <LogOut className="h-4 w-4" />Sign out
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function AppShell({ user, children }: { user: User; children: ReactNode }) {
  return (
    <div className="min-h-dvh">
      <Aurora intensity={0.7} />
      <Constellation density={0.45} />
      <header className="sticky top-0 z-30 mx-auto w-full max-w-[1680px] px-3 pt-3 sm:px-6">
        <div className="glass flex h-14 items-center gap-2 rounded-[20px] px-2.5 shadow-[0_10px_30px_-18px_rgba(0,0,0,0.5)] sm:h-16 sm:gap-3 sm:px-4">
          <NavLink to="/projects" className="shrink-0" aria-label="Orkestra home">
            <span className="hidden sm:block"><Wordmark size={34} /></span>
            <span className="sm:hidden"><Wordmark size={30} /></span>
          </NavLink>
          <nav className="no-scrollbar mx-auto flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="Main">
            {NAV.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} title={label}
                className={({ isActive }) => clsx("focus-ring relative flex h-10 shrink-0 items-center gap-2 rounded-[12px] px-3 text-sm font-medium transition-colors",
                  isActive ? "text-text" : "text-muted hover:text-text")}>
                {({ isActive }) => (
                  <>
                    {isActive && <motion.span layoutId="top-nav" className="neu-inset absolute inset-0 rounded-[12px]"
                      transition={{ type: "spring", stiffness: 500, damping: 36 }} />}
                    <Icon className={clsx("relative h-[18px] w-[18px]", isActive && "text-primary")} />
                    <span className="relative hidden md:inline">{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
          <div className="flex shrink-0 items-center gap-2">
            <EnvBadge />
            <button onClick={() => window.dispatchEvent(new Event("ork:palette"))} title="Search and commands (Ctrl+K)"
              className="focus-ring press neu-sm group flex h-10 items-center gap-2 rounded-[12px] px-2.5 text-sm text-muted hover:text-text lg:w-52 lg:px-3">
              <Search className="h-[18px] w-[18px] group-hover:text-primary" />
              <span className="hidden lg:inline">Search…</span>
              <span className="ml-auto hidden items-center gap-0.5 lg:flex"><span className="kbd">Ctrl</span><span className="kbd">K</span></span>
            </button>
            <ThemeSwitcher />
            <UserMenu user={user} />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1680px] px-3 pb-24 pt-5 sm:px-6">{children}</main>
      <CommandPalette />
    </div>
  );
}
