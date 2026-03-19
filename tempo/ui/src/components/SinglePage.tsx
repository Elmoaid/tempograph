import { useState, useEffect, useRef, useCallback } from "react";
import {
  RefreshCw, FolderOpen, Plus, X, GitBranch,
  FileText, Folder, ChevronRight, PenLine, Shield,
  PanelRightClose, PanelRight,
} from "lucide-react";
import {
  runTempo, readConfig, writeConfig, listNotes, readFile, readTelemetry,
  gitInfo, listDir, writeNote,
} from "./tempo";
import { ClaudePanel } from "./ClaudePanel";
import { ModeRunner } from "./ModeRunner";
import { QualityPanel } from "./QualityPanel";
import { PluginPanel } from "./PluginPanel";
import { SettingsPanel } from "./SettingsPanel";
import { ErrorBoundary } from "./ErrorBoundary";
import type { PluginInfo } from "./PluginPanel";
import type { TempoResult } from "../App";

interface Props {
  repoPath: string;
  workspaces: string[];
  activeIdx: number;
  setActiveIdx: (i: number) => void;
  addWorkspace: (path: string) => void;
  removeWorkspace: (i: number) => void;
}

interface WorkspaceData {
  overview: TempoResult | null;
  quality: TempoResult | null;
  learning: TempoResult | null;
  tokens: TempoResult | null;
  plugins: PluginInfo[];
  notes: NoteEntry[];
  telemetry: string;
  config: Record<string, unknown>;
  git: string;
  loaded: boolean;
}

interface DirEntry { name: string; path: string; is_dir: boolean; size: number; modified: string | null; }
interface NoteEntry { name: string; path: string; size: number; modified: string | null; }

interface FeedbackSummary {
  total: number;
  helpful: number;
  recentNotes: { mode: string; helpful: boolean; note: string; ts: string }[];
  byMode: Record<string, { total: number; helpful: number }>;
}

