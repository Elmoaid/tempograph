import { useState, useEffect, useRef, useCallback } from "react";
import { save as saveDialog } from "@tauri-apps/plugin-dialog";
import { runTempo, saveOutput, reportFeedback } from "./tempo";
import { MODES, loadHistory, saveRecentCommand } from "./modes";
import { BUILTIN_KITS, type KitInfo } from "./kits";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";
import { useRunMode } from "../hooks/useRunMode";
import { useOutputFilter } from "../hooks/useOutputFilter";
import { useOutputSearch } from "../hooks/useOutputSearch";
import { useCustomKits } from "../hooks/useCustomKits";

export interface RunHistoryEntry {
  mode: string;
  args: string;
}

export function updateRunHistory(prev: RunHistoryEntry[], entry: RunHistoryEntry, max = 5): RunHistoryEntry[] {
  const deduped = prev.filter(e => !(e.mode === entry.mode && e.args === entry.args));
  return [entry, ...deduped].slice(0, max);
}

// Static suggest_next map (suggest_next is MCP-only, not a CLI mode)
export const SUGGEST_NEXT_MAP: Record<string, string[]> = {
  overview:    ["hotspots", "dead_code", "focus"],
  focus:       ["blast", "hotspots"],
  blast:       ["focus", "hotspots"],
  hotspots:    ["dead_code", "focus"],
  dead_code:   ["hotspots", "focus"],
  diff:        ["focus", "blast"],
  deps:        ["focus", "blast"],
  arch:        ["hotspots", "deps"],
  map:         ["focus", "hotspots"],
  context:     ["focus", "blast"],
  prepare:     ["focus", "hotspots"],
  quality:     ["hotspots", "focus"],
  token_stats: ["focus", "hotspots"],
  learn:       ["focus", "overview"],
};

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
  showHelp: boolean;
  showWhichKey: boolean;
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
  searchInputRef: React.RefObject<HTMLInputElement | null>;
  feedbackGiven: React.RefObject<Map<string, boolean>>;
  searchText: string;
  searchActive: boolean;
  searchMatchCount: number;
  searchCurrentMatch: number;
  runHistory: RunHistoryEntry[];
  suggestions: string[];
  statusText: string;
}

