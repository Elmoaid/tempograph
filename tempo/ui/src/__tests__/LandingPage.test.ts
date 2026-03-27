import { describe, it, expect } from "vitest";

describe("LandingPage logic", () => {
  it("recent repos truncates to 5 and deduplicates", () => {
    const existing = ["/a", "/b", "/c", "/d", "/e"];
    const newPath = "/f";
    const updated = [newPath, ...existing.filter(r => r !== newPath)].slice(0, 5);
    expect(updated).toEqual(["/f", "/a", "/b", "/c", "/d"]);
  });

  it("recent repos moves existing to front", () => {
    const existing = ["/a", "/b", "/c"];
    const newPath = "/b";
    const updated = [newPath, ...existing.filter(r => r !== newPath)].slice(0, 5);
    expect(updated).toEqual(["/b", "/a", "/c"]);
  });

  it("repo path display shows last 2 segments", () => {
    const path = "/Users/elmo/Desktop/tempograph";
    const display = path.split("/").slice(-2).join("/");
    expect(display).toBe("Desktop/tempograph");
  });

  it("empty input disables submit", () => {
    expect(!"  ".trim()).toBe(true);
    expect(!"".trim()).toBe(true);
    expect(!"/valid/path".trim()).toBe(false);
  });
});
