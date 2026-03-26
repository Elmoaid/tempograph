import { useState, useEffect, useRef } from "react";
import { runTempo } from "../components/tempo";

export interface RepoStats {
  symbols: number;
  files: number;
  fetchedAt: Date;
}

function parseStats(output: string): { symbols: number; files: number } | null {
  const m = output.match(/Files:\s*(\d[\d,]*),\s*Symbols:\s*(\d[\d,]*)/);
  if (!m) return null;
  return {
    files: parseInt(m[1].replace(/,/g, ""), 10),
    symbols: parseInt(m[2].replace(/,/g, ""), 10),
  };
}

export function formatAge(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

export function useRepoStats(repoPath: string): RepoStats | null {
  const [stats, setStats] = useState<RepoStats | null>(null);
  const abortRef = useRef(false);

  useEffect(() => {
    if (!repoPath) { setStats(null); return; }
    abortRef.current = false;
    setStats(null);

    runTempo(repoPath, "stats").then((result) => {
      if (abortRef.current) return;
      if (!result.success) return;
      const parsed = parseStats(result.output);
      if (parsed) setStats({ ...parsed, fetchedAt: new Date() });
    }).catch(() => { /* silent */ });

    return () => { abortRef.current = true; };
  }, [repoPath]);

  return stats;
}
