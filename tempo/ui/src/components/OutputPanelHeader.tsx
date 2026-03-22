import { Copy, Check, Save, Search, WrapText, FolderCheck, GitCompare } from "lucide-react";

interface OutputPanelHeaderProps {
  label: string;
  isKitMode: boolean;
  modeOutput: string;
  copied: boolean;
  wrapEnabled: boolean;
  fontSize: number;
  fontSizeMin: number;
  fontSizeMax: number;
  saved: boolean;
  hasPrevOutput: boolean;
  diffMode: boolean;
  onFilterToggle: () => void;
  onSave: () => void;
  onFontDecrease: () => void;
  onFontIncrease: () => void;
  onWrapToggle: () => void;
  onDiffToggle: () => void;
  onCopy: () => void;
}

function estimateTokens(text: string): string {
  const count = Math.round(text.length / 4);
  return count >= 1000 ? `~${(count / 1000).toFixed(1)}k tokens` : `~${count} tokens`;
}

export function OutputPanelHeader({
  label,
  isKitMode,
  modeOutput,
  copied,
  saved,
  wrapEnabled,
  fontSize,
  fontSizeMin,
  fontSizeMax,
  hasPrevOutput,
  diffMode,
  onFilterToggle,
  onSave,
  onFontDecrease,
  onFontIncrease,
  onWrapToggle,
  onDiffToggle,
  onCopy,
}: OutputPanelHeaderProps) {
  return (
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
            <button
              className="btn btn-ghost"
              onClick={onFilterToggle}
              style={{ padding: "2px 6px", fontSize: 10 }}
              title="Filter output (⌘F)"
              aria-label="Filter output (⌘F)"
            >
              <Search size={10} aria-hidden="true" />
            </button>
            {hasPrevOutput && (
              <button
                className="btn btn-ghost"
                onClick={onDiffToggle}
                style={{ padding: "2px 6px", fontSize: 10, opacity: diffMode ? 1 : 0.45 }}
                title={diffMode ? "Diff mode: ON — showing changes from last run" : "Diff mode: OFF — click to compare with last run"}
                aria-label={diffMode ? "Disable diff mode" : "Enable diff mode"}
                aria-pressed={diffMode}
              >
                <GitCompare size={10} aria-hidden="true" />
              </button>
            )}
            <button
              className="btn btn-ghost"
              onClick={onSave}
              style={{ padding: "2px 6px", fontSize: 10 }}
              title={saved ? "Saved to .tempo/" : "Save to .tempo/"}
              aria-label={saved ? "Saved to .tempo/" : "Save output to .tempo/"}
            >
              {saved
                ? <><FolderCheck size={10} aria-hidden="true" /><span style={{ marginLeft: 3 }}>Saved!</span></>
                : <Save size={10} aria-hidden="true" />}
            </button>
            <button
              className="btn btn-ghost"
              onClick={onFontDecrease}
              disabled={fontSize <= fontSizeMin}
              title={`Decrease font size (${fontSize}px)`}
              aria-label="Decrease output font size"
              style={{
                padding: "2px 5px", fontSize: 9,
                opacity: fontSize <= fontSizeMin ? 0.3 : 1,
                fontFamily: "var(--font-mono)", letterSpacing: "-0.5px",
              }}
            >
              A-
            </button>
            <button
              className="btn btn-ghost"
              onClick={onFontIncrease}
              disabled={fontSize >= fontSizeMax}
              title={`Increase font size (${fontSize}px)`}
              aria-label="Increase output font size"
              style={{
                padding: "2px 5px", fontSize: 9,
                opacity: fontSize >= fontSizeMax ? 0.3 : 1,
                fontFamily: "var(--font-mono)", letterSpacing: "-0.5px",
              }}
            >
              A+
            </button>
            <button
              className="btn btn-ghost"
              onClick={onWrapToggle}
              title={wrapEnabled ? "Disable line wrap" : "Enable line wrap"}
              aria-label={wrapEnabled ? "Disable line wrap" : "Enable line wrap"}
              aria-pressed={wrapEnabled}
              style={{ padding: "2px 6px", fontSize: 10, opacity: wrapEnabled ? 1 : 0.45 }}
            >
              <WrapText size={10} aria-hidden="true" />
            </button>
            <span style={{
              fontSize: "0.75rem", color: "var(--text-tertiary)",
              marginRight: "0.25rem", alignSelf: "center",
              fontFamily: "var(--font-mono)",
            }}>
              {estimateTokens(modeOutput)}
            </span>
          </>
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
  );
}
