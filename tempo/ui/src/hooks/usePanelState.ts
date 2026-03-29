import { useState } from "react";

interface PanelState {
  sidebarTab: "kits" | "modes";
  setSidebarTab: (tab: "kits" | "modes") => void;
  kitBuilderOpen: boolean;
  setKitBuilderOpen: (open: boolean) => void;
  paletteOpen: boolean;
  setPaletteOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  showHelp: boolean;
  setHelpOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  showWhichKey: boolean;
  setWhichKeyVisible: (visible: boolean) => void;
  historyOpen: boolean;
  setHistoryOpen: (open: boolean) => void;
}

/** Manages all panel/overlay open-state booleans for the mode runner UI. */
export function usePanelState(): PanelState {
  const [sidebarTab, setSidebarTab] = useState<"kits" | "modes">("kits");
  const [kitBuilderOpen, setKitBuilderOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [showHelp, setHelpOpen] = useState(false);
  const [showWhichKey, setWhichKeyVisible] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  return {
    sidebarTab,
    setSidebarTab,
    kitBuilderOpen,
    setKitBuilderOpen,
    paletteOpen,
    setPaletteOpen,
    showHelp,
    setHelpOpen,
    showWhichKey,
    setWhichKeyVisible,
    historyOpen,
    setHistoryOpen,
  };
}
