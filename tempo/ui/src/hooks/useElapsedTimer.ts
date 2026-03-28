import { useState, useEffect } from "react";

/**
 * Tracks elapsed seconds since a run started.
 * Returns 0 when not running. Ticks every 250ms while running.
 */
export function useElapsedTimer(
  modeRunning: boolean,
  runStart: React.RefObject<number | null>,
): { elapsed: number; resetElapsed: () => void } {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!modeRunning) {
      setElapsed(0);
      return;
    }
    const id = setInterval(() => {
      setElapsed(runStart.current ? Math.floor((Date.now() - runStart.current) / 1000) : 0);
    }, 250);
    return () => clearInterval(id);
  }, [modeRunning, runStart]);

  return { elapsed, resetElapsed: () => setElapsed(0) };
}
