import { useState, useRef, useEffect, useCallback } from "react";
import { FolderOpen, Upload, Clock, Trash2 } from "lucide-react";
import { openFolderDialog } from "./tempo";

interface LandingPageProps {
  onSelectRepo: (path: string) => void;
  onShowSnapshots: () => void;
  recentRepos: string[];
  onClearRecent?: () => void;
}

export function LandingPage({ onSelectRepo, onShowSnapshots, recentRepos, onClearRecent }: LandingPageProps) {
  const [dragging, setDragging] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dragCounter = useRef(0);

  // Tauri native file drop
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    (async () => {
      try {
        const { getCurrentWebviewWindow } = await import("@tauri-apps/api/webviewWindow");
        const win = getCurrentWebviewWindow();
        const u = await win.onDragDropEvent((event) => {
          if (event.payload.type === "over") {
            setDragging(true);
          } else if (event.payload.type === "drop") {
            setDragging(false);
            const paths = event.payload.paths;
            if (paths && paths.length > 0) {
              onSelectRepo(paths[0]);
            }
          } else if (event.payload.type === "cancel") {
            setDragging(false);
          }
        });
        unlisten = u;
      } catch {
        // Not in Tauri runtime
      }
    })();
    return () => { unlisten?.(); };
  }, [onSelectRepo]);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current++;
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current--;
    if (dragCounter.current <= 0) {
      setDragging(false);
      dragCounter.current = 0;
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    dragCounter.current = 0;
  }, []);

  const handleBrowse = useCallback(async () => {
    const path = await openFolderDialog();
    if (path) onSelectRepo(path);
  }, [onSelectRepo]);

  const handleSubmit = useCallback(() => {
    const v = inputValue.trim();
    if (v) onSelectRepo(v);
  }, [inputValue, onSelectRepo]);

  return (
    <div
      className={`landing-page${dragging ? " landing-dragging" : ""}`}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <div className="landing-content">
        <div className="landing-brand">Tempo</div>
        <div className="landing-subtitle">
          Drop a folder to explore your codebase
        </div>

        <div className="landing-drop-zone" onClick={handleBrowse} role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleBrowse(); }}>
          <Upload size={32} strokeWidth={1.5} />
          <span>Drop folder here or click to browse</span>
        </div>

        <div className="landing-divider">
          <span>or</span>
        </div>

        <div className="landing-input-row">
          <FolderOpen size={16} className="landing-input-icon" />
          <input
            ref={inputRef}
            className="landing-input"
            placeholder="/path/to/repo"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
          />
          <button className="landing-btn" onClick={handleSubmit} disabled={!inputValue.trim()}>
            Index
          </button>
        </div>

        <button className="landing-snapshot-btn" onClick={onShowSnapshots}>
          Load a pre-indexed snapshot
        </button>

        {recentRepos.length > 0 && (
          <div className="landing-recent">
            <div className="landing-recent-header">
              <Clock size={12} />
              <span>Recent</span>
              {onClearRecent && (
                <button className="landing-clear-btn" onClick={onClearRecent} title="Clear history">
                  <Trash2 size={10} />
                </button>
              )}
            </div>
            <div className="landing-recent-list">
              {recentRepos.map((repo) => (
                <button
                  key={repo}
                  className="landing-recent-item"
                  onClick={() => onSelectRepo(repo)}
                  title={repo}
                >
                  <FolderOpen size={12} />
                  <span>{repo.split("/").slice(-2).join("/")}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
