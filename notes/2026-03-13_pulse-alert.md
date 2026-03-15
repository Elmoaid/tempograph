# Telemetry Pulse Alert — 2026-03-13

## 1. CRITICAL: Zero feedback ever recorded

**What's wrong:** `feedback.jsonl` does not exist at `.tempograph/global/feedback.jsonl` or anywhere on disk. No agent has ever called `report_feedback`.

**Affected:** All repos, all sessions, all time.

**Impact:** The autonomous improvement system has zero signal to act on. Tempograph cannot self-improve without this data.

**Suggested fix:** The CLAUDE.md mandate ("Feedback is MANDATORY") isn't being followed. Two possible causes:
- Agents aren't invoking `report_feedback` after tempograph calls
- `report_feedback` isn't writing to the global store — check `telemetry.py` to confirm feedback is appended to `.tempograph/global/feedback.jsonl`, not just the local one

Check `tempograph/telemetry.py`: look for `report_feedback` write path and verify it targets the global store alongside the local one.

---

## 2. `stats` mode accounts for 95%+ of all invocations

**What's wrong:** 38/40 total invocations (local + global) are `stats` mode. Only 1 `overview`. Zero `focus`, `blast`, `dead`, or `hotspots`.

**Affected:** Both repos — tempograph and NeedEnd.

**Impact:** `stats` appears to be an internal telemetry collection mode, not a useful analysis mode. The pulse task itself seems to be generating most of the logged traffic. Agents are not using tempograph for actual code analysis.

**Suggested fix:** Either agents aren't following the CLAUDE.md `--mode overview` session-start instruction, or `stats` is being auto-called by the telemetry system and polluting the usage log. If the latter, filter `stats` from agent-visible stats in `report.py`.

---

## 3. NeedEnd repo: `stats` called 4x in 3.5 minutes (global log)

**What's wrong:** Entries at 00:02:02, 00:02:40, 00:03:51, 00:05:34 — same repo, same mode, rapid succession.

**Threshold:** 3+ consecutive same-mode/same-repo = potential confusion signal.

**Suggested fix:** Investigate whether a scheduled task looped or an agent retried incorrectly. Could be normal if NeedEnd had multiple agents in parallel, but worth watching.