export interface ModeRunnerActions {
  setActiveMode: (mode: string) => void;
  setSidebarTab: (tab: "kits" | "modes") => void;
  setKitBuilderOpen: (open: boolean) => void;
  setModeArgs: (args: string) => void;
  setHistoryOpen: (open: boolean) => void;
  setPaletteOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  setHelpOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
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
  cancelMode: () => void;
  onSearchOpen: () => void;
  onSearchClose: () => void;
  onSearchChange: (text: string) => void;
  onSearchNavigate: (dir: "next" | "prev") => void;
  runHistoryEntry: (entry: RunHistoryEntry) => void;
  runSuggestion: (mode: string) => void;
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
const modeArgsKey = (path: string, modeOrKit: string) => `tempo-mode-args-${path}-${modeOrKit}`;

export function useModeRunner(repoPath: string, excludeDirs?: string[]): ModeRunnerState & ModeRunnerActions {
  const [activeMode, setActiveMode] = useState(() => localStorage.getItem(lastModeKey(repoPath)) || "overview");
  const [activeKit, setActiveKit] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"kits" | "modes">("kits");
  const { customKits, loadCustomKits } = useCustomKits(repoPath);
  const [kitBuilderOpen, setKitBuilderOpen] = useState(false);
  const [modeArgs, setModeArgs] = useState(() => {
    const initMode = localStorage.getItem(lastModeKey(repoPath)) || "overview";
    return localStorage.getItem(modeArgsKey(repoPath, initMode)) || "";
  });
  const [modeOutput, setModeOutput] = useState("");
  const [prevOutput, setPrevOutput] = useState<string | null>(null);
  const [modeRunning, setModeRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [showHelp, setHelpOpen] = useState(false);
  const [showWhichKey, setWhichKeyVisible] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
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
  const [runHistory, setRunHistory] = useState<RunHistoryEntry[]>([]);

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

  const {
    searchText,
    setSearchText,
    matchCount: searchMatchCount,
    currentMatch: searchCurrentMatch,
    active: searchActive,
    searchInputRef,
    open: onSearchOpen,
    close: onSearchClose,
    navigateMatch: onSearchNavigate,
  } = useOutputSearch(filteredOutput);

  // Stable refs so keyboard/auto-run closures always call the latest functions
  const runModeRef = useRef<(() => void) | null>(null);
  const cancelModeRef = useRef<(() => void) | null>(null);
  const saveOutputRef = useRef<(() => Promise<void>) | null>(null);

  const switchMode = (mode: string) => {
    // Persist args for the mode we're leaving
    localStorage.setItem(modeArgsKey(repoPath, activeKit ? `kit:${activeKit}` : activeMode), modeArgs);
    setActiveKit(null);
    setActiveMode(mode);
    localStorage.setItem(lastModeKey(repoPath), mode);
    setModeArgs(localStorage.getItem(modeArgsKey(repoPath, mode)) || "");
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
    // Persist args for the mode/kit we're leaving
    localStorage.setItem(modeArgsKey(repoPath, activeKit ? `kit:${activeKit}` : activeMode), modeArgs);
    setActiveKit(kitId);
    setActiveMode("kit");
    setModeArgs(localStorage.getItem(modeArgsKey(repoPath, `kit:${kitId}`)) || "");
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
    searchActive,
    helpOpen: showHelp,
    runModeRef,
    cancelModeRef,
    saveOutputRef,
    argsInputRef,
    filterInputRef,
    clearOutput,
    closeSearch: onSearchClose,
    openSearch: onSearchOpen,
    switchMode,
    setPaletteOpen,
    setKitBuilderOpen,
    setSidebarTab,
    setFilterVisible,
    setHelpOpen,
    setWhichKeyVisible,
  });

  // Live elapsed counter
  useEffect(() => {
    if (!modeRunning) return;
    const id = setInterval(() => {
      setElapsed(runStart.current ? Math.floor((Date.now() - runStart.current) / 1000) : 0);
    }, 250);
    return () => clearInterval(id);
  }, [modeRunning]);

  // Suggest follow-up modes after each run (static map; suggest_next is MCP-only)
  useEffect(() => {
    if (modeRunning) { setSuggestions([]); return; }
    if (!modeOutput || activeKit ||
        modeOutput.startsWith("[Cancelled]") ||
        modeOutput.startsWith("Failed to run")) {
      setSuggestions([]);
      return;
    }
    setSuggestions((SUGGEST_NEXT_MAP[activeMode] ?? []).slice(0, 3));
  }, [modeOutput, modeRunning, activeMode, activeKit]);

  // Auto-run overview on mount
  useEffect(() => { runModeRef.current?.(); }, []);

  const { runMode: _runMode, cancelMode, statusText } = useRunMode({
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
    onRunSuccess: (mode, args) => {
      setRunHistory(prev => updateRunHistory(prev, { mode, args }));
    },
  });
  const runMode = useCallback(async () => {
    if (modeOutput) setPrevOutput(modeOutput);
    saveRecentCommand(activeMode, modeArgs);
    return _runMode();
  }, [_runMode, modeOutput, activeMode, modeArgs]);
  runModeRef.current = runMode;
  cancelModeRef.current = cancelMode;

  const copyOutput = () => {
    navigator.clipboard.writeText(modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleSaveOutput = async () => {
    if (!modeOutput) return;
    const label = activeKit ? `kit-${activeKit}` : activeMode;
    const date = new Date().toISOString().slice(0, 10);
    const defaultName = `tempograph-${label}-${date}.txt`;
    const chosenPath = await saveDialog({
      defaultPath: defaultName,
      filters: [{ name: "Text", extensions: ["txt"] }],
    });
    if (!chosenPath) return;
    await saveOutput(chosenPath, modeOutput);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };
  saveOutputRef.current = handleSaveOutput;

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

  const runHistoryEntry = useCallback((entry: RunHistoryEntry) => {
    localStorage.setItem(modeArgsKey(repoPath, activeKit ? `kit:${activeKit}` : activeMode), modeArgs);
    setActiveKit(null);
    setActiveMode(entry.mode);
    localStorage.setItem(lastModeKey(repoPath), entry.mode);
    setModeArgs(entry.args);
    setHistoryOpen(false);
    resetFilter();
    setPrevOutput(null);
    setHistory(loadHistory(entry.mode));
    setModeOutput("");
    setOutputTs(null);
    setTimeout(() => runModeRef.current?.(), 0);
  }, [repoPath, activeMode, activeKit, modeArgs, resetFilter]);

  const runSuggestion = (mode: string) => {
    switchMode(mode);
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
    showHelp,
    showWhichKey,
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
    searchInputRef,
    feedbackGiven,
    searchText,
    searchActive,
    searchMatchCount,
    searchCurrentMatch,
    runHistory,
    suggestions,
    statusText,
    // actions
    setActiveMode,
    setSidebarTab,
    setKitBuilderOpen,
    setModeArgs,
    setHistoryOpen,
    setPaletteOpen,
    setHelpOpen,
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
    cancelMode,
    onSearchOpen,
    onSearchClose,
    onSearchChange: setSearchText,
    onSearchNavigate,
    runHistoryEntry,
    runSuggestion,
  };
}
