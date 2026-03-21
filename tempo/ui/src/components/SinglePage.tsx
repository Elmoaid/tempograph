import { useState, useEffect, useRef, useCallback } from "react";
import { FolderOpen } from "lucide-react";
import {
  runTempo, readConfig, writeConfig, listNotes, readFile, readTelemetry,
  gitInfo, listDir, getHomeDir,
} from "./tempo";
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
import type { PluginInfo } from "./PluginPanel";
import type { TempoResult } from "../App";
import type { WorkspaceData, DirEntry, NoteEntry } from "./workspaceTypes";

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

function parsePlugins(output: string): PluginInfo[] {
  return output.split("\n").map(line => {
    const m = line.match(/^\s*(?:\[([x ])\]|([●○]))\s+(\w+)\s*[-—]\s*(.+)/);
    if (!m) return null;
    return { enabled: m[1] === "x" || m[2] === "●", name: m[3], description: m[4].trim() };
  }).filter(Boolean) as PluginInfo[];
}

function parseStats(output: string) {
  const m = output.match(/(\d+)\s+files.*?(\d+)\s+symbols.*?([\d,]+)\s+lines/s);
  return m ? { files: m[1], symbols: m[2], lines: m[3] } : null;
}

export function SinglePage({ repoPath, workspaces, activeIdx, setActiveIdx, addWorkspace, removeWorkspace, theme, onToggleTheme }: Props) {
  const [loading, setLoading] = useState(false);
  const [showClaude, setShowClaude] = useState(false);
  const [showSnapshots, setShowSnapshots] = useState(false);
  const [homeDir, setHomeDir] = useState("");
  const [rightHidden, setRightHidden] = useState(() =>
    localStorage.getItem("tempo-right-hidden") === "true"
  );
  const [configDirty, setConfigDirty] = useState(false);
  const [noteContent, setNoteContent] = useState<string | null>(null);
  const [noteName, setNoteName] = useState("");
  const [fileBrowserPath, setFileBrowserPath] = useState("");
  const [fileBrowserEntries, setFileBrowserEntries] = useState<DirEntry[]>([]);
  const [fileViewContent, setFileViewContent] = useState<string | null>(null);
  const [fileViewName, setFileViewName] = useState("");

  const cacheRef = useRef<Record<string, WorkspaceData>>({});
  const emptyInputRef = useRef<HTMLInputElement>(null);

  const getWsData = useCallback((path: string): WorkspaceData => {
    return cacheRef.current[path] || { overview: null, quality: null, learning: null, tokens: null, plugins: [], notes: [], telemetry: "", config: {}, git: "", loaded: false };
  }, []);

  const setWsData = useCallback((path: string, data: Partial<WorkspaceData>) => {
    cacheRef.current[path] = { ...getWsData(path), ...data };
  }, [getWsData]);

  const loadAll = useCallback(async (path: string, force = false) => {
    if (!path) return;
    if (!force && cacheRef.current[path]?.loaded) return;
    setLoading(true);
    setNoteContent(null);

    const safe = async <T,>(fn: () => Promise<T>, fallback: T): Promise<T> => {
      try { return await fn(); } catch { return fallback; }
    };
    const emptyResult: TempoResult = { success: false, output: "", mode: "" };

    const cfgResult = await safe(() => readConfig(path), { success: false, data: {}, path: "", error: "" });
    const cfgData = (cfgResult as { success: boolean; data: Record<string, unknown> }).success
      ? ((cfgResult as { data: Record<string, unknown> }).data || {}) : {};
    const excludeArgs: string[] = [];
    const excludeDirs = cfgData.exclude_dirs;
    if (Array.isArray(excludeDirs) && excludeDirs.length > 0) {
      excludeArgs.push("--exclude", excludeDirs.join(","));
    }

    const [ov, q, l, t, pl, nt, tel, gi] = await Promise.all([
      safe(() => runTempo(path, "overview", excludeArgs), emptyResult),
      safe(() => runTempo(path, "quality", excludeArgs), emptyResult),
      safe(() => runTempo(path, "learn", excludeArgs), emptyResult),
      safe(() => runTempo(path, "token_stats", excludeArgs), emptyResult),
      safe(() => runTempo(path, "plugins", excludeArgs), emptyResult),
      safe(() => listNotes(path), []),
      safe(() => readTelemetry(path), emptyResult),
      safe(() => gitInfo(path), emptyResult),
    ]);

    cacheRef.current[path] = {
      overview: ov,
      quality: q,
      learning: l,
      tokens: t,
      plugins: parsePlugins(pl.output || ""),
      notes: (Array.isArray(nt) ? nt : []) as NoteEntry[],
      telemetry: (tel as TempoResult).output || "",
      config: cfgData as Record<string, unknown>,
      git: (gi as TempoResult).output || "",
      loaded: true,
    };
    setFileBrowserPath(path);
    const entries = await listDir(path);
    setFileBrowserEntries(Array.isArray(entries) ? entries as DirEntry[] : []);
    setFileViewContent(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (repoPath) loadAll(repoPath);
  }, [repoPath, loadAll]);

  useEffect(() => {
    getHomeDir().then(setHomeDir);
  }, []);

  const ws = getWsData(repoPath);

  const togglePlugin = async (name: string, on: boolean) => {
    const disabled: string[] = (ws.config.disabled_plugins as string[]) || [];
    const enabled: string[] = (ws.config.enabled_plugins as string[]) || [];
    const newConfig = {
      ...ws.config,
      disabled_plugins: on ? [...disabled.filter((n) => n !== name), name] : disabled.filter((n) => n !== name),
      enabled_plugins: on ? enabled.filter((n) => n !== name) : [...enabled.filter((n) => n !== name), name],
    };
    setWsData(repoPath, { config: newConfig });
    await writeConfig(repoPath, newConfig);
    const pl = await runTempo(repoPath, "plugins");
    setWsData(repoPath, { plugins: parsePlugins(pl.output) });
  };

  const saveConfig = async () => {
    await writeConfig(repoPath, ws.config);
    setConfigDirty(false);
  };

  const updateConfig = (key: string, val: unknown) => {
    setWsData(repoPath, { config: { ...ws.config, [key]: val } });
    setConfigDirty(true);
  };

  const openNote = async (path: string, name: string) => {
    const r = await readFile(path);
    setNoteContent(r.output);
    setNoteName(name);
  };

  const handleNoteCreated = async () => {
    const nt = await listNotes(repoPath);
    setWsData(repoPath, { notes: (nt || []) as NoteEntry[] });
  };

  const browseTo = async (dirPath: string) => {
    setFileBrowserPath(dirPath);
    setFileViewContent(null);
    const entries = await listDir(dirPath);
    setFileBrowserEntries(Array.isArray(entries) ? entries as DirEntry[] : []);
  };

  const viewFile = async (filePath: string, name: string) => {
    const r = await readFile(filePath);
    setFileViewContent(r.output);
    setFileViewName(name);
  };

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
        onSelect={(i) => { setActiveIdx(i); setNoteContent(null); }}
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
            onNoteBack={() => setNoteContent(null)}
            onFileViewBack={() => setFileViewContent(null)}
            onBrowseTo={browseTo}
            onViewFile={viewFile}
            onNoteCreated={handleNoteCreated}
          />
        </ErrorBoundary>
      </div>
    </div>
  );
}
