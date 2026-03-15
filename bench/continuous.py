"""Continuous local benchmark runner for M2 Max.

Smart scheduling: rotates through models, builds statistical significance,
skips when system is busy, stops when signal is clear.

Usage:
    python3 -m bench.continuous          # one cycle
    python3 -m bench.continuous --status  # show progress
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results" / "crosscode"
CONTINUOUS_DIR = Path(__file__).parent / "results" / "continuous"

# Model ladder: ordered by speed (fast first, heavy last).
# Each model runs until it hits TARGET_N with stable results, then deprioritized.
MODEL_LADDER = [
    "qwen2.5-coder:14b",   # fast, good baseline (~9GB)
    "qwen2.5-coder:32b",   # strong code model (~19GB)
    "qwen3-coder:latest",   # next-gen code specialist, 256k context
    "deepseek-coder-v2:latest",  # SOTA code model, 300+ languages
    "qwen3:8b",             # smaller reasoning model (~5GB)
    "llama3.3:70b",         # heavyweight general (~42GB)
]

TARGET_N = 300       # samples needed for statistical significance
BATCH_SIZE = 25      # examples per run (keeps each run ~2-5 min)
STABLE_THRESHOLD = 0.02  # if EM delta variance < 2% over last 3 runs, signal is clear
MAX_CPU_PERCENT = 70     # skip if system load exceeds this


def get_cpu_load() -> float:
    """Get 1-minute load average as percentage of cores."""
    import os
    load = os.getloadavg()[0]
    cores = os.cpu_count() or 1
    return (load / cores) * 100


def check_ollama_running() -> bool:
    """Check if Ollama is serving."""
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return req.status == 200
    except Exception:
        return False


def get_model_stats() -> dict[str, dict]:
    """Read all continuous results and compute per-model statistics."""
    stats: dict[str, dict] = {}
    for model in MODEL_LADDER:
        model_dir = CONTINUOUS_DIR / model.replace(":", "_")
        if not model_dir.exists():
            stats[model] = {"n": 0, "em_no_context": [], "em_tempograph": [], "runs": 0}
            continue

        results = []
        for f in sorted(model_dir.glob("*.jsonl")):
            for line in f.read_text().splitlines():
                if line.strip():
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        n = len(set((r.get("task_id", ""), r.get("condition", "")) for r in results))
        em_nc = [r["em"] for r in results if r.get("condition") == "no_context" and "em" in r]
        em_tg = [r["em"] for r in results if r.get("condition") == "tempograph" and "em" in r]

        stats[model] = {
            "n": len(em_tg),  # count tempograph examples as the primary metric
            "em_no_context": em_nc,
            "em_tempograph": em_tg,
            "runs": len(list(model_dir.glob("*.jsonl"))),
        }
    return stats


def pick_next_model(stats: dict[str, dict]) -> str | None:
    """Pick the model that needs the most data. Returns None if all done."""
    candidates = []
    for model in MODEL_LADDER:
        s = stats.get(model, {"n": 0})
        n = s["n"]
        if n >= TARGET_N and _is_stable(s):
            continue  # this model is done
        candidates.append((n, model))

    if not candidates:
        return None

    # Prioritize models with fewest samples (build significance evenly)
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _is_stable(s: dict) -> bool:
    """Check if the last 3 runs show stable delta."""
    em_nc = s.get("em_no_context", [])
    em_tg = s.get("em_tempograph", [])
    if len(em_nc) < 75 or len(em_tg) < 75:  # need at least 3 batches
        return False

    # Compute rolling delta over last 3 windows of 25
    deltas = []
    for i in range(3):
        start = len(em_tg) - 25 * (i + 1)
        end = len(em_tg) - 25 * i
        if start < 0:
            return False
        nc_window = em_nc[start:end] if len(em_nc) > end else em_nc[-25:]
        tg_window = em_tg[start:end]
        d = (sum(tg_window) / len(tg_window)) - (sum(nc_window) / len(nc_window))
        deltas.append(d)

    variance = max(deltas) - min(deltas)
    return variance < STABLE_THRESHOLD


def run_batch(model: str) -> dict:
    """Run one batch of BATCH_SIZE examples on the given model."""
    CONTINUOUS_DIR.mkdir(parents=True, exist_ok=True)
    model_dir = CONTINUOUS_DIR / model.replace(":", "_")
    model_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    output = str(model_dir / f"batch_{timestamp}.jsonl")

    cmd = [
        sys.executable, "-m", "bench.crosscode.run",
        "--real-repos",
        "--subset", str(BATCH_SIZE),
        "--conditions", "no_context,tempograph",
        "--model", model,
        "--concurrency", "2",  # conservative for local inference
        "--output", output,
    ]

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    elapsed = time.time() - start

    return {
        "model": model,
        "output": output,
        "elapsed_s": round(elapsed, 1),
        "returncode": result.returncode,
        "stderr_tail": result.stderr[-500:] if result.stderr else "",
    }


def print_status(stats: dict[str, dict]) -> None:
    """Print current benchmark progress."""
    print("Continuous Benchmark Status")
    print("=" * 65)
    print(f"{'Model':<25} {'N':>5} {'Runs':>5} {'EM(nc)':>8} {'EM(tg)':>8} {'Delta':>8} {'Status':<10}")
    print("-" * 65)

    for model in MODEL_LADDER:
        s = stats.get(model, {"n": 0, "runs": 0, "em_no_context": [], "em_tempograph": []})
        n = s["n"]
        runs = s["runs"]

        if s["em_no_context"] and s["em_tempograph"]:
            em_nc = sum(s["em_no_context"]) / len(s["em_no_context"])
            em_tg = sum(s["em_tempograph"]) / len(s["em_tempograph"])
            delta = em_tg - em_nc
            em_nc_str = f"{em_nc:.1%}"
            em_tg_str = f"{em_tg:.1%}"
            delta_str = f"{delta:+.1%}"
        else:
            em_nc_str = em_tg_str = delta_str = "—"

        if n >= TARGET_N and _is_stable(s):
            status = "DONE"
        elif n >= TARGET_N:
            status = "CONVERGING"
        elif n > 0:
            status = f"{n}/{TARGET_N}"
        else:
            status = "PENDING"

        print(f"{model:<25} {n:>5} {runs:>5} {em_nc_str:>8} {em_tg_str:>8} {delta_str:>8} {status:<10}")

    print("=" * 65)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Continuous local benchmark runner")
    parser.add_argument("--status", action="store_true", help="Show progress and exit")
    parser.add_argument("--force-model", default=None, help="Override model selection")
    parser.add_argument("--skip-load-check", action="store_true", help="Run even if system is busy")
    args = parser.parse_args()

    stats = get_model_stats()

    if args.status:
        print_status(stats)
        return

    # Pre-flight checks
    if not args.skip_load_check:
        cpu = get_cpu_load()
        if cpu > MAX_CPU_PERCENT:
            print(f"System busy ({cpu:.0f}% load). Skipping. Use --skip-load-check to override.")
            return

    if not check_ollama_running():
        print("Ollama not running. Start with: ollama serve")
        return

    # Pick model
    model = args.force_model or pick_next_model(stats)
    if model is None:
        print("All models have reached target N with stable results. Benchmarking complete.")
        print_status(stats)
        return

    s = stats.get(model, {"n": 0})
    print(f"Selected: {model} (n={s['n']}/{TARGET_N})")
    print(f"Running {BATCH_SIZE} examples...")

    result = run_batch(model)

    if result["returncode"] != 0:
        print(f"FAILED (exit {result['returncode']})")
        if result["stderr_tail"]:
            print(result["stderr_tail"])
        return

    print(f"Done in {result['elapsed_s']}s. Results: {result['output']}")

    # Show updated stats
    stats = get_model_stats()
    print()
    print_status(stats)


if __name__ == "__main__":
    main()
