import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useModeOutputState } from "../hooks/useModeOutputState";

// ── initial state ─────────────────────────────────────────────────────────────

describe("useModeOutputState — initial state", () => {
  it("modeOutput starts as empty string", () => {
    const { result } = renderHook(() => useModeOutputState());
    expect(result.current.modeOutput).toBe("");
  });

  it("prevOutput starts as null", () => {
    const { result } = renderHook(() => useModeOutputState());
    expect(result.current.prevOutput).toBeNull();
  });

  it("modeRunning starts as false", () => {
    const { result } = renderHook(() => useModeOutputState());
    expect(result.current.modeRunning).toBe(false);
  });
});

// ── setModeOutput ─────────────────────────────────────────────────────────────

describe("useModeOutputState — setModeOutput", () => {
  it("updates modeOutput", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeOutput("hello world"); });
    expect(result.current.modeOutput).toBe("hello world");
  });

  it("does not affect prevOutput", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeOutput("data"); });
    expect(result.current.prevOutput).toBeNull();
  });

  it("does not affect modeRunning", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeOutput("data"); });
    expect(result.current.modeRunning).toBe(false);
  });

  it("can be cleared back to empty string", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeOutput("some output"); });
    act(() => { result.current.setModeOutput(""); });
    expect(result.current.modeOutput).toBe("");
  });
});

// ── setPrevOutput ─────────────────────────────────────────────────────────────

describe("useModeOutputState — setPrevOutput", () => {
  it("updates prevOutput", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setPrevOutput("previous run"); });
    expect(result.current.prevOutput).toBe("previous run");
  });

  it("can be set to null", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setPrevOutput("data"); });
    act(() => { result.current.setPrevOutput(null); });
    expect(result.current.prevOutput).toBeNull();
  });

  it("does not affect modeOutput", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setPrevOutput("prev"); });
    expect(result.current.modeOutput).toBe("");
  });

  it("does not affect modeRunning", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setPrevOutput("prev"); });
    expect(result.current.modeRunning).toBe(false);
  });
});

// ── setModeRunning ────────────────────────────────────────────────────────────

describe("useModeOutputState — setModeRunning", () => {
  it("updates modeRunning to true", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeRunning(true); });
    expect(result.current.modeRunning).toBe(true);
  });

  it("updates modeRunning back to false", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeRunning(true); });
    act(() => { result.current.setModeRunning(false); });
    expect(result.current.modeRunning).toBe(false);
  });

  it("does not affect modeOutput", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeRunning(true); });
    expect(result.current.modeOutput).toBe("");
  });

  it("does not affect prevOutput", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => { result.current.setModeRunning(true); });
    expect(result.current.prevOutput).toBeNull();
  });
});

// ── independent updates ───────────────────────────────────────────────────────

describe("useModeOutputState — independent updates", () => {
  it("all three fields update independently", () => {
    const { result } = renderHook(() => useModeOutputState());
    act(() => {
      result.current.setModeOutput("current");
      result.current.setPrevOutput("previous");
      result.current.setModeRunning(true);
    });
    expect(result.current.modeOutput).toBe("current");
    expect(result.current.prevOutput).toBe("previous");
    expect(result.current.modeRunning).toBe(true);
  });
});

// ── module exports ─────────────────────────────────────────────────────────────

describe("useModeOutputState — module exports", () => {
  it("exports useModeOutputState as a function", async () => {
    const mod = await import("../hooks/useModeOutputState");
    expect(typeof mod.useModeOutputState).toBe("function");
  });
});
