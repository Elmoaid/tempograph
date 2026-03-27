import { useEffect, useRef, useState, useCallback } from "react";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import type { GraphData, FileNode, DirNode } from "../hooks/useGraphData";

cytoscape.use(fcose);

const HEALTH_COLORS: Record<string, string> = {
  healthy: "#4ade80",
  hotspot: "#fbbf24",
  dead: "#f87171",
  stable: "#6b7280",
  modified: "#60a5fa",
};

interface GraphCanvasProps {
  data: GraphData;
  onSelectFile?: (fileId: string) => void;
  onSelectDir?: (dirId: string) => void;
}

type ViewLevel = "dirs" | "files";

export function GraphCanvas({ data, onSelectFile, onSelectDir }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [viewLevel, setViewLevel] = useState<ViewLevel>("dirs");
  const [expandedDir, setExpandedDir] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  // Build cytoscape elements for directory-level view
  const buildDirElements = useCallback(() => {
    const nodes = data.directories.map((d) => {
      const worstHealth = getBestDirHealth(data.files.filter(f => f.dir === d.id));
      return {
        data: {
          id: d.id,
          label: d.id.replace(/\/$/, ""),
          size: Math.max(30, Math.min(80, Math.sqrt(d.lines) / 2)),
          color: HEALTH_COLORS[worstHealth] || HEALTH_COLORS.stable,
          fileCount: d.files,
          lineCount: d.lines,
          symCount: d.syms,
        },
      };
    });

    // Aggregate edges between directories
    const dirEdges = new Map<string, number>();
    for (const e of data.edges) {
      const sDir = data.files.find(f => f.id === e.s)?.dir;
      const tDir = data.files.find(f => f.id === e.t)?.dir;
      if (sDir && tDir && sDir !== tDir) {
        const key = sDir < tDir ? `${sDir}|${tDir}` : `${tDir}|${sDir}`;
        dirEdges.set(key, (dirEdges.get(key) || 0) + e.w);
      }
    }

    const edges = Array.from(dirEdges.entries()).map(([key, weight]) => {
      const [s, t] = key.split("|");
      return {
        data: {
          id: `e-${s}-${t}`,
          source: s,
          target: t,
          weight: Math.min(weight, 20),
        },
      };
    });

    return [...nodes, ...edges];
  }, [data]);

  // Build cytoscape elements for file-level view (within a directory)
  const buildFileElements = useCallback((dirId: string) => {
    const dirFiles = data.files.filter(f => f.dir === dirId);
    const fileIds = new Set(dirFiles.map(f => f.id));

    const nodes = dirFiles.map((f) => ({
      data: {
        id: f.id,
        label: f.id.split("/").pop() || f.id,
        size: Math.max(20, Math.min(60, Math.sqrt(f.lines) / 1.5)),
        color: HEALTH_COLORS[f.health] || HEALTH_COLORS.stable,
        lines: f.lines,
        cx: f.cx,
        syms: f.syms,
        health: f.health,
        lang: f.lang,
      },
    }));

    const edges = data.edges
      .filter(e => fileIds.has(e.s) && fileIds.has(e.t))
      .map((e) => ({
        data: {
          id: `e-${e.s}-${e.t}`,
          source: e.s,
          target: e.t,
          weight: Math.min(e.w, 10),
        },
      }));

    return [...nodes, ...edges];
  }, [data]);

  // Initialize/update cytoscape
  useEffect(() => {
    if (!containerRef.current || !data) return;

    const elements = viewLevel === "dirs"
      ? buildDirElements()
      : buildFileElements(expandedDir || data.directories[0]?.id || "");

    if (cyRef.current) {
      cyRef.current.destroy();
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            label: "data(label)",
            "font-size": 10,
            "text-valign": "bottom",
            "text-margin-y": 6,
            color: "#a3a3a3",
            "text-outline-color": "#0f0f0f",
            "text-outline-width": 2,
            width: "data(size)",
            height: "data(size)",
            "border-width": 2,
            "border-color": "data(color)",
            "border-opacity": 0.5,
            "background-opacity": 0.7,
          },
        },
        {
          selector: "edge",
          style: {
            width: "mapData(weight, 1, 20, 1, 4)",
            "line-color": "#3b3b4f",
            "curve-style": "bezier",
            opacity: 0.4,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-width": 3,
            "border-color": "#7c3aed",
            "background-opacity": 1,
          },
        },
        {
          selector: "node.highlighted",
          style: {
            "border-width": 3,
            "border-color": "#7c3aed",
          },
        },
        {
          selector: "node.dimmed",
          style: {
            opacity: 0.15,
          },
        },
        {
          selector: "edge.dimmed",
          style: {
            opacity: 0.05,
          },
        },
      ],
      layout: {
        name: "fcose",
        animate: true,
        animationDuration: 600,
        randomize: true,
        nodeSeparation: 120,
        idealEdgeLength: 150,
        nodeRepulsion: () => 8000,
        gravity: 0.3,
      } as any,
      minZoom: 0.2,
      maxZoom: 4,
      wheelSensitivity: 0.3,
    });

    // Hover: highlight connected nodes
    cy.on("mouseover", "node", (e) => {
      const node = e.target;
      const neighborhood = node.neighborhood().add(node);
      cy.elements().addClass("dimmed");
      neighborhood.removeClass("dimmed");
      node.addClass("highlighted");
    });

    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("dimmed").removeClass("highlighted");
    });

    // Click: select node
    cy.on("tap", "node", (e) => {
      const nodeId = e.target.id();
      setSelectedNode(nodeId);
      if (viewLevel === "dirs") {
        onSelectDir?.(nodeId);
      } else {
        onSelectFile?.(nodeId);
      }
    });

    // Double-click: drill down into directory
    cy.on("dbltap", "node", (e) => {
      const nodeId = e.target.id();
      if (viewLevel === "dirs") {
        setExpandedDir(nodeId);
        setViewLevel("files");
      }
    });

    // Click background: deselect
    cy.on("tap", (e) => {
      if (e.target === cy) {
        setSelectedNode(null);
      }
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [data, viewLevel, expandedDir, buildDirElements, buildFileElements, onSelectFile, onSelectDir]);

  const handleBack = useCallback(() => {
    setViewLevel("dirs");
    setExpandedDir(null);
    setSelectedNode(null);
  }, []);

  const handleFit = useCallback(() => {
    cyRef.current?.fit(undefined, 40);
  }, []);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", position: "relative" }}>
      {/* Controls bar */}
      <div className="graph-controls">
        {viewLevel === "files" && (
          <button className="graph-control-btn" onClick={handleBack} title="Back to directories">
            ← Back
          </button>
        )}
        <span className="graph-level-label">
          {viewLevel === "dirs" ? `${data.directories.length} directories` : `${expandedDir} — ${data.files.filter(f => f.dir === expandedDir).length} files`}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <button className="graph-control-btn" onClick={handleFit} title="Fit to screen">Fit</button>
        </div>
      </div>

      {/* Cytoscape container */}
      <div
        ref={containerRef}
        style={{ flex: 1, background: "var(--bg)" }}
      />

      {/* Selected node info */}
      {selectedNode && (
        <SelectedNodeInfo
          nodeId={selectedNode}
          data={data}
          viewLevel={viewLevel}
        />
      )}
    </div>
  );
}

