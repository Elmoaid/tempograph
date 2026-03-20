import { useState, type RefObject } from "react";
import { Play } from "lucide-react";

interface ArgsInputProps {
  value: string;
  history: string[];
  historyOpen: boolean;
  placeholder: string;
  argsInputRef: RefObject<HTMLInputElement>;
  modeRunning: boolean;
  onChange: (v: string) => void;
  onRun: () => void;
  onHistoryOpen: (v: boolean) => void;
  onHistorySelect: (q: string) => void;
}

export function ArgsInput({
  value, history, historyOpen, placeholder, argsInputRef, modeRunning,
  onChange, onRun, onHistoryOpen, onHistorySelect,
}: ArgsInputProps) {
  const [historyIdx, setHistoryIdx] = useState(-1);

  const closeHistory = () => { onHistoryOpen(false); setHistoryIdx(-1); };

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

  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 8, position: "relative" }}>
      <div style={{ flex: 1, position: "relative" }}>
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
          onFocus={() => { if (history.length > 0) onHistoryOpen(true); }}
          onBlur={() => setTimeout(() => { closeHistory(); }, 150)}
          style={{ width: "100%" }}
        />
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
        className="btn"
        onClick={onRun}
        disabled={modeRunning}
        style={{ padding: "4px 10px" }}
        title="Run (⌘R)"
        aria-label={modeRunning ? "Running…" : "Run mode (⌘R)"}
      >
        <Play size={11} aria-hidden="true" /> {modeRunning ? "..." : "Run"}
      </button>
    </div>
  );
}
