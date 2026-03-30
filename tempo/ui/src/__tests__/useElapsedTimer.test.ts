/**
 * Tests for useElapsedTimer hook.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useElapsedTimer } from "../hooks/useElapsedTimer";

afterEach(() => {
  vi.useRealTimers();
});

describe("useElapsedTimer", () => {
  it("returns elapsed=0 when not running", () => {
    const runStart = { current: null };
    const { result } = renderHook(() => useElapsedTimer(false, runStart));
    expect(result.current.elapsed).toBe(0);
  });

  it("returns elapsed=0 when modeRunning starts false and stays false", () => {
    vi.useFakeTimers();
    const runStart = { current: null };
    const { result } = renderHook(() => useElapsedTimer(false, runStart));
    act(() => { vi.advanceTimersByTime(2000); });
    expect(result.current.elapsed).toBe(0);
  });

  it("ticks elapsed when modeRunning is true", () => {
    vi.useFakeTimers();
    const now = Date.now();
    const runStart = { current: now };
    const { result, rerender } = renderHook(
      ({ running }: { running: boolean }) => useElapsedTimer(running, runStart),
      { initialProps: { running: false } },
    );
    act(() => { rerender({ running: true }); });
    act(() => {
      runStart.current = Date.now();
      vi.advanceTimersByTime(1500);
    });
    expect(result.current.elapsed).toBeGreaterThanOrEqual(1);
  });

  it("resets to 0 when modeRunning goes false", () => {
    vi.useFakeTimers();
    const runStart = { current: Date.now() };
    const { result, rerender } = renderHook(
      ({ running }: { running: boolean }) => useElapsedTimer(running, runStart),
      { initialProps: { running: true } },
    );
    act(() => { vi.advanceTimersByTime(3000); });
    act(() => { rerender({ running: false }); });
    expect(result.current.elapsed).toBe(0);
  });

  it("resetElapsed sets elapsed to 0", () => {
    vi.useFakeTimers();
    const runStart = { current: Date.now() };
    const { result } = renderHook(
      ({ running }: { running: boolean }) => useElapsedTimer(running, runStart),
      { initialProps: { running: true } },
    );
    act(() => { vi.advanceTimersByTime(2500); });
    act(() => { result.current.resetElapsed(); });
    expect(result.current.elapsed).toBe(0);
  });
});

describe("useElapsedTimer — module exports", () => {
  it("exports useElapsedTimer function", async () => {
    const mod = await import("../hooks/useElapsedTimer");
    expect(typeof mod.useElapsedTimer).toBe("function");
  });
});
