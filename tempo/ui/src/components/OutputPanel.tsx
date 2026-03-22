import { useState, type RefObject } from "react";
import { Copy, Check, ThumbsUp, ThumbsDown, X } from "lucide-react";
import type { ModeInfo } from "./modes";
import { formatAge } from "./modes";
import { ArgsInput } from "./ArgsInput";
import { OutputPanelHeader } from "./OutputPanelHeader";
import { KitSectionAccordion } from "./KitSectionAccordion";

const FONT_SIZE_MIN = 9;
const FONT_SIZE_MAX = 16;
const FONT_SIZE_DEFAULT = 11;
const FONT_SIZE_KEY = "tempo_output_font_size";

interface OutputPanelProps {
  activeModeInfo: ModeInfo | undefined;
  activeMode: string;
  modeArgs: string;
  modeRunning: boolean;
  modeOutput: string;
  prevOutput: string | null;
  elapsed: number;
  outputTs: number | null;
  runDuration: number | null;
  copied: boolean;
  saved: boolean;
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

function parseKitSections(output: string): Array<{ mode: string; content: string }> {
  // Kit output format: "── MODE ──\ncontent\n\n── MODE2 ──\ncontent2"
  const parts = output.split(/^──\s+\w+\s+──$/m);
  const headers = [...output.matchAll(/^──\s+(\w+)\s+──$/mg)].map(m => m[1]);
  return headers.map((mode, i) => ({
    mode,
    content: (parts[i + 1] || "").trim(),
  })).filter(s => s.content.length > 0);
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

type DiffLine = { type: "add" | "remove" | "same"; line: string };

function computeLineDiff(prev: string, curr: string): DiffLine[] {
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

function DiffOutput({ prev, curr, style }: { prev: string; curr: string; style: React.CSSProperties }) {
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

export function OutputPanel(props: OutputPanelProps) {
  const {
    activeModeInfo, activeMode, modeArgs, modeRunning, modeOutput, prevOutput,
    elapsed, outputTs, runDuration, copied, filterVisible, outputFilter,
    filteredOutput, filterMatchCount, history, historyOpen, feedbackGiven,
    argsInputRef, filterInputRef,
    onArgsChange, onHistoryOpen, onHistorySelect, onRun, onCopy, onSave,
    onFilterToggle, onFilterChange, onFilterClose, onFeedback, saved,
  } = props;

  const isKitMode = activeMode.startsWith("kit:");
  const kitSections = isKitMode && filteredOutput ? parseKitSections(filteredOutput) : [];
  const hasKitSections = isKitMode && kitSections.length > 0;

  const [wrapEnabled, setWrapEnabled] = useState(() => localStorage.getItem("tempo_output_wrap") !== "false");
  const [diffMode, setDiffMode] = useState(() => localStorage.getItem("tempo_diff_mode") === "true");
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

  const label = activeModeInfo?.label ?? activeMode;

  return (
    <div className="cell" style={{ flex: 1 }}>
      <OutputPanelHeader
        label={label}
        isKitMode={isKitMode}
        modeOutput={modeOutput}
        copied={copied}
        wrapEnabled={wrapEnabled}
        fontSize={fontSize}
        fontSizeMin={FONT_SIZE_MIN}
        fontSizeMax={FONT_SIZE_MAX}
        hasPrevOutput={prevOutput !== null}
        diffMode={diffMode}
        onFilterToggle={onFilterToggle}
        onSave={onSave}
        saved={saved}
        onFontDecrease={() => changeFontSize(-1)}
        onFontIncrease={() => changeFontSize(1)}
        onWrapToggle={() => {
          const next = !wrapEnabled;
          setWrapEnabled(next);
          localStorage.setItem("tempo_output_wrap", String(next));
        }}
        onDiffToggle={() => {
          const next = !diffMode;
          setDiffMode(next);
          localStorage.setItem("tempo_diff_mode", String(next));
        }}
        onCopy={onCopy}
      />
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
              <KitSectionAccordion
                kitSections={kitSections}
                activeMode={activeMode}
                wrapEnabled={wrapEnabled}
                fontSize={fontSize}
              />
            ) : diffMode && prevOutput ? (
              <DiffOutput
                prev={prevOutput}
                curr={filteredOutput}
                style={{ maxHeight: activeMode === "prepare" ? "calc(100% - 96px)" : "calc(100% - 64px)", overflow: "auto", whiteSpace: wrapEnabled ? "pre-wrap" : "pre", fontSize, margin: 0 }}
              />
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
