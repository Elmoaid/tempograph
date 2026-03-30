import { useState } from "react";
import { lastModeKey } from "./useModeSelectionState";

export const modeArgsKey = (path: string, modeOrKit: string) => `tempo-mode-args-${path}-${modeOrKit}`;

/**
 * Owns the modeArgs state variable for useModeRunner:
 * - modeArgs: the current argument string for the active mode/kit
 *
 * Initialises from localStorage using the last-used mode so that the
 * user's most recent query survives page reloads. The key is namespaced
 * by repoPath so different repos have independent arg histories.
 */
export function useModeArgsState(repoPath: string) {
  const [modeArgs, setModeArgs] = useState(() => {
    const initMode = localStorage.getItem(lastModeKey(repoPath)) || "overview";
    return localStorage.getItem(modeArgsKey(repoPath, initMode)) || "";
  });

  return { modeArgs, setModeArgs };
}
