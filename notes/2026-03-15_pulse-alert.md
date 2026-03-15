# Telemetry Pulse Alert — 2026-03-15

**Status: ANOMALY DETECTED**

---

## Anomaly: stats mode noise pollution

**Threshold:** >50% of invocations
**Actual:** 81.0% (51/63 invocations in last 24h)

Stats mode is being called excessively — almost certainly from the nightly bench script or a scheduled task that polls stats every ~1 minute during active work sessions. This drowns out signal from meaningful modes.

**Mode breakdown (24h):**
```
stats        51  (81.0%)  ← FLAGGED
overview      4
quality       3
learn         2
dead          1
context       1
token_stats   1
```

**Impact:** The report filters these out (74 internal stats filtered), so usage metrics are still clean — but it inflates the raw JSONL and may mask real usage patterns in raw telemetry.

---

## All-Clear: Other Checks

| Check | Threshold | Actual | Status |
|-------|-----------|--------|--------|
| Empty results | >30% | 0% | OK |
| Unhelpful feedback streak | 3+ consecutive | 0 | OK |
| Avg duration | >15s | max 88ms | OK |
| Token budget | >8000 avg | max 2047 (dead mode) | OK |
| Feedback volume | Zero = broken | 5 entries | OK |

---

## Feedback Sentiment (all time)

All 5 feedback reports are helpful (100%). Modes with feedback:
- `context` — 1/1 helpful
- `overview` — 2/2 helpful
- `quality` — 1/1 helpful
- `learn` — 1/1 helpful

No modes with 3+ consecutive unhelpful reports.

---

## Recommendation

Investigate what is calling `stats` mode so frequently. Likely candidates:
- `bench/nightly.sh` loop
- A scheduled task polling stats every ~30-60s during dev sessions
- The nightly bench harness warming the cache

Consider suppressing stats-mode calls from scheduled/bench contexts, or rate-limiting stats calls to once per session.
