import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

// ── mock useCustomKits ────────────────────────────────────────────────────────
// vi.mock is hoisted — factory must not reference module-level variables

vi.mock("../hooks/useCustomKits", () => ({
  useCustomKits: vi.fn().mockReturnValue({
    customKits: [],
    loadCustomKits: vi.fn(),
  }),
}));

import { useKitLoadingState } from "../hooks/useKitLoadingState";
import { BUILTIN_KITS, type KitInfo } from "../components/kits";
import { useCustomKits } from "../hooks/useCustomKits";

const mockUseCustomKits = vi.mocked(useCustomKits);

const BUILTIN_COUNT = BUILTIN_KITS.length; // 5

const makeKit = (id: string): KitInfo => ({
  id,
  label: id,
  icon: BUILTIN_KITS[0].icon,
  description: `Custom ${id}`,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockUseCustomKits.mockReturnValue({
    customKits: [],
    loadCustomKits: vi.fn(),
  });
});

// ── allKits — no custom kits ──────────────────────────────────────────────────

describe("useKitLoadingState — allKits (no custom kits)", () => {
  it("allKits length equals BUILTIN_KITS.length when no custom kits", () => {
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.allKits).toHaveLength(BUILTIN_COUNT);
  });

  it("allKits[0] is the 'explore' builtin kit", () => {
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.allKits[0].id).toBe("explore");
  });

  it("allKits[4] is the 'health' builtin kit", () => {
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.allKits[4].id).toBe("health");
  });

  it("allKits builtin ids match BUILTIN_KITS ids in order", () => {
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    const builtinIds = BUILTIN_KITS.map(k => k.id);
    expect(result.current.allKits.map(k => k.id)).toEqual(builtinIds);
  });
});

// ── allKits — with custom kits ────────────────────────────────────────────────

describe("useKitLoadingState — allKits (with custom kits)", () => {
  it("allKits length = BUILTIN_COUNT + 1 when one custom kit", () => {
    mockUseCustomKits.mockReturnValue({
      customKits: [makeKit("my_kit")],
      loadCustomKits: vi.fn(),
    });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.allKits).toHaveLength(BUILTIN_COUNT + 1);
  });

  it("allKits length = BUILTIN_COUNT + 2 when two custom kits", () => {
    mockUseCustomKits.mockReturnValue({
      customKits: [makeKit("kit_a"), makeKit("kit_b")],
      loadCustomKits: vi.fn(),
    });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.allKits).toHaveLength(BUILTIN_COUNT + 2);
  });

  it("custom kit appears after builtins in allKits", () => {
    mockUseCustomKits.mockReturnValue({
      customKits: [makeKit("custom_one")],
      loadCustomKits: vi.fn(),
    });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.allKits[BUILTIN_COUNT].id).toBe("custom_one");
  });

  it("builtin order is preserved when custom kits are present", () => {
    mockUseCustomKits.mockReturnValue({
      customKits: [makeKit("extra")],
      loadCustomKits: vi.fn(),
    });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    const builtinIds = BUILTIN_KITS.map(k => k.id);
    const resultBuiltinIds = result.current.allKits.slice(0, BUILTIN_COUNT).map(k => k.id);
    expect(resultBuiltinIds).toEqual(builtinIds);
  });
});

// ── customKits passthrough ────────────────────────────────────────────────────

describe("useKitLoadingState — customKits passthrough", () => {
  it("customKits is empty array when useCustomKits returns empty", () => {
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.customKits).toEqual([]);
  });

  it("customKits reflects custom kits from useCustomKits", () => {
    const kits = [makeKit("alpha"), makeKit("beta")];
    mockUseCustomKits.mockReturnValue({ customKits: kits, loadCustomKits: vi.fn() });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.customKits).toEqual(kits);
  });
});

// ── loadCustomKits passthrough ────────────────────────────────────────────────

describe("useKitLoadingState — loadCustomKits passthrough", () => {
  it("loadCustomKits is a function", () => {
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(typeof result.current.loadCustomKits).toBe("function");
  });

  it("calling loadCustomKits invokes the underlying mock", () => {
    const innerLoad = vi.fn();
    mockUseCustomKits.mockReturnValue({ customKits: [], loadCustomKits: innerLoad });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    result.current.loadCustomKits();
    expect(innerLoad).toHaveBeenCalledOnce();
  });

  it("returned loadCustomKits is the same function reference from useCustomKits", () => {
    const innerLoad = vi.fn();
    mockUseCustomKits.mockReturnValue({ customKits: [], loadCustomKits: innerLoad });
    const { result } = renderHook(() => useKitLoadingState("/repo"));
    expect(result.current.loadCustomKits).toBe(innerLoad);
  });
});

// ── repoPath forwarding ───────────────────────────────────────────────────────

describe("useKitLoadingState — repoPath forwarding", () => {
  it("passes repoPath to useCustomKits", () => {
    renderHook(() => useKitLoadingState("/my/project"));
    expect(mockUseCustomKits).toHaveBeenCalledWith("/my/project");
  });

  it("does not crash with empty repoPath", () => {
    expect(() => renderHook(() => useKitLoadingState(""))).not.toThrow();
  });
});

// ── module export guard ───────────────────────────────────────────────────────

describe("useKitLoadingState — module export", () => {
  it("exports useKitLoadingState function", async () => {
    const mod = await import("../hooks/useKitLoadingState");
    expect(typeof mod.useKitLoadingState).toBe("function");
  });
});
