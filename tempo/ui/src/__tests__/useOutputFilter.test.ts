import { describe, it, expect } from "vitest";
import { applyOutputFilter } from "../hooks/useOutputFilter";

describe("applyOutputFilter", () => {
  it("returns null for empty query", () => {
    expect(applyOutputFilter("line1\nline2", "")).toBeNull();
  });

  it("returns null for whitespace-only query", () => {
    expect(applyOutputFilter("line1\nline2", "   ")).toBeNull();
  });

  it("returns null for empty output", () => {
    expect(applyOutputFilter("", "foo")).toBeNull();
  });

  it("filters lines that match the query (case-insensitive)", () => {
    const out = "foo bar\nbaz qux\nFOO baz";
    const result = applyOutputFilter(out, "foo");
    expect(result).not.toBeNull();
    expect(result!.filtered).toBe("foo bar\nFOO baz");
    expect(result!.matchCount).toBe(2);
  });

  it("returns matchCount 0 and empty filtered when no lines match", () => {
    const result = applyOutputFilter("line1\nline2\nline3", "zzz");
    expect(result).not.toBeNull();
    expect(result!.filtered).toBe("");
    expect(result!.matchCount).toBe(0);
  });

  it("matches partial substrings within lines", () => {
    const result = applyOutputFilter("hello world\ngoodbye moon\nhello again", "hell");
    expect(result!.matchCount).toBe(2);
    expect(result!.filtered).toBe("hello world\nhello again");
  });

  it("is case-insensitive for mixed-case query", () => {
    const result = applyOutputFilter("Alpha\nbeta\nALPHA", "aLpHa");
    expect(result!.matchCount).toBe(2);
  });

  it("single-line output that matches returns that line", () => {
    const result = applyOutputFilter("single line match", "match");
    expect(result!.filtered).toBe("single line match");
    expect(result!.matchCount).toBe(1);
  });

  it("single-line output that does not match returns empty + count 0", () => {
    const result = applyOutputFilter("single line", "zzz");
    expect(result!.filtered).toBe("");
    expect(result!.matchCount).toBe(0);
  });

  it("module exports applyOutputFilter as named export", () => {
    expect(typeof applyOutputFilter).toBe("function");
  });
});
