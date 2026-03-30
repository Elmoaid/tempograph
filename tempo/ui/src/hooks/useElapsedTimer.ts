import { useState, useEffect, useCallback } from "react";

/**
 * Tracks elapsed seconds since a run started.
 * Returns 0 when not running. Ticks every 250ms while running.
 */
export function useElapsedTimer(
  modeRunning: boolean,
  runStartRef: React.RefObject<number | null>,
): { elapsed: number; resetElapsed: () => void } {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!modeRunning) return;
    const id = setInterval(() => {
      setElapsed(runStartRef.current ? Math.floor((Date.now() - runStartRef.current) / 1000) : 0);
    }, 250);
    return () => clearInterval(id);
  }, [modeRunning, runStartRef]);

  // Reset to 0 when run stops
  const [wasRunning, setWasRunning] = useState(modeRunning);
  if (wasRunning !== modeRunning) {
    setWasRunning(modeRunning);
    if (!modeRunning) setElapsed(0);
  }

  const resetElapsed = useCallback(() => setElapsed(0), []);

  return { elapsed, resetElapsed };
}
