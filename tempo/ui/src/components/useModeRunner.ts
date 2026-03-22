import { useState, useEffect, useRef, useCallback } from "react";
import { runTempo, saveOutput, reportFeedback, readFile } from "./tempo";
import { MODES, loadHistory } from "./modes";
import { BUILTIN_KITS, type KitInfo } from "./kits";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";
import { useRunMode } from "../hooks/useRunMode";
import { useOutputFilter } from "../hooks/useOutputFilter";

export interface ModeRunnerState {
  activeMode: string;
  activeKit: string | null;
  sidebarTab: "kits" | "modes";
  customKits: KitInfo[];
  kitBuilderOpen: boolean;
  modeArgs: string;
  modeOutput: string;
  prevOutput: string | null;
  modeRunning: boolean;
  copied: boolean;
  saved: boolean;
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
  clearOutput: () => void;
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

const lastModeKey = (path: string) => `tempo-last-mode-${path}`;

export function useModeRunner(repoPath: string, excludeDirs?: string[]): ModeRunnerState & ModeRunnerActions {
  const [activeMode, setActiveMode] = useState(() => localStorage.getItem(lastModeKey(repoPath)) || "overview");
  const [activeKit, setActiveKit] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"kits" | "modes">("kits");
  const [customKits, setCustomKits] = useState<KitInfo[]>([]);
  const [kitBuilderOpen, setKitBuilderOpen] = useState(false);
  const [modeArgs, setModeArgs] = useState("");
  const [modeOutput, setModeOutput] = useState("");
  const [prevOutput, setPrevOutput] = useState<string | null>(null);
  const [modeRunning, setModeRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<string[]>(() => loadHistory(localStorage.getItem(lastModeKey(repoPath)) || "overview"));
  const feedbackGiven = useRef<Map<string, boolean>>(new Map<string, boolean>());
  const [feedbackMode, setFeedbackMode] = useState<string | null>(null);
  const argsInputRef = useRef<HTMLInputElement>(null);
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

  const {
    outputFilter,
    setOutputFilter,
    filterVisible,
    setFilterVisible,
    filterInputRef,
    filteredOutput,
    filterMatchCount,
    onFilterToggle,
    onFilterClose,
    resetFilter,
  } = useOutputFilter(modeOutput);

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
    localStorage.setItem(lastModeKey(repoPath), mode);
    setModeArgs("");
    setHistoryOpen(false);
    resetFilter();
    setPrevOutput(null);
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
    resetFilter();
    setPrevOutput(null);
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

  const clearOutput = useCallback(() => {
    const cacheKey = activeKit ? `kit:${activeKit}` : activeMode;
    outputCache.current.delete(cacheKey);
    outputTsCache.current.delete(cacheKey);
    setModeOutput("");
    setOutputTs(null);
    setCachedModes(prev => { const s = new Set(prev); s.delete(cacheKey); return s; });
  }, [activeMode, activeKit]);

  useKeyboardShortcuts({
    modeRunning,
    modeOutput,
    historyOpen,
    runModeRef,
    argsInputRef,
    filterInputRef,
    clearOutput,
    switchMode,
    setPaletteOpen,
    setKitBuilderOpen,
    setSidebarTab,
    setFilterVisible,
  });

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

  const { runMode: _runMode } = useRunMode({
    repoPath,
    excludeDirs,
    activeMode,
    activeKit,
    modeArgs,
    modeRunning,
    outputCache,
    outputTsCache,
    runDurationCache,
    runStart,
    setElapsed,
    setModeRunning,
    setModeOutput,
    setOutputTs,
    setRunDuration,
    setCachedModes,
    setHistory,
  });
  const runMode = useCallback(async () => {
    if (modeOutput) setPrevOutput(modeOutput);
    return _runMode();
  }, [_runMode, modeOutput]);
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
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
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

  return {
    // state
    activeMode,
    activeKit,
    sidebarTab,
    customKits,
    kitBuilderOpen,
    modeArgs,
    modeOutput,
    prevOutput,
    modeRunning,
    copied,
    saved,
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
    clearOutput,
  };
}
