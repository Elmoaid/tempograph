import { useState, useEffect, type RefObject } from "react";
import { Copy, Check, Save, Search, ThumbsUp, ThumbsDown, X, ChevronDown, ChevronRight, WrapText } from "lucide-react";

const FONT_SIZE_MIN = 9;
const FONT_SIZE_MAX = 16;
const FONT_SIZE_DEFAULT = 11;
const FONT_SIZE_KEY = "tempo_output_font_size";
import type { ModeInfo } from "./modes";
import { formatAge } from "./modes";
import { ArgsInput } from "./ArgsInput";

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

interface KitSection {
  mode: string;
  content: string;
}

function parseKitSections(output: string): KitSection[] {
  // Kit output format: "── MODE ──\ncontent\n\n── MODE2 ──\ncontent2"
  const parts = output.split(/^──\s+\w+\s+──$/m);
  const headers = [...output.matchAll(/^──\s+(\w+)\s+──$/mg)].map(m => m[1]);
  return headers.map((mode, i) => ({
    mode,
    content: (parts[i + 1] || "").trim(),
  })).filter(s => s.content.length > 0);
}

function estimateTokens(text: string): string {
  const count = Math.round(text.length / 4);
  return count >= 1000 ? `~${(count / 1000).toFixed(1)}k tokens` : `~${count} tokens`;
}

function HighlightedOutput({ text, query, style }: { text: string; query: string; style: React.CSSProperties }) {
  if (!query.trim()) return <pre className="output" role="region" aria-label="Mode output" aria-live="polite" style={style}>{text}</pre>;
  const lowerQ = query.toLowerCase();
  const lines = text.split("\n");
  return (
    <pre className="output" role="region" aria-label="Mode output" aria-live="polite" style={style}>
      {lines.map((line, i) => {
        const parts: React.ReactNode[] = [];
        let rest = line;
        while (rest) {
          const idx = rest.toLowerCase().indexOf(lowerQ);
          if (idx === -1) { parts.push(rest); break; }
          if (idx > 0) parts.push(rest.slice(0, idx));
          parts.push(
            <mark key={parts.length} style={{ background: "var(--accent-dim, rgba(99,102,241,0.25))", color: "inherit", borderRadius: 2, padding: "0 1px" }}>
              {rest.slice(idx, idx + query.length)}
            </mark>
          );
          rest = rest.slice(idx + query.length);
        }
        return <span key={i}>{parts}{i < lines.length - 1 ? "\n" : ""}</span>;
      })}
    </pre>
  );
}

const EXPANDED_KEY = (activeMode: string) => `tempo-kit-expanded-${activeMode}`;

function loadExpanded(activeMode: string, modes: string[]): Set<string> {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY(activeMode));
    if (raw) return new Set(JSON.parse(raw) as string[]);
  } catch { /* ignore */ }
  return new Set(modes); // default: all expanded
}

