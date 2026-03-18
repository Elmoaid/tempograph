import { useState, useEffect, useRef, useMemo } from "react";
import {
  Eye, Crosshair, Bomb, Skull, Flame, GitBranch,
  Package, Layers, Hash, Map as MapIcon, Brain, Gauge, BookOpen, Coins,
  Play, Copy, Check, Save, Search, BarChart3, Zap,
  ThumbsUp, ThumbsDown, X,
} from "lucide-react";
import { runTempo, saveOutput, reportFeedback } from "./tempo";
import { CommandPalette } from "./CommandPalette";
import type { ComponentType } from "react";

interface ModeInfo {
  mode: string;
  label: string;
  icon: ComponentType<{ size?: number }>;
  tag: string;
  hint?: string;
  argPrefix?: string;
  desc?: string;
}

const MODES: ModeInfo[] = [
  { mode: "prepare", label: "Prepare Context", icon: Zap, tag: "mcp", hint: "describe your task", argPrefix: "--query", desc: "Optimized context snapshot for your task — paste directly into Claude." },
  { mode: "overview", label: "Overview", icon: Eye, tag: "mcp", desc: "High-level summary: languages, top files, key symbols, recent activity." },
  { mode: "focus", label: "Focus", icon: Crosshair, tag: "mcp", hint: "what are you working on?", argPrefix: "--query", desc: "Deep-dive into a symbol or task area — BFS from entry point, depth 3." },
  { mode: "lookup", label: "Lookup", icon: Search, tag: "mcp", hint: "where is X? / what calls X?", argPrefix: "--query", desc: "Find where a symbol is defined and what calls it across the codebase." },
  { mode: "blast", label: "Blast Radius", icon: Bomb, tag: "mcp", hint: "symbol or file path", argPrefix: "--query", desc: "Show all files/symbols that would be affected if this symbol changes." },
  { mode: "hotspots", label: "Hotspots", icon: Flame, tag: "mcp", desc: "Files with the most cross-module dependencies — highest refactor risk." },
  { mode: "diff", label: "Diff Context", icon: GitBranch, tag: "mcp", hint: "file1.py,file2.py (blank = unstaged)", argPrefix: "--file", desc: "Context around changed files — what else could break from this diff." },
  { mode: "dead_code", label: "Dead Code", icon: Skull, tag: "mcp", desc: "Symbols with no callers — ranked by confidence. Review before deleting." },
  { mode: "symbols", label: "Symbols", icon: Hash, tag: "mcp", desc: "All exported symbols in the repo with types, locations, and caller counts." },
  { mode: "map", label: "File Map", icon: MapIcon, tag: "mcp", desc: "Directory tree with file sizes and symbol counts — codebase topology." },
  { mode: "deps", label: "Dependencies", icon: Package, tag: "mcp", desc: "Import graph: which modules depend on which, and circular dependency detection." },
  { mode: "arch", label: "Architecture", icon: Layers, tag: "mcp", desc: "Layer diagram inferred from import patterns — entry points to leaf modules." },
  { mode: "stats", label: "Stats", icon: BarChart3, tag: "mcp", desc: "Token counts, file sizes, language breakdown — input budget awareness." },
  { mode: "context", label: "Context Engine", icon: Brain, tag: "ai", desc: "AI-ranked context: most relevant files for the current task." },
  { mode: "quality", label: "Quality Score", icon: Gauge, tag: "ai", desc: "Code health metrics: complexity, coupling, test coverage signals." },
  { mode: "learn", label: "Learning", icon: BookOpen, tag: "mcp", desc: "Patterns learned from past feedback — what worked, what to avoid." },
  { mode: "token_stats", label: "Token Stats", icon: Coins, tag: "ai", desc: "Per-mode token usage history — optimize your context budget." },
];

interface Props {
  repoPath: string;
  excludeDirs?: string[];
}

const HISTORY_MAX = 8;
const historyKey = (mode: string) => `tempo-history-${mode}`;
const loadHistory = (mode: string): string[] => {
  try { return JSON.parse(localStorage.getItem(historyKey(mode)) || "[]"); } catch { return []; }
};
const saveHistory = (mode: string, query: string) => {
  const prev = loadHistory(mode).filter(q => q !== query);
  localStorage.setItem(historyKey(mode), JSON.stringify([query, ...prev].slice(0, HISTORY_MAX)));
};

