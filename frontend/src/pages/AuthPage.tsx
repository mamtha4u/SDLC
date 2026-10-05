import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion, useAnimationControls, useMotionValueEvent, useScroll, useSpring } from "framer-motion";
import { ArrowRight, AtSign, ChevronDown, Coins, Eye, EyeOff, KeyRound, ShieldCheck, UserRound } from "lucide-react";
import { Fragment, lazy, Suspense, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AgentDetail, type DetailOrigin } from "../components/AgentDetail";
import { Aurora } from "../components/Aurora";
import { Constellation } from "../components/Constellation";
import { LogoMark, Wordmark } from "../components/Logo";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import { Button, Input } from "../components/ui";
import { Blueprint } from "../features/landing/Blueprint";
import { BARS, Score } from "../features/landing/Score";
import { api, ApiError, type User } from "../lib/api";
import { CREW } from "../lib/crew";
import { applyTheme } from "../lib/themes";

// the story below the fold loads after the first screen, so sign-in is never slowed down by it
const Story = lazy(() => import("../features/landing/Story"));
const LAST_USER = "orkestra-last-user";
const phraseOf = (i: number) => (i < CREW.length ? CREW[i].phrase : "ships it.");

/** The score plays bar by bar; it pauses while you explore (hovering it, or reading an agent's details). */
function useConductor(paused: boolean, ms = 2600) {
  const [active, setActive] = useState(0);
  useEffect(() => {
    if (paused) return;
    const t = window.setInterval(() => setActive((a) => (a + 1) % BARS), ms);
    return () => window.clearInterval(t);
  }, [ms, paused]);
  return [active, setActive] as const;
}

/* ── Headline: words rise in; the last phrase changes with the bar being played, with a baton stroke under it ── */
function Reveal({ children, delay = 0 }: { children: ReactNode; delay?: number }) {
  return (
    <span className="inline-block overflow-hidden pb-[0.08em] align-bottom">
      <motion.span className="inline-block" initial={{ y: "110%", rotate: 4 }} animate={{ y: 0, rotate: 0 }}
        transition={{ delay, type: "spring", stiffness: 160, damping: 20 }}>{children}</motion.span>
    </span>
  );
}

function Headline({ active, size }: { active: number; size: string }) {
  return (
    <h1 className="font-display font-bold leading-[1.02] tracking-[-0.03em]" style={{ fontSize: size }}>
      <span className="block">
        {["You", "write", "the", "requirement."].map((w, i) => <Fragment key={w}><Reveal delay={0.1 + i * 0.07}>{w}</Reveal>{" "}</Fragment>)}
      </span>
      <span className="block whitespace-nowrap">
        <Reveal delay={0.42}>The crew</Reveal>{" "}
        <span className="relative inline-block overflow-hidden align-bottom" style={{ height: "1.2em" }}>
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span key={active} className="text-gradient-anim inline-block pr-[0.06em]"
              initial={{ y: "100%", opacity: 0, filter: "blur(8px)" }} animate={{ y: 0, opacity: 1, filter: "blur(0px)" }}
              exit={{ y: "-100%", opacity: 0, filter: "blur(8px)" }} transition={{ type: "spring", stiffness: 240, damping: 26 }}>
              {phraseOf(active)}
            </motion.span>
          </AnimatePresence>
          {/* the conductor's baton stroke */}
          <svg className="pointer-events-none absolute -bottom-[0.02em] left-0 h-[0.22em] w-full" viewBox="0 0 200 20" preserveAspectRatio="none" aria-hidden>
            <motion.path key={active} d="M3 14 C 50 4, 110 4, 197 11" fill="none" stroke="var(--primary-2)" strokeWidth="4" strokeLinecap="round"
              initial={{ pathLength: 0, opacity: 0.9 }} animate={{ pathLength: 1, opacity: 0.75 }} transition={{ duration: 0.7, delay: 0.15, ease: [0.2, 0.7, 0.2, 1] }} />
          </svg>
        </span>
      </span>
    </h1>
  );
}

