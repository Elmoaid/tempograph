import { useCallback } from "react";
import { runTempo } from "../components/tempo";
import { MODES, loadHistory, saveHistory } from "../components/modes";

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
  runStart: React.RefObject<number | null>;
  setElapsed: (n: number) => void;
  setModeRunning: (v: boolean) => void;
  setModeOutput: (v: string) => void;
  setOutputTs: (v: number | null) => void;
  setRunDuration: (v: number | null) => void;
  setCachedModes: (updater: (prev: Set<string>) => Set<string>) => void;
  setHistory: (v: string[]) => void;
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
  runStart,
  setElapsed,
  setModeRunning,
  setModeOutput,
  setOutputTs,
  setRunDuration,
  setCachedModes,
  setHistory,
}: RunModeConfig) {
  const runMode = useCallback(async () => {
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
  }, [repoPath, activeMode, activeKit, modeArgs, modeRunning, excludeDirs]);

  return { runMode };
}
