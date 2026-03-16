import { useState, useEffect, useRef, useCallback } from "react";
import {
  Eye, Crosshair, Bomb, Skull, Flame, GitBranch,
  Package, Layers, Hash, Map, Brain, Gauge, BookOpen, Coins,
  Play, Copy, Check, RefreshCw, FolderOpen, Plus, X,
  Save, FileText, Folder, ChevronRight, PenLine, Shield,
  Search, BarChart3, Zap,
} from "lucide-react";
import {
  runTempo, readConfig, writeConfig, listNotes, readFile, readTelemetry,
  gitInfo, listDir, writeNote, saveOutput,
} from "./tempo";
import { ClaudePanel } from "./ClaudePanel";
import type { TempoResult } from "../App";
import type { ComponentType } from "react";

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

interface ModeInfo {
  mode: string;
  label: string;
  icon: ComponentType<{ size?: number }>;
  tag: string;
}

const MODES: ModeInfo[] = [
  { mode: "prepare", label: "Prepare Context", icon: Zap, tag: "mcp" },
  { mode: "overview", label: "Overview", icon: Eye, tag: "mcp" },
  { mode: "focus", label: "Focus", icon: Crosshair, tag: "mcp" },
  { mode: "lookup", label: "Lookup", icon: Search, tag: "mcp" },
  { mode: "blast", label: "Blast Radius", icon: Bomb, tag: "mcp" },
  { mode: "hotspots", label: "Hotspots", icon: Flame, tag: "mcp" },
  { mode: "diff", label: "Diff Context", icon: GitBranch, tag: "mcp" },
  { mode: "dead_code", label: "Dead Code", icon: Skull, tag: "mcp" },
  { mode: "symbols", label: "Symbols", icon: Hash, tag: "mcp" },
  { mode: "map", label: "File Map", icon: Map, tag: "mcp" },
  { mode: "deps", label: "Dependencies", icon: Package, tag: "mcp" },
  { mode: "arch", label: "Architecture", icon: Layers, tag: "mcp" },
  { mode: "stats", label: "Stats", icon: BarChart3, tag: "mcp" },
  { mode: "context", label: "Context Engine", icon: Brain, tag: "ai" },
  { mode: "quality", label: "Quality Score", icon: Gauge, tag: "ai" },
  { mode: "learn", label: "Learning", icon: BookOpen, tag: "mcp" },
  { mode: "token_stats", label: "Token Stats", icon: Coins, tag: "ai" },
];

interface PluginInfo { name: string; enabled: boolean; description: string; }
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
  const summary: FeedbackSummary = { total: 0, helpful: 0, recentNotes: [], byMode: {} };
  for (const e of entries) {
    summary.total++;
    if (e.helpful) summary.helpful++;
    const mode = e.mode || "unknown";
    if (!summary.byMode[mode]) summary.byMode[mode] = { total: 0, helpful: 0 };
    summary.byMode[mode].total++;
    if (e.helpful) summary.byMode[mode].helpful++;
    if (e.note) summary.recentNotes.push({ mode, helpful: e.helpful, note: e.note, ts: e.ts || e.timestamp || "" });
  }
  summary.recentNotes = summary.recentNotes.slice(-5).reverse();
  return summary.total > 0 ? summary : null;
}

function parsePlugins(output: string): PluginInfo[] {
  return output.split("\n").reduce<PluginInfo[]>((acc, line) => {
    const m = line.match(/^\s*(?:\[([x ])\]|([●○]))\s+(\w+)\s*[-—]\s*(.+)/);
    if (m) acc.push({ name: m[3], enabled: m[1] === "x" || m[2] === "●", description: m[4].trim() });
    return acc;
  }, []);
}

function parseQuality(output: string) {
  const overall = parseInt(output.match(/Quality Score:\s*(\d+)/)?.[1] || "0", 10);
  const parse = (n: string) => {
    const m = output.match(new RegExp(`${n}:\\s*(\\d+)/100\\s*\\((.+?)\\)`));
    return m ? { score: parseInt(m[1], 10), detail: m[2] } : { score: 0, detail: "" };
  };
  return { overall, minimality: parse("Minimality"), simplicity: parse("Simplicity"), independence: parse("Independence"), convention: parse("Convention") };
}

