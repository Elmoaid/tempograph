import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";

vi.mock("../components/modes", () => ({
  MODES: [
    { mode: "prepare", label: "Prepare" },
    { mode: "overview", label: "Overview" },
    { mode: "focus", label: "Focus" },
    { mode: "blast", label: "Blast" },
    { mode: "hotspots", label: "Hotspots" },
    { mode: "diff", label: "Diff" },
    { mode: "dead_code", label: "Dead Code" },
    { mode: "lookup", label: "Lookup" },
    { mode: "symbols", label: "Symbols" },
  ],
}));

function makeConfig(overrides: Partial<Parameters<typeof useKeyboardShortcuts>[0]> = {}) {
  return {
    modeRunning: false,
    modeOutput: "",
    historyOpen: false,
    searchActive: false,
    helpOpen: false,
    runModeRef: { current: vi.fn() },
    cancelModeRef: { current: vi.fn() },
    saveOutputRef: { current: vi.fn().mockResolvedValue(undefined) },
    argsInputRef: { current: null },
    filterInputRef: { current: null },
    clearOutput: vi.fn(),
    closeSearch: vi.fn(),
    openSearch: vi.fn(),
    switchMode: vi.fn(),
    setPaletteOpen: vi.fn(),
    setKitBuilderOpen: vi.fn(),
    setSidebarTab: vi.fn(),
    setFilterVisible: vi.fn(),
    setHelpOpen: vi.fn(),
    setWhichKeyVisible: vi.fn(),
    ...overrides,
  };
}

function fireKey(key: string, opts: Partial<KeyboardEventInit> = {}) {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, ...opts });
  window.dispatchEvent(ev);
}

