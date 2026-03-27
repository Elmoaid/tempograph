import { useEffect } from "react";

export function HighlightedOutput({
  text, query, searchText, currentSearchMatch, style,
}: {
  text: string;
  query: string;
  searchText?: string;
  currentSearchMatch?: number;
  style: React.CSSProperties;
}) {
  // Scroll to active search match when it changes
  useEffect(() => {
    if (!searchText?.trim() || !currentSearchMatch) return;
    const el = document.getElementById(`osm-${currentSearchMatch}`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [searchText, currentSearchMatch]);

  // Search highlighting takes priority over filter highlighting
  const activeQuery = searchText?.trim() ? searchText : query;
  const isSearch = Boolean(searchText?.trim());

  if (!activeQuery.trim()) {
    return <pre className="output" role="region" aria-label="Mode output" aria-live="polite" style={style}>{text}</pre>;
  }

  const lowerQ = activeQuery.toLowerCase();
  const qLen = activeQuery.length;
  const lines = text.split("\n");
  let matchIndex = 0;

  return (
    <pre className="output" role="region" aria-label="Mode output" aria-live="polite" style={style}>
      {lines.map((line, i) => {
        const parts: React.ReactNode[] = [];
        let rest = line;
        while (rest) {
          const idx = rest.toLowerCase().indexOf(lowerQ);
          if (idx === -1) { parts.push(rest); break; }
          if (idx > 0) parts.push(rest.slice(0, idx));
          matchIndex++;
          const isActive = isSearch && matchIndex === currentSearchMatch;
          parts.push(
            <mark
              key={parts.length}
              id={isSearch ? `osm-${matchIndex}` : undefined}
              style={{
                background: isSearch
                  ? isActive ? "#ffd700" : "rgba(255,215,0,0.35)"
                  : "var(--accent-dim, rgba(99,102,241,0.25))",
                color: isActive ? "#000" : "inherit",
                borderRadius: 2,
                padding: "0 1px",
                outline: isActive ? "1px solid #ffd700" : "none",
              }}
            >
              {rest.slice(idx, idx + qLen)}
            </mark>
          );
          rest = rest.slice(idx + qLen);
        }
        return <span key={i}>{parts}{i < lines.length - 1 ? "\n" : ""}</span>;
      })}
    </pre>
  );
}
