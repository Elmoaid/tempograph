import { useEffect } from "react";
import { MODES } from "../components/modes";

interface KeyboardShortcutsConfig {
  modeRunning: boolean;
  modeOutput: string;
  runModeRef: React.RefObject<(() => void) | null>;
  filterInputRef: React.RefObject<HTMLInputElement | null>;
  switchMode: (mode: string) => void;
  setPaletteOpen: (open: boolean) => void;
  setKitBuilderOpen: (open: boolean) => void;
  setSidebarTab: (tab: "kits" | "modes") => void;
  setFilterVisible: (updater: boolean | ((v: boolean) => boolean)) => void;
}

export function useKeyboardShortcuts({
  modeRunning,
  modeOutput,
  runModeRef,
  filterInputRef,
  switchMode,
  setPaletteOpen,
  setKitBuilderOpen,
  setSidebarTab,
  setFilterVisible,
}: KeyboardShortcutsConfig) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.metaKey && !e.ctrlKey) return;
      if (e.key === "k") { e.preventDefault(); setPaletteOpen(true); }
      if (e.key === "n") { e.preventDefault(); setKitBuilderOpen(true); setSidebarTab("kits"); }
      if (e.key === "r" && !modeRunning) { e.preventDefault(); runModeRef.current?.(); }
      if (e.key === "f" && modeOutput) {
        e.preventDefault();
        setFilterVisible(v => { if (!v) setTimeout(() => filterInputRef.current?.focus(), 50); return true; });
      }
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= 9 && n <= MODES.length) { e.preventDefault(); switchMode(MODES[n - 1].mode); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [modeRunning, modeOutput]);
}
