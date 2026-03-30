import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useModeSelectionState, lastModeKey } from "../hooks/useModeSelectionState";

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

beforeEach(() => {
  storage.clear();
});

// ── lastModeKey helper ────────────────────────────────────────────────────────

describe("lastModeKey", () => {
  it("returns the expected key format", () => {
    expect(lastModeKey("/my/repo")).toBe("tempo-last-mode-/my/repo");
  });

  it("includes the full path (no trailing slash trimming)", () => {
    expect(lastModeKey("/a/b/c/")).toBe("tempo-last-mode-/a/b/c/");
  });

  it("different paths produce different keys", () => {
    expect(lastModeKey("/repo/a")).not.toBe(lastModeKey("/repo/b"));
  });
});

// ── initial state ─────────────────────────────────────────────────────────────

describe("useModeSelectionState — initial state", () => {
  it("activeMode defaults to 'overview' when no localStorage entry", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    expect(result.current.activeMode).toBe("overview");
  });

  it("activeKit starts as null", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    expect(result.current.activeKit).toBeNull();
  });

  it("reads activeMode from localStorage if set", () => {
    localStorage.setItem(lastModeKey("/test/repo"), "focus");
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    expect(result.current.activeMode).toBe("focus");
  });

  it("falls back to 'overview' when localStorage value is empty string", () => {
    localStorage.setItem(lastModeKey("/test/repo"), "");
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    expect(result.current.activeMode).toBe("overview");
  });

  it("preserves arbitrary mode slugs from localStorage", () => {
    localStorage.setItem(lastModeKey("/test/repo"), "blast");
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    expect(result.current.activeMode).toBe("blast");
  });

  it("uses the repoPath to namespace localStorage (different paths are independent)", () => {
    localStorage.setItem(lastModeKey("/repo/a"), "hotspots");
    localStorage.setItem(lastModeKey("/repo/b"), "dead");
    const { result: a } = renderHook(() => useModeSelectionState("/repo/a"));
    const { result: b } = renderHook(() => useModeSelectionState("/repo/b"));
    expect(a.current.activeMode).toBe("hotspots");
    expect(b.current.activeMode).toBe("dead");
  });
});

// ── setActiveMode ─────────────────────────────────────────────────────────────

describe("useModeSelectionState — setActiveMode", () => {
  it("updates activeMode", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveMode("focus"); });
    expect(result.current.activeMode).toBe("focus");
  });

  it("does not affect activeKit", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveKit("explore"); });
    act(() => { result.current.setActiveMode("blast"); });
    expect(result.current.activeKit).toBe("explore");
  });

  it("can change mode multiple times", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveMode("focus"); });
    act(() => { result.current.setActiveMode("hotspots"); });
    act(() => { result.current.setActiveMode("overview"); });
    expect(result.current.activeMode).toBe("overview");
  });
});

// ── setActiveKit ──────────────────────────────────────────────────────────────

describe("useModeSelectionState — setActiveKit", () => {
  it("updates activeKit from null to a value", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveKit("explore"); });
    expect(result.current.activeKit).toBe("explore");
  });

  it("can be set back to null", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveKit("explore"); });
    act(() => { result.current.setActiveKit(null); });
    expect(result.current.activeKit).toBeNull();
  });

  it("does not affect activeMode", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveKit("explore"); });
    expect(result.current.activeMode).toBe("overview");
  });

  it("can switch between different kit ids", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => { result.current.setActiveKit("explore"); });
    act(() => { result.current.setActiveKit("review"); });
    expect(result.current.activeKit).toBe("review");
  });
});

// ── independent updates ───────────────────────────────────────────────────────

describe("useModeSelectionState — independent updates", () => {
  it("both fields update independently", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => {
      result.current.setActiveMode("focus");
      result.current.setActiveKit("explore");
    });
    expect(result.current.activeMode).toBe("focus");
    expect(result.current.activeKit).toBe("explore");
  });

  it("clearing kit does not reset mode", () => {
    const { result } = renderHook(() => useModeSelectionState("/test/repo"));
    act(() => {
      result.current.setActiveMode("blast");
      result.current.setActiveKit("explore");
    });
    act(() => { result.current.setActiveKit(null); });
    expect(result.current.activeMode).toBe("blast");
    expect(result.current.activeKit).toBeNull();
  });
});

// ── module exports ─────────────────────────────────────────────────────────────

describe("useModeSelectionState — module exports", () => {
  it("exports useModeSelectionState as a function", async () => {
    const mod = await import("../hooks/useModeSelectionState");
    expect(typeof mod.useModeSelectionState).toBe("function");
  });

  it("exports lastModeKey as a function", async () => {
    const mod = await import("../hooks/useModeSelectionState");
    expect(typeof mod.lastModeKey).toBe("function");
  });
});
