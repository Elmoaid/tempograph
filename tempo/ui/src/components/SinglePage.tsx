import { useState, useEffect, useRef } from "react";
import { getHomeDir } from "./tempo";
import { LandingPage } from "./LandingPage";
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
import { ViewNav, type AppView } from "./ViewNav";
import { DashboardView } from "./DashboardView";
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
  const [activeView, setActiveView] = useState<AppView>(
    () => (localStorage.getItem("tempo-active-view") as AppView) || "modes"
  );
  const [rightHidden, setRightHidden] = useState(() =>
    localStorage.getItem("tempo-right-hidden") === "true"
  );


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
    const stored = localStorage.getItem("tempo-recent-repos");
    const recentRepos: string[] = stored ? (() => { try { return JSON.parse(stored); } catch { return []; } })() : [];

    return (
      <>
        <LandingPage
          onSelectRepo={(path) => addWorkspace(path)}
          onShowSnapshots={() => setShowSnapshots(true)}
          recentRepos={recentRepos}
          onClearRecent={() => localStorage.removeItem("tempo-recent-repos")}
        />
        {showSnapshots && homeDir && (
          <div style={{ position: "fixed", top: 0, left: 0, right: 0, zIndex: 10 }}>
            <ErrorBoundary label="Snapshots">
              <SnapshotPanel
                homeDir={homeDir}
                onLoad={(path) => { addWorkspace(path); setShowSnapshots(false); }}
                onClose={() => setShowSnapshots(false)}
              />
            </ErrorBoundary>
          </div>
        )}
      </>
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

      <ViewNav
        activeView={activeView}
        onViewChange={(v) => {
          setActiveView(v);
          localStorage.setItem("tempo-active-view", v);
        }}
      />

      {showSnapshots && homeDir && (
        <ErrorBoundary label="Snapshots">
          <SnapshotPanel
            homeDir={homeDir}
            onLoad={(path) => { addWorkspace(path); setShowSnapshots(false); }}
            onClose={() => setShowSnapshots(false)}
          />
        </ErrorBoundary>
      )}

      {showClaude && (
        <ErrorBoundary label="Claude">
          <ClaudePanel onClose={() => setShowClaude(false)} workspaces={workspaces} />
        </ErrorBoundary>
      )}

      {activeView === "graph" && !showClaude && (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 12, color: "var(--text-tertiary)" }}>
          <div style={{ fontSize: 32, opacity: 0.3 }}>⬡</div>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-secondary)" }}>Graph View</div>
          <div style={{ fontSize: 12, maxWidth: 360, textAlign: "center", lineHeight: 1.6 }}>
            Interactive force-directed graph of file and symbol dependencies. Coming soon — requires Cytoscape.js integration.
          </div>
        </div>
      )}

      {activeView === "dashboard" && !showClaude && (
        <DashboardView repoPath={repoPath} />
      )}

      <div className={`grid-shell${rightHidden ? " right-hidden" : ""}`} style={{ display: (showClaude || activeView !== "modes") ? "none" : undefined }}>
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
