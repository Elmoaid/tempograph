import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { runTempo, saveOutput, reportFeedback, readFile } from "./tempo";
import { MODES, loadHistory, saveHistory } from "./modes";
import { BUILTIN_KITS, type KitInfo } from "./kits";

export interface ModeRunnerState {
  activeMode: string;
  activeKit: string | null;
  sidebarTab: "kits" | "modes";
  customKits: KitInfo[];
  kitBuilderOpen: boolean;
  modeArgs: string;
  modeOutput: string;
  modeRunning: boolean;
  copied: boolean;
  paletteOpen: boolean;
  historyOpen: boolean;
  history: string[];
  feedbackMode: string | null;
  outputFilter: string;
  filterVisible: boolean;
  cachedModes: Set<string>;
  outputTs: number | null;
  runDuration: number | null;
  elapsed: number;
  activeModeInfo: ReturnType<typeof buildActiveModeInfo>;
  allKits: KitInfo[];
  filteredOutput: string;
  filterMatchCount: number | null;
  argsInputRef: React.RefObject<HTMLInputElement | null>;
  filterInputRef: React.RefObject<HTMLInputElement | null>;
  feedbackGiven: React.RefObject<Map<string, boolean>>;
}

export interface ModeRunnerActions {
  setActiveMode: (mode: string) => void;
  setSidebarTab: (tab: "kits" | "modes") => void;
  setKitBuilderOpen: (open: boolean) => void;
  setModeArgs: (args: string) => void;
  setHistoryOpen: (open: boolean) => void;
  setPaletteOpen: (open: boolean) => void;
  setOutputFilter: (filter: string) => void;
  setFilterVisible: (visible: boolean | ((v: boolean) => boolean)) => void;
  switchMode: (mode: string) => void;
  switchKit: (kitId: string) => void;
  runMode: () => Promise<void>;
  copyOutput: () => void;
  handleSaveOutput: () => Promise<void>;
  submitFeedback: (helpful: boolean) => Promise<void>;
  loadCustomKits: () => void;
  onHistorySelect: (q: string) => void;
  onFilterToggle: () => void;
  onFilterClose: () => void;
}

function buildActiveModeInfo(activeKit: string | null, activeMode: string, customKits: KitInfo[]) {
  if (activeKit) {
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
  }
  return MODES.find(m => m.mode === activeMode);
}

export function useModeRunner(repoPath: string, excludeDirs?: string[]): ModeRunnerState & ModeRunnerActions {
  const [activeMode, setActiveMode] = useState("overview");
  const [activeKit, setActiveKit] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"kits" | "modes">("kits");
  const [customKits, setCustomKits] = useState<KitInfo[]>([]);
  const [kitBuilderOpen, setKitBuilderOpen] = useState(false);
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

  const activeModeInfo = buildActiveModeInfo(activeKit, activeMode, customKits);
  const allKits = [...BUILTIN_KITS, ...customKits];

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

  const loadCustomKits = useCallback(() => {
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
            icon: BUILTIN_KITS[0].icon,
            description: spec.description || `Custom kit: ${spec.steps?.join(" + ")}`,
            needsQuery: spec.needsQuery,
          }));
        setCustomKits(loaded);
      } catch {
        // malformed kits.json — silently ignore
      }
    });
  }, [repoPath]);

  useEffect(() => { loadCustomKits(); }, [loadCustomKits]);

  // Stable ref so keyboard/auto-run closures always call the latest runMode
  const runModeRef = useRef<(() => void) | null>(null);

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
    const kit = allKits.find(k => k.id === kitId);
    if (!cached && !kit?.needsQuery) {
      setTimeout(() => runModeRef.current?.(), 0);
    } else if (!cached && kit?.needsQuery) {
      setTimeout(() => argsInputRef.current?.focus(), 50);
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.metaKey && !e.ctrlKey) return;
      if (e.key === "k") { e.preventDefault(); setPaletteOpen(true); }
      if (e.key === "n") { e.preventDefault(); setKitBuilderOpen(true); setSidebarTab("kits"); }
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

  // Live elapsed counter
  useEffect(() => {
    if (!modeRunning) return;
    const id = setInterval(() => {
      setElapsed(runStart.current ? Math.floor((Date.now() - runStart.current) / 1000) : 0);
    }, 250);
    return () => clearInterval(id);
  }, [modeRunning]);

  // Auto-run overview on mount
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

  const onHistorySelect = (q: string) => {
    setModeArgs(q);
    setHistoryOpen(false);
    setTimeout(() => runModeRef.current?.(), 0);
  };

  const onFilterToggle = () => {
    setFilterVisible(v => !v);
    setTimeout(() => filterInputRef.current?.focus(), 50);
  };

  const onFilterClose = () => {
    setFilterVisible(false);
    setOutputFilter("");
  };

  return {
    // state
    activeMode,
    activeKit,
    sidebarTab,
    customKits,
    kitBuilderOpen,
    modeArgs,
    modeOutput,
    modeRunning,
    copied,
    paletteOpen,
    historyOpen,
    history,
    feedbackMode,
    outputFilter,
    filterVisible,
    cachedModes,
    outputTs,
    runDuration,
    elapsed,
    activeModeInfo,
    allKits,
    filteredOutput,
    filterMatchCount,
    argsInputRef,
    filterInputRef,
    feedbackGiven,
    // actions
    setActiveMode,
    setSidebarTab,
    setKitBuilderOpen,
    setModeArgs,
    setHistoryOpen,
    setPaletteOpen,
    setOutputFilter,
    setFilterVisible,
    switchMode,
    switchKit,
    runMode,
    copyOutput,
    handleSaveOutput,
    submitFeedback,
    loadCustomKits,
    onHistorySelect,
    onFilterToggle,
    onFilterClose,
  };
}
