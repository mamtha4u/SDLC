import { useEffect, useRef, useState } from "react";

/** A number that counts up to its value (and glides between later values). Tabular digits, so it never jitters. */
export function CountUp({ value, format = (n: number) => Math.round(n).toLocaleString(), duration = 900, className }: {
  value: number; format?: (n: number) => string; duration?: number; className?: string;
}) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setShown(value); from.current = value; return; }
    const start = performance.now(), a = from.current, b = value;
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - k, 4);
      setShown(a + (b - a) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = b;
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); from.current = b; };
  }, [value, duration]);
  return <span className={`tabular ${className ?? ""}`}>{format(shown)}</span>;
}
