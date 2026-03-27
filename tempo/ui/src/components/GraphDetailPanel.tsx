import { useState, useCallback } from "react";
import { X, Play, FileCode, Layers, AlertTriangle, Skull, Zap } from "lucide-react";
import { runTempo } from "./tempo";
import type { GraphData, FileNode, DirNode } from "../hooks/useGraphData";

interface Props {
  nodeId: string;
  data: GraphData;
  viewLevel: "dirs" | "files";
  onClose: () => void;
  repoPath: string;
}

const QUICK_MODES = [
  { mode: "focus", label: "Focus", icon: <Zap size={12} />, needsQuery: true },
  { mode: "blast", label: "Blast Radius", icon: <AlertTriangle size={12} />, needsFile: true },
  { mode: "dead_code", label: "Dead Code", icon: <Skull size={12} /> },
  { mode: "hotspots", label: "Hotspots", icon: <Layers size={12} /> },
];

export function GraphDetailPanel({ nodeId, data, viewLevel, onClose, repoPath }: Props) {
  const [modeOutput, setModeOutput] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [activeMode, setActiveMode] = useState<string | null>(null);

  const file = viewLevel === "files" ? data.files.find(f => f.id === nodeId) : null;
  const dir = viewLevel === "dirs" ? data.directories.find(d => d.id === nodeId) : null;

  const runMode = useCallback(async (mode: string) => {
    setRunning(true);
    setActiveMode(mode);
    setModeOutput(null);

    let args: Record<string, string> = {};
    if (mode === "focus" && file) {
      args = { query: file.id.split("/").pop()?.replace(/\.\w+$/, "") || "" };
    } else if (mode === "blast" && file) {
      args = { file: file.id };
    }

    const result = await runTempo(repoPath, mode, args.query, args.file);
    setModeOutput(result.output);
    setRunning(false);
  }, [repoPath, file]);

  if (!file && !dir) return null;

  return (
    <div className="graph-detail-panel">
      <div className="graph-detail-header">
        <FileCode size={14} />
        <span className="graph-detail-title">
          {file ? file.id.split("/").pop() : dir?.id}
        </span>
        <button className="graph-detail-close" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      <div className="graph-detail-body">
        {file && (
          <>
            <div className="graph-detail-path">{file.id}</div>
            <div className="graph-detail-stats">
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{file.lines.toLocaleString()}</span>
                <span className="graph-detail-stat-label">lines</span>
              </div>
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{file.syms}</span>
                <span className="graph-detail-stat-label">symbols</span>
              </div>
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{file.cx}</span>
                <span className="graph-detail-stat-label">complexity</span>
              </div>
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{file.dead_pct}%</span>
                <span className="graph-detail-stat-label">dead</span>
              </div>
            </div>
            <div className="graph-detail-meta">
              <span className={`graph-health-badge graph-health-${file.health}`}>{file.health}</span>
              <span className="graph-detail-lang">{file.lang}</span>
            </div>

            <div className="graph-detail-section-title">Quick Analysis</div>
            <div className="graph-detail-modes">
              {QUICK_MODES.map(({ mode, label, icon }) => (
                <button
                  key={mode}
                  className={`graph-detail-mode-btn${activeMode === mode ? " active" : ""}`}
                  onClick={() => runMode(mode)}
                  disabled={running}
                >
                  {icon}
                  <span>{label}</span>
                  {running && activeMode === mode && <span className="spin">↻</span>}
                </button>
              ))}
            </div>
          </>
        )}

        {dir && (
          <>
            <div className="graph-detail-stats">
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{dir.files}</span>
                <span className="graph-detail-stat-label">files</span>
              </div>
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{dir.lines.toLocaleString()}</span>
                <span className="graph-detail-stat-label">lines</span>
              </div>
              <div className="graph-detail-stat">
                <span className="graph-detail-stat-value">{dir.syms}</span>
                <span className="graph-detail-stat-label">symbols</span>
              </div>
            </div>
            <div className="graph-detail-meta">
              {dir.langs.map(l => (
                <span key={l} className="graph-detail-lang">{l}</span>
              ))}
            </div>
            <div className="graph-detail-hint">Double-click node to explore files</div>
          </>
        )}

        {modeOutput && (
          <div className="graph-detail-output">
            <div className="graph-detail-section-title">{activeMode} output</div>
            <pre className="graph-detail-pre">{modeOutput}</pre>
          </div>
        )}
      </div>
    </div>
  );
}
