import { describe, it, expect } from "vitest";
import { parseCustomKits } from "../hooks/useCustomKits";

describe("parseCustomKits", () => {
  it("returns empty array for empty object", () => {
    expect(parseCustomKits("{}")).toEqual([]);
  });

  it("filters out entries with no steps", () => {
    const json = JSON.stringify({ empty_kit: { description: "no steps" } });
    expect(parseCustomKits(json)).toEqual([]);
  });

  it("filters out entries with empty steps array", () => {
    const json = JSON.stringify({ empty_kit: { steps: [] } });
    expect(parseCustomKits(json)).toEqual([]);
  });

  it("parses a valid kit with id, label, description", () => {
    const json = JSON.stringify({
      my_kit: { steps: ["overview", "hotspots"], description: "My custom workflow" },
    });
    const result = parseCustomKits(json);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("my_kit");
    expect(result[0].label).toBe("My Kit");
    expect(result[0].description).toBe("My custom workflow");
  });

  it("generates description from steps when none provided", () => {
    const json = JSON.stringify({ auto_kit: { steps: ["focus", "blast"] } });
    const result = parseCustomKits(json);
    expect(result[0].description).toBe("Custom kit: focus + blast");
  });

  it("propagates needsQuery flag", () => {
    const json = JSON.stringify({ q_kit: { steps: ["focus"], needsQuery: true } });
    expect(parseCustomKits(json)[0].needsQuery).toBe(true);
  });

  it("throws on malformed JSON (caller catches)", () => {
    expect(() => parseCustomKits("not json")).toThrow();
  });
});
