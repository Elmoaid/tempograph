/**
 * Tests for OutputFilterBar component — filter input strip extracted from OutputPanel.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { createRef } from "react";
import { OutputFilterBar } from "../components/OutputFilterBar";

function makeProps(overrides: Partial<Parameters<typeof OutputFilterBar>[0]> = {}) {
  return {
    filterInputRef: createRef<HTMLInputElement>(),
    value: "",
    matchCount: null,
    onChange: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
}

describe("OutputFilterBar", () => {
  it("renders input with provided value", () => {
    render(<OutputFilterBar {...makeProps({ value: "error" })} />);
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("error");
  });

  it("calls onChange with new value when input changes", () => {
    const onChange = vi.fn();
    render(<OutputFilterBar {...makeProps({ onChange })} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "warn" } });
    expect(onChange).toHaveBeenCalledWith("warn");
  });

  it("calls onClose when Escape key pressed in input", () => {
    const onClose = vi.fn();
    render(<OutputFilterBar {...makeProps({ onClose })} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose when close button clicked", () => {
    const onClose = vi.fn();
    render(<OutputFilterBar {...makeProps({ onClose })} />);
    fireEvent.click(screen.getByRole("button", { name: /close filter/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows match count when matchCount is non-null", () => {
    render(<OutputFilterBar {...makeProps({ matchCount: 42 })} />);
    expect(screen.getByText("42 lines")).toBeTruthy();
  });

  it("hides match count when matchCount is null", () => {
    render(<OutputFilterBar {...makeProps({ matchCount: null })} />);
    expect(screen.queryByText(/lines/)).toBeNull();
  });
});
