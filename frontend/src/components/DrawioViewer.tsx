import { useEffect, useRef, useState } from "react";

let viewerScript: Promise<void> | null = null;
function loadViewer(): Promise<void> {
  viewerScript ??= new Promise((resolve, reject) => {
    const s = Object.assign(document.createElement("script"), { src: "https://viewer.diagrams.net/js/viewer-static.min.js", async: true });
    s.onload = () => resolve();
    s.onerror = () => { viewerScript = null; reject(new Error("blocked")); };
    document.head.appendChild(s);
  });
  return viewerScript;
}

/** The official draw.io viewer (zoom, pan, layers, lightbox). The diagram never leaves the browser. */
export function DrawioViewer({ xml }: { xml: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let alive = true;
    setReady(false);
    loadViewer().then(() => {
      const el = box.current;
      const gv = (window as unknown as { GraphViewer?: { processElements: () => void } }).GraphViewer;
      if (!alive || !el || !gv) return;
      el.innerHTML = "";
      const div = document.createElement("div");
      div.className = "mxgraph";
      div.style.maxWidth = "100%";
      div.setAttribute("data-mxgraph", JSON.stringify({ xml, lightbox: false, nav: true, resize: true, toolbar: "zoom layers lightbox", highlight: "#7C5CFF", "auto-fit": true }));
      el.appendChild(div);
      gv.processElements();
      setReady(true);
    }).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [xml]);
  if (failed) {
    return <p className="p-6 text-sm text-muted">The draw.io viewer couldn't load (your network may block viewer.diagrams.net). Download the .drawio or use “Edit in diagrams.net”.</p>;
  }
  // the diagram is drawn on white "paper" (draw.io colours are made for it), laid on a dotted, theme-coloured canvas
  return (
    <div className="relative p-3 sm:p-5" style={{ background: "radial-gradient(color-mix(in srgb, var(--text) 14%, transparent) 1px, transparent 1.2px) 0 0 / 18px 18px, var(--bg-2)" }}>
      <div ref={box} className="min-h-[420px] overflow-auto rounded-[14px] bg-white p-3 shadow-[0_18px_50px_-24px_rgba(0,0,0,0.6)]" />
      {!ready && (
        <div className="absolute inset-3 grid place-items-center rounded-[14px] sm:inset-5">
          <div className="skeleton absolute inset-0 !rounded-[14px]" />
          <p className="relative text-sm font-medium text-muted">Drawing the architecture…</p>
        </div>
      )}
    </div>
  );
}
