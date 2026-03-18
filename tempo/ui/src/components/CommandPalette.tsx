import { useState, useEffect, useRef } from "react";
import type { ComponentType } from "react";

interface ModeInfo {
  mode: string;
  label: string;
  icon: ComponentType<{ size?: number }>;
  tag: string;
}

interface Props {
  modes: ModeInfo[];
  onSelect: (mode: string) => void;
  onClose: () => void;
}

export function CommandPalette({ modes, onSelect, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const filtered = query
    ? modes.filter(m => m.label.toLowerCase().includes(query.toLowerCase()) || m.mode.includes(query.toLowerCase()))
    : modes;

  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement;
    inputRef.current?.focus();
    return () => { previousFocusRef.current?.focus(); };
  }, []);

  useEffect(() => {
    setSelected(0);
  }, [query]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      if (e.key === "ArrowDown") { e.preventDefault(); setSelected(s => Math.min(s + 1, filtered.length - 1)); }
      if (e.key === "ArrowUp") { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)); }
      if (e.key === "Enter" && filtered[selected]) { e.preventDefault(); onSelect(filtered[selected].mode); onClose(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [filtered, selected, onSelect, onClose]);

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
          borderRadius: 8, width: 400, maxHeight: 380, overflow: "hidden",
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
          aria-activedescendant={filtered[selected] ? `cmd-opt-${filtered[selected].mode}` : undefined}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ margin: 8, fontSize: 13, padding: "8px 12px" }}
        />
        <div id="cmd-palette-list" role="listbox" aria-label="Mode options" style={{ overflowY: "auto", maxHeight: 300 }}>
          {filtered.map((m, i) => (
            <div
              key={m.mode}
              id={`cmd-opt-${m.mode}`}
              role="option"
              aria-selected={i === selected}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "8px 12px", cursor: "pointer",
                background: i === selected ? "var(--bg-active)" : "transparent",
                fontSize: 13,
              }}
              onMouseEnter={() => setSelected(i)}
              onClick={() => { onSelect(m.mode); onClose(); }}
            >
              <m.icon size={14} aria-hidden="true" />
              <span style={{ flex: 1 }}>{m.label}</span>
              <span style={{ fontSize: 10, color: "var(--text-tertiary)", background: "var(--bg-active)", padding: "1px 6px", borderRadius: 3 }} aria-hidden="true">{m.tag}</span>
            </div>
          ))}
          {filtered.length === 0 && (
            <div style={{ padding: "16px 12px", color: "var(--text-tertiary)", fontSize: 12 }}>No modes match "{query}"</div>
          )}
        </div>
        <div style={{ padding: "6px 12px", borderTop: "1px solid var(--border)", fontSize: 10, color: "var(--text-tertiary)", display: "flex", gap: 12 }}>
          <span>↑↓ navigate</span><span>↵ select</span><span>Esc close</span>
        </div>
      </div>
    </div>
  );
}
