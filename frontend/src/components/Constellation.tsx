import { useEffect, useRef } from "react";

/** Interactive constellation: drifting stars that link up when close and reach toward the cursor.
 *  Canvas + requestAnimationFrame, DPR-aware, re-reads theme colours on theme change,
 *  pauses when the tab is hidden, draws a single static frame under prefers-reduced-motion. */
export function Constellation({ density = 1 }: { density?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, raf = 0;
    let colors = { a: "#7c5cff", b: "#22d3ee" };
    const mouse = { x: -9999, y: -9999 };
    type Star = { x: number; y: number; vx: number; vy: number; r: number; hue: 0 | 1; tw: number };
    let stars: Star[] = [];

    const readColors = () => {
      const cs = getComputedStyle(document.documentElement);
      colors = { a: cs.getPropertyValue("--primary").trim() || colors.a, b: cs.getPropertyValue("--primary-2").trim() || colors.b };
    };
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // O(n²) linking: keep n small (≤ 60) so it stays well under 2 ms/frame even on laptops
      const count = Math.round(Math.min(60, (w * h) / 24000) * density);
      stars = Array.from({ length: count }, () => ({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
        r: Math.random() * 1.4 + 0.4, hue: Math.random() > 0.5 ? 1 : 0, tw: Math.random() * Math.PI * 2,
      }));
    };
    const LINK = 120, REACH = 170;

    const frame = (t: number) => {
      if (document.body.classList.contains("modal-open")) { raf = requestAnimationFrame(frame); return; } // frozen behind dialogs
      ctx.clearRect(0, 0, w, h);
      for (const s of stars) {
        if (!reduce) {
          // gentle pull toward the cursor
          const dx = mouse.x - s.x, dy = mouse.y - s.y, d2 = dx * dx + dy * dy;
          if (d2 < REACH * REACH) { s.vx += dx * 0.00002; s.vy += dy * 0.00002; }
          s.vx *= 0.995; s.vy *= 0.995;
          s.x += s.vx; s.y += s.vy;
          if (s.x < -10) s.x = w + 10; if (s.x > w + 10) s.x = -10;
          if (s.y < -10) s.y = h + 10; if (s.y > h + 10) s.y = -10;
        }
      }
      ctx.lineWidth = 0.6;
      for (let i = 0; i < stars.length; i++) {
        const a = stars[i];
        for (let j = i + 1; j < stars.length; j++) {
          const b = stars[j], dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy);
          if (d < LINK) {
            ctx.globalAlpha = (1 - d / LINK) * 0.35;
            ctx.strokeStyle = a.hue ? colors.b : colors.a;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
        const md = Math.hypot(mouse.x - a.x, mouse.y - a.y);
        if (md < REACH) {
          ctx.globalAlpha = (1 - md / REACH) * 0.6;
          ctx.strokeStyle = colors.b;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke();
        }
      }
      for (const s of stars) {
        ctx.globalAlpha = 0.55 + Math.sin(t / 700 + s.tw) * 0.35;
        ctx.fillStyle = s.hue ? colors.b : colors.a;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
      if (!reduce) raf = requestAnimationFrame(frame);
    };

    const onMove = (e: PointerEvent) => { mouse.x = e.clientX; mouse.y = e.clientY; };
    const onLeave = () => { mouse.x = mouse.y = -9999; };
    const onVis = () => { cancelAnimationFrame(raf); if (!document.hidden) raf = requestAnimationFrame(frame); };
    const themeObs = new MutationObserver(readColors);

    readColors(); resize();
    raf = requestAnimationFrame(frame);
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", onMove);
    document.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVis);
    themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      themeObs.disconnect();
    };
  }, [density]);

  return <canvas ref={ref} aria-hidden className="pointer-events-none fixed inset-0 -z-[5] h-full w-full" />;
}