function SelectedNodeInfo({ nodeId, data, viewLevel }: { nodeId: string; data: GraphData; viewLevel: ViewLevel }) {
  if (viewLevel === "dirs") {
    const dir = data.directories.find(d => d.id === nodeId);
    if (!dir) return null;
    return (
      <div className="graph-info-bar">
        <strong>{dir.id}</strong>
        <span>{dir.files} files</span>
        <span>{dir.lines.toLocaleString()} lines</span>
        <span>{dir.syms} symbols</span>
        <span className="graph-info-hint">Double-click to explore</span>
      </div>
    );
  }

  const file = data.files.find(f => f.id === nodeId);
  if (!file) return null;
  return (
    <div className="graph-info-bar">
      <strong>{file.id.split("/").pop()}</strong>
      <span>{file.lines.toLocaleString()} lines</span>
      <span>cx: {file.cx}</span>
      <span>{file.syms} symbols</span>
      <span className={`graph-health-badge graph-health-${file.health}`}>{file.health}</span>
      {file.dead_pct > 0 && <span>{file.dead_pct}% dead</span>}
    </div>
  );
}

function getBestDirHealth(files: FileNode[]): string {
  if (files.some(f => f.health === "dead")) return "dead";
  if (files.some(f => f.health === "hotspot")) return "hotspot";
  if (files.every(f => f.health === "healthy")) return "healthy";
  return "stable";
}
