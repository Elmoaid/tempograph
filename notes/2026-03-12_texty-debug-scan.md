# Texty Debug Scan — 2026-03-12

## Session Context
- Full app scan: 4 parallel agents (Rust, React, Scratchpad/Editor, Construction apps)
- Tempograph used for: hotspot analysis, dead code detection, overview indexing
- Build: clean pass, no type errors

## Tempograph Observations

### What worked
- **Dead code detection**: Found 812 unused symbols (~73K lines). Actionable — includes entire classes (`LegacyEditorWrapper` 400L), large constants (`TEMPLATES` 961L), custom nodes (`FootnoteNode`, `MirrorNode`, `VariableNode`).
- **Hotspot analysis**: Correctly identified blast-radius risks. `Sparkline.max` (117 callers, 45 cross-file) is a real refactor hazard. All 9 construction apps flagged as extreme complexity — matches reality.
- **Overview indexing**: Fast (2.2s for 249 files, 3830 symbols). Useful as a session primer.

### What didn't help
- Bug finding: Tempograph identifies *structural* risk (coupling, complexity), not *semantic* bugs (stale closures, race conditions, wrong SQL ordering). The agents found all 27 bugs through grep + contextual reads.
- For this codebase (monolithic files, 17K-24K lines), tempograph's `focus` and `blast` modes are less useful because everything is in 2-3 god files.

### Recommendations for tempograph
1. **Add a "stale closure detector" mode** — grep for `useEffect`/`useCallback` with `eslint-disable.*exhaustive-deps` and cross-reference with the dependency graph. This is the #1 bug pattern in React codebases.
2. **Dead code confidence scoring** — current output is flat list. Rank by: callers=0 AND not exported AND >50 lines. Those are safe deletes. Exported-but-unused-internally needs manual review.
3. **Monolith file support** — for files >5K lines, `focus` should work at function/component level within the file, not just file level.

## Bug Summary (27 total)
- 10 critical, 14 important, 3 lower priority
- Top patterns: stale closures (5), missing cleanup (4), security (3), wrong ordering (2)
- Most affected: Scratchpad.tsx (4 bugs), editor plugins (5 bugs), Rust backend (7 bugs)

## Token Usage
- 4 agents total: ~275K tokens combined
- Tempograph CLI calls: ~negligible (stdout text)
- Most token-efficient: construction apps agent (46K tokens, 5 bugs found)
- Least efficient: scratchpad agent (86K tokens, 9 bugs found — justified by complexity)
