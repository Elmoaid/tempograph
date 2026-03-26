import { useEffect, useRef, useState } from "react";
import { FolderOpen, Plus, X, RefreshCw } from "lucide-react";
import { useRecentRepos } from "../hooks/useRecentRepos";
import { useRepoStats, formatAge } from "../hooks/useRepoStats";
import { RecentRepos } from "./RecentRepos";

interface WorkspaceTabsProps {
  workspaces: string[];
  activeIdx: number;
  loading: boolean;
  onSelect: (i: number) => void;
  onRemove: (i: number) => void;
  onAdd: (path: string) => void;
}

function folderName(p: string): string {
  return p.split("/").filter(Boolean).pop() || p;
}

export function WorkspaceTabs({ workspaces, activeIdx, loading, onSelect, onRemove, onAdd }: WorkspaceTabsProps) {
  const [addingWs, setAddingWs] = useState(false);
  const [newWsPath, setNewWsPath] = useState("");
  const addInputRef = useRef<HTMLInputElement>(null);
  const { recentRepos, addRecentRepo, removeRecentRepo, clearRecentRepos } = useRecentRepos();
  const activeRepoPath = workspaces[activeIdx] || "";
  const stats = useRepoStats(activeRepoPath);
  const [, forceUpdate] = useState(0);

  // Refresh age display every 30s
  useEffect(() => {
    const id = setInterval(() => forceUpdate((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (addingWs) addInputRef.current?.focus();
  }, [addingWs]);

  const handleAdd = () => {
    if (newWsPath.trim()) {
      const path = newWsPath.trim();
      addRecentRepo(path);
      onAdd(path);
      setNewWsPath("");
      setAddingWs(false);
    }
  };

  const handleSelectRecent = (path: string) => {
    addRecentRepo(path);
    onAdd(path);
  };

  return (
    <>
    <div className="ws-strip">
      {workspaces.map((w, i) => (
        <button
          key={w}
          className={`ws-tab ${i === activeIdx ? "active" : ""}`}
          onClick={() => onSelect(i)}
          title={w}
        >
          <FolderOpen size={12} />
          <span className="ws-tab-name">{folderName(w)}</span>
          {i === activeIdx && loading && <RefreshCw size={10} className="spin" />}
          <span className="ws-tab-close" onClick={(e) => { e.stopPropagation(); onRemove(i); }}>
            <X size={10} />
          </span>
        </button>
      ))}
      {addingWs ? (
        <div className="ws-add-input">
          <input
            ref={addInputRef}
            className="input"
            placeholder="/path/to/repo"
            value={newWsPath}
            onChange={(e) => setNewWsPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAdd();
              if (e.key === "Escape") { setAddingWs(false); setNewWsPath(""); }
            }}
            onBlur={() => { if (!newWsPath.trim()) setAddingWs(false); }}
            style={{ width: 250, fontSize: 11, padding: "3px 8px" }}
          />
        </div>
      ) : (
        <button className="ws-tab ws-add" onClick={() => setAddingWs(true)} title="Add workspace">
          <Plus size={12} />
        </button>
      )}
      {stats && (
        <span className="ws-stats-badge" title={`${stats.symbols.toLocaleString()} symbols · ${stats.files} files · indexed ${formatAge(stats.fetchedAt)}`}>
          {(stats.symbols / 1000).toFixed(1)}K sym · {stats.files} files · {formatAge(stats.fetchedAt)}
        </span>
      )}
    </div>
    <RecentRepos
      repos={recentRepos}
      activeWorkspaces={workspaces}
      onSelect={handleSelectRecent}
      onRemove={removeRecentRepo}
      onClear={clearRecentRepos}
    />
    </>
  );
}
