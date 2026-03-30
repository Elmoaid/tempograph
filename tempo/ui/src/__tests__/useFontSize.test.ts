/**
 * Tests for useFontSize hook.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useFontSize } from "../hooks/useFontSize";

const FONT_SIZE_KEY = "tempo_output_font_size";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

describe("useFontSize — initial value", () => {
  it("returns default (11) when localStorage is empty", () => {
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(11);
  });

  it("restores saved value within bounds", () => {
    localStorage.setItem(FONT_SIZE_KEY, "13");
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(13);
  });

  it("falls back to default when saved value exceeds MAX (16)", () => {
    localStorage.setItem(FONT_SIZE_KEY, "20");
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(11);
  });

  it("falls back to default when saved value is below MIN (9)", () => {
    localStorage.setItem(FONT_SIZE_KEY, "5");
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(11);
  });

  it("falls back to default when saved value is NaN", () => {
    localStorage.setItem(FONT_SIZE_KEY, "notanumber");
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(11);
  });

  it("accepts boundary value MIN (9)", () => {
    localStorage.setItem(FONT_SIZE_KEY, "9");
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(9);
  });

  it("accepts boundary value MAX (16)", () => {
    localStorage.setItem(FONT_SIZE_KEY, "16");
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSize).toBe(16);
  });
});

describe("useFontSize — changeFontSize", () => {
  it("increments fontSize by delta", () => {
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.changeFontSize(1); });
    expect(result.current.fontSize).toBe(12);
  });

  it("decrements fontSize by delta", () => {
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.changeFontSize(-1); });
    expect(result.current.fontSize).toBe(10);
  });

  it("clamps at MAX (16)", () => {
    localStorage.setItem(FONT_SIZE_KEY, "16");
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.changeFontSize(5); });
    expect(result.current.fontSize).toBe(16);
  });

  it("clamps at MIN (9)", () => {
    localStorage.setItem(FONT_SIZE_KEY, "9");
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.changeFontSize(-5); });
    expect(result.current.fontSize).toBe(9);
  });

  it("persists new value to localStorage", () => {
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.changeFontSize(2); });
    expect(localStorage.getItem(FONT_SIZE_KEY)).toBe("13");
  });
});

describe("useFontSize — resetFontSize", () => {
  it("resets to default (11) from a custom value", () => {
    localStorage.setItem(FONT_SIZE_KEY, "15");
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.resetFontSize(); });
    expect(result.current.fontSize).toBe(11);
  });

  it("persists default to localStorage after reset", () => {
    localStorage.setItem(FONT_SIZE_KEY, "14");
    const { result } = renderHook(() => useFontSize());
    act(() => { result.current.resetFontSize(); });
    expect(localStorage.getItem(FONT_SIZE_KEY)).toBe("11");
  });
});

describe("useFontSize — constants", () => {
  it("exposes fontSizeMin = 9", () => {
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSizeMin).toBe(9);
  });

  it("exposes fontSizeMax = 16", () => {
    const { result } = renderHook(() => useFontSize());
    expect(result.current.fontSizeMax).toBe(16);
  });
});

describe("useFontSize — module exports", () => {
  it("exports useFontSize function", async () => {
    const mod = await import("../hooks/useFontSize");
    expect(typeof mod.useFontSize).toBe("function");
  });
});
