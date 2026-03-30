import { useRef, useState } from "react";

export interface CacheEntry {
  output: string | undefined;
  ts: number | undefined;
  duration: number | null;
}

export function useOutputCache() {
  const outputCache = useRef<Map<string, string>>(new Map());
  const outputTsCache = useRef<Map<string, number>>(new Map());
  const runDurationCache = useRef<Map<string, number>>(new Map());
  const [cachedModes, setCachedModes] = useState<Set<string>>(new Set());
  const [outputTs, setOutputTs] = useState<number | null>(null);
  const [runDuration, setRunDuration] = useState<number | null>(null);

  const getCache = (key: string): CacheEntry => ({
    output: outputCache.current.get(key),
    ts: outputTsCache.current.get(key),
    duration: runDurationCache.current.get(key) ?? null,
  });

  const clearCache = (key: string) => {
    outputCache.current.delete(key);
    outputTsCache.current.delete(key);
    setCachedModes(prev => { const s = new Set(prev); s.delete(key); return s; });
  };

  return {
    // refs — passed through to useRunMode
    outputCache,
    outputTsCache,
    runDurationCache,
    // state
    cachedModes,
    setCachedModes,
    outputTs,
    setOutputTs,
    runDuration,
    setRunDuration,
    // helpers
    getCache,
    clearCache,
  };
}
