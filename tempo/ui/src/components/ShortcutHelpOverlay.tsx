import type { CSSProperties } from "react";

const SHORTCUTS = [
  { key: "⌘K", desc: "Toggle command palette" },
  { key: "⌘1–⌘9", desc: "Switch to mode N" },
  { key: "⌘Enter", desc: "Run current mode" },
  { key: "⌘R", desc: "Re-run mode" },
  { key: "⌘L", desc: "Focus args input" },
  { key: "⌘F", desc: "Find in output" },
  { key: "⌘S", desc: "Save output to file" },
  { key: "⌘N", desc: "New kit" },
  { key: "⌘+/⌘−", desc: "Output font size up / down" },
  { key: "⌘0", desc: "Reset output font size" },
  { key: "Escape", desc: "Cancel run / close palette / clear output" },
  { key: "?", desc: "Toggle this overlay" },
];

const kbdStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  padding: "2px 6px",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  color: "var(--text-secondary)",
  minWidth: 56,
  textAlign: "center",
  flexShrink: 0,
};

const kbdInlineStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  padding: "1px 4px",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 3,
};

export function ShortcutHelpOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: "20px 24px",
          minWidth: 320,
          maxWidth: 420,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 14 }}>
          Keyboard Shortcuts
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {SHORTCUTS.map(({ key, desc }) => (
            <div key={key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <kbd style={kbdStyle}>{key}</kbd>
              <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{desc}</span>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 16, fontSize: 11, color: "var(--text-tertiary)", opacity: 0.6, textAlign: "center" }}>
          Press <kbd style={kbdInlineStyle}>?</kbd> or <kbd style={kbdInlineStyle}>Esc</kbd> to close
        </div>
      </div>
    </div>
  );
}
