import { useState, useEffect, type RefObject } from "react";
import { Copy, Check, X } from "lucide-react";
import type { ModeInfo } from "./modes";
import { ArgsInput } from "./ArgsInput";
import { OutputPanelHeader } from "./OutputPanelHeader";
import { KitSectionAccordion } from "./KitSectionAccordion";
import { OutputSearchBar } from "./OutputSearchBar";
import { OutputFooter } from "./OutputFooter";

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
  searchInputRef: RefObject<HTMLInputElement>;
  searchText: string;
  searchActive: boolean;
  searchMatchCount: number;
  searchCurrentMatch: number;
  onArgsChange: (v: string) => void;
  onHistoryOpen: (v: boolean) => void;
  onHistorySelect: (q: string) => void;
  onRun: () => void;
  onCopy: () => void;
  onSave: () => void;
  onFilterToggle: () => void;
  onFilterChange: (v: string) => void;
  onFilterClose: () => void;
  onSearchChange: (text: string) => void;
  onSearchClose: () => void;
  onSearchNavigate: (dir: "next" | "prev") => void;
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

function HighlightedOutput({
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
    searchInputRef, searchText, searchActive, searchMatchCount, searchCurrentMatch,
    onArgsChange, onHistoryOpen, onHistorySelect, onRun, onCopy, onSave,
    onFilterToggle, onFilterChange, onFilterClose,
    onSearchChange, onSearchClose, onSearchNavigate,
    onFeedback, saved,
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
      {searchActive && (
        <OutputSearchBar
          searchText={searchText}
          matchCount={searchMatchCount}
          currentMatch={searchCurrentMatch}
          searchInputRef={searchInputRef}
          onChange={onSearchChange}
          onNavigate={onSearchNavigate}
          onClose={onSearchClose}
        />
      )}
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
          activeMode={activeMode}
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
                searchText={searchActive ? searchText : ""}
                currentSearchMatch={searchActive ? searchCurrentMatch : 0}
                style={{ maxHeight: activeMode === "prepare" ? "calc(100% - 96px)" : "calc(100% - 64px)", overflow: "auto", whiteSpace: wrapEnabled ? "pre-wrap" : "pre", fontSize, margin: 0 }}
              />
            )}
            <OutputFooter
              feedbackGiven={feedbackGiven}
              activeMode={activeMode}
              runDuration={runDuration}
              outputTs={outputTs}
              outputLength={modeOutput.length}
              onFeedback={onFeedback}
            />
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
