# Meta-Review — 2026-03-15 (Run 3)

## What Ran This Session

### 1. stats mode fix — tempo/cli.py
Stats/report/plugins modes were still logging to usage.jsonl in the `tempo` CLI (the `tempograph` CLI was fixed last session, but the kernel CLI wasn't). Fixed.

### 2. Relative Python import resolution (builder.py — both kernel + legacy)
**Root cause found**: `from .types import Tempo, Edge` was NOT creating any file-level edges because `.types` → `/types` path resolution was broken. Fixed proper leading-dot counting to resolve relative imports against the current file's directory.

**Named import symbol edges**: After resolving the target file, now also parse `from X import A, B, C` and create CALLS edges to `target::A`, `target::B`, `target::C`. This allows the dead code checker to see that `class Tempo` is referenced by `render.py`, `server.py`, etc.

Result: +122 new edges. `tempograph/types.py::Tempo` no longer flagged as HIGH confidence dead code.

### 3. Plugin run() confidence fix (render.py)
Functions named `run` in `/plugins/` directories get -30 confidence penalty. They're called via `plugin.run(graph)` dynamic dispatch which static analysis can't see.

### 4. Quality plugin improvements
- Filter dead code by confidence >= 50 (was using all dead symbols including low-confidence)
- Per-language naming convention scoring (Python=snake_case, JS/TS=camelCase)

### 5. Scheduled task prompts updated
`tempograph-daily-health` and `tempograph-weekly-health` now use `--exclude archive,bench` in all CLI calls.

## Quality Metrics

| Metric | Before (run 1) | After (run 3) | Delta |
|--------|----------------|---------------|-------|
| Overall quality | 68/100 | 90/100 | +22 |
| Minimality | 20/100 (40.2%) | 81/100 (9.9%) | +61 |
| Convention | 70/100 | 100/100 | +30 |
| False positives (HIGH) | 1 (Tempo class) | 0 | -1 |

## System Score

| Dimension | Score | Delta |
|-----------|-------|-------|
| Coverage | 70% | +5% |
| Signal/Noise | 88% | +3% |
| Self-Improvement | 55% | 0% (still at 6 sessions) |

## 10% Exploration: Per-Language Convention Scoring

The naming convention check was scoring across all languages simultaneously — penalizing projects that correctly use snake_case (Python) AND camelCase (TypeScript). Fixed by grouping functions per language and scoring each group against its expected style. Convention: 70 → 100.

**Finding**: Cross-language codebases with correct per-language conventions were systematically under-scored. The fix applies to any mixed Python/JS/TS project — relevant for tempo itself (Python kernel + React UI).

## Next Session Priorities

1. L3 skeleton — write the cross-repo pattern analyzer now, ready to run when 20 sessions accumulate
2. Check if `tempo/kernel/git.py::file_hashes` and `is_git_repo` are truly dead (MEDIUM 50 confidence)
3. Instrument tempo/cli.py to accept `--task-type` flag for explicit L2 tagging (vs inferred)

## Commits

- fix: resolve relative Python imports + reduce dead code false positives
- fix: score naming convention per language in quality plugin
