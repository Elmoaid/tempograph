import { useState, useCallback, useRef, useEffect } from "react";

export interface OutputSearchState {
  searchText: string;
  matchCount: number;
  currentMatch: number; // 1-based, 0 = none
  active: boolean;
}

export function useOutputSearch(output: string) {
  const [searchText, setSearchText] = useState("");
  const [active, setActive] = useState(false);
  const [currentMatch, setCurrentMatch] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const matchCount = searchText.trim()
    ? (() => {
        const q = searchText.toLowerCase();
        let count = 0;
        let pos = 0;
        const lower = output.toLowerCase();
        while (true) {
          const idx = lower.indexOf(q, pos);
          if (idx === -1) break;
          count++;
          pos = idx + q.length;
        }
        return count;
      })()
    : 0;

  // Reset currentMatch when text or output changes
  useEffect(() => {
    setCurrentMatch(matchCount > 0 ? 1 : 0);
  }, [searchText, output]);

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
