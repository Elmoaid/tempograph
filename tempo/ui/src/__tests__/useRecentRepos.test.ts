/**
 * Tests for useRecentRepos hook — specifically addRecentRepo.
 */
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRecentRepos } from "../hooks/useRecentRepos";

describe("addRecentRepo", () => {
  it("adds a repo to the list", () => {
    const { result } = renderHook(() => useRecentRepos());
    act(() => { result.current.addRecentRepo("/projects/my-app"); });
    expect(result.current.recentRepos).toHaveLength(1);
    expect(result.current.recentRepos[0].path).toBe("/projects/my-app");
  });

  it("infers label from last path segment", () => {
    const { result } = renderHook(() => useRecentRepos());
    act(() => { result.current.addRecentRepo("/users/alice/my-repo"); });
    expect(result.current.recentRepos[0].label).toBe("my-repo");
  });

  it("moves existing entry to front on re-add", () => {
    const { result } = renderHook(() => useRecentRepos());
    act(() => { result.current.addRecentRepo("/a"); });
    act(() => { result.current.addRecentRepo("/b"); });
    act(() => { result.current.addRecentRepo("/a"); });
    expect(result.current.recentRepos[0].path).toBe("/a");
    expect(result.current.recentRepos).toHaveLength(2);
  });

  it("persists to localStorage", () => {
    const { result } = renderHook(() => useRecentRepos());
    act(() => { result.current.addRecentRepo("/p"); });
    const stored = JSON.parse(localStorage.getItem("tempo_recent_repos") || "[]");
    expect(stored[0].path).toBe("/p");
  });

  it("caps at 8 entries", () => {
    const { result } = renderHook(() => useRecentRepos());
    for (let i = 0; i < 10; i++) {
      act(() => { result.current.addRecentRepo(`/repo${i}`); });
    }
    expect(result.current.recentRepos).toHaveLength(8);
  });
});
