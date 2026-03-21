import { useState } from "react";
import { RefreshCw, PanelRightClose, PanelRight, Shield, Package, HelpCircle } from "lucide-react";

interface Stats {
  files: string;
  symbols: string;
  lines: string;
}

interface TopBarProps {
  stats: Stats | null;
  showClaude: boolean;
  onToggleClaude: () => void;
  showSnapshots: boolean;
  onToggleSnapshots: () => void;
  rightHidden: boolean;
  onToggleRight: () => void;
  loading: boolean;
  onRefresh: () => void;
}

const SHORTCUTS = [
  { keys: ["⌘↵", "⌘R"], desc: "Run mode" },
  { keys: ["Esc"], desc: "Clear output" },
  { keys: ["⌘L"], desc: "Focus arguments" },
  { keys: ["⌘K"], desc: "Command palette" },
  { keys: ["⌘N"], desc: "New kit" },
  { keys: ["⌘F"], desc: "Filter output" },
  { keys: ["⌘1–9"], desc: "Switch mode" },
];

export function TopBar({ stats, showClaude, onToggleClaude, showSnapshots, onToggleSnapshots, rightHidden, onToggleRight, loading, onRefresh }: TopBarProps) {
  const [showShortcuts, setShowShortcuts] = useState(false);

  return (
    <div className="topbar">
      <span className="topbar-brand">Tempo</span>
      {stats && (
        <div style={{ display: "flex", gap: 16, marginLeft: 12, fontSize: 12, color: "var(--text-secondary)" }}>
          <span><strong style={{ color: "var(--text-primary)" }}>{stats.files}</strong> files</span>
          <span><strong style={{ color: "var(--text-primary)" }}>{stats.symbols}</strong> symbols</span>
          <span><strong style={{ color: "var(--text-primary)" }}>{stats.lines}</strong> lines</span>
        </div>
      )}
      <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
        <button
          className={`btn ${showSnapshots ? "" : "btn-ghost"}`}
          onClick={onToggleSnapshots}
          style={{ padding: "4px 8px", fontSize: 11 }}
          title="Pre-indexed snapshots"
        >
          <Package size={12} /> Snapshots
        </button>
        <button
          className={`btn ${showClaude ? "" : "btn-ghost"}`}
          onClick={onToggleClaude}
          style={{ padding: "4px 8px", fontSize: 11 }}
          title="Claude Code Config"
        >
          <Shield size={12} /> Claude
        </button>
        <div style={{ position: "relative" }}>
          <button
            className={`btn ${showShortcuts ? "" : "btn-ghost"}`}
            onClick={() => setShowShortcuts(v => !v)}
            style={{ padding: "4px 8px" }}
            title="Keyboard shortcuts"
            aria-label="Keyboard shortcuts"
            aria-expanded={showShortcuts}
          >
            <HelpCircle size={12} />
          </button>
          {showShortcuts && (
            <>
              <div
                style={{ position: "fixed", inset: 0, zIndex: 49 }}
                onClick={() => setShowShortcuts(false)}
                aria-hidden="true"
              />
              <div
                role="dialog"
                aria-label="Keyboard shortcuts"
                style={{
                  position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 50,
                  background: "var(--bg-secondary, #1e2433)",
                  border: "1px solid var(--border, #2d3748)",
                  borderRadius: 8, padding: "10px 14px", minWidth: 220,
                  boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
                }}
              >
                <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-tertiary)", marginBottom: 8 }}>
                  Shortcuts
                </div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                  <tbody>
                    {SHORTCUTS.map(({ keys, desc }) => (
                      <tr key={desc}>
                        <td style={{ paddingBottom: 5, paddingRight: 12, whiteSpace: "nowrap" }}>
                          {keys.map((k, i) => (
                            <span key={k}>
                              <kbd style={{
                                fontFamily: "var(--font-mono)", fontSize: 10,
                                background: "var(--bg, #0f1117)", border: "1px solid var(--border)",
                                borderRadius: 3, padding: "1px 4px",
                                color: "var(--text-secondary)",
                              }}>{k}</kbd>
                              {i < keys.length - 1 && <span style={{ color: "var(--text-tertiary)", margin: "0 3px" }}>/</span>}
                            </span>
                          ))}
                        </td>
                        <td style={{ paddingBottom: 5, color: "var(--text-secondary)" }}>{desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
        <button
          className="btn btn-ghost"
          onClick={onToggleRight}
          style={{ padding: "4px 8px" }}
          title={rightHidden ? "Show info panel" : "Hide info panel"}
        >
          {rightHidden ? <PanelRight size={12} /> : <PanelRightClose size={12} />}
        </button>
        <button className="btn btn-ghost" onClick={onRefresh} disabled={loading} style={{ padding: "4px 8px" }}>
          <RefreshCw size={12} className={loading ? "spin" : ""} />
        </button>
      </div>
    </div>
  );
}
