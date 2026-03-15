import { useState, useEffect, useCallback } from "react";
import { SinglePage } from "./components/SinglePage";
import { detectRepo } from "./components/tempo";
import "./App.css";

export interface TempoResult {
  success: boolean;
  output: string;
  mode: string;
}

const STORAGE_KEY = "tempo-workspaces";
const DEFAULT_WORKSPACES = [
  "/Users/elmoaidali/Desktop/Final NeedSpec Production Review",
  "/Users/elmoaidali/Desktop/NeedEnd - Production Review",
];

function loadWorkspaces(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch { /* ignore */ }
  return DEFAULT_WORKSPACES;
}

function saveWorkspaces(ws: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ws));
}

function App() {
  const [workspaces, setWorkspaces] = useState<string[]>(loadWorkspaces);
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    // If no workspaces yet, try auto-detect and prepend
    if (workspaces.length === 0) {
      detectRepo().then((r) => {
        if (r.success && r.output) {
          setWorkspaces([r.output]);
          saveWorkspaces([r.output]);
        }
      });
    }
  }, []);

  const addWorkspace = useCallback((path: string) => {
    if (!path || workspaces.includes(path)) {
      // If already exists, just switch to it
      const idx = workspaces.indexOf(path);
      if (idx >= 0) setActiveIdx(idx);
      return;
    }
    const next = [...workspaces, path];
    setWorkspaces(next);
    setActiveIdx(next.length - 1);
    saveWorkspaces(next);
  }, [workspaces]);

  const removeWorkspace = useCallback((idx: number) => {
    const next = workspaces.filter((_, i) => i !== idx);
    setWorkspaces(next);
    saveWorkspaces(next);
    if (activeIdx >= next.length) setActiveIdx(Math.max(0, next.length - 1));
    else if (idx < activeIdx) setActiveIdx(activeIdx - 1);
  }, [workspaces, activeIdx]);

  const repoPath = workspaces[activeIdx] || "";

  return (
    <SinglePage
      repoPath={repoPath}
      workspaces={workspaces}
      activeIdx={activeIdx}
      setActiveIdx={setActiveIdx}
      addWorkspace={addWorkspace}
      removeWorkspace={removeWorkspace}
    />
  );
}

export default App;
