import { useEffect, useRef } from "react";

/** The blueprint under the page: a cloud flow drawn like an architect's sheet, revealed only around the pointer
 *  ("the conductor's light"). Compositor-only: a round window moves with `transform` while the sheet inside moves the
 *  opposite way, so nothing repaints per frame. Without a mouse (or when idle) the light drifts on its own. */
export function Blueprint() {
  const lens = useRef<HTMLDivElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const L = lens.current, S = sheet.current;
    if (!L || !S) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const R = 280; // lens radius
    let x = innerWidth * 0.62, y = innerHeight * 0.42, tx = x, ty = y, last = 0, raf = 0;
    const place = () => {
      L.style.transform = `translate3d(${x - R}px, ${y - R}px, 0)`;
      S.style.transform = `translate3d(${R - x}px, ${R - y}px, 0)`;
    };
    const tick = (t: number) => {
      if (t - last > 3500 && !reduce) { // idle: drift slowly in a lazy figure-eight
        tx = innerWidth * (0.55 + 0.3 * Math.sin(t / 4200));
        ty = innerHeight * (0.45 + 0.22 * Math.sin(t / 2700));
      }
      x += (tx - x) * 0.09;
      y += (ty - y) * 0.09;
      place();
      raf = requestAnimationFrame(tick);
    };
    const move = (e: PointerEvent) => { if (e.pointerType === "mouse") { tx = e.clientX; ty = e.clientY; last = performance.now(); } };
    const vis = () => { cancelAnimationFrame(raf); if (!document.hidden) raf = requestAnimationFrame(tick); };
    window.addEventListener("pointermove", move, { passive: true });
    document.addEventListener("visibilitychange", vis);
    place();
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); window.removeEventListener("pointermove", move); document.removeEventListener("visibilitychange", vis); };
  }, []);
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-[5] overflow-hidden">
      <div ref={lens} className="absolute left-0 top-0 h-[560px] w-[560px] overflow-hidden rounded-full will-change-transform"
        style={{ WebkitMaskImage: "radial-gradient(circle, #000 0%, rgba(0,0,0,.85) 35%, transparent 70%)",
          maskImage: "radial-gradient(circle, #000 0%, rgba(0,0,0,.85) 35%, transparent 70%)" }}>
        <div ref={sheet} className="absolute left-0 top-0 h-[100vh] w-[100vw] will-change-transform"
          style={{
            backgroundImage: [
              "linear-gradient(color-mix(in srgb, var(--primary-2) 20%, transparent) 1px, transparent 1px)",
              "linear-gradient(90deg, color-mix(in srgb, var(--primary-2) 20%, transparent) 1px, transparent 1px)",
              "linear-gradient(color-mix(in srgb, var(--primary-2) 11%, transparent) 1px, transparent 1px)",
              "linear-gradient(90deg, color-mix(in srgb, var(--primary-2) 11%, transparent) 1px, transparent 1px)",
            ].join(","),
            backgroundSize: "120px 120px, 120px 120px, 24px 24px, 24px 24px",
          }}>
          <Sheet />
        </div>
      </div>
    </div>
  );
}

