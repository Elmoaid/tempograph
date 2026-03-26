import { useState } from "react";
import { MODES } from "./modes";
import { KitCard } from "./KitCard";
import type { KitInfo } from "./kits";

type ModeGroup = "all" | "analyze" | "navigate" | "ai";
const MODE_GROUPS: ModeGroup[] = ["all", "analyze", "navigate", "ai"];

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
  onTogglePalette?: () => void;
  onToggleHelp?: () => void;
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
  onTogglePalette,
  onToggleHelp,
}: Props) {
  const [modeGroup, setModeGroup] = useState<ModeGroup>("all");
  const filteredModes = modeGroup === "all" ? MODES : MODES.filter(m => m.group === modeGroup);

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
        {onTogglePalette && (
          <button
            onClick={onTogglePalette}
            title="Command palette (⌘K)"
            aria-label="Open command palette"
            style={{
              padding: "8px 10px",
              border: "none",
              background: "var(--bg-tertiary)",
              color: "var(--text-tertiary)",
              fontSize: 10,
              fontFamily: "var(--font-mono)",
              lineHeight: 1,
              cursor: "pointer",
              borderBottom: "1px solid var(--border-subtle)",
              transition: "color 0.1s",
            }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--accent-hover)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
          >⌘K</button>
        )}
      </div>

      {/* Group filter strip (modes tab only) */}
      {sidebarTab === "modes" && (
        <div style={{ display: "flex", gap: 2, padding: "3px 6px", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>
          {MODE_GROUPS.map(g => (
            <button
              key={g}
              onClick={() => setModeGroup(g)}
              style={{
                flex: g === "all" ? "0 0 auto" : 1,
                padding: "2px 5px",
                fontSize: 9,
                fontWeight: 600,
                letterSpacing: "0.3px",
                textTransform: "uppercase",
                border: "none",
                borderRadius: 3,
                cursor: "pointer",
                background: modeGroup === g ? "var(--accent)" : "transparent",
                color: modeGroup === g ? "var(--bg-primary)" : "var(--text-tertiary)",
                transition: "background 0.1s, color 0.1s",
              }}
              title={g === "all" ? "Show all modes" : `Filter: ${g} modes`}
            >
              {g === "all" ? "All" : g.charAt(0).toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
      )}

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
          filteredModes.map(m => {
            const absIdx = MODES.indexOf(m);
            return (
              <button
                key={m.mode}
                role="option"
                aria-selected={!activeKit && activeMode === m.mode}
                aria-label={`${m.label} (${m.tag})${absIdx < 9 ? ` — ⌘${absIdx + 1}` : ""}${cachedModes.has(m.mode) ? " — cached" : ""}`}
                title={m.desc}
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
                {absIdx < 9 && (
                  <span
                    title={`Keyboard shortcut: ⌘${absIdx + 1}`}
                    aria-hidden="true"
                    style={{
                      fontSize: 9,
                      color: "var(--text-tertiary)",
                      fontFamily: "var(--font-mono)",
                      opacity: 0.5,
                      flexShrink: 0,
                      marginLeft: cachedModes.has(m.mode) ? 2 : "auto",
                      marginRight: 2,
                    }}
                  >
                    ⌘{absIdx + 1}
                  </span>
                )}
                <span className="mode-row-tag" aria-hidden="true">{m.tag}</span>
              </button>
            );
          })
        )}
      </div>
      {onToggleHelp && (
        <div style={{ borderTop: "1px solid var(--border-subtle)", display: "flex", justifyContent: "center", padding: "3px 0" }}>
          <button
            onClick={onToggleHelp}
            title="Keyboard shortcuts (?)"
            aria-label="Show keyboard shortcuts"
            style={{
              background: "none",
              border: "none",
              color: "var(--text-tertiary)",
              fontSize: 12,
              cursor: "pointer",
              opacity: 0.5,
              padding: "2px 8px",
              fontFamily: "var(--font-mono)",
            }}
            onMouseEnter={e => (e.currentTarget.style.opacity = "0.9")}
            onMouseLeave={e => (e.currentTarget.style.opacity = "0.5")}
          >?</button>
        </div>
      )}
    </div>
  );
}