describe("useKeyboardShortcuts — Cmd+number mode switching", () => {
  let config: ReturnType<typeof makeConfig>;

  beforeEach(() => {
    config = makeConfig();
    renderHook(() => useKeyboardShortcuts(config));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("Cmd+1 switches to first mode (prepare)", () => {
    fireKey("1", { metaKey: true });
    expect(config.switchMode).toHaveBeenCalledWith("prepare");
  });

  it("Cmd+2 switches to second mode (overview)", () => {
    fireKey("2", { metaKey: true });
    expect(config.switchMode).toHaveBeenCalledWith("overview");
  });

  it("Cmd+5 switches to fifth mode (hotspots)", () => {
    fireKey("5", { metaKey: true });
    expect(config.switchMode).toHaveBeenCalledWith("hotspots");
  });

  it("Cmd+9 switches to ninth mode (symbols)", () => {
    fireKey("9", { metaKey: true });
    expect(config.switchMode).toHaveBeenCalledWith("symbols");
  });

  it("Ctrl+3 also triggers mode switch", () => {
    fireKey("3", { ctrlKey: true });
    expect(config.switchMode).toHaveBeenCalledWith("focus");
  });

  it("number without Cmd/Ctrl does NOT trigger mode switch", () => {
    fireKey("1");
    expect(config.switchMode).not.toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Cmd+R run", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+R triggers runMode when not running", () => {
    const config = makeConfig({ modeRunning: false });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("r", { metaKey: true });
    expect(config.runModeRef.current).toHaveBeenCalled();
  });

  it("Cmd+R does NOT trigger runMode when already running", () => {
    const config = makeConfig({ modeRunning: true });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("r", { metaKey: true });
    expect(config.runModeRef.current).not.toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Cmd+Enter run", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+Enter triggers runMode when not running", () => {
    const config = makeConfig({ modeRunning: false });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Enter", { metaKey: true });
    expect(config.runModeRef.current).toHaveBeenCalled();
  });

  it("Cmd+Enter does NOT run when already running", () => {
    const config = makeConfig({ modeRunning: true });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Enter", { metaKey: true });
    expect(config.runModeRef.current).not.toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Cmd+K palette", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+K toggles palette", () => {
    const config = makeConfig();
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("k", { metaKey: true });
    expect(config.setPaletteOpen).toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Cmd+N kit builder", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+N opens kit builder and switches sidebar to kits", () => {
    const config = makeConfig();
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("n", { metaKey: true });
    expect(config.setKitBuilderOpen).toHaveBeenCalledWith(true);
    expect(config.setSidebarTab).toHaveBeenCalledWith("kits");
  });
});

describe("useKeyboardShortcuts — Cmd+F search", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+F opens search when output exists", () => {
    const config = makeConfig({ modeOutput: "some output" });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("f", { metaKey: true });
    expect(config.openSearch).toHaveBeenCalled();
  });

  it("Cmd+F does nothing when no output", () => {
    const config = makeConfig({ modeOutput: "" });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("f", { metaKey: true });
    expect(config.openSearch).not.toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Cmd+S save", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+S calls save when output exists", () => {
    const config = makeConfig({ modeOutput: "content" });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("s", { metaKey: true });
    expect(config.saveOutputRef.current).toHaveBeenCalled();
  });

  it("Cmd+S does nothing when no output", () => {
    const config = makeConfig({ modeOutput: "" });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("s", { metaKey: true });
    expect(config.saveOutputRef.current).not.toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Cmd+L focus input", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Cmd+L focuses and selects the args input", () => {
    const mockInput = { focus: vi.fn(), select: vi.fn() } as unknown as HTMLInputElement;
    const config = makeConfig({ argsInputRef: { current: mockInput } });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("l", { metaKey: true });
    expect(mockInput.focus).toHaveBeenCalled();
    expect(mockInput.select).toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Escape", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Escape closes help overlay when open", () => {
    const config = makeConfig({ helpOpen: true });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Escape");
    expect(config.setHelpOpen).toHaveBeenCalledWith(false);
    expect(config.cancelModeRef.current).not.toHaveBeenCalled();
  });

  it("Escape cancels running mode when help is closed", () => {
    const config = makeConfig({ modeRunning: true, helpOpen: false });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Escape");
    expect(config.cancelModeRef.current).toHaveBeenCalled();
  });

  it("Escape closes search when active and not running", () => {
    const config = makeConfig({ searchActive: true, modeRunning: false });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Escape");
    expect(config.closeSearch).toHaveBeenCalled();
  });

  it("Escape clears output when no search active and not running", () => {
    const config = makeConfig({ modeOutput: "some output", modeRunning: false, searchActive: false });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Escape");
    expect(config.clearOutput).toHaveBeenCalled();
  });

  it("Escape does NOT clear output when history is open", () => {
    const config = makeConfig({ modeOutput: "output", historyOpen: true, modeRunning: false });
    renderHook(() => useKeyboardShortcuts(config));
    fireKey("Escape");
    expect(config.clearOutput).not.toHaveBeenCalled();
  });
});

describe("useKeyboardShortcuts — Meta key visibility", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("Meta keydown sets whichKey visible", () => {
    const config = makeConfig();
    renderHook(() => useKeyboardShortcuts(config));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Meta", bubbles: true }));
    expect(config.setWhichKeyVisible).toHaveBeenCalledWith(true);
  });

  it("Meta keyup hides whichKey", () => {
    const config = makeConfig();
    renderHook(() => useKeyboardShortcuts(config));
    window.dispatchEvent(new KeyboardEvent("keyup", { key: "Meta", bubbles: true }));
    expect(config.setWhichKeyVisible).toHaveBeenCalledWith(false);
  });
});

describe("useKeyboardShortcuts — cleanup on unmount", () => {
  it("removes event listeners on unmount", () => {
    const config = makeConfig();
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderHook(() => useKeyboardShortcuts(config));
    unmount();
    // Two useEffects: one for meta visibility (keydown+keyup) and one for shortcuts (keydown)
    const removedEvents = removeSpy.mock.calls.map(c => c[0]);
    expect(removedEvents).toContain("keydown");
    expect(removedEvents).toContain("keyup");
    removeSpy.mockRestore();
  });
});
