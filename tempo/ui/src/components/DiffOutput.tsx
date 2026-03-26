export type DiffLine = { type: "add" | "remove" | "same"; line: string };

export function computeLineDiff(prev: string, curr: string): DiffLine[] {
  const prevLines = prev.split("\n");
  const currLines = curr.split("\n");
  const prevCount = new Map<string, number>();
  for (const l of prevLines) prevCount.set(l, (prevCount.get(l) ?? 0) + 1);
  const result: DiffLine[] = [];
  for (const line of currLines) {
    const count = prevCount.get(line) ?? 0;
    if (count > 0) {
      result.push({ type: "same", line });
      prevCount.set(line, count - 1);
    } else {
      result.push({ type: "add", line });
    }
  }
  for (const line of prevLines) {
    const count = prevCount.get(line) ?? 0;
    if (count > 0) {
      result.push({ type: "remove", line });
      prevCount.set(line, count - 1);
    }
  }
  return result;
}

export function DiffOutput({ prev, curr, style }: { prev: string; curr: string; style: React.CSSProperties }) {
  const lines = computeLineDiff(prev, curr);
  return (
    <pre className="output" role="region" aria-label="Mode output diff" aria-live="polite" style={style}>
      {lines.map((l, i) => (
        <span key={i} style={{
          display: "block",
          background: l.type === "add" ? "rgba(34, 197, 94, 0.15)" : l.type === "remove" ? "rgba(239, 68, 68, 0.15)" : "transparent",
          color: l.type === "remove" ? "var(--text-tertiary)" : "inherit",
        }}>
          <span style={{ userSelect: "none", opacity: 0.6 }}>{l.type === "add" ? "+ " : l.type === "remove" ? "- " : "  "}</span>
          {l.line}
        </span>
      ))}
    </pre>
  );
}
