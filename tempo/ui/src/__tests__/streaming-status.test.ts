/**
 * Tests for S76 — Streaming Output Feedback
 *
 * Covers:
 *   - getRunPhaseLabel: phase transitions based on elapsed ms
 */
import { describe, it, expect } from "vitest";
import { getRunPhaseLabel } from "../hooks/useRunMode";

describe("getRunPhaseLabel", () => {
  it("returns Indexing... for 0ms", () => {
    expect(getRunPhaseLabel(0)).toBe("Indexing...");
  });

  it("returns Indexing... for < 800ms", () => {
    expect(getRunPhaseLabel(799)).toBe("Indexing...");
  });

  it("returns Analyzing... at 800ms boundary", () => {
    expect(getRunPhaseLabel(800)).toBe("Analyzing...");
  });

  it("returns Analyzing... between 800ms and 2500ms", () => {
    expect(getRunPhaseLabel(1500)).toBe("Analyzing...");
    expect(getRunPhaseLabel(2499)).toBe("Analyzing...");
  });

  it("returns Rendering... at 2500ms boundary", () => {
    expect(getRunPhaseLabel(2500)).toBe("Rendering...");
  });

  it("returns Rendering... for long-running runs", () => {
    expect(getRunPhaseLabel(10000)).toBe("Rendering...");
    expect(getRunPhaseLabel(60000)).toBe("Rendering...");
  });
});
