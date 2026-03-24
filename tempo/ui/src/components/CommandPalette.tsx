import React, { useState, useEffect, useRef } from "react";
import { Clock } from "lucide-react";
import type { ModeInfo, RecentCommand } from "./modes";
import { loadRecentCommands } from "./modes";

interface Props {
  modes: ModeInfo[];
  onSelect: (mode: string, args?: string) => void;
  onClose: () => void;
}

const GROUP_LABELS: Record<string, string> = {
  analyze: "Analyze",
  navigate: "Navigate",
  ai: "AI-Powered",
};

interface FuzzyMatch {
  score: number;
  indices: number[]; // indices in label string
}

function fuzzyMatch(query: string, target: string): FuzzyMatch | null {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  const indices: number[] = [];
  let qi = 0, score = 0, streak = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (q[qi] === t[ti]) {
      indices.push(ti);
      score += 1 + streak;
      streak++;
      qi++;
    } else {
      streak = 0;
    }
  }
  if (qi < q.length) return null;
  if (indices[0] === 0) score += 10; // prefix bonus
  return { score, indices };
}

export function CommandPalette({ modes, onSelect, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [recent, setRecent] = useState<RecentCommand[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setRecent(loadRecentCommands().slice(0, 3));
    previousFocusRef.current = document.activeElement as HTMLElement;
    inputRef.current?.focus();
    return () => { previousFocusRef.current?.focus(); };
  }, []);

  useEffect(() => { setSelected(0); }, [query]);

  // Build flat list for keyboard navigation — fuzzy when query set, all modes otherwise
  const fuzzyResults = query
    ? modes.flatMap(m => {
        const labelResult = fuzzyMatch(query, m.label);
        const modeResult = fuzzyMatch(query, m.mode);
        const best = labelResult && modeResult
          ? (labelResult.score >= modeResult.score ? labelResult : modeResult)
          : (labelResult ?? modeResult);
        if (!best) return [];
        // Always use label indices for highlighting
        const labelMatch = fuzzyMatch(query, m.label);
        return [{ m, score: best.score, labelIndices: labelMatch?.indices ?? [] }];
      }).sort((a, b) => b.score - a.score)
    : null;

  const filtered = fuzzyResults ? fuzzyResults.map(r => r.m) : modes;
  const matchMap = fuzzyResults
    ? new Map(fuzzyResults.map(r => [r.m.mode, r.labelIndices]))
    : new Map<string, number[]>();

  // In empty-query state, items = recent entries + all modes (for keyboard nav index)
  const showRecent = !query && recent.length > 0;
  const recentCount = showRecent ? recent.length : 0;
  const totalItems = recentCount + filtered.length;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      if (e.key === "ArrowDown") { e.preventDefault(); setSelected(s => Math.min(s + 1, totalItems - 1)); }
      if (e.key === "ArrowUp") { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)); }
      if (e.key === "Enter") {
        e.preventDefault();
        if (selected < recentCount) {
          const r = recent[selected];
          onSelect(r.mode, r.args);
          onClose();
        } else {
          const m = filtered[selected - recentCount];
          if (m) { onSelect(m.mode); onClose(); }
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [filtered, selected, recentCount, recent, onSelect, onClose, totalItems]);

  // Group modes for display (only in empty-query state)
  const groups = !query
    ? (["analyze", "navigate", "ai"] as const).map(g => ({
        key: g,
        label: GROUP_LABELS[g],
        items: filtered.filter(m => m.group === g),
      })).filter(g => g.items.length > 0)
    : null;

  // For keyboard nav offset calculation when grouped
  const modeIndexOffset = (modeItem: ModeInfo) => {
    return recentCount + filtered.indexOf(modeItem);
  };

  const modeForRecent = (r: RecentCommand) => modes.find(m => m.mode === r.mode);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.6)", display: "flex",
        alignItems: "flex-start", justifyContent: "center", paddingTop: 80,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-secondary)", border: "1px solid var(--border)",
          borderRadius: 8, width: 440, maxHeight: 440, overflow: "hidden",
          display: "flex", flexDirection: "column", boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="input"
          placeholder="Search modes..."
          aria-label="Search modes"
          aria-autocomplete="list"
          aria-controls="cmd-palette-list"
          aria-activedescendant={
            selected < recentCount
              ? `cmd-recent-${selected}`
              : filtered[selected - recentCount] ? `cmd-opt-${filtered[selected - recentCount].mode}` : undefined
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ margin: 8, fontSize: 13, padding: "8px 12px" }}
        />

        <div id="cmd-palette-list" role="listbox" aria-label="Mode options" style={{ overflowY: "auto", maxHeight: 340 }}>
          {/* Recent commands */}
          {showRecent && (
            <>
              <GroupHeader label="Recent" />
              {recent.map((r, i) => {
                const info = modeForRecent(r);
                const isSelected = i === selected;
                return (
                  <div
                    key={`recent-${i}`}
                    id={`cmd-recent-${i}`}
                    role="option"
                    aria-selected={isSelected}
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "7px 12px", cursor: "pointer",
                      background: isSelected ? "var(--bg-active)" : "transparent",
                      fontSize: 13,
                    }}
                    onMouseEnter={() => setSelected(i)}
                    onClick={() => { onSelect(r.mode, r.args); onClose(); }}
                  >
                    <Clock size={13} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} aria-hidden="true" />
                    <span style={{ flex: 1 }}>
                      {info?.label ?? r.mode}
                      {r.args && <span style={{ color: "var(--text-tertiary)", marginLeft: 6, fontSize: 11 }}>{r.args.length > 30 ? r.args.slice(0, 30) + "…" : r.args}</span>}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-tertiary)" }} aria-hidden="true">recent</span>
                  </div>
                );
              })}
            </>
          )}

          {/* Grouped modes (empty query) or flat fuzzy results */}
          {groups
            ? groups.map(group => (
                <div key={group.key}>
                  <GroupHeader label={group.label} />
                  {group.items.map(m => {
                    const idx = modeIndexOffset(m);
                    const isSelected = idx === selected;
                    return <ModeItem key={m.mode} m={m} isSelected={isSelected} onHover={() => setSelected(idx)} onSelect={() => { onSelect(m.mode); onClose(); }} matchIndices={[]} />;
                  })}
                </div>
              ))
            : filtered.map((m, i) => {
                const idx = recentCount + i;
                const isSelected = idx === selected;
                return <ModeItem key={m.mode} m={m} isSelected={isSelected} onHover={() => setSelected(idx)} onSelect={() => { onSelect(m.mode); onClose(); }} matchIndices={matchMap.get(m.mode) ?? []} />;
              })
          }

          {filtered.length === 0 && (
            <div style={{ padding: "16px 12px", color: "var(--text-tertiary)", fontSize: 12 }}>No modes match "{query}"</div>
          )}
        </div>

        <div style={{ borderTop: "1px solid var(--border)" }}>
          {(() => {
            const desc = selected < recentCount
              ? (modeForRecent(recent[selected])?.desc)
              : filtered[selected - recentCount]?.desc;
            return desc ? (
              <div style={{ padding: "6px 12px", fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5, borderBottom: "1px solid var(--border-subtle)" }}>
                {desc}
              </div>
            ) : null;
          })()}
          <div style={{ padding: "5px 12px", fontSize: 10, color: "var(--text-tertiary)", display: "flex", gap: 12 }}>
            <span>↑↓ navigate</span><span>↵ select</span><span>Esc close</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function GroupHeader({ label }: { label: string }) {
  return (
    <div style={{ padding: "5px 12px 3px", fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
      {label}
    </div>
  );
}

function highlightLabel(label: string, indices: number[]): React.ReactNode {
  if (!indices.length) return label;
  const indexSet = new Set(indices);
  const parts: React.ReactNode[] = [];
  for (let i = 0; i < label.length; i++) {
    if (indexSet.has(i)) {
      parts.push(
        <mark key={i} style={{ background: "var(--accent-muted)", color: "var(--accent-hover)", borderRadius: 2, padding: "0 1px" }}>{label[i]}</mark>
      );
    } else {
      // Merge consecutive non-highlighted chars
      const last = parts[parts.length - 1];
      if (typeof last === "string") { parts[parts.length - 1] = last + label[i]; }
      else { parts.push(label[i]); }
    }
  }
  return <>{parts}</>;
}

function ModeItem({ m, isSelected, onHover, onSelect, matchIndices }: { m: ModeInfo; isSelected: boolean; onHover: () => void; onSelect: () => void; matchIndices: number[] }) {
  return (
    <div
      id={`cmd-opt-${m.mode}`}
      role="option"
      aria-selected={isSelected}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "7px 12px", cursor: "pointer",
        background: isSelected ? "var(--bg-active)" : "transparent",
        fontSize: 13,
      }}
      onMouseEnter={onHover}
      onClick={onSelect}
    >
      <m.icon size={14} aria-hidden="true" />
      <span style={{ flex: 1 }}>{highlightLabel(m.label, matchIndices)}</span>
      <span style={{ fontSize: 10, color: "var(--text-tertiary)", background: "var(--bg-active)", padding: "1px 6px", borderRadius: 3 }} aria-hidden="true">{m.tag}</span>
    </div>
  );
}
