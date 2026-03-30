import { describe, it, expect } from "vitest";
import { countMatches, useOutputSearch } from "../hooks/useOutputSearch";
import { renderHook, act } from "@testing-library/react";

// ── countMatches (pure function) ────────────────────────────────────────────

describe("countMatches", () => {
  it("returns 0 for blank query", () => {
    expect(countMatches("some output text", "")).toBe(0);
  });

  it("returns 0 for whitespace-only query", () => {
    expect(countMatches("some output text", "   ")).toBe(0);
  });

  it("returns 0 for empty output", () => {
    expect(countMatches("", "foo")).toBe(0);
  });

  it("counts single match", () => {
    expect(countMatches("foo bar baz", "bar")).toBe(1);
  });

  it("counts multiple non-overlapping matches", () => {
    expect(countMatches("foo foo foo", "foo")).toBe(3);
  });

  it("is case-insensitive", () => {
    expect(countMatches("Foo FOO foo", "foo")).toBe(3);
  });

  it("handles query with mixed case", () => {
    expect(countMatches("Hello World hello world", "HELLO")).toBe(2);
  });

  it("counts non-overlapping (aaa has 1 match of aa, not 2)", () => {
    // 'aa' in 'aaa': match at 0 (pos moves to 2), idx=2 would need pos=2, idx=lower.indexOf('aa',2)=no match
    // actually 'aaa'.indexOf('aa', 0)=0, pos=2; 'aaa'.indexOf('aa', 2)=-1; count=1
    expect(countMatches("aaa", "aa")).toBe(1);
  });

  it("returns 0 when no match", () => {
    expect(countMatches("hello world", "xyz")).toBe(0);
  });

  it("handles multiline output", () => {
    const output = "line one: error\nline two: error\nline three: ok";
    expect(countMatches(output, "error")).toBe(2);
  });

  it("handles special regex-like characters literally", () => {
    expect(countMatches("a.b a.b a.b", "a.b")).toBe(3);
  });
});

// ── useOutputSearch (hook) ──────────────────────────────────────────────────

describe("useOutputSearch", () => {
  it("starts inactive with empty search text", () => {
    const { result } = renderHook(() => useOutputSearch("some output"));
    expect(result.current.active).toBe(false);
    expect(result.current.searchText).toBe("");
    expect(result.current.matchCount).toBe(0);
    expect(result.current.currentMatch).toBe(0);
  });

  it("matchCount reflects countMatches", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.setSearchText("foo"));
    expect(result.current.matchCount).toBe(3);
  });

  it("currentMatch resets to 1 when searchText changes and matches exist", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.setSearchText("foo"));
    expect(result.current.currentMatch).toBe(1);
  });

  it("currentMatch stays 0 when no matches", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.setSearchText("xyz"));
    expect(result.current.currentMatch).toBe(0);
  });

  it("navigateMatch next wraps from last to first", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.setSearchText("foo"));
    // currentMatch=1 after search. Navigate next twice → 3, next again → wraps to 1
    act(() => result.current.navigateMatch("next"));
    act(() => result.current.navigateMatch("next"));
    expect(result.current.currentMatch).toBe(3);
    act(() => result.current.navigateMatch("next"));
    expect(result.current.currentMatch).toBe(1);
  });

  it("navigateMatch prev wraps from first to last", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.setSearchText("foo"));
    // currentMatch=1, prev → wraps to 3
    act(() => result.current.navigateMatch("prev"));
    expect(result.current.currentMatch).toBe(3);
  });

  it("navigateMatch does nothing when matchCount is 0", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.navigateMatch("next"));
    expect(result.current.currentMatch).toBe(0);
  });

  it("close resets state", () => {
    const { result } = renderHook(() => useOutputSearch("foo foo foo"));
    act(() => result.current.setSearchText("foo"));
    act(() => result.current.close());
    expect(result.current.active).toBe(false);
    expect(result.current.searchText).toBe("");
    expect(result.current.currentMatch).toBe(0);
  });
});

// ── module exports ──────────────────────────────────────────────────────────

describe("useOutputSearch module exports", () => {
  it("exports countMatches", () => {
    expect(typeof countMatches).toBe("function");
  });

  it("exports useOutputSearch", () => {
    expect(typeof useOutputSearch).toBe("function");
  });
});
