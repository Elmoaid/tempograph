import { useState, useEffect, useCallback, type RefObject } from "react";
import { Copy, Check } from "lucide-react";
import { MODES, type ModeInfo } from "./modes";
import { ArgsInput } from "./ArgsInput";
import { OutputPanelHeader } from "./OutputPanelHeader";
import { KitSectionAccordion } from "./KitSectionAccordion";
import { OutputSearchBar } from "./OutputSearchBar";
import { OutputFooter } from "./OutputFooter";
import { HighlightedOutput } from "./HighlightedOutput";
import { DiffOutput } from "./DiffOutput";
import { OutputFilterBar } from "./OutputFilterBar";

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
  onCancel: () => void;
  onCopy: () => void;
  onSave: () => void;
  onFilterToggle: () => void;
  onFilterChange: (v: string) => void;
  onFilterClose: () => void;
  onSearchChange: (text: string) => void;
  onSearchClose: () => void;
  onSearchNavigate: (dir: "next" | "prev") => void;
  onFeedback: (helpful: boolean) => void;
  runHistory?: { mode: string; args: string }[];
  onRunHistoryEntry?: (entry: { mode: string; args: string }) => void;
  suggestions: string[];
  onSuggestionClick: (mode: string) => void;
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

export function OutputPanel(props: OutputPanelProps) {
  const {
    activeModeInfo, activeMode, modeArgs, modeRunning, modeOutput, prevOutput,
    elapsed, outputTs, runDuration, copied, filterVisible, outputFilter,
    filteredOutput, filterMatchCount, history, historyOpen, feedbackGiven,
    argsInputRef, filterInputRef,
    searchInputRef, searchText, searchActive, searchMatchCount, searchCurrentMatch,
    onArgsChange, onHistoryOpen, onHistorySelect, onRun, onCancel, onCopy, onSave,
    onFilterToggle, onFilterChange, onFilterClose,
    onSearchChange, onSearchClose, onSearchNavigate,
    onFeedback, saved, runHistory, onRunHistoryEntry, suggestions, onSuggestionClick,
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

  const changeFontSize = useCallback((delta: number) => {
    setFontSize(prev => {
      const next = Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, prev + delta));
      localStorage.setItem(FONT_SIZE_KEY, String(next));
      return next;
    });
  }, []);

  const resetFontSize = useCallback(() => {
    setFontSize(FONT_SIZE_DEFAULT);
    localStorage.setItem(FONT_SIZE_KEY, String(FONT_SIZE_DEFAULT));
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.metaKey && !e.ctrlKey) return;
      if (e.key === "=" || e.key === "+") { e.preventDefault(); changeFontSize(1); }
      else if (e.key === "-") { e.preventDefault(); changeFontSize(-1); }
      else if (e.key === "0") { e.preventDefault(); resetFontSize(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [changeFontSize, resetFontSize]);

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
        {runHistory && runHistory.length > 0 && onRunHistoryEntry && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
            {runHistory.map((entry, i) => {
              const truncArgs = entry.args.length > 20 ? entry.args.slice(0, 20) + "…" : entry.args;
              const label = truncArgs ? `${entry.mode} ${truncArgs}` : entry.mode;
              return (
                <button
                  key={i}
                  className="btn btn-ghost"
                  onClick={() => onRunHistoryEntry(entry)}
                  title={entry.args ? `${entry.mode} ${entry.args}` : entry.mode}
                  style={{ fontSize: 10, padding: "2px 7px", borderRadius: 10, opacity: 0.75 }}
                >
                  ⟳ {label}
                </button>
              );
            })}
          </div>
        )}
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
            <button
              className="btn btn-ghost"
              onClick={onCancel}
              style={{ marginLeft: 10, fontSize: 10, padding: "2px 8px", verticalAlign: "middle" }}
              title="Stop waiting for result"
            >
              Cancel
            </button>
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
              <OutputFilterBar
                filterInputRef={filterInputRef}
                value={outputFilter}
                matchCount={filterMatchCount}
                onChange={onFilterChange}
                onClose={onFilterClose}
              />
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
              outputLines={modeOutput.split("\n").length}
              onFeedback={onFeedback}
            />
            {suggestions.length > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
                <span style={{ fontSize: 9, color: "var(--text-tertiary)", opacity: 0.7, marginRight: 2 }}>↳ try:</span>
                {suggestions.map(mode => {
                  const modeInfo = MODES.find(m => m.mode === mode);
                  return (
                    <button
                      key={mode}
                      className="btn btn-ghost"
                      onClick={() => onSuggestionClick(mode)}
                      style={{ fontSize: 9, padding: "1px 7px", borderRadius: 10, opacity: 0.8 }}
                      title={modeInfo?.desc ?? `Switch to ${mode} mode`}
                    >
                      {modeInfo?.label ?? mode}
                    </button>
                  );
                })}
              </div>
            )}
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
