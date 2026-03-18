import { type RefObject } from "react";
import { Play, Copy, Check, Save, Search, ThumbsUp, ThumbsDown, X } from "lucide-react";
import type { ModeInfo } from "./modes";
import { formatAge } from "./modes";

interface OutputPanelProps {
  activeModeInfo: ModeInfo | undefined;
  activeMode: string;
  modeArgs: string;
  modeRunning: boolean;
  modeOutput: string;
  elapsed: number;
  outputTs: number | null;
  runDuration: number | null;
  copied: boolean;
  filterVisible: boolean;
  outputFilter: string;
  filteredOutput: string;
  filterMatchCount: number | null;
  history: string[];
  historyOpen: boolean;
  feedbackGiven: RefObject<Map<string, boolean>>;
  feedbackMode: string | null;
  argsInputRef: RefObject<HTMLInputElement>;
  filterInputRef: RefObject<HTMLInputElement>;
  onArgsChange: (v: string) => void;
  onHistoryOpen: (v: boolean) => void;
  onHistorySelect: (q: string) => void;
  onRun: () => void;
  onCopy: () => void;
  onSave: () => void;
  onFilterToggle: () => void;
  onFilterChange: (v: string) => void;
  onFilterClose: () => void;
  onFeedback: (helpful: boolean) => void;
}

