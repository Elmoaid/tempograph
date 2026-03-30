import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useModeArgsState, modeArgsKey } from "../hooks/useModeArgsState";

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

// ── modeArgsKey helper ────────────────────────────────────────────────────────

describe("modeArgsKey", () => {
  it("returns the expected key format", () => {
    expect(modeArgsKey("/my/repo", "overview")).toBe("tempo-mode-args-/my/repo-overview");
  });

  it("includes the full path without trimming", () => {
    expect(modeArgsKey("/a/b/c/", "focus")).toBe("tempo-mode-args-/a/b/c/-focus");
  });

  it("different paths produce different keys for the same mode", () => {
    expect(modeArgsKey("/repo/a", "overview")).not.toBe(modeArgsKey("/repo/b", "overview"));
  });

  it("different modes produce different keys for the same path", () => {
    expect(modeArgsKey("/repo", "overview")).not.toBe(modeArgsKey("/repo", "focus"));
  });

  it("same path and mode return the same key", () => {
    expect(modeArgsKey("/repo", "blast")).toBe(modeArgsKey("/repo", "blast"));
  });

  it("handles kit: prefix in modeOrKit", () => {
    expect(modeArgsKey("/repo", "kit:explore")).toBe("tempo-mode-args-/repo-kit:explore");
  });

  it("kit keys are distinct from plain mode keys", () => {
    expect(modeArgsKey("/repo", "kit:explore")).not.toBe(modeArgsKey("/repo", "explore"));
  });

  it("handles empty string modeOrKit", () => {
    expect(modeArgsKey("/repo", "")).toBe("tempo-mode-args-/repo-");
  });

  it("handles empty string path", () => {
    expect(modeArgsKey("", "focus")).toBe("tempo-mode-args--focus");
  });
});

// ── initial state ─────────────────────────────────────────────────────────────

describe("useModeArgsState — initial state", () => {
  it("defaults to empty string when no localStorage entries", () => {
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("");
  });

  it("reads modeArgs from localStorage using the last-used mode", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem(modeArgsKey("/test/repo", "focus"), "--query MySymbol");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("--query MySymbol");
  });

  it("falls back to overview mode when no lastMode key in localStorage", () => {
    localStorage.setItem(modeArgsKey("/test/repo", "overview"), "someArg");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("someArg");
  });

  it("returns empty string when mode args key is absent", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "blast");
    // no modeArgsKey set for blast
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("");
  });

  it("uses overview as fallback when lastMode value is empty string", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "");
    localStorage.setItem(modeArgsKey("/test/repo", "overview"), "fallbackArg");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("fallbackArg");
  });

  it("does not read args for the wrong mode", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem(modeArgsKey("/test/repo", "overview"), "wrongArg");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("");
  });

  it("namespaces by repoPath — different repos are independent", () => {
    localStorage.setItem("tempo-last-mode-/repo/a", "focus");
    localStorage.setItem(modeArgsKey("/repo/a", "focus"), "argsA");
    localStorage.setItem("tempo-last-mode-/repo/b", "blast");
    localStorage.setItem(modeArgsKey("/repo/b", "blast"), "argsB");
    const { result: a } = renderHook(() => useModeArgsState("/repo/a"));
    const { result: b } = renderHook(() => useModeArgsState("/repo/b"));
    expect(a.current.modeArgs).toBe("argsA");
    expect(b.current.modeArgs).toBe("argsB");
  });

  it("two repos with the same last-mode do not share args", () => {
    localStorage.setItem("tempo-last-mode-/repo/a", "focus");
    localStorage.setItem(modeArgsKey("/repo/a", "focus"), "argsOnlyForA");
    const { result: a } = renderHook(() => useModeArgsState("/repo/a"));
    const { result: b } = renderHook(() => useModeArgsState("/repo/b"));
    expect(a.current.modeArgs).toBe("argsOnlyForA");
    expect(b.current.modeArgs).toBe("");
  });

  it("preserves whitespace in stored args", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem(modeArgsKey("/test/repo", "focus"), "  --query foo bar  ");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("  --query foo bar  ");
  });

  it("preserves args with special characters", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem(modeArgsKey("/test/repo", "focus"), "--query src/foo.ts:42");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("--query src/foo.ts:42");
  });

  it("works with kit: prefix in stored last-mode", () => {
    // last mode stored as kit key by switchKit
    localStorage.setItem("tempo-last-mode-/test/repo", "kit");
    // No kit args stored — result is ""
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    expect(result.current.modeArgs).toBe("");
  });
});

// ── setModeArgs ───────────────────────────────────────────────────────────────

describe("useModeArgsState — setModeArgs", () => {
  it("updates modeArgs", () => {
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    act(() => { result.current.setModeArgs("--query Foo"); });
    expect(result.current.modeArgs).toBe("--query Foo");
  });

  it("can set modeArgs to empty string", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem(modeArgsKey("/test/repo", "focus"), "initial");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    act(() => { result.current.setModeArgs(""); });
    expect(result.current.modeArgs).toBe("");
  });

  it("can change multiple times", () => {
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    act(() => { result.current.setModeArgs("first"); });
    act(() => { result.current.setModeArgs("second"); });
    act(() => { result.current.setModeArgs("third"); });
    expect(result.current.modeArgs).toBe("third");
  });

  it("can set args with flag syntax", () => {
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    act(() => { result.current.setModeArgs("--query MyClass --depth 2"); });
    expect(result.current.modeArgs).toBe("--query MyClass --depth 2");
  });

  it("can set args back to the initial value", () => {
    localStorage.setItem("tempo-last-mode-/test/repo", "focus");
    localStorage.setItem(modeArgsKey("/test/repo", "focus"), "original");
    const { result } = renderHook(() => useModeArgsState("/test/repo"));
    act(() => { result.current.setModeArgs("changed"); });
    act(() => { result.current.setModeArgs("original"); });
    expect(result.current.modeArgs).toBe("original");
  });

  it("updates are independent across two hooks with different repos", () => {
    const { result: a } = renderHook(() => useModeArgsState("/repo/a"));
    const { result: b } = renderHook(() => useModeArgsState("/repo/b"));
    act(() => { a.current.setModeArgs("argsA"); });
    expect(a.current.modeArgs).toBe("argsA");
    expect(b.current.modeArgs).toBe("");
  });
});

// ── module exports ─────────────────────────────────────────────────────────────

describe("useModeArgsState — module exports", () => {
  it("exports useModeArgsState as a function", async () => {
    const mod = await import("../hooks/useModeArgsState");
    expect(typeof mod.useModeArgsState).toBe("function");
  });

  it("exports modeArgsKey as a function", async () => {
    const mod = await import("../hooks/useModeArgsState");
    expect(typeof mod.modeArgsKey).toBe("function");
  });
});
