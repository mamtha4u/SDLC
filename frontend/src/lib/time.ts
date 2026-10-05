/** "3m ago" for a server timestamp (naive timestamps are UTC). */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z").getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** 1209600 → "14 days" (for settings in seconds). */
export function duration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 60) return `${sec}s`;
  const units: [number, string][] = [[86400, "day"], [3600, "hour"], [60, "minute"]];
  for (const [u, name] of units) {
    if (sec >= u && sec % u === 0) return `${sec / u} ${name}${sec / u === 1 ? "" : "s"}`;
  }
  return sec >= 3600 ? `${(sec / 3600).toFixed(1)} hours` : `${(sec / 60).toFixed(1)} minutes`;
}
