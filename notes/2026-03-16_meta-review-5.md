# Meta-Review — 2026-03-16 (Run 5)

## What Ran This Session

### 1. Explicit task_type priority in infer_from_telemetry()
`infer_from_telemetry()` now checks for explicit `task_type` field in usage entries (set via `--task-type` flag) before falling back to mode-based inference. Most common explicit label wins; `_MODE_TO_TASK_TYPE` is used only when no explicit labels are present. Notes field now records `task_type='explicit'` vs `task_type='mode-inferred'` for debugging.

### 2. learn query routing to get_recommendation()
`run()` in the learn plugin previously ignored all non-l3 queries and always returned the full summary. Now:
- `--query <task_type>` → returns targeted `get_recommendation()` output with modes, avg tokens, success rate
- Unknown types → returns "No learned strategy yet" + lists known types
- Empty query or `--query summary` → returns full summary (old behavior)

### 3. MCP server learn_recommendation tool (Tool 8)
New `learn_recommendation` tool in `tempograph/server.py`:
- Calls `infer_from_telemetry()` on each invocation to auto-populate from telemetry
- Routes `task_type` arg to `get_recommendation()` or returns full summary
- Graceful fallback if `tempo` package unavailable (ImportError guard)
- Bridges Phase 2 learning capabilities to the MCP interface agents actually use

## System Score

| Dimension | Score | Delta |
|-----------|-------|-------|
| Coverage | 76% | +4% (MCP now exposes learning data) |
| Signal/Noise | 90% | 0% |
| Self-Improvement | 55% | 0% (still 11/20 sessions for L3) |

## L3 Unblock Timeline

- Current: 11 sessions (3 inferred this run from telemetry)
- Target: 20 sessions
- Estimated: ~4 more days at current velocity
- L3 will auto-trigger on next `--mode learn --query l3` call

## 10% Exploration: learn query routing

Run 4's todo: "explore whether global task_type distribution from L3 data can tune infer_from_telemetry() priors." This turned into the explicit task_type priority fix — a better solution since it doesn't need L3 data, it uses the data we already have. The recommendation query routing was also discovered as a missing feature while exploring this.

## Commits

- feat: learn query routing, explicit task_type priority, MCP learn tool
