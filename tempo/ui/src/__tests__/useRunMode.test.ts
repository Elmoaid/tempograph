import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { getRunPhaseLabel, useRunMode } from "../hooks/useRunMode";

vi.mock("../components/tempo", () => ({
  runTempo: vi.fn().mockResolvedValue({ output: "mock output" }),
}));

vi.mock("../components/modes", () => ({
  MODES: [{ mode: "overview", label: "Overview", argPrefix: "--query" }],
  loadHistory: vi.fn().mockReturnValue([]),
  saveHistory: vi.fn(),
}));

// ── getRunPhaseLabel ─────────────────────────────────────────────────────────

describe("getRunPhaseLabel", () => {
  it("returns 'Indexing...' at 0ms", () => {
    expect(getRunPhaseLabel(0)).toBe("Indexing...");
  });

  it("returns 'Indexing...' just below 800ms threshold", () => {
    expect(getRunPhaseLabel(799)).toBe("Indexing...");
  });

  it("returns 'Analyzing...' at exactly 800ms", () => {
    expect(getRunPhaseLabel(800)).toBe("Analyzing...");
  });

  it("returns 'Analyzing...' between 800ms and 2500ms", () => {
    expect(getRunPhaseLabel(1500)).toBe("Analyzing...");
  });

  it("returns 'Analyzing...' just below 2500ms threshold", () => {
    expect(getRunPhaseLabel(2499)).toBe("Analyzing...");
  });

  it("returns 'Rendering...' at exactly 2500ms", () => {
    expect(getRunPhaseLabel(2500)).toBe("Rendering...");
  });

  it("returns 'Rendering...' for large elapsed values", () => {
    expect(getRunPhaseLabel(10000)).toBe("Rendering...");
  });
});

// ── useRunMode ───────────────────────────────────────────────────────────────

function makeConfig(overrides: Record<string, unknown> = {}) {
  return {
    repoPath: "/test/repo",
    excludeDirs: [],
    activeMode: "overview",
    activeKit: null,
    modeArgs: "",
    modeRunning: false,
    outputCache: { current: new Map<string, string>() },
    outputTsCache: { current: new Map<string, number>() },
    runDurationCache: { current: new Map<string, number>() },
    runStart: { current: null as number | null },
    setElapsed: vi.fn(),
    setModeRunning: vi.fn(),
    setModeOutput: vi.fn(),
    setOutputTs: vi.fn(),
    setRunDuration: vi.fn(),
    setCachedModes: vi.fn(),
    setHistory: vi.fn(),
    ...overrides,
  };
}

describe("useRunMode", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("statusText starts as empty string", () => {
    const config = makeConfig();
    const { result } = renderHook(() => useRunMode(config));
    expect(result.current.statusText).toBe("");
  });

  it("cancelMode sets modeRunning to false", () => {
    const setModeRunning = vi.fn();
    const config = makeConfig({ setModeRunning });
    const { result } = renderHook(() => useRunMode(config));
    act(() => { result.current.cancelMode(); });
    expect(setModeRunning).toHaveBeenCalledWith(false);
  });

  it("cancelMode sets modeOutput to '[Cancelled]'", () => {
    const setModeOutput = vi.fn();
    const config = makeConfig({ setModeOutput });
    const { result } = renderHook(() => useRunMode(config));
    act(() => { result.current.cancelMode(); });
    expect(setModeOutput).toHaveBeenCalledWith("[Cancelled]");
  });

  it("cancelMode clears statusText", async () => {
    const config = makeConfig();
    const { result } = renderHook(() => useRunMode(config));
    // Start a run to set status text, then immediately cancel
    act(() => { result.current.runMode(); });
    act(() => { result.current.cancelMode(); });
    expect(result.current.statusText).toBe("");
  });

  it("runMode returns false when modeRunning is true", async () => {
    const config = makeConfig({ modeRunning: true });
    const { result } = renderHook(() => useRunMode(config));
    let returnValue: boolean | undefined;
    await act(async () => {
      returnValue = await result.current.runMode();
    });
    expect(returnValue).toBe(false);
  });

  it("runMode returns false when repoPath is empty", async () => {
    const config = makeConfig({ repoPath: "" });
    const { result } = renderHook(() => useRunMode(config));
    let returnValue: boolean | undefined;
    await act(async () => {
      returnValue = await result.current.runMode();
    });
    expect(returnValue).toBe(false);
  });
});

// ── module exports ───────────────────────────────────────────────────────────

describe("useRunMode — module exports", () => {
  it("exports getRunPhaseLabel as a function", async () => {
    const mod = await import("../hooks/useRunMode");
    expect(typeof mod.getRunPhaseLabel).toBe("function");
  });

  it("exports useRunMode as a function", async () => {
    const mod = await import("../hooks/useRunMode");
    expect(typeof mod.useRunMode).toBe("function");
  });
});
