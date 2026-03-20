import { RefreshCw, PanelRightClose, PanelRight, Shield, Package } from "lucide-react";

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

export function TopBar({ stats, showClaude, onToggleClaude, showSnapshots, onToggleSnapshots, rightHidden, onToggleRight, loading, onRefresh }: TopBarProps) {
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
