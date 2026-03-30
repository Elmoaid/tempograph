import { useState, useCallback } from "react";
import { lastModeKey } from "./useModeSelectionState";
import { loadHistory } from "../components/modes";

export interface RunHistoryEntry {
  mode: string;
  args: string;
}

export function updateRunHistory(prev: RunHistoryEntry[], entry: RunHistoryEntry, max = 5): RunHistoryEntry[] {
  const deduped = prev.filter(e => !(e.mode === entry.mode && e.args === entry.args));
  return [entry, ...deduped].slice(0, max);
}

export function useRunHistory(repoPath: string) {
  const [runHistory, setRunHistory] = useState<RunHistoryEntry[]>([]);
  const [history, setHistory] = useState<string[]>(() => {
    const initialMode = localStorage.getItem(lastModeKey(repoPath)) || "overview";
    return loadHistory(initialMode);
  });

  const addRunHistory = useCallback((mode: string, args: string) => {
    setRunHistory(prev => updateRunHistory(prev, { mode, args }));
  }, []);

  const loadModeHistory = useCallback((mode: string) => {
    setHistory(loadHistory(mode));
  }, []);

  return { runHistory, addRunHistory, history, setHistory, loadModeHistory };
}
