import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRunHistory, updateRunHistory, type RunHistoryEntry } from "../hooks/useRunHistory";

// ── localStorage stub ─────────────────────────────────────────────────────────

const makeStorage = () => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = String(value); },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
};

const storage = makeStorage();
vi.stubGlobal("localStorage", storage);

// ── vi.mock must use static string, not variable ───────────────────────────────
vi.mock("../components/modes", () => ({
  loadHistory: (mode: string): string[] => {
    const key = `tempo-history-${mode}`;
    try { return JSON.parse(storage.getItem(key) || "[]"); } catch { return []; }
  },
}));

beforeEach(() => {
  storage.clear();
});

// ── updateRunHistory (pure function) ─────────────────────────────────────────

describe("updateRunHistory", () => {
  it("prepends a new entry", () => {
    const result = updateRunHistory([], { mode: "overview", args: "" });
    expect(result).toEqual([{ mode: "overview", args: "" }]);
  });

  it("deduplicates: moves existing to front", () => {
    const prev: RunHistoryEntry[] = [
      { mode: "focus", args: "--query Foo" },
      { mode: "overview", args: "" },
    ];
    const result = updateRunHistory(prev, { mode: "overview", args: "" });
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({ mode: "overview", args: "" });
    expect(result[1]).toEqual({ mode: "focus", args: "--query Foo" });
  });

  it("caps at max (default 5)", () => {
    const prev: RunHistoryEntry[] = Array.from({ length: 5 }, (_, i) => ({ mode: `m${i}`, args: "" }));
    const result = updateRunHistory(prev, { mode: "new", args: "" });
    expect(result).toHaveLength(5);
    expect(result[0]).toEqual({ mode: "new", args: "" });
  });

  it("respects custom max", () => {
    const prev: RunHistoryEntry[] = [{ mode: "a", args: "" }, { mode: "b", args: "" }];
    const result = updateRunHistory(prev, { mode: "c", args: "" }, 2);
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({ mode: "c", args: "" });
  });
});

// ── runHistory state ──────────────────────────────────────────────────────────

describe("useRunHistory — runHistory", () => {
  it("starts with empty runHistory", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.runHistory).toEqual([]);
  });

  it("addRunHistory adds an entry", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    act(() => { result.current.addRunHistory("overview", ""); });
    expect(result.current.runHistory).toHaveLength(1);
    expect(result.current.runHistory[0]).toEqual({ mode: "overview", args: "" });
  });

  it("addRunHistory prepends (most recent first)", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    act(() => { result.current.addRunHistory("overview", ""); });
    act(() => { result.current.addRunHistory("focus", "--query Foo"); });
    expect(result.current.runHistory[0]).toEqual({ mode: "focus", args: "--query Foo" });
    expect(result.current.runHistory[1]).toEqual({ mode: "overview", args: "" });
  });

  it("addRunHistory caps at 5 entries", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    for (let i = 0; i < 6; i++) {
      act(() => { result.current.addRunHistory(`mode${i}`, ""); });
    }
    expect(result.current.runHistory).toHaveLength(5);
    expect(result.current.runHistory[0]).toEqual({ mode: "mode5", args: "" });
  });

  it("addRunHistory deduplicates: moves existing entry to front", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    act(() => { result.current.addRunHistory("overview", ""); });
    act(() => { result.current.addRunHistory("focus", "--query Foo"); });
    act(() => { result.current.addRunHistory("overview", ""); });
    expect(result.current.runHistory).toHaveLength(2);
    expect(result.current.runHistory[0]).toEqual({ mode: "overview", args: "" });
  });

  it("runHistory is independent across two repo instances", () => {
    const { result: r1 } = renderHook(() => useRunHistory("/repo/a"));
    const { result: r2 } = renderHook(() => useRunHistory("/repo/b"));
    act(() => { r1.current.addRunHistory("focus", "Foo"); });
    expect(r1.current.runHistory).toHaveLength(1);
    expect(r2.current.runHistory).toHaveLength(0);
  });
});

// ── history (per-mode args) state ─────────────────────────────────────────────

describe("useRunHistory — history", () => {
  it("starts with empty history when no localStorage data", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.history).toEqual([]);
  });

  it("initialises from localStorage using lastModeKey", () => {
    storage.setItem("tempo-last-mode-/repo", "focus");
    storage.setItem("tempo-history-focus", JSON.stringify(["--query Bar", "--query Baz"]));
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.history).toEqual(["--query Bar", "--query Baz"]);
  });

  it("falls back to overview history when no lastMode stored", () => {
    storage.setItem("tempo-history-overview", JSON.stringify(["prev"]));
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.history).toEqual(["prev"]);
  });

  it("returns empty array when localStorage is corrupt", () => {
    storage.setItem("tempo-last-mode-/repo", "focus");
    storage.setItem("tempo-history-focus", "not-json");
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.history).toEqual([]);
  });
});