export function OutputPanel(props: OutputPanelProps) {
  const {
    activeModeInfo, activeMode, modeArgs, modeRunning, modeOutput,
    elapsed, outputTs, runDuration, copied, filterVisible, outputFilter,
    filteredOutput, filterMatchCount, history, historyOpen, feedbackGiven,
    argsInputRef, filterInputRef,
    onArgsChange, onHistoryOpen, onHistorySelect, onRun, onCopy, onSave,
    onFilterToggle, onFilterChange, onFilterClose, onFeedback,
  } = props;

  return (
    <div className="cell" style={{ flex: 1 }}>
      <div className="cell-head">
        {activeModeInfo?.label ?? activeMode}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {modeOutput && (
            <>
              <button className="btn btn-ghost" onClick={onFilterToggle} style={{ padding: "2px 6px", fontSize: 10 }} title="Filter output (⌘F)" aria-label="Filter output (⌘F)">
                <Search size={10} aria-hidden="true" />
              </button>
              <button className="btn btn-ghost" onClick={onSave} style={{ padding: "2px 6px", fontSize: 10 }} title="Save to .tempo/" aria-label="Save output to .tempo/">
                <Save size={10} aria-hidden="true" />
              </button>
              <button className="btn btn-ghost" onClick={onCopy} style={{ padding: "2px 6px", fontSize: 10 }} aria-label={copied ? "Copied" : "Copy output"}>
                {copied ? <Check size={10} aria-hidden="true" /> : <Copy size={10} aria-hidden="true" />}
              </button>
            </>
          )}
        </div>
      </div>
      <div className="cell-body">
        {activeModeInfo?.desc && (
          <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 8, lineHeight: 1.5 }}>
            {activeModeInfo.desc}
          </div>
        )}
        <div style={{ display: "flex", gap: 6, marginBottom: 8, position: "relative" }}>
          <div style={{ flex: 1, position: "relative" }}>
            <input
              ref={argsInputRef}
              className="input"
              placeholder={activeModeInfo?.hint || "arguments (optional)"}
              aria-label="Mode arguments"
              value={modeArgs}
              onChange={(e) => onArgsChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { onHistoryOpen(false); onRun(); }
                if (e.key === "Escape") onHistoryOpen(false);
              }}
              onFocus={() => { if (history.length > 0) onHistoryOpen(true); }}
              onBlur={() => setTimeout(() => onHistoryOpen(false), 150)}
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
                    aria-selected={false}
                    style={{ padding: "5px 10px", fontSize: 11, cursor: "pointer", color: "var(--text-secondary)" }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    onMouseDown={() => onHistorySelect(q)}
                  >
                    {q}
                  </div>
                ))}
              </div>
            )}
          </div>
          <button className="btn" onClick={onRun} disabled={modeRunning} style={{ padding: "4px 10px" }} title="Run (⌘R)" aria-label={modeRunning ? "Running…" : "Run mode (⌘R)"}>
            <Play size={11} aria-hidden="true" /> {modeRunning ? "..." : "Run"}
          </button>
        </div>
        {modeRunning ? (
          <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16, textAlign: "center" }}>
            <span style={{ animation: "pulse 1.2s ease-in-out infinite", display: "inline-block" }}>
              Running {activeMode}…
            </span>
            {elapsed > 0 && (
              <span style={{ marginLeft: 8, fontFamily: "var(--font-mono)", opacity: 0.7 }}>
                {elapsed}s
              </span>
            )}
          </div>
        ) : modeOutput ? (
          <>
            {activeMode === "prepare" && (
              <button
                className="btn"
                onClick={onCopy}
                style={{ width: "100%", marginBottom: 6, fontSize: 11, padding: "5px 0", justifyContent: "center" }}
              >
                {copied ? <><Check size={11} /> Copied!</> : <><Copy size={11} /> Copy for Claude</>}
              </button>
            )}
            {filterVisible && (
              <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 4 }}>
                <input
                  ref={filterInputRef}
                  className="input"
                  placeholder="Filter lines…"
                  aria-label="Filter output lines"
                  value={outputFilter}
                  onChange={e => onFilterChange(e.target.value)}
                  onKeyDown={e => { if (e.key === "Escape") onFilterClose(); }}
                  style={{ flex: 1, fontSize: 10, padding: "2px 6px" }}
                />
                {filterMatchCount !== null && (
                  <span style={{ fontSize: 9, color: "var(--text-tertiary)", whiteSpace: "nowrap" }} aria-live="polite" aria-atomic="true">
                    {filterMatchCount} lines
                  </span>
                )}
                <button className="btn btn-ghost" onClick={onFilterClose} style={{ padding: "2px 4px" }} aria-label="Close filter">
                  <X size={9} aria-hidden="true" />
                </button>
              </div>
            )}
            <pre className="output" role="region" aria-label="Mode output" aria-live="polite" style={{ maxHeight: activeMode === "prepare" ? "calc(100% - 96px)" : "calc(100% - 64px)", overflow: "auto" }}>{filteredOutput}</pre>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
              <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginRight: 2 }}>Helpful?</span>
              {feedbackGiven.current.has(activeMode) ? (
                <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>
                  {feedbackGiven.current.get(activeMode) ? "✓ marked helpful" : "✓ marked unhelpful"}
                </span>
              ) : (
                <>
                  <button className="btn btn-ghost" onClick={() => onFeedback(true)} style={{ padding: "1px 6px", fontSize: 9 }} title="Helpful" aria-label="Mark as helpful">
                    <ThumbsUp size={9} aria-hidden="true" />
                  </button>
                  <button className="btn btn-ghost" onClick={() => onFeedback(false)} style={{ padding: "1px 6px", fontSize: 9 }} title="Not helpful" aria-label="Mark as not helpful">
                    <ThumbsDown size={9} aria-hidden="true" />
                  </button>
                </>
              )}
              <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginLeft: "auto", display: "flex", gap: 8 }}>
                {runDuration !== null && <span title="Run duration" style={{ fontFamily: "var(--font-mono)" }}>{runDuration < 10 ? runDuration.toFixed(1) : Math.round(runDuration)}s</span>}
                {outputTs && <span title="Time since this output was generated">{formatAge(outputTs)}</span>}
                <span>~{Math.round(modeOutput.length / 4).toLocaleString()} tok</span>
              </span>
            </div>
          </>
        ) : (
          <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16, textAlign: "center" }}>
            Click a mode and Run <span style={{ opacity: 0.5 }}>(⌘R)</span>
          </div>
        )}
      </div>
    </div>
  );
}
