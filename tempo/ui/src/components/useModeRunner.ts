import { useEffect, useRef, useCallback } from "react";
import { MODES, saveRecentCommand } from "./modes";
import { BUILTIN_KITS, type KitInfo } from "./kits";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";
import { useRunMode } from "../hooks/useRunMode";
import { useOutputFilter } from "../hooks/useOutputFilter";
import { useOutputSearch } from "../hooks/useOutputSearch";
import { useCustomKits } from "../hooks/useCustomKits";
import { useOutputActions } from "../hooks/useOutputActions";
import { useSuggestions } from "../hooks/useSuggestions";
import { useFeedback } from "../hooks/useFeedback";
import { useElapsedTimer } from "../hooks/useElapsedTimer";
import { usePanelState } from "../hooks/usePanelState";
import { useRunHistory, type RunHistoryEntry } from "../hooks/useRunHistory";
import { useOutputCache } from "../hooks/useOutputCache";
import { useModeOutputState } from "../hooks/useModeOutputState";
import { useModeSelectionState, lastModeKey } from "../hooks/useModeSelectionState";
import { useModeArgsState, modeArgsKey } from "../hooks/useModeArgsState";

export type { RunHistoryEntry };


interface ModeRunnerState {
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

interface ModeRunnerActions {
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

export function useModeRunner(repoPath: string, excludeDirs?: string[]): ModeRunnerState & ModeRunnerActions {
  const {
    activeMode, setActiveMode,
    activeKit, setActiveKit,
  } = useModeSelectionState(repoPath);
  const {
    sidebarTab, setSidebarTab,
    kitBuilderOpen, setKitBuilderOpen,
    paletteOpen, setPaletteOpen,
    showHelp, setHelpOpen,
    showWhichKey, setWhichKeyVisible,
    historyOpen, setHistoryOpen,
  } = usePanelState();
  const { customKits, loadCustomKits } = useCustomKits(repoPath);
  const { modeArgs, setModeArgs } = useModeArgsState(repoPath);
  const {
    modeOutput, setModeOutput,
    prevOutput, setPrevOutput,
    modeRunning, setModeRunning,
  } = useModeOutputState();
  const { feedbackMode, feedbackGiven, submitFeedback } = useFeedback(repoPath, activeMode, activeKit);
  const argsInputRef = useRef<HTMLInputElement>(null);
  const {
    outputCache, outputTsCache, runDurationCache,
    cachedModes, setCachedModes,
    outputTs, setOutputTs,
    runDuration, setRunDuration,
    getCache, clearCache,
  } = useOutputCache();
  const runStartRef = useRef<number | null>(null);
  const { elapsed, resetElapsed: setElapsed } = useElapsedTimer(modeRunning, runStartRef);
  const { runHistory, addRunHistory, history, setHistory, loadModeHistory } = useRunHistory(repoPath);

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

  const { copied, saved, copyOutput, handleSaveOutput, saveOutputRef } = useOutputActions(modeOutput, activeMode, activeKit);
  const { suggestions } = useSuggestions(modeOutput, modeRunning, activeMode, activeKit);

  // Stable refs so keyboard/auto-run closures always call the latest functions
  const runModeRef = useRef<(() => void) | null>(null);
  const cancelModeRef = useRef<(() => void) | null>(null);

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
    loadModeHistory(mode);
    const { output: cached, ts: cachedTs, duration: cachedDur } = getCache(mode);
    setModeOutput(cached ?? "");
    setOutputTs(cached ? (cachedTs ?? null) : null);
    setRunDuration(cachedDur);
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
    const { output: cached, ts: cachedTs, duration: cachedDur } = getCache(cacheKey);
    setModeOutput(cached ?? "");
    setOutputTs(cached ? (cachedTs ?? null) : null);
    setRunDuration(cachedDur);
    const kit = allKits.find(k => k.id === kitId);
    if (!cached && !kit?.needsQuery) {
      setTimeout(() => runModeRef.current?.(), 0);
    } else if (!cached && kit?.needsQuery) {
      setTimeout(() => argsInputRef.current?.focus(), 50);
    }
  };

  const clearOutput = useCallback(() => {
    const cacheKey = activeKit ? `kit:${activeKit}` : activeMode;
    clearCache(cacheKey);
    setModeOutput("");
    setOutputTs(null);
  }, [activeMode, activeKit, clearCache]);

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
    runStartRef,
    setElapsed,
    setModeRunning,
    setModeOutput,
    setOutputTs,
    setRunDuration,
    setCachedModes,
    setHistory,
    onRunSuccess: (mode, args) => { addRunHistory(mode, args); },
  });
  const runMode = useCallback(async () => {
    if (modeOutput) setPrevOutput(modeOutput);
    saveRecentCommand(activeMode, modeArgs);
    return _runMode();
  }, [_runMode, modeOutput, activeMode, modeArgs]);
  useEffect(() => { runModeRef.current = runMode; }, [runMode]);
  useEffect(() => { cancelModeRef.current = cancelMode; }, [cancelMode]);

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
    loadModeHistory(entry.mode);
    setModeOutput("");
    setOutputTs(null);
    setTimeout(() => runModeRef.current?.(), 0);
  }, [repoPath, activeMode, activeKit, modeArgs, resetFilter, setHistoryOpen, loadModeHistory]);

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