export function ModeRunner({ repoPath, excludeDirs }: Props) {
  const [activeMode, setActiveMode] = useState("overview");
  const [modeArgs, setModeArgs] = useState("");
  const [modeOutput, setModeOutput] = useState("");
  const [modeRunning, setModeRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  // Per-mode feedback tracking (null=not yet, true=helpful, false=unhelpful)
  const feedbackGiven = useRef<Map<string, boolean>>(new Map<string, boolean>());
  const [feedbackMode, setFeedbackMode] = useState<string | null>(null);
  const argsInputRef = useRef<HTMLInputElement>(null);
  const [outputFilter, setOutputFilter] = useState("");
  const [filterVisible, setFilterVisible] = useState(false);
  const filterInputRef = useRef<HTMLInputElement>(null);
  // Per-mode result cache: avoids re-running when switching back to a mode
  const outputCache = useRef<Map<string, string>>(new Map());

  const activeModeInfo = MODES.find(m => m.mode === activeMode);

  const filteredOutput = useMemo(() => {
    if (!outputFilter.trim() || !modeOutput) return modeOutput;
    const q = outputFilter.toLowerCase();
    return modeOutput
      .split("\n")
      .filter(line => line.toLowerCase().includes(q))
      .join("\n");
  }, [modeOutput, outputFilter]);

  const filterMatchCount = useMemo(() => {
    if (!outputFilter.trim() || !modeOutput) return null;
    const q = outputFilter.toLowerCase();
    return modeOutput.split("\n").filter(l => l.toLowerCase().includes(q)).length;
  }, [modeOutput, outputFilter]);

  const switchMode = (mode: string) => {
    setActiveMode(mode);
    setModeArgs("");
    setHistoryOpen(false);
    setOutputFilter("");
    setFilterVisible(false);
    setHistory(loadHistory(mode));
    const cached = outputCache.current.get(mode);
    setModeOutput(cached ?? "");
    // Auto-run arg-free modes if no cached result
    if (!cached && !MODES.find(m => m.mode === mode)?.argPrefix) {
      setTimeout(() => runModeRef.current?.(), 0);
    }
  };

  // Keyboard shortcuts: Cmd+K = palette, Cmd+R = run, Cmd+F = filter, Cmd+1-9 = switch mode
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.metaKey && !e.ctrlKey) return;
      if (e.key === "k") { e.preventDefault(); setPaletteOpen(true); }
      if (e.key === "r" && !modeRunning) { e.preventDefault(); runModeRef.current?.(); }
      if (e.key === "f" && modeOutput) {
        e.preventDefault();
        setFilterVisible(v => { if (!v) setTimeout(() => filterInputRef.current?.focus(), 50); return true; });
      }
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= 9 && n <= MODES.length) { e.preventDefault(); switchMode(MODES[n - 1].mode); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [modeRunning, modeOutput]);

  // Stable ref so the keydown closure always calls the latest runMode
  const runModeRef = useRef<(() => void) | null>(null);

  // Auto-run overview when workspace mounts (component is keyed by repoPath so remounts on switch)
  useEffect(() => { runModeRef.current?.(); }, []);

  const runMode = async () => {
    if (!repoPath || modeRunning) return;
    setModeRunning(true);
    setModeOutput("");
    try {
      const args: string[] = [];
      const raw = modeArgs.trim();
      if (raw && activeModeInfo?.argPrefix && !raw.startsWith("--")) {
        args.push(activeModeInfo.argPrefix, raw);
      } else if (raw) {
        args.push(...raw.split(/\s+/));
      }
      if (excludeDirs && excludeDirs.length > 0 && !args.includes("--exclude")) {
        args.push("--exclude", excludeDirs.join(","));
      }
      const r = await runTempo(repoPath, activeMode, args);
      const out = r.output || "No output";
      outputCache.current.set(activeMode, out);
      setModeOutput(out);
      if (raw && activeModeInfo?.argPrefix) {
        saveHistory(activeMode, raw);
        setHistory(loadHistory(activeMode));
      }
    } catch {
      setModeOutput("Failed to run mode. Check that tempo is installed.");
    }
    setModeRunning(false);
  };
  runModeRef.current = runMode;

  const copyOutput = () => {
    navigator.clipboard.writeText(modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleSaveOutput = async () => {
    if (!modeOutput || !repoPath) return;
    const outPath = `${repoPath}/.tempo/output-${activeMode}-${Date.now()}.txt`;
    await saveOutput(outPath, modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const submitFeedback = async (helpful: boolean) => {
    if (feedbackGiven.current.has(activeMode)) return;
    feedbackGiven.current.set(activeMode, helpful);
    setFeedbackMode(activeMode); // trigger re-render to show "thanks"
    await reportFeedback(repoPath, activeMode, helpful);
  };

  return (
    <>
    {paletteOpen && (
      <CommandPalette
        modes={MODES}
        onSelect={(mode) => { switchMode(mode); setTimeout(() => argsInputRef.current?.focus(), 50); }}
        onClose={() => setPaletteOpen(false)}
      />
    )}
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="cell" style={{ flex: "0 0 auto", maxHeight: "45%" }}>
        <div className="cell-head">
          Modes ({MODES.length})
          <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-tertiary)", fontWeight: 400 }}>⌘K · ⌘F filter</span>
        </div>
        <div className="cell-body">
          {MODES.map((m) => (
            <button
              key={m.mode}
              className={`mode-row ${activeMode === m.mode ? "active" : ""}`}
              onClick={() => switchMode(m.mode)}
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
          {activeModeInfo?.label ?? activeMode}
          <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
            {modeOutput && (
              <>
                <button className="btn btn-ghost" onClick={() => { setFilterVisible(v => !v); setTimeout(() => filterInputRef.current?.focus(), 50); }} style={{ padding: "2px 6px", fontSize: 10 }} title="Filter output (⌘F)">
                  <Search size={10} />
                </button>
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
          {activeModeInfo?.desc && (
            <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 8, lineHeight: 1.5 }}>
              {activeModeInfo.desc}
            </div>
          )}
          <div style={{ display: "flex", gap: 6, marginBottom: 8, position: "relative" }}>
            <div style={{ flex: 1, position: "relative" }}>
              <input
                ref={argsInputRef}
                className="input"
                placeholder={activeModeInfo?.hint || "arguments (optional)"}
                value={modeArgs}
                onChange={(e) => setModeArgs(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { setHistoryOpen(false); runMode(); }
                  if (e.key === "Escape") setHistoryOpen(false);
                }}
                onFocus={() => { if (history.length > 0) setHistoryOpen(true); }}
                onBlur={() => setTimeout(() => setHistoryOpen(false), 150)}
                style={{ width: "100%" }}
              />
              {historyOpen && history.length > 0 && (
                <div style={{
                  position: "absolute", top: "100%", left: 0, right: 0, zIndex: 100,
                  background: "var(--bg-secondary)", border: "1px solid var(--border)",
                  borderRadius: 4, marginTop: 2, boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
                }}>
                  {history.map((q, i) => (
                    <div
                      key={i}
                      style={{ padding: "5px 10px", fontSize: 11, cursor: "pointer", color: "var(--text-secondary)" }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                      onMouseDown={() => { setModeArgs(q); setHistoryOpen(false); setTimeout(() => runModeRef.current?.(), 0); }}
                    >
                      {q}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <button className="btn" onClick={runMode} disabled={modeRunning} style={{ padding: "4px 10px" }} title="Run (⌘R)">
              <Play size={11} /> {modeRunning ? "..." : "Run"}
            </button>
          </div>
          {modeRunning ? (
            <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16, textAlign: "center" }}>
              <span style={{ animation: "pulse 1.2s ease-in-out infinite", display: "inline-block" }}>
                Running {activeMode}…
              </span>
            </div>
          ) : modeOutput ? (
            <>
              {activeMode === "prepare" && (
                <button
                  className="btn"
                  onClick={copyOutput}
                  style={{ width: "100%", marginBottom: 6, fontSize: 11, padding: "5px 0", justifyContent: "center" }}
                >
                  {copied ? <><Check size={11} /> Copied!</> : <><Copy size={11} /> Copy for Claude</>}
                </button>
              )}
              {filterVisible && (
                <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 4 }}>
                  <input
                    ref={filterInputRef}
                    className="input"
                    placeholder="Filter lines…"
                    value={outputFilter}
                    onChange={e => setOutputFilter(e.target.value)}
                    onKeyDown={e => { if (e.key === "Escape") { setFilterVisible(false); setOutputFilter(""); } }}
                    style={{ flex: 1, fontSize: 10, padding: "2px 6px" }}
                  />
                  {filterMatchCount !== null && (
                    <span style={{ fontSize: 9, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
                      {filterMatchCount} lines
                    </span>
                  )}
                  <button className="btn btn-ghost" onClick={() => { setFilterVisible(false); setOutputFilter(""); }} style={{ padding: "2px 4px" }}>
                    <X size={9} />
                  </button>
                </div>
              )}
              <pre className="output" style={{ maxHeight: activeMode === "prepare" ? "calc(100% - 96px)" : "calc(100% - 64px)", overflow: "auto" }}>{filteredOutput}</pre>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
                <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginRight: 2 }}>Helpful?</span>
                {feedbackGiven.current.has(activeMode) ? (
                  <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>
                    {feedbackGiven.current.get(activeMode) ? "✓ marked helpful" : "✓ marked unhelpful"}
                  </span>
                ) : (
                  <>
                    <button className="btn btn-ghost" onClick={() => submitFeedback(true)} style={{ padding: "1px 6px", fontSize: 9 }} title="Helpful">
                      <ThumbsUp size={9} />
                    </button>
                    <button className="btn btn-ghost" onClick={() => submitFeedback(false)} style={{ padding: "1px 6px", fontSize: 9 }} title="Not helpful">
                      <ThumbsDown size={9} />
                    </button>
                  </>
                )}
              </div>
            </>
          ) : (
            <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16, textAlign: "center" }}>
              Click a mode and Run <span style={{ opacity: 0.5 }}>(⌘R)</span>
            </div>
          )}
        </div>
      </div>
    </div>
    </>
  );
}
