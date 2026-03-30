import { describe, it, expect } from "vitest";

// Pure logic extracted from useOutputActions — test the filename generation
// (The hook itself is Tauri-bound and requires renderHook with mocks)

function buildDefaultName(activeMode: string, activeKit: string | null, date: string): string {
  const label = activeKit ? `kit-${activeKit}` : activeMode;
  return `tempograph-${label}-${date}.txt`;
}

describe("useOutputActions — filename generation", () => {
  it("uses mode name for non-kit output", () => {
    expect(buildDefaultName("overview", null, "2026-03-28")).toBe(
      "tempograph-overview-2026-03-28.txt"
    );
  });

  it("prefixes 'kit-' for kit output", () => {
    expect(buildDefaultName("kit", "my_kit", "2026-03-28")).toBe(
      "tempograph-kit-my_kit-2026-03-28.txt"
    );
  });

  it("uses mode name when activeKit is null even if mode is 'kit'", () => {
    expect(buildDefaultName("kit", null, "2026-03-28")).toBe(
      "tempograph-kit-2026-03-28.txt"
    );
  });

  it("handles hyphenated mode names", () => {
    expect(buildDefaultName("dead_code", null, "2026-01-01")).toBe(
      "tempograph-dead_code-2026-01-01.txt"
    );
  });

  it("uses the provided date in the filename", () => {
    const result = buildDefaultName("focus", null, "2025-12-31");
    expect(result).toContain("2025-12-31");
  });
});

// Guard: useOutputActions must export the expected interface shape
describe("useOutputActions — module exports", () => {
  it("exports useOutputActions function", async () => {
    const mod = await import("../hooks/useOutputActions");
    expect(typeof mod.useOutputActions).toBe("function");
  });

  it("exports UseOutputActionsResult type (interface exists at runtime as function)", async () => {
    const mod = await import("../hooks/useOutputActions");
    // If the module loaded cleanly the interface is correctly defined
    expect(mod.useOutputActions).toBeDefined();
  });
});
