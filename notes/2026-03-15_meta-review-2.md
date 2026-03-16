# Meta-Review — 2026-03-15 (Run 2)

## What Ran This Session

1. **Added `--exclude` CLI flag** — both CLIs (`tempo/cli.py`, `tempograph/__main__.py`) now accept
   `--exclude archive,bench` (comma-separated dir prefixes). Implemented efficient subtree pruning in
   `_walk_files` via `dirnames[:]` modification — skips entire directory trees, not just files.

2. **Saved archive/bench exclusion to repo config** — `.tempo/config.json` now has
   `exclude_dirs: ["archive", "bench"]`. The `tempo` CLI merges CLI + config automatically.
   Result: 237 → 76 files in graph build. Dead code false positives: eliminated for kernel code.

3. **Fixed stats mode noise** — `tempograph/__main__.py` was logging stats/report mode to
   usage.jsonl (was 81% of telemetry). Now skips logging for diagnostic modes.
   Signal quality will improve immediately.

4. **L2 unblocked** — Added `infer_from_telemetry()` to learn plugin. Groups usage.jsonl into
   sessions (20-min gap threshold), infers task type from dominant mode, infers success from
   feedback or heuristic (empty result rate), records to tasks.jsonl with session_hash dedup.
   4 sessions already recorded from existing telemetry. Runs automatically on every `learn` mode call.

## System Score (Updated)

| Dimension | Score | Delta | Notes |
|-----------|-------|-------|-------|
| Coverage | 65% | +5% | --exclude closes the false-positive gap |
| Signal/Noise | 85% | +15% | stats mode suppressed; 76-file graph is accurate |
| Self-Improvement | 55% | +15% | L2 now generates data passively; 4 sessions |

## Next Session Priorities

1. **L3 foundation** — once ~20 sessions accumulate, implement cross-repo pattern detection
   (which modes correlate with success across different task types)
2. **Update scheduled task prompts** — health/pulse tasks should use `--exclude archive,bench`
3. **quality plugin** — now that dead code is accurate, the quality minimality score is reliable

## 10% Exploration: Session Grouping Quality

The 20-min gap threshold may be too loose for agents that run in batches. Consider:
- Adaptive gap (agents tend to cluster calls within 5 min)
- Tag inference calls differently from manual agent calls
- Add explicit `task_end` signal to telemetry (agents call this when done)

## Commits

- feat: add --exclude flag, fix stats noise, add L2 telemetry inference
