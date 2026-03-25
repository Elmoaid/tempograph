import { useState, useCallback } from "react";

const RECENT_KEY = "tempo_recent_repos";
const MAX_RECENT = 8;

export interface RecentRepo {
  path: string;
  label: string;
  lastUsed: number;
}

function loadRecent(): RecentRepo[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as RecentRepo[]) : [];
  } catch { return []; }
}

function saveRecent(list: RecentRepo[]) {
  localStorage.setItem(RECENT_KEY, JSON.stringify(list));
}

export function useRecentRepos() {
  const [recentRepos, setRecentRepos] = useState<RecentRepo[]>(loadRecent);

  const addRecentRepo = useCallback((path: string) => {
    setRecentRepos(prev => {
      const label = path.split("/").filter(Boolean).pop() || path;
      const filtered = prev.filter(r => r.path !== path);
      const next = [{ path, label, lastUsed: Date.now() }, ...filtered].slice(0, MAX_RECENT);
      saveRecent(next);
      return next;
    });
  }, []);

  const removeRecentRepo = useCallback((path: string) => {
    setRecentRepos(prev => {
      const next = prev.filter(r => r.path !== path);
      saveRecent(next);
      return next;
    });
  }, []);

  const clearRecentRepos = useCallback(() => {
    localStorage.removeItem(RECENT_KEY);
    setRecentRepos([]);
  }, []);

  return { recentRepos, addRecentRepo, removeRecentRepo, clearRecentRepos };
}
