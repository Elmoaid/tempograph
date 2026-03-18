import type { ComponentType } from "react";
import {
  Eye, Crosshair, Bomb, Skull, Flame, GitBranch,
  Package, Layers, Hash, Map as MapIcon, Brain, Gauge, BookOpen, Coins,
  BarChart3, Search, Zap,
} from "lucide-react";

export interface ModeInfo {
  mode: string;
  label: string;
  icon: ComponentType<{ size?: number }>;
  tag: string;
  hint?: string;
  argPrefix?: string;
  desc?: string;
}

export const MODES: ModeInfo[] = [
  { mode: "prepare", label: "Prepare Context", icon: Zap, tag: "mcp", hint: "describe your task", argPrefix: "--query", desc: "Optimized context snapshot for your task — paste directly into Claude." },
  { mode: "overview", label: "Overview", icon: Eye, tag: "mcp", desc: "High-level summary: languages, top files, key symbols, recent activity." },
  { mode: "focus", label: "Focus", icon: Crosshair, tag: "mcp", hint: "what are you working on?", argPrefix: "--query", desc: "Deep-dive into a symbol or task area — BFS from entry point, depth 3." },
  { mode: "lookup", label: "Lookup", icon: Search, tag: "mcp", hint: "where is X? / what calls X?", argPrefix: "--query", desc: "Find where a symbol is defined and what calls it across the codebase." },
  { mode: "blast", label: "Blast Radius", icon: Bomb, tag: "mcp", hint: "symbol or file path", argPrefix: "--query", desc: "Show all files/symbols that would be affected if this symbol changes." },
  { mode: "hotspots", label: "Hotspots", icon: Flame, tag: "mcp", desc: "Files with the most cross-module dependencies — highest refactor risk." },
  { mode: "diff", label: "Diff Context", icon: GitBranch, tag: "mcp", hint: "file1.py,file2.py (blank = unstaged)", argPrefix: "--file", desc: "Context around changed files — what else could break from this diff." },
  { mode: "dead_code", label: "Dead Code", icon: Skull, tag: "mcp", desc: "Symbols with no callers — ranked by confidence. Review before deleting." },
  { mode: "symbols", label: "Symbols", icon: Hash, tag: "mcp", desc: "All exported symbols in the repo with types, locations, and caller counts." },
  { mode: "map", label: "File Map", icon: MapIcon, tag: "mcp", desc: "Directory tree with file sizes and symbol counts — codebase topology." },
  { mode: "deps", label: "Dependencies", icon: Package, tag: "mcp", desc: "Import graph: which modules depend on which, and circular dependency detection." },
  { mode: "arch", label: "Architecture", icon: Layers, tag: "mcp", desc: "Layer diagram inferred from import patterns — entry points to leaf modules." },
  { mode: "stats", label: "Stats", icon: BarChart3, tag: "mcp", desc: "Token counts, file sizes, language breakdown — input budget awareness." },
  { mode: "context", label: "Context Engine", icon: Brain, tag: "ai", desc: "AI-ranked context: most relevant files for the current task." },
  { mode: "quality", label: "Quality Score", icon: Gauge, tag: "ai", desc: "Code health metrics: complexity, coupling, test coverage signals." },
  { mode: "learn", label: "Learning", icon: BookOpen, tag: "mcp", desc: "Patterns learned from past feedback — what worked, what to avoid." },
  { mode: "token_stats", label: "Token Stats", icon: Coins, tag: "ai", desc: "Per-mode token usage history — optimize your context budget." },
];

export const HISTORY_MAX = 8;
export const historyKey = (mode: string) => `tempo-history-${mode}`;

export const loadHistory = (mode: string): string[] => {
  try { return JSON.parse(localStorage.getItem(historyKey(mode)) || "[]"); } catch { return []; }
};

export const saveHistory = (mode: string, query: string) => {
  const prev = loadHistory(mode).filter(q => q !== query);
  localStorage.setItem(historyKey(mode), JSON.stringify([query, ...prev].slice(0, HISTORY_MAX)));
};

export function formatAge(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
