/**
 * Tests for usePanelState hook.
 */
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePanelState } from "../hooks/usePanelState";

describe("usePanelState — initial state", () => {
  it("starts with sidebarTab='kits'", () => {
    const { result } = renderHook(() => usePanelState());
    expect(result.current.sidebarTab).toBe("kits");
  });

  it("starts with all overlays closed", () => {
    const { result } = renderHook(() => usePanelState());
    expect(result.current.kitBuilderOpen).toBe(false);
    expect(result.current.paletteOpen).toBe(false);
    expect(result.current.showHelp).toBe(false);
    expect(result.current.showWhichKey).toBe(false);
    expect(result.current.historyOpen).toBe(false);
  });
});

describe("usePanelState — setters", () => {
  it("setSidebarTab switches to modes", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => { result.current.setSidebarTab("modes"); });
    expect(result.current.sidebarTab).toBe("modes");
  });

  it("setKitBuilderOpen toggles kit builder", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => { result.current.setKitBuilderOpen(true); });
    expect(result.current.kitBuilderOpen).toBe(true);
    act(() => { result.current.setKitBuilderOpen(false); });
    expect(result.current.kitBuilderOpen).toBe(false);
  });

  it("setPaletteOpen accepts boolean updater fn", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => { result.current.setPaletteOpen(prev => !prev); });
    expect(result.current.paletteOpen).toBe(true);
    act(() => { result.current.setPaletteOpen(prev => !prev); });
    expect(result.current.paletteOpen).toBe(false);
  });

  it("setHelpOpen accepts boolean updater fn", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => { result.current.setHelpOpen(true); });
    expect(result.current.showHelp).toBe(true);
    act(() => { result.current.setHelpOpen(prev => !prev); });
    expect(result.current.showHelp).toBe(false);
  });

  it("setWhichKeyVisible controls showWhichKey", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => { result.current.setWhichKeyVisible(true); });
    expect(result.current.showWhichKey).toBe(true);
    act(() => { result.current.setWhichKeyVisible(false); });
    expect(result.current.showWhichKey).toBe(false);
  });

  it("setHistoryOpen controls historyOpen", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => { result.current.setHistoryOpen(true); });
    expect(result.current.historyOpen).toBe(true);
    act(() => { result.current.setHistoryOpen(false); });
    expect(result.current.historyOpen).toBe(false);
  });

  it("panel states are independent", () => {
    const { result } = renderHook(() => usePanelState());
    act(() => {
      result.current.setPaletteOpen(true);
      result.current.setKitBuilderOpen(true);
    });
    expect(result.current.paletteOpen).toBe(true);
    expect(result.current.kitBuilderOpen).toBe(true);
    expect(result.current.showHelp).toBe(false);
    expect(result.current.historyOpen).toBe(false);
  });
});

describe("usePanelState — module exports", () => {
  it("exports usePanelState function", async () => {
    const mod = await import("../hooks/usePanelState");
    expect(typeof mod.usePanelState).toBe("function");
  });
});
