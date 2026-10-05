import clsx from "clsx";

/** A unified diff with added / removed lines coloured. */
export function DiffView({ diff, className = "" }: { diff: string; className?: string }) {
  return (
    <pre className={clsx("max-h-[360px] overflow-auto rounded-[12px] border border-line bg-bg-2 py-2 font-mono text-[12px] leading-relaxed", className)}>
      {diff.split("\n").map((l, i) => (
        <div key={i} className={clsx("px-3",
          l.startsWith("+++") || l.startsWith("---") ? "font-semibold text-muted"
            : l.startsWith("+") ? "bg-success/12 text-success" : l.startsWith("-") ? "bg-danger/12 text-danger"
              : l.startsWith("@@") ? "text-primary-2" : "text-muted")}>{l || " "}</div>
      ))}
    </pre>
  );
}
