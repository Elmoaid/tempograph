/**
 * Tests for UI pure-logic modules (no Tauri dependencies).
 *
 * Covers:
 *   - modeHints.ts: completeness + format invariants
 *   - modes.ts: formatAge, loadRecentCommands, saveRecentCommand
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { MODE_HINTS } from "../components/modeHints";
import { MODES, formatAge, loadRecentCommands, saveRecentCommand, type RecentCommand } from "../components/modes";

// ── modeHints coverage ────────────────────────────────────────────────────────

describe("MODE_HINTS completeness", () => {
  const modeIds = MODES.map(m => m.mode);

  it("has an entry for every mode defined in MODES", () => {
    const missing = modeIds.filter(id => !(id in MODE_HINTS));
    expect(missing).toEqual([]);
  });

  it("has no stale entries not in MODES", () => {
    const stale = Object.keys(MODE_HINTS).filter(id => !modeIds.includes(id));
    expect(stale).toEqual([]);
  });

  it("all hint values are non-empty strings", () => {
    for (const [mode, hint] of Object.entries(MODE_HINTS)) {
      expect(typeof hint).toBe("string");
      expect(hint.trim().length, `hint for '${mode}' is empty`).toBeGreaterThan(0);
    }
  });

  it("modes without argPrefix get '(no args needed)' hint or a plain descriptor", () => {
    const noArgModes = MODES.filter(m => !m.argPrefix).map(m => m.mode);
    for (const id of noArgModes) {
      expect(MODE_HINTS[id], `'${id}' should have a hint`).toBeDefined();
    }
  });
});

// ── formatAge ─────────────────────────────────────────────────────────────────

describe("formatAge", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:00Z"));
  });

  it("shows seconds for age < 60s", () => {
    const ts = Date.now() - 30_000;
    expect(formatAge(ts)).toBe("30s ago");
  });

  it("shows minutes for age 60s–3599s", () => {
    const ts = Date.now() - 90_000; // 1m 30s
    expect(formatAge(ts)).toBe("1m ago");
  });

  it("shows hours for age >= 3600s", () => {
    const ts = Date.now() - 7_200_000; // 2h
    expect(formatAge(ts)).toBe("2h ago");
  });

  it("shows 0s for very recent timestamps", () => {
    expect(formatAge(Date.now())).toBe("0s ago");
  });
});

// ── loadRecentCommands / saveRecentCommand ────────────────────────────────────

describe("loadRecentCommands", () => {
  it("returns empty array when nothing stored", () => {
    expect(loadRecentCommands()).toEqual([]);
  });

  it("returns previously saved commands", () => {
    const cmd: RecentCommand = { mode: "focus", args: "--query render", ts: 1 };
    localStorage.setItem("tempo_cmd_recent", JSON.stringify([cmd]));
    expect(loadRecentCommands()).toEqual([cmd]);
  });

  it("returns empty array on corrupt JSON", () => {
    localStorage.setItem("tempo_cmd_recent", "{bad json");
    expect(loadRecentCommands()).toEqual([]);
  });
});

describe("saveRecentCommand", () => {
  it("saves a command that can be loaded back", () => {
    saveRecentCommand("overview", "");
    const cmds = loadRecentCommands();
    expect(cmds.length).toBe(1);
    expect(cmds[0].mode).toBe("overview");
    expect(cmds[0].args).toBe("");
  });

  it("deduplicates — same mode+args moves to front", () => {
    saveRecentCommand("focus", "--query foo");
    saveRecentCommand("blast", "--query bar");
    saveRecentCommand("focus", "--query foo");
    const cmds = loadRecentCommands();
    expect(cmds[0].mode).toBe("focus");
    expect(cmds.filter(c => c.mode === "focus" && c.args === "--query foo")).toHaveLength(1);
  });

  it("caps at 5 entries", () => {
    for (let i = 0; i < 8; i++) saveRecentCommand(`mode${i}`, "");
    expect(loadRecentCommands()).toHaveLength(5);
  });

  it("most recent command is first", () => {
    saveRecentCommand("overview", "");
    saveRecentCommand("focus", "--query render");
    const cmds = loadRecentCommands();
    expect(cmds[0].mode).toBe("focus");
  });

  it("stores a timestamp", () => {
    const before = Date.now();
    saveRecentCommand("stats", "");
    const after = Date.now();
    const ts = loadRecentCommands()[0].ts;
    expect(ts).toBeGreaterThanOrEqual(before);
    expect(ts).toBeLessThanOrEqual(after);
  });
});
