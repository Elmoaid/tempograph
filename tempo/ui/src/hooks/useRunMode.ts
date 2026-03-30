import { useCallback, useRef, useState } from "react";
import { runTempo } from "../components/tempo";
import { MODES, loadHistory, saveHistory } from "../components/modes";

/** Returns the phase label for the current run based on elapsed ms. */
export function getRunPhaseLabel(elapsedMs: number): string {
  if (elapsedMs < 800) return "Indexing...";
  if (elapsedMs < 2500) return "Analyzing...";
  return "Rendering...";
}

interface RunModeConfig {
  repoPath: string;
  excludeDirs?: string[];
  activeMode: string;
  activeKit: string | null;
  modeArgs: string;
  modeRunning: boolean;
  outputCache: React.RefObject<Map<string, string>>;
  outputTsCache: React.RefObject<Map<string, number>>;
  runDurationCache: React.RefObject<Map<string, number>>;
  runStartRef: React.RefObject<number | null>;
  setElapsed: (n: number) => void;
  setModeRunning: (v: boolean) => void;
  setModeOutput: (v: string) => void;
  setOutputTs: (v: number | null) => void;
  setRunDuration: (v: number | null) => void;
  setCachedModes: (updater: (prev: Set<string>) => Set<string>) => void;
  setHistory: (v: string[]) => void;
  onRunSuccess?: (mode: string, args: string) => void;
}

export function useRunMode({
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
  onRunSuccess,
}: RunModeConfig) {
  // Monotonically increasing serial — cancel checks serial to ignore stale results
  const runSerial = useRef(0);
  const [statusText, setStatusText] = useState("");
  const statusTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const cancelMode = useCallback(() => {
    runSerial.current++;
    statusTimers.current.forEach(clearTimeout);
    statusTimers.current = [];
    setStatusText("");
    setModeRunning(false);
    setModeOutput("[Cancelled]");
  }, [setModeRunning, setModeOutput]);

  const runMode = useCallback(async (): Promise<boolean> => {
    if (!repoPath || modeRunning) return false;
    const serial = ++runSerial.current;
    const cacheKey = activeKit ? `kit:${activeKit}` : activeMode;
    runStartRef.current = Date.now();
    setElapsed(0);
    setModeRunning(true);
    setModeOutput("");
    // Phase labels: simulate progress stages (cleared when result arrives)
    statusTimers.current.forEach(clearTimeout);
    statusTimers.current = [];
    setStatusText("Indexing...");
    statusTimers.current.push(setTimeout(() => setStatusText("Analyzing..."), 800));
    statusTimers.current.push(setTimeout(() => setStatusText("Rendering..."), 2500));
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
      // If cancelled while awaiting, discard result
      if (serial !== runSerial.current) return false;
      statusTimers.current.forEach(clearTimeout);
      statusTimers.current = [];
      setStatusText("");
      const out = r.output || "No output";
      const now = Date.now();
      const dur = runStartRef.current ? (now - runStartRef.current) / 1000 : null;
      outputCache.current.set(cacheKey, out);
      outputTsCache.current.set(cacheKey, now);
      if (dur !== null) runDurationCache.current.set(cacheKey, dur);
      setModeOutput(out);
      setOutputTs(now);
      if (dur !== null) setRunDuration(dur);
      setCachedModes(prev => new Set(prev).add(cacheKey));
      onRunSuccess?.(activeKit ? `kit:${activeKit}` : activeMode, modeArgs);
      if (!activeKit) {
        const raw = modeArgs.trim();
        const modeInfo = MODES.find(m => m.mode === activeMode);
        if (raw && modeInfo?.argPrefix) {
          saveHistory(activeMode, raw);
          setHistory(loadHistory(activeMode));
        }
      }
      if (serial === runSerial.current) setModeRunning(false);
      return true;
    } catch {
      if (serial !== runSerial.current) return false;
      statusTimers.current.forEach(clearTimeout);
      statusTimers.current = [];
      setStatusText("");
      setModeOutput("Failed to run mode. Check that tempo is installed.");
    }
    if (serial === runSerial.current) setModeRunning(false);
    return false;
  }, [repoPath, activeMode, activeKit, modeArgs, modeRunning, excludeDirs, onRunSuccess, outputCache, outputTsCache, runDurationCache, runStartRef, setCachedModes, setElapsed, setHistory, setModeOutput, setModeRunning, setOutputTs, setRunDuration]);

  return { runMode, cancelMode, statusText };
}
