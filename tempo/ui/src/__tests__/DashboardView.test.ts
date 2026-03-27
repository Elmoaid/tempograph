import { describe, it, expect } from "vitest";
import {
  parseDashboardStats,
  parseTopHotspots,
  parseDeadPct,
} from "../components/DashboardView";

const STATS_OUTPUT = `Build: 0.0s
Files: 480, Symbols: 7,279, Edges: 31,573
Lines: 169,272

Token costs:
  overview:  2,516`;

const HOTSPOTS_OUTPUT = `Top 20 hotspots (highest coupling + complexity):

 1. method FileParser.parse [risk=919] [tested] (tempograph/parser.py:85)
    36 caller files (35 cross-file), 5 callees, 48 lines, cx=9
 2. class FileParser [risk=878] [tested] (tempograph/parser.py:70)
    29 caller files (29 cross-file), 10 children, 393 lines
 3. class Symbol [risk=812] (tempograph/types.py:146)
    36 caller files (36 cross-file), 1 children, 19 lines
 4. function build_graph [risk=643] [tested] (tempograph/builder.py:93)
    19 caller files, 35 callees, 222 lines, cx=55
 5. method FileParser._make_id [risk=551] [tested] (tempograph/parser.py:134)
    22 caller files (22 cross-file), 1 callees, 6 lines`;

const DEAD_OUTPUT = `Potential dead code (1688 symbols) (~910 lines removable) [8% of 1686 source symbols]:
Largest dead: _watch_loop (92L, conf:55)`;

describe("parseDashboardStats", () => {
  it("parses files, symbols, edges, lines from stats output", () => {
    const result = parseDashboardStats(STATS_OUTPUT);
    expect(result).not.toBeNull();
    expect(result!.files).toBe(480);
    expect(result!.symbols).toBe(7279);
    expect(result!.edges).toBe(31573);
    expect(result!.lines).toBe("169,272");
  });

  it("returns null when output does not match", () => {
    expect(parseDashboardStats("no match here")).toBeNull();
    expect(parseDashboardStats("")).toBeNull();
  });
});

describe("parseTopHotspots", () => {
  it("parses top 5 hotspot entries", () => {
    const entries = parseTopHotspots(HOTSPOTS_OUTPUT);
    expect(entries).toHaveLength(5);
    expect(entries[0].rank).toBe(1);
    expect(entries[0].name).toBe("FileParser.parse");
    expect(entries[0].risk).toBe(919);
    expect(entries[0].tested).toBe(true);
  });

  it("marks tested=false when [tested] tag absent", () => {
    const entries = parseTopHotspots(HOTSPOTS_OUTPUT);
    expect(entries[2].name).toBe("Symbol");
    expect(entries[2].tested).toBe(false);
  });

  it("respects limit parameter", () => {
    expect(parseTopHotspots(HOTSPOTS_OUTPUT, 3)).toHaveLength(3);
    expect(parseTopHotspots(HOTSPOTS_OUTPUT, 1)).toHaveLength(1);
  });

  it("returns empty array for non-matching output", () => {
    expect(parseTopHotspots("")).toHaveLength(0);
    expect(parseTopHotspots("no hotspots here")).toHaveLength(0);
  });
});

describe("parseDeadPct", () => {
  it("parses dead code percentage", () => {
    expect(parseDeadPct(DEAD_OUTPUT)).toBe(8);
  });

  it("returns null when percentage not found", () => {
    expect(parseDeadPct("no percentage")).toBeNull();
    expect(parseDeadPct("")).toBeNull();
  });
});