/* ── Sign-in card: tilts toward the pointer, light follows it ─────────────── */
function SpotlightCard({ children, shake }: { children: ReactNode; shake: ReturnType<typeof useAnimationControls> }) {
  const ref = useRef<HTMLDivElement>(null);
  const rx = useSpring(0, { stiffness: 150, damping: 20 });
  const ry = useSpring(0, { stiffness: 150, damping: 20 });
  const move = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || e.pointerType !== "mouse") return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    el.style.setProperty("--mx", `${px * 100}%`);
    el.style.setProperty("--my", `${py * 100}%`);
    rx.set((0.5 - py) * 4);
    ry.set((px - 0.5) * 4);
  };
  return (
    <motion.div animate={shake} className="relative w-full max-w-[440px]">
      {/* an aura that breathes behind the card */}
      <div className="pointer-events-none absolute -inset-6 -z-10 rounded-[40px] opacity-60 blur-2xl"
        style={{ background: "conic-gradient(from 180deg, var(--primary), var(--primary-2), var(--primary))", animation: "float-y 7s ease-in-out infinite" }} />
      <motion.div ref={ref} onPointerMove={move} onPointerLeave={() => { rx.set(0); ry.set(0); }}
        style={{ rotateX: rx, rotateY: ry, transformPerspective: 1200 }}
        initial={{ opacity: 0, y: 28, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 180, damping: 22, delay: 0.25 }}
        className="glass sheen beam-border group relative overflow-hidden rounded-[30px] p-6 shadow-2xl sm:p-8">
        <div className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
          style={{ background: "radial-gradient(420px circle at var(--mx, 50%) var(--my, 0%), color-mix(in srgb, var(--primary) 18%, transparent), transparent 60%)" }} />
        <div className="relative">{children}</div>
      </motion.div>
    </motion.div>
  );
}

function Warp({ at }: { at: { x: number; y: number } }) {
  return (
    <motion.div className="fixed inset-0 z-[100] grid place-items-center"
      style={{ background: "radial-gradient(circle at center, color-mix(in srgb, var(--primary) 70%, var(--bg)), var(--bg) 75%)" }}
      initial={{ clipPath: `circle(0px at ${at.x}px ${at.y}px)` }} animate={{ clipPath: `circle(150vmax at ${at.x}px ${at.y}px)` }}
      transition={{ duration: 0.75, ease: [0.7, 0, 0.3, 1] }}>
      <motion.div initial={{ scale: 0.3, opacity: 0, rotate: -20 }} animate={{ scale: 1, opacity: 1, rotate: 0 }}
        transition={{ delay: 0.3, type: "spring", stiffness: 220, damping: 16 }} className="text-center">
        <LogoMark size={96} />
        <p className="mt-3 font-display text-lg font-semibold">Assembling your crew…</p>
      </motion.div>
    </motion.div>
  );
}

const TRUST = [
  { icon: KeyRound, text: "A least-privilege AWS role per agent" },
  { icon: ShieldCheck, text: "You approve every step; every action audited" },
  { icon: Coins, text: "Exact cost of every model call" },
];
const NAV = [["How it works", "how"], ["The crew", "crew"], ["Safety", "safety"]] as const;

