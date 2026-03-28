import { useState, useEffect } from "react";

export const SUGGEST_NEXT_MAP: Record<string, string[]> = {
  overview:    ["hotspots", "dead_code", "focus"],
  focus:       ["blast", "hotspots"],
  blast:       ["focus", "hotspots"],
  hotspots:    ["dead_code", "focus"],
  dead_code:   ["hotspots", "focus"],
  diff:        ["focus", "blast"],
  deps:        ["focus", "blast"],
  arch:        ["hotspots", "deps"],
  map:         ["focus", "hotspots"],
  context:     ["focus", "blast"],
  prepare:     ["focus", "hotspots"],
  quality:     ["hotspots", "focus"],
  token_stats: ["focus", "hotspots"],
  learn:       ["focus", "overview"],
};

export function computeSuggestions(
  modeOutput: string,
  modeRunning: boolean,
  activeMode: string,
  activeKit: string | null,
): string[] {
  if (modeRunning) return [];
  if (
    !modeOutput ||
    activeKit ||
    modeOutput.startsWith("[Cancelled]") ||
    modeOutput.startsWith("Failed to run")
  ) return [];
  return (SUGGEST_NEXT_MAP[activeMode] ?? []).slice(0, 3);
}

export function useSuggestions(
  modeOutput: string,
  modeRunning: boolean,
  activeMode: string,
  activeKit: string | null,
): { suggestions: string[] } {
  const [suggestions, setSuggestions] = useState<string[]>([]);

  useEffect(() => {
    setSuggestions(computeSuggestions(modeOutput, modeRunning, activeMode, activeKit));
  }, [modeOutput, modeRunning, activeMode, activeKit]);

  return { suggestions };
}
