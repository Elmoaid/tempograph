import { useState, useMemo, useRef, useCallback } from "react";

/**
 * Pure function — applies a filter query to output lines.
 * Returns null when query is blank or output is empty (caller uses original).
 * Otherwise returns the matching lines (joined) and count.
 */
export function applyOutputFilter(
  output: string,
  query: string
): { filtered: string; matchCount: number } | null {
  if (!query.trim() || !output) return null;
  const q = query.toLowerCase();
  const matching = output.split("\n").filter(line => line.toLowerCase().includes(q));
  return { filtered: matching.join("\n"), matchCount: matching.length };
}

export function useOutputFilter(modeOutput: string) {
  const [outputFilter, setOutputFilter] = useState("");
  const [filterVisible, setFilterVisible] = useState(false);
  const filterInputRef = useRef<HTMLInputElement>(null);

  const filterResult = useMemo(
    () => applyOutputFilter(modeOutput, outputFilter),
    [modeOutput, outputFilter]
  );
  const filteredOutput = filterResult ? filterResult.filtered : modeOutput;
  const filterMatchCount = filterResult ? filterResult.matchCount : null;

  const onFilterToggle = useCallback(() => {
    setFilterVisible(v => !v);
    setTimeout(() => filterInputRef.current?.focus(), 50);
  }, []);

  const onFilterClose = useCallback(() => {
    setFilterVisible(false);
    setOutputFilter("");
  }, []);

  const resetFilter = useCallback(() => {
    setFilterVisible(false);
    setOutputFilter("");
  }, []);

  return {
    outputFilter,
    setOutputFilter,
    filterVisible,
    setFilterVisible,
    filterInputRef,
    filteredOutput,
    filterMatchCount,
    onFilterToggle,
    onFilterClose,
    resetFilter,
  };
}
