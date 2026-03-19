import { MODES } from "./modes";
import { KitCard } from "./KitCard";
import type { KitInfo } from "./kits";

interface Props {
  sidebarTab: "kits" | "modes";
  onTabChange: (tab: "kits" | "modes") => void;
  allKits: KitInfo[];
  activeKit: string | null;
  activeMode: string;
  cachedModes: Set<string>;
  onKitSelect: (kitId: string) => void;
  onModeSelect: (mode: string) => void;
  onCreateKit?: () => void;
}

export function SidebarTabs({
  sidebarTab,
  onTabChange,
  allKits,
  activeKit,
  activeMode,
  cachedModes,
  onKitSelect,
  onModeSelect,
  onCreateKit,
}: Props) {
  return (
    <div className="cell" style={{ flex: "0 0 auto", maxHeight: "45%" }}>
      {/* Tab header */}
      <div className="cell-head" style={{ padding: 0 }}>
        <button
          onClick={() => onTabChange("kits")}
          style={{
            flex: 1,
            padding: "8px 0",
            border: "none",
            background: sidebarTab === "kits" ? "var(--bg-secondary)" : "var(--bg-tertiary)",
            color: sidebarTab === "kits" ? "var(--accent-hover)" : "var(--text-tertiary)",
            fontWeight: 600,
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.4px",
            cursor: "pointer",
            borderBottom: sidebarTab === "kits" ? "1px solid var(--accent)" : "1px solid var(--border-subtle)",
            transition: "all 0.1s",
          }}
        >
          Kits
        </button>
        {sidebarTab === "kits" && onCreateKit && (
          <button
            onClick={onCreateKit}
            title="Create Kit (⌘N)"
            aria-label="Create new kit"
            style={{
              padding: "8px 10px",
              border: "none",
              background: "var(--bg-secondary)",
              color: "var(--text-tertiary)",
              fontSize: 16,
              lineHeight: 1,
              cursor: "pointer",
              borderBottom: "1px solid var(--accent)",
              transition: "color 0.1s",
            }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--accent-hover)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
          >+</button>
        )}
        <button
          onClick={() => onTabChange("modes")}
          style={{
            flex: 1,
            padding: "8px 0",
            border: "none",
            background: sidebarTab === "modes" ? "var(--bg-secondary)" : "var(--bg-tertiary)",
            color: sidebarTab === "modes" ? "var(--text-secondary)" : "var(--text-tertiary)",
            fontWeight: 600,
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.4px",
            cursor: "pointer",
            borderBottom: sidebarTab === "modes" ? "1px solid var(--border)" : "1px solid var(--border-subtle)",
            transition: "all 0.1s",
          }}
        >
          All Modes
        </button>
      </div>

      {/* Tab content */}
      <div className="cell-body" role="listbox" aria-label={sidebarTab === "kits" ? "Kits" : "Modes"}>
        {sidebarTab === "kits" ? (
          allKits.map(kit => (
            <KitCard
              key={kit.id}
              kit={kit}
              active={activeKit === kit.id}
              cached={cachedModes.has(`kit:${kit.id}`)}
              onClick={onKitSelect}
            />
          ))
        ) : (
          MODES.map(m => (
            <button
              key={m.mode}
              role="option"
              aria-selected={!activeKit && activeMode === m.mode}
              aria-label={`${m.label} (${m.tag})${cachedModes.has(m.mode) ? " — cached" : ""}`}
              className={`mode-row ${!activeKit && activeMode === m.mode ? "active" : ""}`}
              onClick={() => onModeSelect(m.mode)}
            >
              <span className="mode-row-icon" aria-hidden="true"><m.icon size={13} /></span>
              <span className="mode-row-name">{m.label}</span>
              {cachedModes.has(m.mode) && (
                <span title="Has cached output" aria-hidden="true" style={{
                  width: 5, height: 5, borderRadius: "50%",
                  background: !activeKit && activeMode === m.mode ? "var(--accent-hover)" : "var(--success)",
                  flexShrink: 0, marginLeft: "auto", marginRight: 4,
                  opacity: !activeKit && activeMode === m.mode ? 0.7 : 1,
                }} />
              )}
              <span className="mode-row-tag" aria-hidden="true">{m.tag}</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
