/** Small, dependency-free effects: the card spotlight and the celebration burst. Both respect reduced motion. */

/** Settings → Experience (10-05): "calm" animations and confetti on/off, set by App from the user's preferences. */
export const experience = { calm: false, celebrate: true };
const reduced = () => experience.calm || (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

/** One passive listener for the whole app: the `.spotlight` element under the pointer gets --mx/--my (once per frame). */
export function installSpotlight(): void {
  let frame = 0;
  let last: PointerEvent | null = null;
  window.addEventListener("pointermove", (e) => {
    last = e;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const el = (last?.target as Element | null)?.closest?.(".spotlight") as HTMLElement | null;
      if (!el || !last) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${last.clientX - r.left}px`);
      el.style.setProperty("--my", `${last.clientY - r.top}px`);
    });
  }, { passive: true });
}

/** Confetti from a point (an element or the screen centre), in the theme's colours. `big` for milestones. */
export function celebrate(from?: Element | null, big = false): void {
  if (reduced() || !experience.celebrate) return;
  const css = getComputedStyle(document.documentElement);
  const colors = ["--primary", "--primary-2", "--success", "--warning", "--info"].map((v) => css.getPropertyValue(v).trim() || "#8466ff");
  const r = from?.getBoundingClientRect();
  const x0 = r ? r.left + r.width / 2 : window.innerWidth / 2;
  const y0 = r ? r.top + r.height / 2 : window.innerHeight / 3;
  const layer = document.createElement("div");
  layer.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:200;overflow:hidden";
  document.body.appendChild(layer);
  const n = big ? 140 : 54;
  for (let i = 0; i < n; i++) {
    const p = document.createElement("span");
    const size = 5 + Math.random() * (big ? 8 : 6);
    const round = Math.random() < 0.35;
    p.style.cssText = `position:absolute;left:${x0}px;top:${y0}px;width:${size}px;height:${round ? size : size * 0.45}px;` +
      `background:${colors[i % colors.length]};border-radius:${round ? "50%" : "2px"};will-change:transform,opacity`;
    layer.appendChild(p);
    const angle = (big ? Math.random() * Math.PI * 2 : -Math.PI / 2 + (Math.random() - 0.5) * 2.2);
    const speed = (big ? 260 : 180) + Math.random() * (big ? 420 : 240);
    const dx = Math.cos(angle) * speed, dy = Math.sin(angle) * speed;
    const spin = (Math.random() - 0.5) * 1080;
    p.animate([
      { transform: "translate(-50%,-50%) rotate(0deg)", opacity: 1 },
      { transform: `translate(calc(-50% + ${dx * 0.75}px), calc(-50% + ${dy * 0.75}px)) rotate(${spin * 0.6}deg)`, opacity: 1, offset: 0.55 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy + (big ? 420 : 260)}px)) rotate(${spin}deg)`, opacity: 0 },
    ], { duration: (big ? 1900 : 1300) + Math.random() * 600, easing: "cubic-bezier(.15,.7,.3,1)", fill: "forwards" });
  }
  window.setTimeout(() => layer.remove(), big ? 2800 : 2100);
}
