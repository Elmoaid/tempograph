import { FolderOpen, X } from "lucide-react";
import type { RecentRepo } from "../hooks/useRecentRepos";

interface RecentReposProps {
  repos: RecentRepo[];
  activeWorkspaces: string[];
  onSelect: (path: string) => void;
  onRemove: (path: string) => void;
  onClear: () => void;
}

function truncatePath(p: string, max = 34): string {
  if (p.length <= max) return p;
  return "…" + p.slice(-(max - 1));
}

export function RecentRepos({ repos, activeWorkspaces, onSelect, onRemove, onClear }: RecentReposProps) {
  if (repos.length === 0) return null;

  // Filter out repos already open as active workspaces
  const visible = repos.filter(r => !activeWorkspaces.includes(r.path));
  if (visible.length === 0) return null;

  return (
    <div className="recent-repos-strip">
      <span className="recent-repos-label">Recent</span>
      {visible.map(r => (
        <button
          key={r.path}
          className="recent-repo-item"
          onClick={() => onSelect(r.path)}
          title={r.path}
        >
          <FolderOpen size={11} />
          <span className="recent-repo-name">{r.label}</span>
          <span className="recent-repo-path">{truncatePath(r.path)}</span>
          <span
            className="recent-repo-remove"
            onClick={(e) => { e.stopPropagation(); onRemove(r.path); }}
            title="Remove from history"
          >
            <X size={10} />
          </span>
        </button>
      ))}
      <button className="recent-repos-clear" onClick={onClear} title="Clear history">
        Clear
      </button>
    </div>
  );
}
