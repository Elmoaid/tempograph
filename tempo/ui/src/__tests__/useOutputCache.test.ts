import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useOutputCache } from "../hooks/useOutputCache";

// ── initial state ────────────────────────────────────────────────────────────

describe("useOutputCache — initial state", () => {
  it("cachedModes starts empty", () => {
    const { result } = renderHook(() => useOutputCache());
    expect(result.current.cachedModes.size).toBe(0);
  });

  it("outputTs starts null", () => {
    const { result } = renderHook(() => useOutputCache());
    expect(result.current.outputTs).toBeNull();
  });

  it("runDuration starts null", () => {
    const { result } = renderHook(() => useOutputCache());
    expect(result.current.runDuration).toBeNull();
  });

  it("outputCache ref starts as empty Map", () => {
    const { result } = renderHook(() => useOutputCache());
    expect(result.current.outputCache.current.size).toBe(0);
  });

  it("outputTsCache ref starts as empty Map", () => {
    const { result } = renderHook(() => useOutputCache());
    expect(result.current.outputTsCache.current.size).toBe(0);
  });

  it("runDurationCache ref starts as empty Map", () => {
    const { result } = renderHook(() => useOutputCache());
    expect(result.current.runDurationCache.current.size).toBe(0);
  });
});

// ── getCache ─────────────────────────────────────────────────────────────────

describe("useOutputCache — getCache", () => {
  it("returns undefined output for unknown key", () => {
    const { result } = renderHook(() => useOutputCache());
    const entry = result.current.getCache("overview");
    expect(entry.output).toBeUndefined();
  });

  it("returns undefined ts for unknown key", () => {
    const { result } = renderHook(() => useOutputCache());
    const entry = result.current.getCache("overview");
    expect(entry.ts).toBeUndefined();
  });

  it("returns null duration for unknown key", () => {
    const { result } = renderHook(() => useOutputCache());
    const entry = result.current.getCache("overview");
    expect(entry.duration).toBeNull();
  });

  it("returns stored output when key is set via ref", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.outputCache.current.set("focus", "some output"); });
    expect(result.current.getCache("focus").output).toBe("some output");
  });

  it("returns stored ts when key is set via ref", () => {
    const { result } = renderHook(() => useOutputCache());
    const now = Date.now();
    act(() => { result.current.outputTsCache.current.set("focus", now); });
    expect(result.current.getCache("focus").ts).toBe(now);
  });

  it("returns stored duration when key is set via ref", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.runDurationCache.current.set("focus", 1.5); });
    expect(result.current.getCache("focus").duration).toBe(1.5);
  });
});

// ── clearCache ───────────────────────────────────────────────────────────────

describe("useOutputCache — clearCache", () => {
  it("removes output from outputCache ref", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.outputCache.current.set("overview", "data"); });
    act(() => { result.current.clearCache("overview"); });
    expect(result.current.outputCache.current.has("overview")).toBe(false);
  });

  it("removes ts from outputTsCache ref", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.outputTsCache.current.set("overview", 1000); });
    act(() => { result.current.clearCache("overview"); });
    expect(result.current.outputTsCache.current.has("overview")).toBe(false);
  });

  it("removes key from cachedModes state", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.setCachedModes(new Set(["overview"])); });
    act(() => { result.current.clearCache("overview"); });
    expect(result.current.cachedModes.has("overview")).toBe(false);
  });

  it("does not affect other keys in cachedModes", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.setCachedModes(new Set(["overview", "focus"])); });
    act(() => { result.current.clearCache("overview"); });
    expect(result.current.cachedModes.has("focus")).toBe(true);
  });

  it("is a no-op for unknown key", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.setCachedModes(new Set(["focus"])); });
    act(() => { result.current.clearCache("unknown"); });
    expect(result.current.cachedModes.size).toBe(1);
  });
});

// ── state setters ────────────────────────────────────────────────────────────

describe("useOutputCache — state setters", () => {
  it("setOutputTs updates outputTs", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.setOutputTs(9999); });
    expect(result.current.outputTs).toBe(9999);
  });

  it("setRunDuration updates runDuration", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.setRunDuration(2.3); });
    expect(result.current.runDuration).toBe(2.3);
  });

  it("setCachedModes updates cachedModes", () => {
    const { result } = renderHook(() => useOutputCache());
    act(() => { result.current.setCachedModes(new Set(["overview", "hotspots"])); });
    expect(result.current.cachedModes.size).toBe(2);
    expect(result.current.cachedModes.has("hotspots")).toBe(true);
  });
});

// ── module exports ───────────────────────────────────────────────────────────

describe("useOutputCache — module exports", () => {
  it("exports useOutputCache as a function", async () => {
    const mod = await import("../hooks/useOutputCache");
    expect(typeof mod.useOutputCache).toBe("function");
  });
});
