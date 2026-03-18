import { useState, useEffect, useRef, useMemo } from "react";
import { runTempo, saveOutput, reportFeedback } from "./tempo";
import { CommandPalette } from "./CommandPalette";
import { MODES, loadHistory, saveHistory } from "./modes";
import { ModeList } from "./ModeList";
import { OutputPanel } from "./OutputPanel";

interface Props {
  repoPath: string;
  excludeDirs?: string[];
}

export function ModeRunner({ repoPath, excludeDirs }: Props) {
  const [activeMode, setActiveMode] = useState("overview");
  const [modeArgs, setModeArgs] = useState("");
  const [modeOutput, setModeOutput] = useState("");
  const [modeRunning, setModeRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const feedbackGiven = useRef<Map<string, boolean>>(new Map<string, boolean>());
  const [feedbackMode, setFeedbackMode] = useState<string | null>(null);
  const argsInputRef = useRef<HTMLInputElement>(null);
  const [outputFilter, setOutputFilter] = useState("");
  const [filterVisible, setFilterVisible] = useState(false);
  const filterInputRef = useRef<HTMLInputElement>(null);
  const outputCache = useRef<Map<string, string>>(new Map());
  const outputTsCache = useRef<Map<string, number>>(new Map());
  const [cachedModes, setCachedModes] = useState<Set<string>>(new Set());
  const [outputTs, setOutputTs] = useState<number | null>(null);
  const runStart = useRef<number | null>(null);
  const runDurationCache = useRef<Map<string, number>>(new Map());
  const [runDuration, setRunDuration] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState<number>(0);

  const activeModeInfo = MODES.find(m => m.mode === activeMode);

  const filteredOutput = useMemo(() => {
    if (!outputFilter.trim() || !modeOutput) return modeOutput;
    const q = outputFilter.toLowerCase();
    return modeOutput.split("\n").filter(line => line.toLowerCase().includes(q)).join("\n");
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
    setOutputTs(cached ? (outputTsCache.current.get(mode) ?? null) : null);
    setRunDuration(runDurationCache.current.get(mode) ?? null);
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

  // Live elapsed counter — ticks every 250ms while a run is active
  useEffect(() => {
    if (!modeRunning) return;
    const id = setInterval(() => {
      setElapsed(runStart.current ? Math.floor((Date.now() - runStart.current) / 1000) : 0);
    }, 250);
    return () => clearInterval(id);
  }, [modeRunning]);

  // Stable ref so the keydown closure always calls the latest runMode
  const runModeRef = useRef<(() => void) | null>(null);

  // Auto-run overview when workspace mounts (component is keyed by repoPath so remounts on switch)
  useEffect(() => { runModeRef.current?.(); }, []);

  const runMode = async () => {
    if (!repoPath || modeRunning) return;
    runStart.current = Date.now();
    setElapsed(0);
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
      const now = Date.now();
      const dur = runStart.current ? (now - runStart.current) / 1000 : null;
      outputCache.current.set(activeMode, out);
      outputTsCache.current.set(activeMode, now);
      if (dur !== null) runDurationCache.current.set(activeMode, dur);
      setModeOutput(out);
      setOutputTs(now);
      if (dur !== null) setRunDuration(dur);
      setCachedModes(prev => new Set(prev).add(activeMode));
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
    setFeedbackMode(activeMode);
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
        <ModeList
          modes={MODES}
          activeMode={activeMode}
          cachedModes={cachedModes}
          onSelect={switchMode}
        />
        <OutputPanel
          activeModeInfo={activeModeInfo}
          activeMode={activeMode}
          modeArgs={modeArgs}
          modeRunning={modeRunning}
          modeOutput={modeOutput}
          elapsed={elapsed}
          outputTs={outputTs}
          runDuration={runDuration}
          copied={copied}
          filterVisible={filterVisible}
          outputFilter={outputFilter}
          filteredOutput={filteredOutput}
          filterMatchCount={filterMatchCount}
          history={history}
          historyOpen={historyOpen}
          feedbackGiven={feedbackGiven}
          feedbackMode={feedbackMode}
          argsInputRef={argsInputRef}
          filterInputRef={filterInputRef}
          onArgsChange={setModeArgs}
          onHistoryOpen={setHistoryOpen}
          onHistorySelect={(q) => { setModeArgs(q); setHistoryOpen(false); setTimeout(() => runModeRef.current?.(), 0); }}
          onRun={runMode}
          onCopy={copyOutput}
          onSave={handleSaveOutput}
          onFilterToggle={() => { setFilterVisible(v => !v); setTimeout(() => filterInputRef.current?.focus(), 50); }}
          onFilterChange={setOutputFilter}
          onFilterClose={() => { setFilterVisible(false); setOutputFilter(""); }}
          onFeedback={submitFeedback}
        />
      </div>
    </>
  );
}
