import { toast } from "sonner";

/** An open tab keeps running the app it loaded. After a deploy, offer a one-click reload instead of showing old screens
 *  (10-02: the user kept seeing the Build page from before two releases). Checks every minute and when the tab comes back. */
export function watchForUpdates() {
  let loaded: string | null = null;
  let told = false;
  const check = async () => {
    try {
      const r = await fetch("/api/system/health", { cache: "no-store" });
      const build = (await r.json()).build as string | undefined;
      if (!build) return;
      if (loaded === null) { loaded = build; return; }
      if (build !== loaded && !told) {
        told = true;
        toast("Orkestra was updated", {
          id: "orkestra-update", duration: Infinity, description: "Reload to get the newest screens. Nothing you did is lost.",
          action: { label: "Reload", onClick: () => window.location.reload() },
        });
      }
    } catch { /* offline or restarting: try again later */ }
  };
  void check();
  window.setInterval(check, 60_000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void check(); });
}
