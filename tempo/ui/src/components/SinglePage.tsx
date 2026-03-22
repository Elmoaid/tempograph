import { useState, useEffect, useRef } from "react";
import { FolderOpen } from "lucide-react";
import { getHomeDir } from "./tempo";
import { ClaudePanel } from "./ClaudePanel";
import { ModeRunner } from "./ModeRunner";
import { QualityPanel } from "./QualityPanel";
import { PluginPanel } from "./PluginPanel";
import { SettingsPanel } from "./SettingsPanel";
import { ErrorBoundary } from "./ErrorBoundary";
import { TopBar } from "./TopBar";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { InfoPanel } from "./InfoPanel";
import { SnapshotPanel } from "./SnapshotPanel";
import { useWorkspaceData } from "../hooks/useWorkspaceData";

interface Props {
  repoPath: string;
  workspaces: string[];
  activeIdx: number;
  setActiveIdx: (i: number) => void;
  addWorkspace: (path: string) => void;
  removeWorkspace: (i: number) => void;
  theme: "dark" | "light";
  onToggleTheme: () => void;
}

function parseStats(output: string) {
  const m = output.match(/(\d+)\s+files.*?(\d+)\s+symbols.*?([\d,]+)\s+lines/s);
  return m ? { files: m[1], symbols: m[2], lines: m[3] } : null;
}

export function SinglePage({ repoPath, workspaces, activeIdx, setActiveIdx, addWorkspace, removeWorkspace, theme, onToggleTheme }: Props) {
  const [showClaude, setShowClaude] = useState(false);
  const [showSnapshots, setShowSnapshots] = useState(false);
  const [homeDir, setHomeDir] = useState("");
  const [rightHidden, setRightHidden] = useState(() =>
    localStorage.getItem("tempo-right-hidden") === "true"
  );
  const emptyInputRef = useRef<HTMLInputElement>(null);

  const {
    loading, ws, configDirty,
    noteContent, noteName, fileBrowserPath, fileBrowserEntries, fileViewContent, fileViewName,
    loadAll, togglePlugin, saveConfig, updateConfig,
    openNote, handleNoteCreated, browseTo, viewFile,
    clearNoteContent, clearFileView,
  } = useWorkspaceData(repoPath);

  useEffect(() => {
    getHomeDir().then(setHomeDir);
  }, []);

  const stats = ws.overview ? parseStats(ws.overview.output) : null;

  if (workspaces.length === 0 && !loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", flexDirection: "column", gap: 16 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: "var(--accent)" }}>Tempo</div>
        <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>Get started by indexing a repository or loading a snapshot.</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <FolderOpen size={16} color="var(--text-tertiary)" />
          <input ref={emptyInputRef} className="input" placeholder="/path/to/repo"
            onKeyDown={(e) => { if (e.key === "Enter") { const v = emptyInputRef.current?.value.trim(); if (v) addWorkspace(v); } }}
            style={{ width: 360 }} autoFocus />
          <button className="btn" onClick={() => { const v = emptyInputRef.current?.value.trim(); if (v) addWorkspace(v); }}>Index</button>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>or</div>
        <button
          className="btn-ghost"
          onClick={() => setShowSnapshots(true)}
          style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 14px", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
        >
          Load a snapshot
        </button>
        {showSnapshots && homeDir && (
          <div style={{ position: "fixed", top: 0, left: 0, right: 0, zIndex: 10 }}>
            <SnapshotPanel
              homeDir={homeDir}
              onLoad={(path) => { addWorkspace(path); setShowSnapshots(false); }}
              onClose={() => setShowSnapshots(false)}
            />
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <TopBar
        stats={stats}
        showClaude={showClaude}
        onToggleClaude={() => setShowClaude(!showClaude)}
        showSnapshots={showSnapshots}
        onToggleSnapshots={() => setShowSnapshots(!showSnapshots)}
        rightHidden={rightHidden}
        onToggleRight={() => {
          const next = !rightHidden;
          setRightHidden(next);
          localStorage.setItem("tempo-right-hidden", String(next));
        }}
        loading={loading}
        onRefresh={() => loadAll(repoPath, true)}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />

      <WorkspaceTabs
        workspaces={workspaces}
        activeIdx={activeIdx}
        loading={loading}
        onSelect={(i) => { setActiveIdx(i); clearNoteContent(); }}
        onRemove={removeWorkspace}
        onAdd={addWorkspace}
      />

      {showSnapshots && homeDir && (
        <SnapshotPanel
          homeDir={homeDir}
          onLoad={(path) => { addWorkspace(path); setShowSnapshots(false); }}
          onClose={() => setShowSnapshots(false)}
        />
      )}

      {showClaude && <ClaudePanel onClose={() => setShowClaude(false)} workspaces={workspaces} />}

      <div className={`grid-shell${rightHidden ? " right-hidden" : ""}`} style={{ display: showClaude ? "none" : undefined }}>
        <ErrorBoundary label="Modes">
          <ModeRunner
            key={repoPath}
            repoPath={repoPath}
            excludeDirs={Array.isArray(ws.config.exclude_dirs) ? ws.config.exclude_dirs as string[] : undefined}
          />
        </ErrorBoundary>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <ErrorBoundary label="Quality">
            <QualityPanel qualityOutput={ws.quality?.output || null} />
          </ErrorBoundary>
          <ErrorBoundary label="Plugins">
            <PluginPanel plugins={ws.plugins} onToggle={togglePlugin} loading={loading} />
          </ErrorBoundary>
          <ErrorBoundary label="Settings">
            <SettingsPanel config={ws.config} isDirty={configDirty} onUpdate={updateConfig} onSave={saveConfig} />
          </ErrorBoundary>
        </div>

        <ErrorBoundary label="Info panels">
          <InfoPanel
            repoPath={repoPath}
            ws={ws}
            fileBrowserPath={fileBrowserPath}
            fileBrowserEntries={fileBrowserEntries}
            fileViewContent={fileViewContent}
            fileViewName={fileViewName}
            noteContent={noteContent}
            noteName={noteName}
            onOpenNote={openNote}
            onNoteBack={clearNoteContent}
            onFileViewBack={clearFileView}
            onBrowseTo={browseTo}
            onViewFile={viewFile}
            onNoteCreated={handleNoteCreated}
          />
        </ErrorBoundary>
      </div>
    </div>
  );
}
