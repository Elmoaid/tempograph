/**
 * Tests for runTempo (tempo.ts).
 * In jsdom, window.__TAURI_INTERNALS__ is absent — runTempo falls back
 * to _fallback and returns a no-op TempoResult.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { runTempo } from "../components/tempo";

// Reset the cached invoke between tests
beforeEach(async () => {
  // Force re-evaluation of invoke cache by resetting the module
  // (vitest isolation handles module re-imports per test file by default)
});

describe("runTempo (non-Tauri fallback)", () => {
  it("returns a TempoResult shape", async () => {
    const result = await runTempo("/some/repo", "focus");
    expect(result).toHaveProperty("success");
    expect(result).toHaveProperty("output");
    expect(result).toHaveProperty("mode");
  });

  it("returns without throwing when Tauri is unavailable", async () => {
    await expect(runTempo("/repo", "blast", ["--query", "fn"])).resolves.toBeDefined();
  });

  it("success is false in non-Tauri env", async () => {
    const result = await runTempo("/repo", "overview");
    expect(result.success).toBe(false);
  });
});
