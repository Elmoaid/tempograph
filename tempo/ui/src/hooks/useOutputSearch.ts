import { useState, useCallback, useRef } from "react";

export interface OutputSearchState {
  searchText: string;
  matchCount: number;
  currentMatch: number; // 1-based, 0 = none
  active: boolean;
}

/** Count non-overlapping case-insensitive occurrences of query in output. Returns 0 for blank query. */
export function countMatches(output: string, query: string): number {
  if (!query.trim()) return 0;
  const q = query.toLowerCase();
  const lower = output.toLowerCase();
  let count = 0;
  let pos = 0;
  while (true) {
    const idx = lower.indexOf(q, pos);
    if (idx === -1) break;
    count++;
    pos = idx + q.length;
  }
  return count;
}

export function useOutputSearch(output: string) {
  const [searchText, setSearchText] = useState("");
  const [active, setActive] = useState(false);
  const [currentMatch, setCurrentMatch] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const matchCount = countMatches(output, searchText);

  // Reset currentMatch when text or output changes
  const [prevSearch, setPrevSearch] = useState(searchText);
  const [prevOutput, setPrevOutput] = useState(output);
  if (prevSearch !== searchText || prevOutput !== output) {
    setPrevSearch(searchText);
    setPrevOutput(output);
    setCurrentMatch(matchCount > 0 ? 1 : 0);
  }

  const open = useCallback(() => {
    setActive(true);
    setTimeout(() => searchInputRef.current?.focus(), 30);
  }, []);

  const close = useCallback(() => {
    setActive(false);
    setSearchText("");
    setCurrentMatch(0);
  }, []);

  const navigateMatch = useCallback((dir: "next" | "prev") => {
    if (matchCount === 0) return;
    setCurrentMatch(prev => {
      if (prev === 0) return 1;
      if (dir === "next") return prev >= matchCount ? 1 : prev + 1;
      return prev <= 1 ? matchCount : prev - 1;
    });
  }, [matchCount]);

  return {
    searchText,
    setSearchText,
    matchCount,
    currentMatch,
    active,
    searchInputRef,
    open,
    close,
    navigateMatch,
  };
}
