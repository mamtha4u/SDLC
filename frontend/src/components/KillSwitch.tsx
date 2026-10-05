import { AnimatePresence, motion } from "framer-motion";
import { Info, OctagonX, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button, Modal } from "./ui";

const WHAT = [
  ["Stops the crew in this project", "No agent starts a new step and none makes another AI call: spending stops."],
  ["Nothing is lost", "Work so far, documents, code and approvals stay. Resume carries on exactly where it stopped."],
  ["AWS is left safe", "A step already talking to AWS (terraform apply, Dev's code upload) finishes first, so nothing is left half-built. Nothing is deleted."],
  ["Only this project", "Other projects keep running. To remove this project's resources from AWS, use Tear down (Build tab)."],
] as const;

/** The project's emergency stop, with what it does (ⓘ) and a confirmation before it's pressed. */
export function KillSwitch({ paused, onPause, onResume }: { paused: boolean; onPause: () => Promise<void> | void; onResume: () => Promise<void> | void }) {
  const [confirm, setConfirm] = useState(false);
  const [info, setInfo] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const [at, setAt] = useState<{ top: number; right: number } | null>(null);
  useEffect(() => {
    if (!info) return;
    // the popover lives in <body> (above the sticky tab bar, 10-02: it slid under it), pinned to the ⓘ button
    const place = () => { const r = btn.current?.getBoundingClientRect(); if (r) setAt({ top: r.bottom + 8, right: Math.max(8, window.innerWidth - r.right) }); };
    place();
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node) && !pop.current?.contains(e.target as Node)) setInfo(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setInfo(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  }, [info]);
  const run = async (fn: () => Promise<void> | void) => { setBusy(true); try { await fn(); } finally { setBusy(false); setConfirm(false); } };
  return (
    <div ref={box} className="relative inline-flex items-center gap-1">
      {paused ? (
        <Button variant="primary" loading={busy} icon={<Play className="h-4 w-4" />} onClick={() => run(onResume)}>Resume</Button>
      ) : (
        <Button variant="danger" icon={<OctagonX className="h-4 w-4" />} onClick={() => setConfirm(true)}>Kill switch</Button>
      )}
      <button ref={btn} onClick={() => setInfo((v) => !v)} aria-label="What does the kill switch do?" aria-expanded={info}
        className="focus-ring grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-text"><Info className="h-4 w-4" /></button>
      {createPortal(
        <AnimatePresence>
          {info && at && (
            <motion.div ref={pop} role="dialog" aria-label="What the kill switch does"
              initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4 }}
              style={{ top: at.top, right: at.right }}
              className="fixed z-[95] w-[min(340px,calc(100vw-16px))] rounded-[18px] border border-line bg-surface p-4 text-left shadow-2xl">
              <p className="font-display font-semibold">{paused ? "The crew is paused" : "Kill switch: the emergency stop"}</p>
              <ul className="mt-2 space-y-2">
                {WHAT.map(([t, d]) => <li key={t} className="text-[12.5px]"><b className="block">{t}</b><span className="text-muted">{d}</span></li>)}
              </ul>
            </motion.div>
          )}
        </AnimatePresence>, document.body)}
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Stop the crew in this project?" width={480}>
        <div className="space-y-3">
          <ul className="space-y-2 rounded-[16px] border border-danger/30 bg-danger/[0.06] p-3.5">
            {WHAT.map(([t, d]) => <li key={t} className="text-sm"><b>{t}.</b> <span className="text-muted">{d}</span></li>)}
          </ul>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirm(false)}>Keep working</Button>
            <Button variant="danger" loading={busy} icon={<OctagonX className="h-4 w-4" />} onClick={() => run(onPause)}>Stop the crew</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
