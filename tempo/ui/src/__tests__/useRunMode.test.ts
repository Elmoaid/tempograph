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
    runStartRef: { current: null as number | null },
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

// ── cancel serial mechanism ──────────────────────────────────────────────────

describe("useRunMode — cancel serial mechanism", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("cancelMode sets output to '[Cancelled]' and stops running", () => {
    const setModeRunning = vi.fn();
    const setModeOutput = vi.fn();
    const config = makeConfig({ setModeRunning, setModeOutput });
    const { result } = renderHook(() => useRunMode(config));
    act(() => { result.current.cancelMode(); });
    expect(setModeOutput).toHaveBeenCalledWith("[Cancelled]");
    expect(setModeRunning).toHaveBeenCalledWith(false);
  });

  it("multiple cancels increment serial without error", () => {
    const config = makeConfig();
    const { result } = renderHook(() => useRunMode(config));
    act(() => { result.current.cancelMode(); });
    act(() => { result.current.cancelMode(); });
    act(() => { result.current.cancelMode(); });
    // Should not throw — each cancel increments serial
    expect(config.setModeRunning).toHaveBeenCalledTimes(3);
  });

  it("cancel during run discards stale result", async () => {
    // Make runTempo hang so we can cancel before it resolves
    const { runTempo } = await import("../components/tempo");
    let resolveRun!: (v: { output: string }) => void;
    (runTempo as ReturnType<typeof vi.fn>).mockImplementationOnce(
      () => new Promise(resolve => { resolveRun = resolve; })
    );

    const setModeOutput = vi.fn();
    const config = makeConfig({ setModeOutput });
    const { result } = renderHook(() => useRunMode(config));

    // Start run
    let runPromise: Promise<boolean>;
    await act(async () => { runPromise = result.current.runMode(); });

    // Cancel before resolve
    act(() => { result.current.cancelMode(); });
    expect(setModeOutput).toHaveBeenCalledWith("[Cancelled]");

    // Now resolve the stale run — the result should be discarded
    await act(async () => {
      resolveRun({ output: "stale result" });
      await runPromise!;
    });

    // The last call to setModeOutput should still be "[Cancelled]", not "stale result"
    const calls = setModeOutput.mock.calls.map((c: [string]) => c[0]);
    expect(calls[calls.length - 1]).toBe("[Cancelled]");
  });
});

// ── error handling ──────────────────────────────────────────────────────────

describe("useRunMode — error handling", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("sets error message when runTempo throws", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("network failure"));

    const setModeOutput = vi.fn();
    const config = makeConfig({ setModeOutput });
    const { result } = renderHook(() => useRunMode(config));

    await act(async () => { await result.current.runMode(); });

    expect(setModeOutput).toHaveBeenCalledWith(
      "Failed to run mode. Check that tempo is installed."
    );
  });

  it("returns false when runTempo throws", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("fail"));

    const config = makeConfig();
    const { result } = renderHook(() => useRunMode(config));

    let returnValue: boolean | undefined;
    await act(async () => { returnValue = await result.current.runMode(); });
    expect(returnValue).toBe(false);
  });

  it("sets modeRunning to false after error", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("fail"));

    const setModeRunning = vi.fn();
    const config = makeConfig({ setModeRunning });
    const { result } = renderHook(() => useRunMode(config));

    await act(async () => { await result.current.runMode(); });

    // Last call should be false (stopped running)
    const lastCall = setModeRunning.mock.calls[setModeRunning.mock.calls.length - 1];
    expect(lastCall[0]).toBe(false);
  });

  it("clears statusText after error", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("fail"));

    const config = makeConfig();
    const { result } = renderHook(() => useRunMode(config));

    await act(async () => { await result.current.runMode(); });
    expect(result.current.statusText).toBe("");
  });
});

// ── runMode behavior ────────────────────────────────────────────────────────

describe("useRunMode — runMode behavior", () => {
  afterEach(() => { vi.clearAllMocks(); });

  it("sets modeRunning to true at start of run", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ output: "ok" });

    const setModeRunning = vi.fn();
    const config = makeConfig({ setModeRunning });
    const { result } = renderHook(() => useRunMode(config));

    await act(async () => { await result.current.runMode(); });

    // First call should be true (started running)
    expect(setModeRunning.mock.calls[0][0]).toBe(true);
  });

  it("clears modeOutput at start of run", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ output: "ok" });

    const setModeOutput = vi.fn();
    const config = makeConfig({ setModeOutput });
    const { result } = renderHook(() => useRunMode(config));

    await act(async () => { await result.current.runMode(); });

    // First call should clear output
    expect(setModeOutput.mock.calls[0][0]).toBe("");
  });

  it("resets elapsed to 0 at start of run", async () => {
    const { runTempo } = await import("../components/tempo");
    (runTempo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ output: "ok" });

    const setElapsed = vi.fn();
    const config = makeConfig({ setElapsed });
    const { result } = renderHook(() => useRunMode(config));

    await act(async () => { await result.current.runMode(); });
    expect(setElapsed).toHaveBeenCalledWith(0);
  });

  it("sets initial status to 'Indexing...'", async () => {
    const { runTempo } = await import("../components/tempo");
    let resolveRun!: (v: { output: string }) => void;
    (runTempo as ReturnType<typeof vi.fn>).mockImplementationOnce(
      () => new Promise(resolve => { resolveRun = resolve; })
    );

    const config = makeConfig();
    const { result } = renderHook(() => useRunMode(config));

    let runPromise: Promise<boolean>;
    await act(async () => { runPromise = result.current.runMode(); });
    expect(result.current.statusText).toBe("Indexing...");

    // Clean up
    await act(async () => {
      resolveRun({ output: "done" });
      await runPromise!;
    });
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
