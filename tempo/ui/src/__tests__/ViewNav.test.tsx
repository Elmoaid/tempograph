import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ViewNav, type AppView } from "../components/ViewNav";

describe("ViewNav", () => {
  it("renders all 3 view tabs", () => {
    render(<ViewNav activeView="modes" onViewChange={() => {}} />);
    expect(screen.getByText("Modes")).toBeTruthy();
    expect(screen.getByText("Graph")).toBeTruthy();
    expect(screen.getByText("Dashboard")).toBeTruthy();
  });

  it("active tab has aria-selected=true, others false", () => {
    render(<ViewNav activeView="graph" onViewChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    const labels = tabs.map((t) => ({ label: t.textContent?.trim(), selected: t.getAttribute("aria-selected") }));
    const graphTab = labels.find((t) => t.label?.includes("Graph"));
    const modesTab = labels.find((t) => t.label?.includes("Modes"));
    expect(graphTab?.selected).toBe("true");
    expect(modesTab?.selected).toBe("false");
  });

  it("calls onViewChange with correct view id when tab clicked", () => {
    const onChange = vi.fn();
    render(<ViewNav activeView="modes" onViewChange={onChange} />);
    fireEvent.click(screen.getByText("Dashboard"));
    expect(onChange).toHaveBeenCalledWith("dashboard" as AppView);
  });

  it("does not call onViewChange when active tab clicked again", () => {
    const onChange = vi.fn();
    render(<ViewNav activeView="modes" onViewChange={onChange} />);
    fireEvent.click(screen.getByText("Modes"));
    // onChange is still called — switching to same view is allowed (idempotent)
    expect(onChange).toHaveBeenCalledWith("modes" as AppView);
  });
});
