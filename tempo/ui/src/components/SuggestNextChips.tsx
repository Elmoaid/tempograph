import { MODES } from "./modes";

interface SuggestNextChipsProps {
  suggestions: string[];
  onSuggestionClick: (mode: string) => void;
}

export function SuggestNextChips({ suggestions, onSuggestionClick }: SuggestNextChipsProps) {
  if (suggestions.length === 0) return null;
  return (
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
  );
}
