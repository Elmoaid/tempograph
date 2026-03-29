import { renderHook, act } from "@testing-library/react";
import { useRunHistory, updateRunHistory, type RunHistoryEntry } from "../hooks/useRunHistory";

describe("useRunHistory", () => {
  it("starts with empty history", () => {
    const { result } = renderHook(() => useRunHistory());
    expect(result.current.runHistory).toEqual([]);
  });

  it("addRunHistory adds an entry", () => {
    const { result } = renderHook(() => useRunHistory());
    act(() => { result.current.addRunHistory("overview", ""); });
    expect(result.current.runHistory).toHaveLength(1);
    expect(result.current.runHistory[0]).toEqual({ mode: "overview", args: "" });
  });

  it("addRunHistory prepends (most recent first)", () => {
    const { result } = renderHook(() => useRunHistory());
    act(() => { result.current.addRunHistory("overview", ""); });
    act(() => { result.current.addRunHistory("focus", "--query Foo"); });
    expect(result.current.runHistory[0]).toEqual({ mode: "focus", args: "--query Foo" });
    expect(result.current.runHistory[1]).toEqual({ mode: "overview", args: "" });
  });

  it("addRunHistory caps at 5 entries", () => {
    const { result } = renderHook(() => useRunHistory());
    for (let i = 0; i < 6; i++) {
      act(() => { result.current.addRunHistory(`mode${i}`, ""); });
    }
    expect(result.current.runHistory).toHaveLength(5);
    expect(result.current.runHistory[0]).toEqual({ mode: "mode5", args: "" });
  });

  it("addRunHistory deduplicates: moves existing entry to front", () => {
    const { result } = renderHook(() => useRunHistory());
    act(() => { result.current.addRunHistory("overview", ""); });
    act(() => { result.current.addRunHistory("focus", "--query Foo"); });
    act(() => { result.current.addRunHistory("overview", ""); });
    expect(result.current.runHistory).toHaveLength(2);
    expect(result.current.runHistory[0]).toEqual({ mode: "overview", args: "" });
  });

  it("module exports: RunHistoryEntry, updateRunHistory, useRunHistory", () => {
    const entry: RunHistoryEntry = { mode: "test", args: "" };
    expect(entry.mode).toBe("test");
    expect(typeof updateRunHistory).toBe("function");
    expect(typeof useRunHistory).toBe("function");
  });
});
