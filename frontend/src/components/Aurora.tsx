/** Animated aurora backdrop: drifting colour fields + layered sine waves.
 *  Perf: the fields are soft radial gradients moved with transforms only — no CSS blur filter
 *  (a 100px blur on full-screen animated layers is what made the page stutter). */
export function Aurora({ intensity = 1 }: { intensity?: number }) {
  const blob = "absolute rounded-full will-change-transform";
  return (
    <div aria-hidden className="bg-anim pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-bg">
      <div style={{ opacity: `calc(var(--aurora-opacity) * ${intensity})` }} className="absolute inset-0">
        <div className={blob} style={{ width: "70vw", height: "70vw", left: "-18vw", top: "-28vw",
          background: "radial-gradient(circle, var(--aurora-1) 0%, transparent 62%)", animation: "aurora-drift 26s ease-in-out infinite" }} />
        <div className={blob} style={{ width: "64vw", height: "64vw", right: "-20vw", top: "-14vw",
          background: "radial-gradient(circle, var(--aurora-2) 0%, transparent 62%)", animation: "aurora-drift 32s ease-in-out -6s infinite reverse" }} />
        <div className={blob} style={{ width: "58vw", height: "58vw", left: "20vw", bottom: "-32vw",
          background: "radial-gradient(circle, var(--aurora-3) 0%, transparent 62%)", animation: "aurora-drift 38s ease-in-out -12s infinite" }} />
      </div>
      <Waves />
      <div className="grain" />
    </div>
  );
}

function Waves() {
  const layers = [
    { color: "var(--aurora-1)", opacity: 0.14, duration: 30, y: 0, amp: 24 },
    { color: "var(--aurora-2)", opacity: 0.1, duration: 42, y: 18, amp: 30 },
    { color: "var(--aurora-3)", opacity: 0.08, duration: 56, y: 36, amp: 20 },
  ];
  return (
    <div className="absolute inset-x-0 bottom-0 h-[34vh]">
      {layers.map((l, i) => (
        <svg key={i} className="absolute bottom-0 left-0 h-full w-[200%] will-change-transform" viewBox="0 0 2880 400" preserveAspectRatio="none"
          style={{ animation: `wave-slide ${l.duration}s linear infinite`, opacity: l.opacity }}>
          <path fill={l.color} d={wavePath(2880, 400, 160 + l.y, l.amp)} />
        </svg>
      ))}
    </div>
  );
}

/** Two identical periods side by side so the -50% slide loops seamlessly. */
function wavePath(w: number, h: number, base: number, amp: number): string {
  const period = w / 2;
  let d = `M0 ${base}`;
  for (let x = 0; x <= w; x += 40) {
    const t = (x / period) * Math.PI * 2;
    d += ` L${x} ${(base + Math.sin(t) * amp + Math.sin(t * 2 + 1) * amp * 0.35).toFixed(1)}`;
  }
  return `${d} L${w} ${h} L0 ${h} Z`;
}