function saveExpanded(activeMode: string, expanded: Set<string>) {
  try {
    localStorage.setItem(EXPANDED_KEY(activeMode), JSON.stringify([...expanded]));
  } catch { /* ignore */ }
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

  const isKitMode = activeMode.startsWith("kit:");
  const kitSections = isKitMode && filteredOutput ? parseKitSections(filteredOutput) : [];
  const hasKitSections = isKitMode && kitSections.length > 0;

  const [expandedModes, setExpandedModes] = useState<Set<string>>(new Set());
  const [wrapEnabled, setWrapEnabled] = useState(() => localStorage.getItem("tempo_output_wrap") !== "false");
  const [fontSize, setFontSize] = useState<number>(() => {
    const saved = parseInt(localStorage.getItem(FONT_SIZE_KEY) || "", 10);
    return saved >= FONT_SIZE_MIN && saved <= FONT_SIZE_MAX ? saved : FONT_SIZE_DEFAULT;
  });

  const changeFontSize = (delta: number) => {
    setFontSize(prev => {
      const next = Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, prev + delta));
      localStorage.setItem(FONT_SIZE_KEY, String(next));
      return next;
    });
  };

  // Load expanded state from localStorage when kit mode or sections change
  useEffect(() => {
    if (!hasKitSections) return;
    setExpandedModes(loadExpanded(activeMode, kitSections.map(s => s.mode)));
  }, [activeMode, hasKitSections]);

  const toggleSection = (mode: string) => {
    setExpandedModes(prev => {
      const next = new Set(prev);
      if (next.has(mode)) next.delete(mode);
      else next.add(mode);
      saveExpanded(activeMode, next);
      return next;
    });
  };

  const label = activeModeInfo?.label ?? activeMode;

  return (
    <div className="cell" style={{ flex: 1 }}>
      <div className="cell-head">
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {label}
          {isKitMode && (
            <span style={{
              fontSize: 8, fontWeight: 700, letterSpacing: "0.08em",
              padding: "1px 5px", borderRadius: 3,
              background: "var(--accent-dim, rgba(99,102,241,0.18))",
              color: "var(--accent, #818cf8)",
              textTransform: "uppercase",
            }}>
              KIT
            </span>
          )}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {modeOutput && (
            <>
              <button className="btn btn-ghost" onClick={onFilterToggle} style={{ padding: "2px 6px", fontSize: 10 }} title="Filter output (⌘F)" aria-label="Filter output (⌘F)">
                <Search size={10} aria-hidden="true" />
              </button>
              <button className="btn btn-ghost" onClick={onSave} style={{ padding: "2px 6px", fontSize: 10 }} title="Save to .tempo/" aria-label="Save output to .tempo/">
                <Save size={10} aria-hidden="true" />
              </button>
            </>
          )}
          {modeOutput && (
            <>
              <button
                className="btn btn-ghost"
                onClick={() => changeFontSize(-1)}
                disabled={fontSize <= FONT_SIZE_MIN}
                title={`Decrease font size (${fontSize}px)`}
                aria-label="Decrease output font size"
                style={{ padding: "2px 5px", fontSize: 9, opacity: fontSize <= FONT_SIZE_MIN ? 0.3 : 1, fontFamily: "var(--font-mono)", letterSpacing: "-0.5px" }}
              >
                A-
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => changeFontSize(1)}
                disabled={fontSize >= FONT_SIZE_MAX}
                title={`Increase font size (${fontSize}px)`}
                aria-label="Increase output font size"
                style={{ padding: "2px 5px", fontSize: 9, opacity: fontSize >= FONT_SIZE_MAX ? 0.3 : 1, fontFamily: "var(--font-mono)", letterSpacing: "-0.5px" }}
              >
                A+
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => {
                  const next = !wrapEnabled;
                  setWrapEnabled(next);
                  localStorage.setItem("tempo_output_wrap", String(next));
                }}
                title={wrapEnabled ? "Disable line wrap" : "Enable line wrap"}
                aria-label={wrapEnabled ? "Disable line wrap" : "Enable line wrap"}
                aria-pressed={wrapEnabled}
                style={{ padding: "2px 6px", fontSize: 10, opacity: wrapEnabled ? 1 : 0.45 }}
              >
                <WrapText size={10} aria-hidden="true" />
              </button>
            </>
          )}
          {modeOutput && (
            <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginRight: "0.25rem", alignSelf: "center", fontFamily: "var(--font-mono)" }}>
              {estimateTokens(modeOutput)}
            </span>
          )}
          <button
            className="btn btn-ghost"
            onClick={onCopy}
            disabled={!modeOutput}
            title={!modeOutput ? "Run a mode first" : "Copy output"}
            aria-label={copied ? "Copied" : "Copy output"}
            style={{ padding: "2px 6px", fontSize: 10, opacity: !modeOutput ? 0.35 : 1 }}
          >
            {copied
              ? <><Check size={10} aria-hidden="true" /><span style={{ marginLeft: 3 }}>Copied!</span></>
              : <Copy size={10} aria-hidden="true" />}
          </button>
        </div>
      </div>
      <div className="cell-body">
        {activeModeInfo?.desc && (
          <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 8, lineHeight: 1.5 }}>
            {activeModeInfo.desc}
          </div>
        )}
        <ArgsInput
          value={modeArgs}
          history={history}
          historyOpen={historyOpen}
          placeholder={activeModeInfo?.hint || "arguments (optional)"}
          argsInputRef={argsInputRef}
          modeRunning={modeRunning}
          onChange={onArgsChange}
          onRun={onRun}
          onHistoryOpen={onHistoryOpen}
          onHistorySelect={onHistorySelect}
        />
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
            {hasKitSections ? (
              <div
                role="region"
                aria-label="Kit mode output"
                style={{ overflow: "auto", maxHeight: "calc(100% - 64px)", display: "flex", flexDirection: "column", gap: 4 }}
              >
                {kitSections.map(({ mode, content }) => {
                  const expanded = expandedModes.has(mode);
                  return (
                    <div key={mode} style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "hidden" }}>
                      <button
                        onClick={() => toggleSection(mode)}
                        aria-expanded={expanded}
                        style={{
                          width: "100%", display: "flex", alignItems: "center", gap: 6,
                          padding: "4px 8px", background: "var(--bg-secondary)",
                          border: "none", cursor: "pointer", textAlign: "left",
                          color: "var(--text-secondary)", fontSize: 10, fontWeight: 600,
                          letterSpacing: "0.06em", textTransform: "uppercase",
                          fontFamily: "var(--font-mono)",
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-secondary)")}
                      >
                        {expanded
                          ? <ChevronDown size={10} aria-hidden="true" />
                          : <ChevronRight size={10} aria-hidden="true" />
                        }
                        {mode}
                        <span style={{ marginLeft: "auto", fontSize: 8, opacity: 0.5, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
                          ~{Math.round(content.length / 4).toLocaleString()} tok
                        </span>
                      </button>
                      {expanded && (
                        <pre className="output" style={{ margin: 0, borderRadius: 0, maxHeight: 300, overflow: "auto", whiteSpace: wrapEnabled ? "pre-wrap" : "pre", fontSize }}>
                          {content}
                        </pre>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <HighlightedOutput
                text={filteredOutput}
                query={filterVisible ? outputFilter : ""}
                style={{ maxHeight: activeMode === "prepare" ? "calc(100% - 96px)" : "calc(100% - 64px)", overflow: "auto", whiteSpace: wrapEnabled ? "pre-wrap" : "pre", fontSize, margin: 0 }}
              />
            )}
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
