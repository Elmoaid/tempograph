import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// ── Mocks ───────────────────────────────────────────────────────────────────

vi.mock("../components/tempo", () => ({
  runTempo: vi.fn().mockResolvedValue({ output: "mock output" }),
  readFile: vi.fn().mockResolvedValue("{}"),
  reportFeedback: vi.fn().mockResolvedValue(undefined),
  saveOutput: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  save: vi.fn().mockResolvedValue(null),
}));

vi.mock("../components/modes", () => ({
  MODES: [
    { mode: "overview", label: "Overview", tag: "mcp", group: "analyze" },
    { mode: "focus", label: "Focus", tag: "mcp", group: "analyze", argPrefix: "--query", hint: "what?" },
    { mode: "blast", label: "Blast", tag: "mcp", group: "analyze", argPrefix: "--query" },
  ],
  loadHistory: vi.fn().mockReturnValue([]),
  saveHistory: vi.fn(),
  saveRecentCommand: vi.fn(),
  loadRecentCommands: vi.fn().mockReturnValue([]),
}));

vi.mock("../components/kits", () => ({
  BUILTIN_KITS: [
    { id: "explore", label: "Explore", icon: () => null, description: "Explore kit" },
  ],
}));

import { useModeRunner } from "../components/useModeRunner";

// ── Initial state ───────────────────────────────────────────────────────────

describe("useModeRunner — initial state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("defaults activeMode to 'overview'", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.activeMode).toBe("overview");
  });

  it("uses localStorage for initial activeMode if set", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.activeMode).toBe("focus");
  });

  it("starts with empty modeOutput", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.modeOutput).toBe("");
  });

  it("starts with modeRunning false", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.modeRunning).toBe(false);
  });

  it("starts with paletteOpen false", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.paletteOpen).toBe(false);
  });

  it("starts with prevOutput null", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.prevOutput).toBeNull();
  });

  it("starts with empty modeArgs", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.modeArgs).toBe("");
  });

  it("loads persisted modeArgs for active mode", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem("tempo-mode-args-/test/repo-focus", "mySymbol");
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.modeArgs).toBe("mySymbol");
  });

  it("allKits includes BUILTIN_KITS", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.allKits.length).toBeGreaterThanOrEqual(1);
    expect(result.current.allKits[0].id).toBe("explore");
  });

  it("activeModeInfo returns info for current mode", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    expect(result.current.activeModeInfo).toBeDefined();
    expect(result.current.activeModeInfo?.mode).toBe("overview");
  });
});

// ── switchMode ──────────────────────────────────────────────────────────────

describe("useModeRunner — switchMode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("changes activeMode", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchMode("focus"); });
    expect(result.current.activeMode).toBe("focus");
  });

  it("persists new mode to localStorage", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchMode("blast"); });
    expect(localStorage.getItem("tempo-last-mode-/test/repo")).toBe("blast");
  });

  it("clears activeKit when switching to a mode", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchKit("explore"); });
    expect(result.current.activeKit).toBe("explore");
    act(() => { result.current.switchMode("focus"); });
    expect(result.current.activeKit).toBeNull();
  });

  it("persists previous mode args before switching", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.setModeArgs("old-arg"); });
    act(() => { result.current.switchMode("focus"); });
    expect(localStorage.getItem("tempo-mode-args-/test/repo-overview")).toBe("old-arg");
  });

  it("restores saved modeArgs for the new mode", () => {
    localStorage.setItem("tempo-mode-args-/test/repo-focus", "saved-arg");
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchMode("focus"); });
    expect(result.current.modeArgs).toBe("saved-arg");
  });

  it("clears prevOutput on switch", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchMode("focus"); });
    expect(result.current.prevOutput).toBeNull();
  });

  it("updates activeModeInfo for new mode", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchMode("focus"); });
    expect(result.current.activeModeInfo?.mode).toBe("focus");
  });
});

// ── switchKit ───────────────────────────────────────────────────────────────

describe("useModeRunner — switchKit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("sets activeKit to the kit id", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchKit("explore"); });
    expect(result.current.activeKit).toBe("explore");
  });

  it("sets activeMode to 'kit'", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchKit("explore"); });
    expect(result.current.activeMode).toBe("kit");
  });

  it("restores saved args for the kit", () => {
    localStorage.setItem("tempo-mode-args-/test/repo-kit:explore", "kit-arg");
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.switchKit("explore"); });
    expect(result.current.modeArgs).toBe("kit-arg");
  });
});

// ── clearOutput ─────────────────────────────────────────────────────────────

describe("useModeRunner — clearOutput", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("sets modeOutput to empty string", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.clearOutput(); });
    expect(result.current.modeOutput).toBe("");
  });

  it("clears outputTs", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.clearOutput(); });
    expect(result.current.outputTs).toBeNull();
  });
});

// ── setters ─────────────────────────────────────────────────────────────────

describe("useModeRunner — setters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("setModeArgs updates modeArgs", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.setModeArgs("new-arg"); });
    expect(result.current.modeArgs).toBe("new-arg");
  });

  it("setPaletteOpen toggles palette", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.setPaletteOpen(true); });
    expect(result.current.paletteOpen).toBe(true);
  });

  it("setHelpOpen toggles help", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.setHelpOpen(true); });
    expect(result.current.showHelp).toBe(true);
  });

  it("setSidebarTab switches sidebar", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.setSidebarTab("modes"); });
    expect(result.current.sidebarTab).toBe("modes");
  });

  it("setHistoryOpen controls history panel", () => {
    const { result } = renderHook(() => useModeRunner("/test/repo"));
    act(() => { result.current.setHistoryOpen(true); });
    expect(result.current.historyOpen).toBe(true);
  });
});

// ── module exports ──────────────────────────────────────────────────────────

describe("useModeRunner — module exports", () => {
  it("exports useModeRunner as a function", async () => {
    const mod = await import("../components/useModeRunner");
    expect(typeof mod.useModeRunner).toBe("function");
  });
});
