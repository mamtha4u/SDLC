import { useQuery } from "@tanstack/react-query";
import { motion, MotionConfig } from "framer-motion";
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Aurora } from "./components/Aurora";
import { LogoMark } from "./components/Logo";
import { api } from "./lib/api";
import { experience } from "./lib/fx";
import { applyTheme, themeState } from "./lib/themes";
import { AuthPage } from "./pages/AuthPage";
// Pages load on demand: the login screen doesn't pay for the workspace, markdown renderer, charts, etc.
const ProjectsPage = lazy(() => import("./pages/ProjectsPage").then((m) => ({ default: m.ProjectsPage })));
const WorkspacePage = lazy(() => import("./pages/WorkspacePage").then((m) => ({ default: m.WorkspacePage })));
const UsagePage = lazy(() => import("./pages/UsagePage").then((m) => ({ default: m.UsagePage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const DesignPage = lazy(() => import("./pages/DesignPage").then((m) => ({ default: m.DesignPage })));

function Splash() {
  return (
    <div className="grid min-h-dvh place-items-center">
      <Aurora />
      <motion.div animate={{ scale: [1, 1.06, 1] }} transition={{ repeat: Infinity, duration: 1.6 }}><LogoMark size={72} /></motion.div>
    </div>
  );
}

/** Page fade-in. Opacity only, on purpose: a `filter` or `transform` left on this wrapper makes every `position: fixed`
 *  child (popups, overlays) position itself against the page instead of the screen. That opened popups far down
 *  the page and let an invisible full-page layer swallow clicks.
 *  No exit animation (user, 10-04: back from a project to Projects showed an empty page until a reload): with
 *  AnimatePresence mode="wait" the next page mounts only after the old one's exit finishes, and that exit can stall
 *  (two quick navigations, a lazy page loading mid-fade), leaving nothing on screen. Each page has its own Suspense
 *  so a page that's still loading never blanks the rest. */
function Page({ children }: { children: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>
      <Suspense fallback={<div className="grid place-items-center py-24"><LogoMark size={56} /></div>}>{children}</Suspense>
    </motion.div>
  );
}

export default function App() {
  const location = useLocation();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, staleTime: 60_000 });
  useEffect(() => {  // a project with its own theme keeps it while you're inside it
    if (me.data?.theme) { themeState.account = me.data.theme; if (!themeState.project) applyTheme(me.data.theme); }
  }, [me.data?.theme]);
  const calm = me.data?.prefs?.motion === "calm";
  experience.calm = calm;
  experience.celebrate = me.data?.prefs?.celebrate ?? true;

  if (location.pathname === "/design") return <Suspense fallback={<Splash />}><DesignPage /></Suspense>; // public gallery
  // On /login, show the page immediately while the session check is in flight (no splash flash).
  if (me.isLoading && location.pathname !== "/login") return <Splash />;
  if (!me.data) {
    return (
      <Routes>
        <Route path="/login" element={<AuthPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  if (location.pathname === "/login") return <Navigate to="/projects" replace />;
  return (
    <MotionConfig reducedMotion={calm ? "always" : "user"}>
    <AppShell user={me.data}>
      <Routes location={location} key={location.pathname}>
        <Route path="/projects" element={<Page><ProjectsPage /></Page>} />
        <Route path="/projects/:id" element={<Page><WorkspacePage /></Page>} />
        <Route path="/usage" element={<Page><UsagePage /></Page>} />
        <Route path="/settings" element={<Page><SettingsPage /></Page>} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </AppShell>
    </MotionConfig>
  );
}
