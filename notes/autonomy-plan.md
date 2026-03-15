# Tempograph Autonomy Plan

Established: 2026-03-13
Last reviewed: 2026-03-13 (v2 — expanded scope, full autonomy)

## Goal

Run tempograph's quality, benchmarking, and telemetry systems autonomously with zero human intervention. The system catches regressions, surfaces quality issues, improves itself over time, and pushes toward benchmark credibility.

## Design Principles

1. **Silence = healthy.** No notes written when everything is green.
2. **Data before opinions.** Recommendations must cite telemetry, benchmarks, or test results.
3. **Self-improving.** The meta-review task tunes the system monthly — frequencies, prompts, alert thresholds, models.
4. **Free.** All benchmarking is local via Ollama. Zero API cost.
5. **Non-destructive.** No task pushes to git or modifies tempograph/ source. Read-only + notes + bench/.
6. **Load-aware.** Benchmark runner checks CPU load and skips if Mac is busy.
7. **Push hard.** Don't coast. Every cycle should produce signal or improve the system.

## Layer 1 — Operational Tasks

### tempograph-continuous-bench
- **Schedule:** Every 2 hours, 24/7
- **Purpose:** Continuous local benchmarking via Ollama on M2 Max (96GB RAM, 12 cores)
- **Cost:** $0 (all local inference)
- **Runner:** `python3 -m bench.continuous`
- **Model ladder:** qwen2.5-coder:14b → qwen2.5-coder:32b → qwen3-coder → deepseek-coder-v2 → qwen3:8b → llama3.3:70b
- **Target:** n=300 per model, 2% stability threshold
- **Output:** bench/results/continuous/{model}/batch_{timestamp}.jsonl
- **Self-improving:** Can edit bench/continuous.py to adjust model ladder, batch size, stability thresholds. Can pull new models.
- **Alert:** EM delta < -2% for tempograph = regression → writes note
- **Estimated throughput:** ~25 examples per batch x 12 runs/day = ~300 examples/day across all models

### tempograph-telemetry-pulse
- **Schedule:** 3x/day (9am, 2pm, 8pm)
- **Purpose:** Scan usage.jsonl + feedback.jsonl for anomalies
- **Cost:** $0
- **Alert thresholds:** >30% empty results, >50% unhelpful, >15s duration, >8000 tokens, zero data in 24h
- **Output:** notes/YYYY-MM-DD_pulse-alert.md (only if issues found)

### tempograph-daily-health
- **Schedule:** Daily 7 AM
- **Purpose:** pytest, dead code analysis, hotspot check, dependency audit
- **Cost:** $0
- **Alert conditions:** Test failures, new HIGH dead code, coupling >0.7, outdated tree-sitter deps
- **Output:** notes/YYYY-MM-DD_health-scan.md (only if issues found)

## Layer 2 — Meta-Review

### tempograph-meta-review
- **Schedule:** 1st of each month, 9 AM
- **Full authority** over all Layer 1 tasks, bench/ code, models, schedules, thresholds
- **Responsibilities:**
  - Score the system (coverage, signal/noise, self-improvement)
  - Search for better models and pull them
  - Research new benchmark suites and tools
  - Tune all task prompts and frequencies
  - Install any needed software or packages
  - Update this document
  - Write notes/YYYY-MM-DD_meta-review.md

## Model Inventory (M2 Max 96GB)

| Model | Size | Purpose | Status |
|-------|------|---------|--------|
| qwen2.5-coder:14b | 9GB | Fast code baseline | Active |
| qwen2.5-coder:32b | 19GB | Strong code model | Active |
| qwen3-coder | TBD | Next-gen code specialist | Pulling |
| deepseek-coder-v2 | TBD | SOTA multi-language code | Pulling |
| qwen3:8b | 5GB | Small reasoning | Active |
| llama3.3:70b | 42GB | Heavyweight general | Active |
| deepseek-r1:70b | 42GB | Reasoning | Standby |
| mixtral:8x22b | 79GB | MoE | Standby |

**Capacity:** 96GB unified memory runs 70B models comfortably. 32B models run concurrently with normal system use. 70B models should only run when load is low.

## Monthly Scores (updated by meta-review)

| Month | Coverage | Signal/Noise | Self-Improvement | Notes |
|-------|----------|-------------|------------------|-------|
| 2026-03 | — | — | — | Initial setup, no data yet |

## Change Log

- 2026-03-13: v1 — Initial autonomy plan. 4 tasks, local-only benchmarking.
- 2026-03-13: v2 — Full autonomy mandate. Added qwen3-coder + deepseek-coder-v2 to ladder. Expanded meta-review authority. Removed boundaries except git push and source code modification.