function parseFeedback(telemetry: string): FeedbackSummary | null {
  const section = telemetry.split("=== feedback.jsonl")[1];
  if (!section) return null;
  const lines = section.split("\n").filter(l => l.trim().startsWith("{"));
  if (lines.length === 0) return null;
  const entries = lines.map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  const helpful = entries.filter((e: { helpful: boolean }) => e.helpful).length;
  const byMode: Record<string, { total: number; helpful: number }> = {};
  entries.forEach((e: { mode: string; helpful: boolean; note?: string; ts?: string }) => {
    if (!byMode[e.mode]) byMode[e.mode] = { total: 0, helpful: 0 };
    byMode[e.mode].total++;
    if (e.helpful) byMode[e.mode].helpful++;
  });
  const recentNotes = entries.filter((e: { note?: string }) => e.note).slice(-5).map((e: { mode: string; helpful: boolean; note: string; ts: string }) => ({
    mode: e.mode, helpful: e.helpful, note: e.note, ts: e.ts,
  }));
  return { total: entries.length, helpful, byMode, recentNotes };
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

export function SinglePage({ repoPath, workspaces, activeIdx, setActiveIdx, addWorkspace, removeWorkspace }: Props) {
  const [loading, setLoading] = useState(false);
  const [showClaude, setShowClaude] = useState(false);
  const [rightHidden, setRightHidden] = useState(() =>
    localStorage.getItem("tempo-right-hidden") === "true"
  );
  const [addingWs, setAddingWs] = useState(false);
  const [newWsPath, setNewWsPath] = useState("");
  const addInputRef = useRef<HTMLInputElement>(null);

  const cacheRef = useRef<Record<string, WorkspaceData>>({});

  const [configDirty, setConfigDirty] = useState(false);

  const [noteContent, setNoteContent] = useState<string | null>(null);
  const [noteName, setNoteName] = useState("");
  const [creatingNote, setCreatingNote] = useState(false);
  const [newNoteName, setNewNoteName] = useState("");
  const [newNoteContent, setNewNoteContent] = useState("");

  const [fileBrowserPath, setFileBrowserPath] = useState("");
  const [fileBrowserEntries, setFileBrowserEntries] = useState<DirEntry[]>([]);
  const [fileViewContent, setFileViewContent] = useState<string | null>(null);
  const [fileViewName, setFileViewName] = useState("");

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
    if (addingWs) addInputRef.current?.focus();
  }, [addingWs]);

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

  const handleCreateNote = async () => {
    if (!newNoteName.trim() || !repoPath) return;
    const fname = newNoteName.endsWith(".md") ? newNoteName : `${newNoteName}.md`;
    await writeNote(repoPath, fname, newNoteContent);
    setCreatingNote(false);
    setNewNoteName("");
    setNewNoteContent("");
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

  const handleAddWs = () => {
    if (newWsPath.trim()) {
      addWorkspace(newWsPath.trim());
      setNewWsPath("");
      setAddingWs(false);
    }
  };

  const folderName = (p: string) => p.split("/").filter(Boolean).pop() || p;
  const stats = ws.overview ? parseStats(ws.overview.output) : null;

  if (workspaces.length === 0 && !loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", flexDirection: "column", gap: 16 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: "var(--accent)" }}>Tempo</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <FolderOpen size={16} color="var(--text-tertiary)" />
          <input className="input" placeholder="/path/to/repo" value={newWsPath} onChange={(e) => setNewWsPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleAddWs()} style={{ width: 400 }} autoFocus />
          <button className="btn" onClick={handleAddWs}>Add Workspace</button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* Top bar */}
      <div className="topbar">
        <span className="topbar-brand">Tempo</span>
        {stats && (
          <div style={{ display: "flex", gap: 16, marginLeft: 12, fontSize: 12, color: "var(--text-secondary)" }}>
            <span><strong style={{ color: "var(--text-primary)" }}>{stats.files}</strong> files</span>
            <span><strong style={{ color: "var(--text-primary)" }}>{stats.symbols}</strong> symbols</span>
            <span><strong style={{ color: "var(--text-primary)" }}>{stats.lines}</strong> lines</span>
          </div>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            className={`btn ${showClaude ? "" : "btn-ghost"}`}
            onClick={() => setShowClaude(!showClaude)}
            style={{ padding: "4px 8px", fontSize: 11 }}
            title="Claude Code Config"
          >
            <Shield size={12} /> Claude
          </button>
          <button
            className="btn btn-ghost"
            onClick={() => {
              const next = !rightHidden;
              setRightHidden(next);
              localStorage.setItem("tempo-right-hidden", String(next));
            }}
            style={{ padding: "4px 8px" }}
            title={rightHidden ? "Show info panel" : "Hide info panel"}
          >
            {rightHidden ? <PanelRight size={12} /> : <PanelRightClose size={12} />}
          </button>
          <button className="btn btn-ghost" onClick={() => loadAll(repoPath, true)} disabled={loading} style={{ padding: "4px 8px" }}>
            <RefreshCw size={12} className={loading ? "spin" : ""} />
          </button>
        </div>
      </div>

      {/* Workspace tabs */}
      <div className="ws-strip">
        {workspaces.map((w, i) => (
          <button
            key={w}
            className={`ws-tab ${i === activeIdx ? "active" : ""}`}
            onClick={() => { setActiveIdx(i); setNoteContent(null); }}
            title={w}
          >
            <FolderOpen size={12} />
            <span className="ws-tab-name">{folderName(w)}</span>
            {i === activeIdx && loading && <RefreshCw size={10} className="spin" />}
            <span className="ws-tab-close" onClick={(e) => { e.stopPropagation(); removeWorkspace(i); }}>
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
                if (e.key === "Enter") handleAddWs();
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
      </div>

      {showClaude && <ClaudePanel onClose={() => setShowClaude(false)} workspaces={workspaces} />}

      {/* 3-column matrix */}
      <div className={`grid-shell${rightHidden ? " right-hidden" : ""}`} style={{ display: showClaude ? "none" : undefined }}>
        {/* COLUMN 1: Mode runner (self-contained, resets on workspace switch) */}
        <ErrorBoundary label="Modes">
          <ModeRunner
            key={repoPath}
            repoPath={repoPath}
            excludeDirs={Array.isArray(ws.config.exclude_dirs) ? ws.config.exclude_dirs as string[] : undefined}
          />
        </ErrorBoundary>

        {/* COLUMN 2: Quality + Plugins + Settings */}
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

        {/* COLUMN 3: Git + Files + Notes + Feedback + Learning */}
        <ErrorBoundary label="Info panels">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="cell" style={{ flex: "0 0 auto" }}>
            <div className="cell-head"><GitBranch size={11} /> Git</div>
            <div className="cell-body">
              {ws.git ? (
                <pre className="output" style={{ maxHeight: 130, fontSize: 10 }}>{ws.git}</pre>
              ) : <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No git data</div>}
            </div>
          </div>

          <div className="cell" style={{ flex: "0 0 auto" }}>
            <div className="cell-head">
              <Folder size={11} /> Files
              <span style={{ marginLeft: 6, fontSize: 9, color: "var(--text-tertiary)", fontWeight: 400 }}>
                {fileBrowserPath.replace(repoPath, ".") || "."}
              </span>
            </div>
            <div className="cell-body">
              {fileViewContent !== null ? (
                <div>
                  <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                    <button className="btn btn-ghost" onClick={() => setFileViewContent(null)} style={{ padding: "2px 8px", fontSize: 10 }}>Back</button>
                    <span style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: "24px" }}>{fileViewName}</span>
                  </div>
                  <pre className="output" style={{ maxHeight: 200, fontSize: 10 }}>{fileViewContent}</pre>
                </div>
              ) : (
                <div style={{ maxHeight: 160, overflowY: "auto" }}>
                  {fileBrowserPath !== repoPath && (
                    <div className="file-row" onClick={() => browseTo(fileBrowserPath.split("/").slice(0, -1).join("/"))}>
                      <ChevronRight size={10} style={{ transform: "rotate(180deg)" }} />
                      <span style={{ color: "var(--text-tertiary)" }}>..</span>
                    </div>
                  )}
                  {fileBrowserEntries.map((e) => (
                    <div
                      key={e.path}
                      className="file-row"
                      onClick={() => e.is_dir ? browseTo(e.path) : viewFile(e.path, e.name)}
                    >
                      {e.is_dir ? <Folder size={11} color="var(--accent)" /> : <FileText size={11} />}
                      <span className={e.is_dir ? "file-row-dir" : ""}>{e.name}</span>
                      {!e.is_dir && <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-tertiary)" }}>{(e.size / 1024).toFixed(1)}K</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="cell" style={{ flex: "0 0 auto" }}>
            <div className="cell-head">
              Notes ({ws.notes.filter(n => n.name.endsWith(".md")).length})
              <button className="btn btn-ghost" onClick={() => setCreatingNote(!creatingNote)} style={{ marginLeft: "auto", padding: "1px 6px", fontSize: 10 }}>
                <PenLine size={10} /> New
              </button>
            </div>
            <div className="cell-body">
              {creatingNote ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <input className="input" placeholder="note-name.md" value={newNoteName} onChange={(e) => setNewNoteName(e.target.value)} style={{ fontSize: 11 }} />
                  <textarea className="input" placeholder="Note content..." value={newNoteContent} onChange={(e) => setNewNoteContent(e.target.value)} rows={4} style={{ fontSize: 11, resize: "vertical", fontFamily: "var(--font-mono)" }} />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn" onClick={handleCreateNote} style={{ padding: "3px 10px", fontSize: 10 }}>Save Note</button>
                    <button className="btn btn-ghost" onClick={() => setCreatingNote(false)} style={{ padding: "3px 10px", fontSize: 10 }}>Cancel</button>
                  </div>
                </div>
              ) : noteContent !== null ? (
                <div>
                  <button className="btn btn-ghost" onClick={() => setNoteContent(null)} style={{ marginBottom: 6, padding: "2px 8px", fontSize: 10 }}>Back</button>
                  <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4, color: "var(--text-primary)" }}>{noteName}</div>
                  <pre className="output" style={{ maxHeight: 150 }}>{noteContent}</pre>
                </div>
              ) : ws.notes.filter(n => n.name.endsWith(".md")).length > 0 ? (
                ws.notes.filter(n => n.name.endsWith(".md")).map((n) => (
                  <div key={n.path} className="note-item" onClick={() => openNote(n.path, n.name)}>
                    <div className="note-item-title">{n.name.replace(".md", "")}</div>
                    <div className="note-item-meta">{(n.size / 1024).toFixed(1)} KB</div>
                  </div>
                ))
              ) : (
                <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No notes — click "New" to create one</div>
              )}
            </div>
          </div>

          {(() => {
            const fb = parseFeedback(ws.telemetry);
            if (!fb) return null;
            const rate = fb.total > 0 ? Math.round((fb.helpful / fb.total) * 100) : 0;
            return (
              <div className="cell" style={{ flex: "0 0 auto" }}>
                <div className="cell-head">Agent Feedback ({fb.total})</div>
                <div className="cell-body">
                  <div style={{ display: "flex", gap: 12, marginBottom: 8, fontSize: 11 }}>
                    <span style={{ color: rate >= 70 ? "var(--success)" : rate >= 40 ? "var(--warning)" : "var(--error)" }}>
                      {rate}% helpful
                    </span>
                    <span style={{ color: "var(--text-tertiary)" }}>{fb.helpful}/{fb.total} reports</span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                    {Object.entries(fb.byMode).sort((a, b) => b[1].total - a[1].total).slice(0, 6).map(([mode, data]) => (
                      <span key={mode} style={{
                        fontSize: 9, padding: "2px 6px", borderRadius: 4,
                        background: "var(--bg-active)",
                        color: data.helpful / data.total >= 0.7 ? "var(--success)" : "var(--text-secondary)"
                      }}>
                        {mode} {Math.round((data.helpful / data.total) * 100)}%
                      </span>
                    ))}
                  </div>
                  {fb.recentNotes.length > 0 && (
                    <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>
                      {fb.recentNotes.map((n, i) => (
                        <div key={i} style={{ marginBottom: 3, display: "flex", gap: 4 }}>
                          <span style={{ color: n.helpful ? "var(--success)" : "var(--error)", flexShrink: 0 }}>{n.helpful ? "+" : "−"}</span>
                          <span style={{ color: "var(--text-tertiary)", flexShrink: 0 }}>[{n.mode}]</span>
                          <span>{n.note}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })()}

          <div className="cell" style={{ flex: 1 }}>
            <div className="cell-head">Learning &amp; Tokens</div>
            <div className="cell-body">
              {ws.learning && (
                <pre className="output" style={{ maxHeight: 100, marginBottom: 8, fontSize: 10 }}>{ws.learning.output}</pre>
              )}
              {ws.tokens && (
                <pre className="output" style={{ maxHeight: 100, fontSize: 10 }}>{ws.tokens.output}</pre>
              )}
              {!ws.learning && !ws.tokens && <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No data</div>}
            </div>
          </div>
        </div>
        </ErrorBoundary>
      </div>
    </div>
  );
}
