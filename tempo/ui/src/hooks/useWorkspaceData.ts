import { useState, useEffect, useRef, useCallback } from "react";
import {
  runTempo, readConfig, writeConfig, listNotes, readFile, readTelemetry,
  gitInfo, listDir,
} from "../components/tempo";
import type { PluginInfo } from "../components/PluginPanel";
import type { TempoResult } from "../App";
import type { WorkspaceData, DirEntry, NoteEntry } from "../components/workspaceTypes";

function parsePlugins(output: string): PluginInfo[] {
  return output.split("\n").map(line => {
    const m = line.match(/^\s*(?:\[([x ])\]|([●○]))\s+(\w+)\s*[-—]\s*(.+)/);
    if (!m) return null;
    return { enabled: m[1] === "x" || m[2] === "●", name: m[3], description: m[4].trim() };
  }).filter(Boolean) as PluginInfo[];
}

const emptyWs = (): WorkspaceData => ({
  overview: null, quality: null, learning: null, tokens: null,
  plugins: [], notes: [], telemetry: "", config: {}, git: "", loaded: false,
});

export function useWorkspaceData(repoPath: string) {
  const [loading, setLoading] = useState(false);
  const [configDirty, setConfigDirty] = useState(false);
  const [noteContent, setNoteContent] = useState<string | null>(null);
  const [noteName, setNoteName] = useState("");
  const [fileBrowserPath, setFileBrowserPath] = useState("");
  const [fileBrowserEntries, setFileBrowserEntries] = useState<DirEntry[]>([]);
  const [fileViewContent, setFileViewContent] = useState<string | null>(null);
  const [fileViewName, setFileViewName] = useState("");

  const cacheRef = useRef<Record<string, WorkspaceData>>({});

  const getWs = useCallback((path: string): WorkspaceData => {
    return cacheRef.current[path] || emptyWs();
  }, []);

  const setWs = useCallback((path: string, data: Partial<WorkspaceData>) => {
    cacheRef.current[path] = { ...getWs(path), ...data };
  }, [getWs]);

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

  const togglePlugin = async (name: string, on: boolean) => {
    const ws = getWs(repoPath);
    const disabled: string[] = (ws.config.disabled_plugins as string[]) || [];
    const enabled: string[] = (ws.config.enabled_plugins as string[]) || [];
    const newConfig = {
      ...ws.config,
      disabled_plugins: on ? [...disabled.filter((n) => n !== name), name] : disabled.filter((n) => n !== name),
      enabled_plugins: on ? enabled.filter((n) => n !== name) : [...enabled.filter((n) => n !== name), name],
    };
    setWs(repoPath, { config: newConfig });
    await writeConfig(repoPath, newConfig);
    const pl = await runTempo(repoPath, "plugins");
    setWs(repoPath, { plugins: parsePlugins(pl.output) });
  };

  const saveConfig = async () => {
    await writeConfig(repoPath, getWs(repoPath).config);
    setConfigDirty(false);
  };

  const updateConfig = (key: string, val: unknown) => {
    setWs(repoPath, { config: { ...getWs(repoPath).config, [key]: val } });
    setConfigDirty(true);
  };

  const openNote = async (path: string, name: string) => {
    const r = await readFile(path);
    setNoteContent(r.output);
    setNoteName(name);
  };

  const handleNoteCreated = async () => {
    const nt = await listNotes(repoPath);
    setWs(repoPath, { notes: (nt || []) as NoteEntry[] });
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

  return {
    loading,
    configDirty,
    noteContent,
    noteName,
    fileBrowserPath,
    fileBrowserEntries,
    fileViewContent,
    fileViewName,
    ws: getWs(repoPath),
    loadAll,
    togglePlugin,
    saveConfig,
    updateConfig,
    openNote,
    handleNoteCreated,
    browseTo,
    viewFile,
    clearNoteContent: () => setNoteContent(null),
    clearFileView: () => setFileViewContent(null),
  };
}
