/**
 * Tests for loadHistory / saveHistory (modes.ts).
 * These use localStorage — available in jsdom.
 */
import { describe, it, expect } from "vitest";
import { loadHistory, saveHistory, historyKey, HISTORY_MAX } from "../components/modes";

describe("loadHistory", () => {
  it("returns empty array when nothing stored", () => {
    expect(loadHistory("focus")).toEqual([]);
  });

  it("returns previously saved queries", () => {
    localStorage.setItem(historyKey("focus"), JSON.stringify(["render"]));
    expect(loadHistory("focus")).toEqual(["render"]);
  });

  it("returns empty array on corrupt JSON", () => {
    localStorage.setItem(historyKey("focus"), "{{not valid");
    expect(loadHistory("focus")).toEqual([]);
  });

  it("is mode-scoped (different modes don't share history)", () => {
    localStorage.setItem(historyKey("focus"), JSON.stringify(["a"]));
    expect(loadHistory("blast")).toEqual([]);
  });
});

describe("saveHistory", () => {
  it("saves a query that can be loaded back", () => {
    saveHistory("focus", "renderItem");
    expect(loadHistory("focus")).toContain("renderItem");
  });

  it("deduplicates — repeated query moves to front", () => {
    saveHistory("focus", "foo");
    saveHistory("focus", "bar");
    saveHistory("focus", "foo");
    const h = loadHistory("focus");
    expect(h[0]).toBe("foo");
    expect(h.filter(q => q === "foo")).toHaveLength(1);
  });

  it(`caps at HISTORY_MAX (${HISTORY_MAX}) entries`, () => {
    for (let i = 0; i < HISTORY_MAX + 3; i++) saveHistory("focus", `q${i}`);
    expect(loadHistory("focus")).toHaveLength(HISTORY_MAX);
  });

  it("most recent query is first", () => {
    saveHistory("focus", "first");
    saveHistory("focus", "second");
    expect(loadHistory("focus")[0]).toBe("second");
  });
});
