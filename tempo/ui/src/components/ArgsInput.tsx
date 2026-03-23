import { useState, useRef, useEffect, type RefObject } from "react";
import { Play, Loader2, ChevronDown } from "lucide-react";
import { MODE_HINTS } from "./modeHints";

interface ArgsInputProps {
  value: string;
  history: string[];
  historyOpen: boolean;
  placeholder: string;
  argsInputRef: RefObject<HTMLInputElement>;
  modeRunning: boolean;
  activeMode: string;
  onChange: (v: string) => void;
  onRun: () => void;
  onHistoryOpen: (v: boolean) => void;
  onHistorySelect: (q: string) => void;
}

export function ArgsInput({
  value, history, historyOpen, placeholder, argsInputRef, modeRunning, activeMode,
  onChange, onRun, onHistoryOpen, onHistorySelect,
}: ArgsInputProps) {
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [isFocused, setIsFocused] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const hint = activeMode.startsWith("kit:") ? "(no args needed)" : MODE_HINTS[activeMode];

  const closeHistory = () => { onHistoryOpen(false); setHistoryIdx(-1); };

  // Close dropdown on outside click
  useEffect(() => {
    if (!historyOpen) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onHistoryOpen(false);
        setHistoryIdx(-1);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [historyOpen, onHistoryOpen]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!historyOpen || history.length === 0) {
      if (e.key === "Enter") { closeHistory(); onRun(); }
      if (e.key === "Escape") closeHistory();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.min(historyIdx + 1, history.length - 1);
      setHistoryIdx(next);
      onChange(history[next]);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (historyIdx <= 0) { setHistoryIdx(-1); onChange(""); return; }
      const next = historyIdx - 1;
      setHistoryIdx(next);
      onChange(history[next]);
    } else if (e.key === "Enter") {
      if (historyIdx >= 0) { onHistorySelect(history[historyIdx]); }
      else { onRun(); }
      closeHistory();
    } else if (e.key === "Escape") {
      closeHistory();
    }
  };

  const toggleDropdown = () => {
    if (history.length === 0) return;
    if (historyOpen) { closeHistory(); } else { onHistoryOpen(true); setHistoryIdx(-1); }
  };

  return (
    <div style={{ marginBottom: 8 }}>
    <div ref={containerRef} style={{ display: "flex", gap: 6, position: "relative" }}>
      <div style={{ flex: 1, position: "relative", display: "flex" }}>
        <input
          ref={argsInputRef}
          className="input"
          placeholder={placeholder}
          aria-label="Mode arguments"
          aria-autocomplete="list"
          aria-haspopup={history.length > 0 ? "listbox" : undefined}
          aria-expanded={historyOpen && history.length > 0}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          style={{ width: "100%", borderTopRightRadius: history.length > 0 ? 0 : undefined, borderBottomRightRadius: history.length > 0 ? 0 : undefined }}
        />
        {history.length > 0 && (
          <button
            type="button"
            onClick={toggleDropdown}
            title="Query history"
            aria-label="Toggle query history"
            aria-expanded={historyOpen}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: 24, flexShrink: 0,
              background: "var(--bg-tertiary)", border: "1px solid var(--border)",
              borderLeft: "none", borderRadius: "0 5px 5px 0",
              color: "var(--text-tertiary)", cursor: "pointer",
              transition: "color 0.15s, background 0.15s",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text-primary)"; e.currentTarget.style.background = "var(--bg-hover)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; e.currentTarget.style.background = "var(--bg-tertiary)"; }}
          >
            <ChevronDown size={11} aria-hidden="true" style={{ transform: historyOpen ? "rotate(180deg)" : undefined, transition: "transform 0.15s" }} />
          </button>
        )}
        {historyOpen && history.length > 0 && (
          <div
            role="listbox"
            aria-label="Argument history"
            style={{
              position: "absolute", top: "100%", left: 0, right: 0, zIndex: 100,
              background: "var(--bg-secondary)", border: "1px solid var(--border)",
              borderRadius: 4, marginTop: 2, boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
            }}
          >
            {history.map((q, i) => (
              <div
                key={i}
                role="option"
                aria-selected={i === historyIdx}
                style={{
                  padding: "5px 10px", fontSize: 11, cursor: "pointer",
                  color: "var(--text-secondary)",
                  background: i === historyIdx ? "var(--bg-hover)" : "transparent",
                }}
                onMouseEnter={(e) => { setHistoryIdx(i); (e.currentTarget.style.background = "var(--bg-hover)"); }}
                onMouseLeave={(e) => { if (historyIdx !== i) e.currentTarget.style.background = "transparent"; }}
                onMouseDown={() => { onHistorySelect(q); closeHistory(); }}
              >
                {q}
              </div>
            ))}
          </div>
        )}
      </div>
      <button
        className={modeRunning ? "btn btn-run running" : "btn btn-run"}
        onClick={onRun}
        disabled={modeRunning}
        style={{ padding: "4px 10px" }}
        title="Run (⌘↵)"
        aria-label={modeRunning ? "Running…" : "Run mode (⌘↵)"}
      >
        {modeRunning
          ? <><Loader2 size={11} className="spin" aria-hidden="true" /> Running...</>
          : <><Play size={11} aria-hidden="true" /> Run</>
        }
      </button>
    </div>
    {isFocused && !value && hint && (
      <div style={{
        fontSize: 10,
        color: "var(--text-tertiary)",
        opacity: 0.7,
        fontFamily: "var(--font-mono)",
        marginTop: 3,
        paddingLeft: 2,
        letterSpacing: "0.01em",
      }}>
        {hint}
      </div>
    )}
    </div>
  );
}
