import { useState } from "react";

/**
 * Owns the three core output-display state variables for useModeRunner:
 * - modeOutput: the raw output string from the last run
 * - prevOutput: snapshot of modeOutput before the most recent run (for diff/compare)
 * - modeRunning: whether a mode run is in progress
 */
export function useModeOutputState() {
  const [modeOutput, setModeOutput] = useState("");
  const [prevOutput, setPrevOutput] = useState<string | null>(null);
  const [modeRunning, setModeRunning] = useState(false);

  return {
    modeOutput, setModeOutput,
    prevOutput, setPrevOutput,
    modeRunning, setModeRunning,
  };
}