// ── loadModeHistory callback ──────────────────────────────────────────────────

describe("useRunHistory — loadModeHistory", () => {
  it("updates history to the given mode's stored history", () => {
    storage.setItem("tempo-history-hotspots", JSON.stringify(["q1", "q2"]));
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.history).toEqual([]);
    act(() => { result.current.loadModeHistory("hotspots"); });
    expect(result.current.history).toEqual(["q1", "q2"]);
  });

  it("clears history when switching to a mode with no history", () => {
    storage.setItem("tempo-last-mode-/repo", "hotspots");
    storage.setItem("tempo-history-hotspots", JSON.stringify(["q1"]));
    const { result } = renderHook(() => useRunHistory("/repo"));
    expect(result.current.history).toEqual(["q1"]);
    act(() => { result.current.loadModeHistory("overview"); });
    expect(result.current.history).toEqual([]);
  });

  it("picks up newly written history on subsequent loads", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    storage.setItem("tempo-history-blast", JSON.stringify(["symbol1"]));
    act(() => { result.current.loadModeHistory("blast"); });
    expect(result.current.history).toEqual(["symbol1"]);
    storage.setItem("tempo-history-blast", JSON.stringify(["symbol2", "symbol1"]));
    act(() => { result.current.loadModeHistory("blast"); });
    expect(result.current.history).toEqual(["symbol2", "symbol1"]);
  });

  it("loadModeHistory is stable (same reference across renders)", () => {
    const { result, rerender } = renderHook(() => useRunHistory("/repo"));
    const ref1 = result.current.loadModeHistory;
    rerender();
    expect(result.current.loadModeHistory).toBe(ref1);
  });
});

// ── setHistory direct setter ──────────────────────────────────────────────────

describe("useRunHistory — setHistory", () => {
  it("sets history to the provided array", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    act(() => { result.current.setHistory(["a", "b"]); });
    expect(result.current.history).toEqual(["a", "b"]);
  });

  it("setting to empty array clears history", () => {
    storage.setItem("tempo-last-mode-/repo", "focus");
    storage.setItem("tempo-history-focus", JSON.stringify(["x"]));
    const { result } = renderHook(() => useRunHistory("/repo"));
    act(() => { result.current.setHistory([]); });
    expect(result.current.history).toEqual([]);
  });

  it("setting history does not affect runHistory", () => {
    const { result } = renderHook(() => useRunHistory("/repo"));
    act(() => { result.current.addRunHistory("overview", ""); });
    act(() => { result.current.setHistory(["q1"]); });
    expect(result.current.runHistory).toHaveLength(1);
    expect(result.current.history).toEqual(["q1"]);
  });
});

// ── repoPath namespacing ──────────────────────────────────────────────────────

describe("useRunHistory — repoPath namespacing", () => {
  it("different repos load independent initial histories", () => {
    storage.setItem("tempo-last-mode-/repo/a", "focus");
    storage.setItem("tempo-history-focus", JSON.stringify(["qA"]));
    storage.setItem("tempo-last-mode-/repo/b", "blast");
    storage.setItem("tempo-history-blast", JSON.stringify(["qB"]));
    const { result: rA } = renderHook(() => useRunHistory("/repo/a"));
    const { result: rB } = renderHook(() => useRunHistory("/repo/b"));
    expect(rA.current.history).toEqual(["qA"]);
    expect(rB.current.history).toEqual(["qB"]);
  });

  it("loadModeHistory on one repo does not affect the other", () => {
    const { result: rA } = renderHook(() => useRunHistory("/repo/a"));
    const { result: rB } = renderHook(() => useRunHistory("/repo/b"));
    storage.setItem("tempo-history-overview", JSON.stringify(["shared"]));
    act(() => { rA.current.loadModeHistory("overview"); });
    expect(rA.current.history).toEqual(["shared"]);
    expect(rB.current.history).toEqual([]);
  });
});

// ── module exports ────────────────────────────────────────────────────────────

describe("module exports", () => {
  it("exports RunHistoryEntry type, updateRunHistory, useRunHistory", () => {
    const entry: RunHistoryEntry = { mode: "test", args: "" };
    expect(entry.mode).toBe("test");
    expect(typeof updateRunHistory).toBe("function");
    expect(typeof useRunHistory).toBe("function");
  });
});
