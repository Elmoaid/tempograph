import type { ModeInfo } from "./modes";

interface ModeListProps {
  modes: ModeInfo[];
  activeMode: string;
  cachedModes: Set<string>;
  onSelect: (mode: string) => void;
}

export function ModeList({ modes, activeMode, cachedModes, onSelect }: ModeListProps) {
  return (
    <div className="cell" style={{ flex: "0 0 auto", maxHeight: "45%" }}>
      <div className="cell-head">
        Modes ({modes.length})
        <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-tertiary)", fontWeight: 400 }}>⌘K · ⌘F filter</span>
      </div>
      <div className="cell-body" role="listbox" aria-label="Modes">
        {modes.map((m) => (
          <button
            key={m.mode}
            role="option"
            aria-selected={activeMode === m.mode}
            aria-label={`${m.label} (${m.tag})${cachedModes.has(m.mode) ? " — cached" : ""}`}
            className={`mode-row ${activeMode === m.mode ? "active" : ""}`}
            onClick={() => onSelect(m.mode)}
          >
            <span className="mode-row-icon" aria-hidden="true"><m.icon size={13} /></span>
            <span className="mode-row-name">{m.label}</span>
            {cachedModes.has(m.mode) && (
              <span title="Has cached output" aria-hidden="true" style={{
                width: 5, height: 5, borderRadius: "50%",
                background: activeMode === m.mode ? "var(--accent-hover)" : "var(--success)",
                flexShrink: 0, marginLeft: "auto", marginRight: 4,
                opacity: activeMode === m.mode ? 0.7 : 1,
              }} />
            )}
            <span className="mode-row-tag" aria-hidden="true">{m.tag}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
