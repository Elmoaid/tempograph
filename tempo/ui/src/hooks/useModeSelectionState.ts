import { useState } from "react";

export const lastModeKey = (path: string) => `tempo-last-mode-${path}`;

/**
 * Owns the two active-selection state variables for useModeRunner:
 * - activeMode: the currently selected mode slug (e.g. "overview", "focus")
 * - activeKit: the currently selected kit id, or null when no kit is active
 *
 * activeMode initialises from localStorage so the last-used mode survives
 * page reloads. activeKit always starts null (kits are session-only).
 */
export function useModeSelectionState(repoPath: string) {
  const [activeMode, setActiveMode] = useState(
    () => localStorage.getItem(lastModeKey(repoPath)) || "overview"
  );
  const [activeKit, setActiveKit] = useState<string | null>(null);

  return {
    activeMode, setActiveMode,
    activeKit, setActiveKit,
  };
}
