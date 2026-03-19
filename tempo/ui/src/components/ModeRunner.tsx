import { useState, useEffect, useRef, useMemo } from "react";
import { runTempo, saveOutput, reportFeedback, readFile } from "./tempo";
import { CommandPalette } from "./CommandPalette";
import { MODES, loadHistory, saveHistory } from "./modes";
import { BUILTIN_KITS, type KitInfo } from "./kits";
import { SidebarTabs } from "./SidebarTabs";
import { OutputPanel } from "./OutputPanel";

interface Props {
  repoPath: string;
  excludeDirs?: string[];
}

export function ModeRunner({ repoPath, excludeDirs }: Props) {
  const [activeMode, setActiveMode] = useState("overview");
  const [activeKit, setActiveKit] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"kits" | "modes">("kits");
  const [customKits, setCustomKits] = useState<KitInfo[]>([]);
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

  const activeModeInfo = activeKit
    ? (() => {
        const kit = [...BUILTIN_KITS, ...customKits].find(k => k.id === activeKit);
        if (!kit) return undefined;
        return {
          mode: `kit:${kit.id}`,
          label: kit.label,
          icon: kit.icon,
          tag: "kit",
          hint: kit.needsQuery ? "symbol or task to focus on" : undefined,
          argPrefix: kit.needsQuery ? "--query" : undefined,
          desc: kit.description,
        };
      })()
    : MODES.find(m => m.mode === activeMode);

  // Load custom kits from .tempo/kits.json on repo change
  useEffect(() => {
    if (!repoPath) return;
    readFile(`${repoPath}/.tempo/kits.json`).then(r => {
      if (!r.success || !r.output) return;
      try {
        const raw = JSON.parse(r.output) as Record<string, { steps?: string[]; description?: string; needsQuery?: boolean }>;
        const loaded: KitInfo[] = Object.entries(raw)
          .filter(([, spec]) => spec.steps && spec.steps.length > 0)
          .map(([id, spec]) => ({
            id,
            label: id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
            icon: BUILTIN_KITS[0].icon, // default icon for custom kits
            description: spec.description || `Custom kit: ${spec.steps?.join(" + ")}`,
            needsQuery: spec.needsQuery,
          }));
        setCustomKits(loaded);
      } catch {
        // malformed kits.json — silently ignore
      }
    });
  }, [repoPath]);

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
    setActiveKit(null);
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

  const switchKit = (kitId: string) => {
    setActiveKit(kitId);
    setActiveMode("kit");
    setModeArgs("");
    setHistoryOpen(false);
    setOutputFilter("");
    setFilterVisible(false);
    const cacheKey = `kit:${kitId}`;
    const cached = outputCache.current.get(cacheKey);
    setModeOutput(cached ?? "");
    setOutputTs(cached ? (outputTsCache.current.get(cacheKey) ?? null) : null);
    setRunDuration(runDurationCache.current.get(cacheKey) ?? null);
    const kit = [...BUILTIN_KITS, ...customKits].find(k => k.id === kitId);
    if (!cached && !kit?.needsQuery) {
      setTimeout(() => runModeRef.current?.(), 0);
    } else if (!cached && kit?.needsQuery) {
      setTimeout(() => argsInputRef.current?.focus(), 50);
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
    const cacheKey = activeKit ? `kit:${activeKit}` : activeMode;
    runStart.current = Date.now();
    setElapsed(0);
    setModeRunning(true);
    setModeOutput("");
    try {
      let r;
      if (activeKit) {
        const args = ["--kit", activeKit];
        const raw = modeArgs.trim();
        if (raw) args.push("--query", raw);
        r = await runTempo(repoPath, "kit", args);
      } else {
        const args: string[] = [];
        const raw = modeArgs.trim();
        const modeInfo = MODES.find(m => m.mode === activeMode);
        if (raw && modeInfo?.argPrefix && !raw.startsWith("--")) {
          args.push(modeInfo.argPrefix, raw);
        } else if (raw) {
          args.push(...raw.split(/\s+/));
        }
        if (excludeDirs && excludeDirs.length > 0 && !args.includes("--exclude")) {
          args.push("--exclude", excludeDirs.join(","));
        }
        r = await runTempo(repoPath, activeMode, args);
      }
      const out = r.output || "No output";
      const now = Date.now();
      const dur = runStart.current ? (now - runStart.current) / 1000 : null;
      outputCache.current.set(cacheKey, out);
      outputTsCache.current.set(cacheKey, now);
      if (dur !== null) runDurationCache.current.set(cacheKey, dur);
      setModeOutput(out);
      setOutputTs(now);
      if (dur !== null) setRunDuration(dur);
      setCachedModes(prev => new Set(prev).add(cacheKey));
      if (!activeKit) {
        const raw = modeArgs.trim();
        const modeInfo = MODES.find(m => m.mode === activeMode);
        if (raw && modeInfo?.argPrefix) {
          saveHistory(activeMode, raw);
          setHistory(loadHistory(activeMode));
        }
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
    const label = activeKit ? `kit-${activeKit}` : activeMode;
    const outPath = `${repoPath}/.tempo/output-${label}-${Date.now()}.txt`;
    await saveOutput(outPath, modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const submitFeedback = async (helpful: boolean) => {
    const feedbackKey = activeKit ? `kit:${activeKit}` : activeMode;
    if (feedbackGiven.current.has(feedbackKey)) return;
    feedbackGiven.current.set(feedbackKey, helpful);
    setFeedbackMode(feedbackKey);
    const mode = activeKit ? "kit" : activeMode;
    await reportFeedback(repoPath, mode, helpful);
  };

  const allKits = [...BUILTIN_KITS, ...customKits];

  return (
    <>
      {paletteOpen && (
        <CommandPalette
          modes={MODES}
          onSelect={(mode) => { switchMode(mode); setSidebarTab("modes"); setTimeout(() => argsInputRef.current?.focus(), 50); }}
          onClose={() => setPaletteOpen(false)}
        />
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* Sidebar: tab switcher + kit/mode list */}
        <SidebarTabs
          sidebarTab={sidebarTab}
          onTabChange={setSidebarTab}
          allKits={allKits}
          activeKit={activeKit}
          activeMode={activeMode}
          cachedModes={cachedModes}
          onKitSelect={switchKit}
          onModeSelect={switchMode}
        />

        <OutputPanel
          activeModeInfo={activeModeInfo}
          activeMode={activeKit ? `kit:${activeKit}` : activeMode}
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
