import { describe, it, expect } from "vitest";
import { buildFeedbackKey } from "../hooks/useFeedback";

describe("buildFeedbackKey", () => {
  it("returns activeMode when no kit active", () => {
    expect(buildFeedbackKey("overview", null)).toBe("overview");
  });

  it("returns kit:<id> when kit is active", () => {
    expect(buildFeedbackKey("kit", "my_kit")).toBe("kit:my_kit");
  });

  it("ignores activeMode value when kit is active", () => {
    expect(buildFeedbackKey("overview", "perf_kit")).toBe("kit:perf_kit");
  });

  it("handles mode with underscores", () => {
    expect(buildFeedbackKey("dead_code", null)).toBe("dead_code");
  });

  it("handles kit id with hyphens", () => {
    expect(buildFeedbackKey("kit", "my-custom-kit")).toBe("kit:my-custom-kit");
  });
});

describe("useFeedback — module exports", () => {
  it("exports useFeedback function", async () => {
    const mod = await import("../hooks/useFeedback");
    expect(typeof mod.useFeedback).toBe("function");
  });

  it("exports buildFeedbackKey function", async () => {
    const mod = await import("../hooks/useFeedback");
    expect(typeof mod.buildFeedbackKey).toBe("function");
  });
});