export function AuthPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [busy, setBusy] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [caps, setCaps] = useState(false);
  const [warp, setWarp] = useState<{ x: number; y: number } | null>(null);
  const [form, setForm] = useState(() => {
    let username = "";
    try { username = localStorage.getItem(LAST_USER) ?? ""; } catch { /* private mode */ }
    return { username, password: "", display_name: "" };
  });
  const qc = useQueryClient();
  const nav = useNavigate();
  const shake = useAnimationControls();
  const [hovering, setHovering] = useState(false);
  const [detail, setDetail] = useState<number | null>(null); // -1 = Orion
  const [origin, setOrigin] = useState<DetailOrigin>({ x: 0, y: 0 });
  const [active, setActive] = useConductor(hovering || detail !== null);
  const card = useRef<HTMLDivElement>(null);
  const userInput = useRef<HTMLInputElement>(null);
  const { scrollY } = useScroll();
  const [scrolled, setScrolled] = useState(false);
  useMotionValueEvent(scrollY, "change", (v) => setScrolled(v > 24));
  // desktop: ready to type (password if we remember you); phones: no keyboard popping up over the page
  useEffect(() => {
    if (!window.matchMedia?.("(min-width: 1024px)").matches) return;
    const t = window.setTimeout(() => {
      const el = form.username ? document.querySelector<HTMLInputElement>('input[name="password"]') : userInput.current;
      el?.focus({ preventScroll: true });
    }, 700);
    return () => window.clearTimeout(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (i: number, e: React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect();
    setOrigin({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
    if (i >= 0) setActive(i);
    setDetail(i);
  };
  const navigateDetail = (i: number) => { if (i >= 0) setActive(i); setDetail(i); };
  const toCard = (m?: "login" | "register") => {
    if (m) setMode(m);
    card.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => userInput.current?.focus({ preventScroll: true }), 650);
  };
  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const btn = (e.nativeEvent as SubmitEvent).submitter?.getBoundingClientRect();
    setBusy(true);
    try {
      const user: User = mode === "login"
        ? await api.login(form.username, form.password)
        : await api.register(form.username, form.password, form.display_name || form.username);
      try { localStorage.setItem(LAST_USER, user.username); } catch { /* ignore */ }
      applyTheme(user.theme);
      setWarp(btn ? { x: btn.left + btn.width / 2, y: btn.top + btn.height / 2 } : { x: innerWidth / 2, y: innerHeight / 2 });
      window.setTimeout(() => {
        toast.success(mode === "login" ? `Welcome back, ${user.display_name}` : "Account created. Welcome to Orkestra");
        qc.setQueryData(["me"], user);
        nav("/projects");
      }, 900);
    } catch (err) {
      setBusy(false);
      shake.start({ x: [0, -12, 12, -8, 8, -4, 0], transition: { duration: 0.5 } });
      toast.error(err instanceof ApiError ? err.message : "Something went wrong");
    }
  };

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const onKey = (e: React.KeyboardEvent) => setCaps(e.getModifierState?.("CapsLock") ?? false);
  const score = { active, paused: hovering || detail !== null, onHover: setHovering, onPick: pick };

  return (
    <div className="relative min-h-dvh overflow-x-clip">
      <Aurora />
      <Constellation />
      <Blueprint />

      {/* header: becomes glass once you scroll */}
      <header className="sticky top-0 z-40 px-3 pt-3 sm:px-6">
        <div className={clsx("mx-auto flex h-14 max-w-[1400px] items-center gap-3 rounded-[20px] px-3 transition-all duration-300 sm:h-16 sm:px-4",
          scrolled ? "border border-line bg-[color-mix(in_srgb,var(--surface)_90%,transparent)] shadow-[0_10px_30px_-18px_rgba(0,0,0,0.5)] backdrop-blur-xl"
            : "border border-transparent")}>
          <button onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} aria-label="Orkestra, back to top"><Wordmark size={34} /></button>
          <nav className="mx-auto hidden items-center gap-1 md:flex" aria-label="Sections">
            {NAV.map(([label, id]) => (
              <button key={id} onClick={() => jump(id)} className="focus-ring rounded-[10px] px-3 py-2 text-sm font-medium text-muted transition-colors hover:text-text">{label}</button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 md:ml-0">
            <ThemeSwitcher />
            <Button size="sm" variant="primary" className="press" onClick={() => toCard("login")}>Sign in</Button>
          </div>
        </div>
      </header>

      {/* ── hero ── */}
      <section className="relative mx-auto grid min-h-[calc(100dvh-76px)] max-w-[1400px] grid-cols-[minmax(0,1fr)] items-center gap-8 px-4 pb-14 pt-4 sm:px-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:gap-12 lg:pb-12 xl:px-12">
        {/* stage light */}
        <div className="pointer-events-none absolute left-[22%] top-0 -z-[1] h-[70%] w-[46%] -translate-x-1/2 opacity-40"
          style={{ background: "radial-gradient(ellipse at top, color-mix(in srgb, var(--primary) 45%, transparent), transparent 70%)" }} />
        {/* sized by width AND height, so the headline, the copy and the whole Score fit on a laptop screen */}
        <div className="min-w-0 space-y-[clamp(0.9rem,2.4vh,1.5rem)]" style={{ containerType: "inline-size" }}>
          <Headline active={active} size="clamp(1.9rem, min(6.7cqi, 8.4vh), 4.9rem)" />
          <motion.p initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.75 }}
            className="max-w-2xl text-[clamp(0.95rem,0.85rem+0.35vw,1.15rem)] text-muted">
            Seven AI agents, conducted by <b className="text-text">Orion</b>, turn your requirement into a documented, deployed and
            tested AWS flow. Every hand-off waits for your approval.
          </motion.p>
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.9, type: "spring", stiffness: 120, damping: 20 }}>
            <div className="hidden sm:block"><Score {...score} /></div>
            <div className="sm:hidden"><Score {...score} compact /></div>
          </motion.div>
        </div>

        <div ref={card} className="flex min-w-0 flex-col items-center gap-4 scroll-mt-28">
          <SpotlightCard shake={shake}>
            <h2 className="font-display text-2xl font-semibold">{mode === "login" ? "Welcome back" : "Join the crew"}</h2>
            <p className="mt-1 text-sm text-muted">
              {mode === "login" ? "Sign in to pick up where your crew left off." : "Create an account. Your projects stay private to you."}
            </p>
            <div className="neu-inset relative mt-5 grid grid-cols-2 rounded-[14px] p-1">
              {(["login", "register"] as const).map((m) => (
                <button key={m} type="button" onClick={() => setMode(m)}
                  className={clsx("focus-ring relative z-10 h-9 rounded-[10px] text-sm font-medium transition-colors", mode === m ? "text-text" : "text-muted")}>
                  {mode === m && <motion.span layoutId="auth-tab" className="neu-sm absolute inset-0 -z-10 rounded-[10px]"
                    transition={{ type: "spring", stiffness: 500, damping: 35 }} />}
                  {m === "login" ? "Sign in" : "Register"}
                </button>
              ))}
            </div>
            <form onSubmit={submit} className="mt-5 space-y-4">
              <AnimatePresence initial={false}>
                {mode === "register" && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}>
                    <Input label="Display name" name="display_name" placeholder="How the crew should address you" value={form.display_name}
                      onChange={set("display_name")} leading={<UserRound className="h-4 w-4" />} autoComplete="name" />
                  </motion.div>
                )}
              </AnimatePresence>
              <Input ref={userInput} label="Username" name="username" placeholder="your.name" value={form.username} onChange={set("username")}
                leading={<AtSign className="h-4 w-4" />} autoComplete="username" required minLength={3} />
              <Input label="Password" name="password" type={showPw ? "text" : "password"} placeholder="At least 8 characters"
                value={form.password} onChange={set("password")} onKeyUp={onKey} onKeyDown={onKey}
                leading={<KeyRound className="h-4 w-4" />} required minLength={8}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                trailing={
                  <button type="button" onClick={() => setShowPw(!showPw)} aria-label={showPw ? "Hide password" : "Show password"}
                    className="focus-ring rounded-md p-1 text-muted hover:text-text">
                    {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                }
                hint={caps ? <span className="text-warning">Caps Lock is on</span> : undefined} />
              <Button type="submit" variant="primary" size="lg" loading={busy} className="shimmer mt-1 w-full">
                {mode === "login" ? "Sign in" : "Create account"} <ArrowRight className="h-4 w-4" />
              </Button>
            </form>
            <p className="mt-5 flex items-center justify-center gap-1.5 text-center text-xs text-muted">
              <ShieldCheck className="h-3.5 w-3.5" /> Sandbox POC · every AWS action is audited
            </p>
          </SpotlightCard>
          <motion.ul initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.1 }} className="flex w-full max-w-[440px] flex-col gap-1.5 px-2">
            {TRUST.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-center gap-2 text-[13px] text-muted"><Icon className="h-4 w-4 shrink-0 text-primary-2" />{text}</li>
            ))}
          </motion.ul>
        </div>

        <motion.button onClick={() => jump("how")} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.6 }}
          className="absolute bottom-4 left-1/2 hidden -translate-x-1/2 flex-col items-center gap-1 text-[11px] font-medium text-muted hover:text-text lg:flex">
          See how a sentence becomes a live flow
          <ChevronDown className="float-y h-4 w-4" />
        </motion.button>
      </section>

      <Suspense fallback={<div className="h-40" />}>
        <Story onPick={pick} onStart={toCard} />
      </Suspense>

      <AgentDetail index={detail} origin={origin} onClose={() => setDetail(null)} onNavigate={navigateDetail} />
      <AnimatePresence>{warp && <Warp at={warp} />}</AnimatePresence>
    </div>
  );
}
