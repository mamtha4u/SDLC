import { AnimatePresence, motion, useIsPresent } from "framer-motion";
import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

let openCount = 0; // several overlays can be open at once (e.g. a dialog over the agent popup)

/** Every popup goes through here. Two rules that keep the page usable:
 *  1. It renders into <body> (a portal). A `filter`/`transform` on any ancestor would otherwise make `fixed` mean
 *     "fixed to that ancestor": the popup then opened somewhere down the page, and its full-page layer could sit
 *     invisibly over everything.
 *  2. While closing, it stops catching clicks at once (useIsPresent), so a half-finished exit animation can never
 *     leave an invisible layer that swallows every click ("I can scroll but can't click anything"). */
export function Overlay({ open, onClose, z = 80, label, align = "center", children }: {
  open: boolean; onClose: () => void; z?: number; label: string; align?: "center" | "bottom-sheet"; children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    openCount += 1;
    document.body.classList.add("modal-open");
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("keydown", esc);
      openCount = Math.max(0, openCount - 1);
      if (!openCount) document.body.classList.remove("modal-open");
    };
  }, [open, onClose]);

  return createPortal(
    <AnimatePresence>
      {open && <Layer key="layer" z={z} label={label} align={align} onClose={onClose}>{children}</Layer>}
    </AnimatePresence>,
    document.body,
  );
}

function Layer({ z, label, align, onClose, children }: { z: number; label: string; align: string; onClose: () => void; children: ReactNode }) {
  const present = useIsPresent();
  return (
    <motion.div role="presentation" aria-label={label}
      className={align === "center" ? "modal-layer fixed inset-0 flex items-end justify-center p-2 sm:items-center sm:p-4"
        : "modal-layer fixed inset-0 flex items-end justify-center sm:items-center sm:p-5"}
      style={{ zIndex: z, pointerEvents: present ? "auto" : "none" }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
      <div className="absolute inset-0 bg-black/45 backdrop-blur-md" onClick={onClose} />
      {children}
    </motion.div>
  );
}
