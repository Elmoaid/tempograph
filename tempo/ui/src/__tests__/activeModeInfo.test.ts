import { describe, it, expect, vi } from "vitest";

// ── Mocks ───────────────────────────────────────────────────────────────────
// vi.hoisted ensures these refs are available inside vi.mock() factories,
// which are hoisted before const declarations.

const { mockCompassIcon, mockMicroscopeIcon } = vi.hoisted(() => ({
  mockCompassIcon: () => null,
  mockMicroscopeIcon: () => null,
}));

vi.mock("../components/modes", () => ({
  MODES: [
    { mode: "overview", label: "Overview", icon: () => null, tag: "mcp", group: "analyze", desc: "Overview desc" },
    { mode: "focus", label: "Focus", icon: () => null, tag: "mcp", group: "analyze", argPrefix: "--query", hint: "symbol?", desc: "Focus desc" },
    { mode: "blast", label: "Blast", icon: () => null, tag: "mcp", group: "analyze", argPrefix: "--query" },
  ],
}));

vi.mock("../components/kits", () => ({
  BUILTIN_KITS: [
    { id: "explore", label: "Explore", icon: mockCompassIcon, description: "Explore kit desc" },
    { id: "deep_dive", label: "Deep Dive", icon: mockMicroscopeIcon, description: "Deep dive desc", needsQuery: true },
  ],
}));

import { buildActiveModeInfo } from "../utils/activeModeInfo";

// ── No activeKit — mode lookup ───────────────────────────────────────────────

describe("buildActiveModeInfo — no activeKit", () => {
  it("returns matching mode from MODES when activeKit is null", () => {
    const result = buildActiveModeInfo(null, "overview", []);
    expect(result).toMatchObject({ mode: "overview", label: "Overview", tag: "mcp" });
  });

  it("returns matching mode for 'focus'", () => {
    const result = buildActiveModeInfo(null, "focus", []);
    expect(result).toMatchObject({ mode: "focus", label: "Focus", argPrefix: "--query" });
  });

  it("returns undefined for unknown mode", () => {
    const result = buildActiveModeInfo(null, "nonexistent", []);
    expect(result).toBeUndefined();
  });

  it("ignores customKits when activeKit is null", () => {
    const customKit = { id: "custom", label: "Custom", icon: () => null, description: "Custom desc" };
    const result = buildActiveModeInfo(null, "blast", [customKit]);
    expect(result).toMatchObject({ mode: "blast" });
  });

  it("returns full mode object including optional fields", () => {
    const result = buildActiveModeInfo(null, "focus", []);
    expect(result).toHaveProperty("hint", "symbol?");
    expect(result).toHaveProperty("argPrefix", "--query");
  });
});

// ── activeKit — BUILTIN_KITS lookup ─────────────────────────────────────────

describe("buildActiveModeInfo — builtin kit", () => {
  it("returns kit object when activeKit matches builtin", () => {
    const result = buildActiveModeInfo("explore", "overview", []);
    expect(result).not.toBeUndefined();
    expect(result?.mode).toBe("kit:explore");
    expect(result?.label).toBe("Explore");
    expect(result?.tag).toBe("kit");
  });

  it("sets desc from kit.description", () => {
    const result = buildActiveModeInfo("explore", "overview", []);
    expect(result?.desc).toBe("Explore kit desc");
  });

  it("sets icon from kit.icon", () => {
    const result = buildActiveModeInfo("explore", "overview", []);
    expect(result?.icon).toBe(mockCompassIcon);
  });

  it("hint and argPrefix are undefined when needsQuery is falsy", () => {
    const result = buildActiveModeInfo("explore", "overview", []);
    expect(result?.hint).toBeUndefined();
    expect(result?.argPrefix).toBeUndefined();
  });

  it("hint and argPrefix are set when needsQuery is true", () => {
    const result = buildActiveModeInfo("deep_dive", "overview", []);
    expect(result?.hint).toBe("symbol or task to focus on");
    expect(result?.argPrefix).toBe("--query");
  });

  it("mode field uses kit:id format", () => {
    const result = buildActiveModeInfo("deep_dive", "focus", []);
    expect(result?.mode).toBe("kit:deep_dive");
  });

  it("activeMode is ignored when activeKit is set", () => {
    const result1 = buildActiveModeInfo("explore", "overview", []);
    const result2 = buildActiveModeInfo("explore", "focus", []);
    expect(result1?.mode).toBe(result2?.mode);
    expect(result1?.label).toBe(result2?.label);
  });
});

// ── activeKit — customKits lookup ────────────────────────────────────────────

describe("buildActiveModeInfo — custom kit", () => {
  const customKit = {
    id: "my_kit",
    label: "My Kit",
    icon: mockCompassIcon,
    description: "My kit description",
    needsQuery: true,
  };

  it("returns custom kit when id not in BUILTIN_KITS", () => {
    const result = buildActiveModeInfo("my_kit", "overview", [customKit]);
    expect(result?.mode).toBe("kit:my_kit");
    expect(result?.label).toBe("My Kit");
    expect(result?.desc).toBe("My kit description");
  });

  it("custom kit with needsQuery sets hint + argPrefix", () => {
    const result = buildActiveModeInfo("my_kit", "overview", [customKit]);
    expect(result?.hint).toBe("symbol or task to focus on");
    expect(result?.argPrefix).toBe("--query");
  });

  it("custom kit without needsQuery has undefined hint + argPrefix", () => {
    const kit = { id: "no_query", label: "No Query", icon: mockCompassIcon, description: "desc" };
    const result = buildActiveModeInfo("no_query", "overview", [kit]);
    expect(result?.hint).toBeUndefined();
    expect(result?.argPrefix).toBeUndefined();
  });

  it("BUILTIN_KITS found first when id exists in both", () => {
    // same id as a builtin — builtin should win (spread order: BUILTIN first)
    const overrideKit = { id: "explore", label: "Override", icon: mockCompassIcon, description: "Override desc" };
    const result = buildActiveModeInfo("explore", "overview", [overrideKit]);
    expect(result?.label).toBe("Explore"); // builtin wins
  });
});

// ── activeKit not found ───────────────────────────────────────────────────────

describe("buildActiveModeInfo — kit not found", () => {
  it("returns undefined when activeKit is set but not in builtin or custom kits", () => {
    const result = buildActiveModeInfo("ghost_kit", "overview", []);
    expect(result).toBeUndefined();
  });

  it("returns undefined when activeKit is set and customKits is empty", () => {
    const result = buildActiveModeInfo("unknown", "overview", []);
    expect(result).toBeUndefined();
  });

  it("returns undefined even when activeMode matches a valid mode", () => {
    const result = buildActiveModeInfo("nonexistent_kit", "overview", []);
    expect(result).toBeUndefined();
  });
});

// ── Module exports ───────────────────────────────────────────────────────────

describe("activeModeInfo — module exports", () => {
  it("exports buildActiveModeInfo function", () => {
    expect(typeof buildActiveModeInfo).toBe("function");
  });
});
