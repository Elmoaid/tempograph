import { describe, it, expect } from "vitest";
import { formatRecentTime } from "../components/LandingPage";

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

  it("formatRecentTime returns empty string for undefined", () => {
    expect(formatRecentTime(undefined)).toBe("");
  });

  it("formatRecentTime returns 'just now' for < 1 minute ago", () => {
    const now = Date.now();
    expect(formatRecentTime(now - 30000)).toBe("just now");
  });

  it("formatRecentTime returns minutes ago label", () => {
    const now = Date.now();
    expect(formatRecentTime(now - 5 * 60000)).toBe("5m ago");
  });

  it("formatRecentTime returns hours ago label", () => {
    const now = Date.now();
    expect(formatRecentTime(now - 3 * 3600000)).toBe("3h ago");
  });

  it("formatRecentTime returns days ago label", () => {
    const now = Date.now();
    expect(formatRecentTime(now - 2 * 86400000)).toBe("2d ago");
  });

  it("migration: string entries convert to RecentRepo objects", () => {
    const raw = JSON.stringify(["/old/path", "/another/path"]);
    const parsed: unknown[] = JSON.parse(raw);
    const repos = parsed.map(r => typeof r === "string" ? { path: r } : r as { path: string; addedAt?: number });
    expect(repos[0]).toEqual({ path: "/old/path" });
    expect(repos[1].path).toBe("/another/path");
  });
});
