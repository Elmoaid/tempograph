import { useState, useEffect, useRef } from "react";
import { runTempo } from "../components/tempo";

export interface DirNode {
  id: string;
  files: number;
  lines: number;
  syms: number;
  langs: string[];
}

export interface FileNode {
  id: string;
  dir: string;
  lines: number;
  lang: string;
  syms: number;
  cx: number;
  dead_pct: number;
  health: "healthy" | "hotspot" | "dead" | "stable";
}

export interface GraphEdge {
  s: string;
  t: string;
  w: number;
}

export interface GraphData {
  repo: string;
  stats: { files: number; symbols: number; edges: number };
  build_ms: number;
  directories: DirNode[];
  files: FileNode[];
  edges: GraphEdge[];
}

export function useGraphData(repoPath: string) {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef(false);

  useEffect(() => {
    if (!repoPath) {
      setData(null);
      return;
    }
    abortRef.current = false;
    setLoading(true);
    setError(null);

    runTempo(repoPath, "graph_data")
      .then((result) => {
        if (abortRef.current) return;
        if (!result.success) {
          setError(result.output || "Failed to load graph data");
          setLoading(false);
          return;
        }
        try {
          const parsed = JSON.parse(result.output);
          setData(parsed);
        } catch {
          setError("Failed to parse graph data");
        }
        setLoading(false);
      })
      .catch((e) => {
        if (!abortRef.current) {
          setError(String(e));
          setLoading(false);
        }
      });

    return () => { abortRef.current = true; };
  }, [repoPath]);

  return { data, loading, error };
}