/** The drawing itself: a flow with service boxes, connectors, dimension lines and spec notes. */
function Sheet() {
  const ink = "color-mix(in srgb, var(--primary-2) 60%, transparent)";
  const soft = "color-mix(in srgb, var(--primary-2) 34%, transparent)";
  const box = (x: number, y: number, w: number, h: number, title: string, spec: string) => (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={10} fill="none" stroke={ink} strokeWidth={1.4} />
      <rect x={x + 6} y={y + 6} width={w - 12} height={h - 12} rx={6} fill="none" stroke={soft} strokeDasharray="3 4" />
      <text x={x + 16} y={y + 30} fill={ink} fontFamily="JetBrains Mono, monospace" fontSize={13} fontWeight={600}>{title}</text>
      <text x={x + 16} y={y + 50} fill={soft} fontFamily="JetBrains Mono, monospace" fontSize={11}>{spec}</text>
    </g>
  );
  const arrow = (x1: number, x2: number, y: number, label: string) => (
    <g>
      <line x1={x1} x2={x2 - 8} y1={y} y2={y} stroke={ink} strokeWidth={1.4} />
      <path d={`M${x2 - 10},${y - 5} L${x2},${y} L${x2 - 10},${y + 5}`} fill="none" stroke={ink} strokeWidth={1.4} />
      <text x={(x1 + x2) / 2} y={y - 8} textAnchor="middle" fill={soft} fontFamily="JetBrains Mono, monospace" fontSize={10}>{label}</text>
    </g>
  );
  const dim = (x1: number, x2: number, y: number, label: string) => (
    <g stroke={soft} fill={soft}>
      <line x1={x1} x2={x2} y1={y} y2={y} />
      <line x1={x1} x2={x1} y1={y - 6} y2={y + 6} />
      <line x1={x2} x2={x2} y1={y - 6} y2={y + 6} />
      <text x={(x1 + x2) / 2} y={y + 16} textAnchor="middle" stroke="none" fontFamily="JetBrains Mono, monospace" fontSize={10}>{label}</text>
    </g>
  );
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">
      <text x={60} y={80} fill={ink} fontFamily="Space Grotesk, sans-serif" fontSize={22} fontWeight={700}>ORKESTRA · FLOW BLUEPRINT</text>
      <text x={60} y={104} fill={soft} fontFamily="JetBrains Mono, monospace" fontSize={12}>sheet 1/1 · created_by=orkestra · every step approved by you</text>
      {box(120, 300, 220, 70, "Partner portal", "HTTPS POST /orders")}
      {arrow(340, 470, 335, "XML · API key")}
      {box(470, 300, 230, 70, "API Gateway", "REST · stage dev")}
      {arrow(700, 830, 335, "proxy event")}
      {box(830, 290, 260, 90, "λ transform", "python3.14 · 256 MB · 10 s")}
      {arrow(1090, 1220, 335, "order.received")}
      {box(1220, 300, 250, 70, "SQS FIFO", "dedup = OrderId · DLQ×3")}
      {box(870, 470, 190, 60, "Logger layer", "mask_pii() · corr id")}
      <line x1={960} x2={960} y1={380} y2={470} stroke={ink} strokeDasharray="4 4" />
      {box(1220, 470, 250, 60, "CloudWatch Logs", "retention 14 d")}
      <path d="M1090 360 C1160 360 1150 500 1220 500" fill="none" stroke={soft} strokeDasharray="4 4" />
      {dim(120, 1470, 230, "≈ 182 ms end to end · p95 < 800 ms")}
      {dim(830, 1090, 600, "coverage 99.5% ≥ gate 70%")}
      <g fill="none" stroke={soft}>
        <circle cx={1380} cy={720} r={70} strokeDasharray="2 6" />
        <circle cx={1380} cy={720} r={46} />
        <circle cx={1380} cy={720} r={22} strokeDasharray="6 4" />
      </g>
      <text x={1380} y={812} textAnchor="middle" fill={soft} fontFamily="JetBrains Mono, monospace" fontSize={10}>fence ⊃ boundary ⊃ agent role</text>
      <g fontFamily="JetBrains Mono, monospace" fontSize={11} fill={soft}>
        <text x={120} y={640}>01 Echo   · 00_requirement.md   ✓ signed off</text>
        <text x={120} y={662}>02 Atlas  · 01_data_mapping.md  ✓ 6/6 examples agree</text>
        <text x={120} y={684}>03 Archie · architecture.drawio ✓ approved</text>
        <text x={120} y={706}>04 Terra  · plan: 13 to add, approved</text>
        <text x={120} y={728}>05 Dev    · 52 tests passed · 99.5%</text>
        <text x={120} y={750}>06 Quinn  · live 5/5 checks ✓</text>
      </g>
    </svg>
  );
}
