import { useState, useCallback } from "react";

export interface RunHistoryEntry {
  mode: string;
  args: string;
}

export function updateRunHistory(prev: RunHistoryEntry[], entry: RunHistoryEntry, max = 5): RunHistoryEntry[] {
  const deduped = prev.filter(e => !(e.mode === entry.mode && e.args === entry.args));
  return [entry, ...deduped].slice(0, max);
}

export function useRunHistory() {
  const [runHistory, setRunHistory] = useState<RunHistoryEntry[]>([]);

  const addRunHistory = useCallback((mode: string, args: string) => {
    setRunHistory(prev => updateRunHistory(prev, { mode, args }));
  }, []);

  return { runHistory, addRunHistory };
}
