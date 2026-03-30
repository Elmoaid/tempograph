import { useEffect } from "react";
import { MODES } from "../components/modes";

interface KeyboardShortcutsConfig {
  modeRunning: boolean;
  modeOutput: string;
  historyOpen: boolean;
  searchActive: boolean;
  helpOpen: boolean;
  runModeRef: React.RefObject<(() => void) | null>;
  cancelModeRef: React.RefObject<(() => void) | null>;
  saveOutputRef: React.RefObject<(() => Promise<void>) | null>;
  argsInputRef: React.RefObject<HTMLInputElement | null>;
  filterInputRef: React.RefObject<HTMLInputElement | null>;
  clearOutput: () => void;
  closeSearch: () => void;
  openSearch: () => void;
  switchMode: (mode: string) => void;
  setPaletteOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  setKitBuilderOpen: (open: boolean) => void;
  setSidebarTab: (tab: "kits" | "modes") => void;
  setFilterVisible: (updater: boolean | ((v: boolean) => boolean)) => void;
  setHelpOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  setWhichKeyVisible: (v: boolean) => void;
}

export function useKeyboardShortcuts({
  modeRunning,
  modeOutput,
  historyOpen,
  searchActive,
  helpOpen,
  runModeRef,
  cancelModeRef,
  saveOutputRef,
  argsInputRef,
  clearOutput,
  closeSearch,
  openSearch,
  switchMode,
  setPaletteOpen,
  setKitBuilderOpen,
  setSidebarTab,
  setHelpOpen,
  setWhichKeyVisible,
}: KeyboardShortcutsConfig) {
  useEffect(() => {
    const onMetaDown = (e: KeyboardEvent) => {
      if (e.key === "Meta" || e.key === "Control") setWhichKeyVisible(true);
    };
    const onMetaUp = (e: KeyboardEvent) => {
      if (e.key === "Meta" || e.key === "Control") setWhichKeyVisible(false);
    };
    window.addEventListener("keydown", onMetaDown);
    window.addEventListener("keyup", onMetaUp);
    return () => {
      window.removeEventListener("keydown", onMetaDown);
      window.removeEventListener("keyup", onMetaUp);
    };
  }, [setWhichKeyVisible]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Escape: close help overlay → cancel running → close search → clear output
      if (e.key === "Escape") {
        if (helpOpen) { setHelpOpen(false); return; }
        if (modeRunning) { cancelModeRef.current?.(); return; }
        if (!historyOpen) {
          if (searchActive) { closeSearch(); return; }
          if (modeOutput) { clearOutput(); return; }
        }
      }

      // ?: toggle shortcut help overlay (guard: skip when input is focused)
      if (e.key === "?" && !e.metaKey && !e.ctrlKey) {
        const el = document.activeElement as HTMLElement | null;
        if (!el || (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA" && !el.isContentEditable)) {
          e.preventDefault();
          setHelpOpen(prev => !prev);
          return;
        }
      }

      if (!e.metaKey && !e.ctrlKey) return;

      // Cmd/Ctrl+Enter: run mode
      if (e.key === "Enter" && !modeRunning) { e.preventDefault(); runModeRef.current?.(); }
      // Cmd/Ctrl+L: focus args input
      if (e.key === "l") { e.preventDefault(); argsInputRef.current?.focus(); argsInputRef.current?.select(); }
      if (e.key === "k") { e.preventDefault(); setPaletteOpen(prev => !prev); }
      if (e.key === "n") { e.preventDefault(); setKitBuilderOpen(true); setSidebarTab("kits"); }
      if (e.key === "r" && !modeRunning) { e.preventDefault(); runModeRef.current?.(); }
      // Cmd/Ctrl+F: open output search (find in output)
      if (e.key === "f" && modeOutput) { e.preventDefault(); openSearch(); }
      // Cmd/Ctrl+S: save output to file
      if (e.key === "s" && modeOutput) { e.preventDefault(); void saveOutputRef.current?.(); }
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= 9 && n <= MODES.length) { e.preventDefault(); switchMode(MODES[n - 1].mode); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [modeRunning, modeOutput, historyOpen, searchActive, helpOpen, clearOutput, closeSearch, openSearch, setHelpOpen, argsInputRef, cancelModeRef, runModeRef, saveOutputRef, setKitBuilderOpen, setPaletteOpen, setSidebarTab, switchMode]);
}
