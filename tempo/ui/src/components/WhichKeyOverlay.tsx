import type { CSSProperties } from "react";

const CMD_SHORTCUTS = [
  { key: "⌘Enter", desc: "Run" },
  { key: "⌘R", desc: "Re-run" },
  { key: "⌘K", desc: "Palette" },
  { key: "⌘L", desc: "Focus args" },
  { key: "⌘F", desc: "Find" },
  { key: "⌘S", desc: "Save" },
  { key: "⌘N", desc: "New kit" },
  { key: "⌘1–9", desc: "Switch mode" },
  { key: "⌘+/−", desc: "Font size" },
  { key: "⌘0", desc: "Reset font" },
  { key: "⌘M", desc: "Modes view" },
  { key: "⌘G", desc: "Graph view" },
  { key: "⌘D", desc: "Dashboard" },
];

const overlayStyle: CSSProperties = {
  position: "fixed",
  bottom: 24,
  left: "50%",
  transform: "translateX(-50%)",
  background: "var(--bg-elevated)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: "10px 16px",
  pointerEvents: "none",
  zIndex: 150,
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: "6px 24px",
  boxShadow: "0 4px 16px rgba(0,0,0,0.3)",
};

const kbdStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  padding: "1px 5px",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 3,
  color: "var(--text-secondary)",
  minWidth: 52,
  textAlign: "center",
  flexShrink: 0,
};

export function WhichKeyOverlay() {
  return (
    <div style={overlayStyle}>
      {CMD_SHORTCUTS.map(({ key, desc }) => (
        <div key={key} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <kbd style={kbdStyle}>{key}</kbd>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{desc}</span>
        </div>
      ))}
    </div>
  );
}