function parseStats(output: string) {
  return {
    files: output.match(/(\d+)\s*files/)?.[1] || "-",
    symbols: output.match(/(\d+)\s*symbols/)?.[1] || "-",
    lines: output.match(/([\d,]+)\s*lines/)?.[1] || "-",
  };
}

function ScoreBar({ label, score }: { label: string; score: number }) {
  const color = score >= 75 ? "var(--success)" : score >= 50 ? "var(--warning)" : "var(--error)";
  return (
    <div className="score-bar-row">
      <span className="score-bar-label">{label}</span>
      <div className="score-bar-track">
        <div className="score-bar-fill" style={{ width: `${score}%`, background: color }} />
      </div>
      <span className="score-bar-num" style={{ color }}>{score}</span>
    </div>
  );
}

const EMPTY_WS: WorkspaceData = {
  overview: null, quality: null, learning: null, tokens: null,
  plugins: [], notes: [], telemetry: "", config: {}, git: "", loaded: false,
};

export function SinglePage({ repoPath, workspaces, activeIdx, setActiveIdx, addWorkspace, removeWorkspace }: Props) {
  const [loading, setLoading] = useState(false);
  const [showClaude, setShowClaude] = useState(false);
  const [addingWs, setAddingWs] = useState(false);
  const [newWsPath, setNewWsPath] = useState("");
  const addInputRef = useRef<HTMLInputElement>(null);

  // Per-workspace cached data
  const cacheRef = useRef<Record<string, WorkspaceData>>({});

  // Mode runner (shared across workspaces)
  const [activeMode, setActiveMode] = useState("overview");
  const [modeArgs, setModeArgs] = useState("");
  const [modeOutput, setModeOutput] = useState("");
  const [modeRunning, setModeRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [configDirty, setConfigDirty] = useState(false);

  // Notes viewer + editor
  const [noteContent, setNoteContent] = useState<string | null>(null);
  const [noteName, setNoteName] = useState("");
  const [creatingNote, setCreatingNote] = useState(false);
  const [newNoteName, setNewNoteName] = useState("");
  const [newNoteContent, setNewNoteContent] = useState("");

  // File browser
  const [fileBrowserPath, setFileBrowserPath] = useState("");
  const [fileBrowserEntries, setFileBrowserEntries] = useState<DirEntry[]>([]);
  const [fileViewContent, setFileViewContent] = useState<string | null>(null);
  const [fileViewName, setFileViewName] = useState("");

  const getWsData = useCallback((path: string): WorkspaceData => {
    return cacheRef.current[path] || EMPTY_WS;
  }, []);

  const setWsData = useCallback((path: string, data: Partial<WorkspaceData>) => {
    cacheRef.current[path] = { ...getWsData(path), ...data };
  }, [getWsData]);

  const loadAll = useCallback(async (path: string, force = false) => {
    if (!path) return;
    if (!force && cacheRef.current[path]?.loaded) return;
    setLoading(true);
    setModeOutput("");
    setNoteContent(null);

    const safe = async <T,>(fn: () => Promise<T>, fallback: T): Promise<T> => {
      try { return await fn(); } catch { return fallback; }
    };
    const emptyResult: TempoResult = { success: false, output: "", mode: "" };

    const [ov, q, l, t, pl, nt, tel, cfg, gi] = await Promise.all([
      safe(() => runTempo(path, "overview"), emptyResult),
      safe(() => runTempo(path, "quality"), emptyResult),
      safe(() => runTempo(path, "learn"), emptyResult),
      safe(() => runTempo(path, "token_stats"), emptyResult),
      safe(() => runTempo(path, "plugins"), emptyResult),
      safe(() => listNotes(path), []),
      safe(() => readTelemetry(path), emptyResult),
      safe(() => readConfig(path), { success: false, data: {}, path: "", error: "" }),
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
      config: (cfg as { success: boolean; data: Record<string, unknown> }).success
        ? ((cfg as { data: Record<string, unknown> }).data || {}) : {},
      git: (gi as TempoResult).output || "",
      loaded: true,
    };
    // Auto-load file browser at repo root
    setFileBrowserPath(path);
    const entries = await listDir(path);
    setFileBrowserEntries(Array.isArray(entries) ? entries as DirEntry[] : []);
    setFileViewContent(null);
    setLoading(false);
  }, []);

  // Load active workspace data on switch
  useEffect(() => {
    if (repoPath) loadAll(repoPath);
  }, [repoPath, loadAll]);

  // Focus add-workspace input
  useEffect(() => {
    if (addingWs) addInputRef.current?.focus();
  }, [addingWs]);

  const runMode = async () => {
    if (!repoPath) return;
    setModeRunning(true);
    setModeOutput("");
    const args = modeArgs.trim() ? modeArgs.trim().split(/\s+/) : [];
    const r = await runTempo(repoPath, activeMode, args);
    setModeOutput(r.output);
    setModeRunning(false);
  };

  const copyOutput = () => {
    navigator.clipboard.writeText(modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

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
    // Refresh notes
    const nt = await listNotes(repoPath);
    setWsData(repoPath, { notes: (nt || []) as NoteEntry[] });
  };

  const handleSaveOutput = async () => {
    if (!modeOutput || !repoPath) return;
    const outPath = `${repoPath}/.tempo/output-${activeMode}-${Date.now()}.txt`;
    await saveOutput(outPath, modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
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
  const q = ws.quality ? parseQuality(ws.quality.output) : null;
  const scoreColor = (n: number) => n >= 75 ? "c-good" : n >= 50 ? "c-warn" : "c-bad";

  // Empty state — no workspaces at all
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
      {/* Top bar with brand + stats */}
      <div className="topbar">
        <span className="topbar-brand">Tempo</span>
        {stats && (
          <div style={{ display: "flex", gap: 16, marginLeft: 12, fontSize: 12, color: "var(--text-secondary)" }}>
            <span><strong style={{ color: "var(--text-primary)" }}>{stats.files}</strong> files</span>
            <span><strong style={{ color: "var(--text-primary)" }}>{stats.symbols}</strong> symbols</span>
            <span><strong style={{ color: "var(--text-primary)" }}>{stats.lines}</strong> lines</span>
            {q && <span>quality: <strong className={scoreColor(q.overall)}>{q.overall}/100</strong></span>}
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
            onClick={() => { setActiveIdx(i); setModeOutput(""); setNoteContent(null); }}
            title={w}
          >
            <FolderOpen size={12} />
            <span className="ws-tab-name">{folderName(w)}</span>
            {i === activeIdx && loading && <RefreshCw size={10} className="spin" />}
            <span
              className="ws-tab-close"
              onClick={(e) => { e.stopPropagation(); removeWorkspace(i); }}
            >
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

      {/* Claude Code panel */}
      {showClaude && <ClaudePanel onClose={() => setShowClaude(false)} workspaces={workspaces} />}

      {/* 3-column matrix */}
      <div className="grid-shell" style={{ display: showClaude ? "none" : undefined }}>
        {/* COLUMN 1: Modes + Run */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="cell" style={{ flex: "0 0 auto", maxHeight: "45%" }}>
            <div className="cell-head">Modes ({MODES.length})</div>
            <div className="cell-body">
              {MODES.map((m) => (
                <button
                  key={m.mode}
                  className={`mode-row ${activeMode === m.mode ? "active" : ""}`}
                  onClick={() => { setActiveMode(m.mode); setModeOutput(""); }}
                >
                  <span className="mode-row-icon"><m.icon size={13} /></span>
                  <span className="mode-row-name">{m.label}</span>
                  <span className="mode-row-tag">{m.tag}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="cell" style={{ flex: 1 }}>
            <div className="cell-head">
              Run: {activeMode}
              <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                {modeOutput && (
                  <>
                    <button className="btn btn-ghost" onClick={handleSaveOutput} style={{ padding: "2px 6px", fontSize: 10 }} title="Save to .tempo/">
                      <Save size={10} />
                    </button>
                    <button className="btn btn-ghost" onClick={copyOutput} style={{ padding: "2px 6px", fontSize: 10 }}>
                      {copied ? <Check size={10} /> : <Copy size={10} />}
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="cell-body">
              <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                <input className="input" placeholder="--query X --file Y" value={modeArgs} onChange={(e) => setModeArgs(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runMode()} style={{ flex: 1 }} />
                <button className="btn" onClick={runMode} disabled={modeRunning} style={{ padding: "4px 10px" }}>
                  <Play size={11} /> {modeRunning ? "..." : "Run"}
                </button>
              </div>
              {modeOutput ? (
                <pre className="output" style={{ maxHeight: "calc(100% - 40px)", overflow: "auto" }}>{modeOutput}</pre>
              ) : (
                <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16, textAlign: "center" }}>
                  Click a mode and Run
                </div>
              )}
            </div>
          </div>
        </div>

        {/* COLUMN 2: Quality + Plugins + Config */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {q && (
            <div className="cell" style={{ flex: "0 0 auto" }}>
              <div className="cell-head">
                Quality
                <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700 }} className={scoreColor(q.overall)}>{q.overall}/100</span>
              </div>
              <div className="cell-body">
                <ScoreBar label="Minimality" score={q.minimality.score} />
                <ScoreBar label="Simplicity" score={q.simplicity.score} />
                <ScoreBar label="Independence" score={q.independence.score} />
                <ScoreBar label="Convention" score={q.convention.score} />
              </div>
            </div>
          )}

          <div className="cell" style={{ flex: "0 0 auto" }}>
            <div className="cell-head">Plugins ({ws.plugins.length})</div>
            <div className="cell-body">
              {ws.plugins.map((p) => (
                <div key={p.name} className="plugin-row">
                  <div className={`toggle ${p.enabled ? "on" : ""}`} onClick={() => togglePlugin(p.name, p.enabled)} />
                  <span className="plugin-name">{p.name}</span>
                  <span className="plugin-desc" title={p.description}>{p.description}</span>
                </div>
              ))}
              {ws.plugins.length === 0 && <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>{loading ? "Loading..." : "No plugins"}</div>}
            </div>
          </div>

          <div className="cell" style={{ flex: 1 }}>
            <div className="cell-head">
              Settings
              {configDirty && (
                <button className="btn" onClick={saveConfig} style={{ marginLeft: "auto", padding: "2px 8px", fontSize: 10 }}>Save</button>
              )}
            </div>
            <div className="cell-body">
              <div className="cfg-row">
                <span className="cfg-label">Max tokens</span>
                <input className="input" type="number" value={(ws.config.max_tokens as number) || 4000} onChange={(e) => updateConfig("max_tokens", parseInt(e.target.value) || 4000)} style={{ width: 80, textAlign: "right" }} />
              </div>
              <div className="cfg-row">
                <span className="cfg-label">Token budget</span>
                <select className="input" value={(ws.config.token_budget as string) || "auto"} onChange={(e) => updateConfig("token_budget", e.target.value)} style={{ width: 100 }}>
                  <option value="auto">Auto</option>
                  <option value="minimal">Minimal</option>
                  <option value="standard">Standard</option>
                  <option value="generous">Generous</option>
                </select>
              </div>
              <div className="cfg-row">
                <span className="cfg-label">Telemetry</span>
                <div className={`toggle ${ws.config.telemetry !== false ? "on" : ""}`} onClick={() => updateConfig("telemetry", !(ws.config.telemetry !== false))} />
              </div>
              <div className="cfg-row">
                <span className="cfg-label">Learning</span>
                <div className={`toggle ${ws.config.learning !== false ? "on" : ""}`} onClick={() => updateConfig("learning", !(ws.config.learning !== false))} />
              </div>
            </div>
          </div>
        </div>

        {/* COLUMN 3: Git + Files + Notes + Learning + Tokens */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {/* Git status */}
          <div className="cell" style={{ flex: "0 0 auto" }}>
            <div className="cell-head"><GitBranch size={11} /> Git</div>
            <div className="cell-body">
              {ws.git ? (
                <pre className="output" style={{ maxHeight: 130, fontSize: 10 }}>{ws.git}</pre>
              ) : <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No git data</div>}
            </div>
          </div>

          {/* File browser */}
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

          {/* Notes with create */}
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

          {/* Agent Feedback */}
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
                    <span style={{ color: "var(--text-tertiary)" }}>
                      {fb.helpful}/{fb.total} reports
                    </span>
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
                          <span style={{ color: n.helpful ? "var(--success)" : "var(--error)", flexShrink: 0 }}>
                            {n.helpful ? "+" : "−"}
                          </span>
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

          {/* Learning + Tokens combined */}
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
      </div>
    </div>
  );
}
