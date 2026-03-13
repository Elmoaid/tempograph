"""Generate comparison report from benchmark results.

Usage:
    python -m bench.report                           # latest results
    python -m bench.report --file results/crosscode/results_*.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from .crosscode.evaluate import aggregate_metrics

RESULTS_DIR = Path(__file__).parent / "results"


def load_results(path: str) -> list[dict]:
    results = []
    for line in open(path):
        line = line.strip()
        if line:
            results.append(json.loads(line))
    return results


def find_latest_results() -> str | None:
    # Check both crosscode and swebench
    for subdir in ("crosscode", "swebench"):
        pattern = str(RESULTS_DIR / subdir / "results_*.jsonl")
        files = sorted(glob.glob(pattern))
        if files:
            return files[-1]
    return None


def print_crosscode_report(results: list[dict]):
    conditions = sorted(set(r["condition"] for r in results))
    languages = sorted(set(r["language"] for r in results))

    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║         CrossCodeEval Benchmark Results                     ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")
    print(f"Total examples: {len(results)} ({len(results) // len(conditions)} per condition)")
    print(f"Conditions: {', '.join(conditions)}")
    print(f"Languages: {', '.join(languages)}")

    # Overall comparison
    print("\n┌─────────────────┬─────────┬─────────┬─────────┐")
    print("│ Condition       │  EM     │  ES     │  ID-F1  │")
    print("├─────────────────┼─────────┼─────────┼─────────┤")

    baseline_em = None
    for condition in conditions:
        cond_results = [r for r in results if r["condition"] == condition]
        agg = aggregate_metrics(cond_results)
        em, es, f1 = agg["em"], agg["es"], agg["id_f1"]

        delta = ""
        if baseline_em is not None:
            diff = em - baseline_em
            delta = f" ({'+' if diff >= 0 else ''}{diff:.1%})"
        else:
            baseline_em = em

        print(f"│ {condition:<15} │ {em:>5.1%}  │ {es:>5.1%}  │ {f1:>5.1%}  │{delta}")
    print("└─────────────────┴─────────┴─────────┴─────────┘")

    # Per-language breakdown
    for lang in languages:
        print(f"\n  {lang}:")
        print(f"  {'Condition':<15} {'EM':>7} {'ES':>7} {'ID-F1':>7}")
        print(f"  {'─' * 40}")
        for condition in conditions:
            lang_results = [r for r in results if r["condition"] == condition and r["language"] == lang]
            if not lang_results:
                continue
            agg = aggregate_metrics(lang_results)
            print(f"  {condition:<15} {agg['em']:>6.1%} {agg['es']:>6.1%} {agg['id_f1']:>6.1%}")

    # Statistical significance hint
    print("\n  Note: For statistical significance, run with --subset 200+")
    print("  and use paired bootstrap or McNemar's test.")


def print_swebench_report(results: list[dict]):
    conditions = sorted(set(r["condition"] for r in results))
    repos = sorted(set(r["repo"] for r in results))

    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║         SWE-bench Lite Results                              ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")
    n_per_cond = len(results) // len(conditions) if conditions else 0
    print(f"Total runs: {len(results)} ({n_per_cond} per condition)")
    print(f"Conditions: {', '.join(conditions)}")
    print(f"Repos: {len(repos)}")

    print("\n┌─────────────────┬──────────┬─────────┬─────────┐")
    print("│ Condition       │ Patches  │  Rate   │ Avg Steps│")
    print("├─────────────────┼──────────┼─────────┼─────────┤")

    baseline_rate = None
    for condition in conditions:
        cond_results = [r for r in results if r["condition"] == condition]
        total = len(cond_results)
        patches = sum(1 for r in cond_results if r.get("has_patch"))
        rate = patches / total if total else 0
        avg_steps = sum(r.get("steps", 0) for r in cond_results) / total if total else 0

        delta = ""
        if baseline_rate is not None:
            diff = rate - baseline_rate
            delta = f" ({'+' if diff >= 0 else ''}{diff:.0%})"
        else:
            baseline_rate = rate

        print(f"│ {condition:<15} │ {patches:>3}/{total:<3}  │ {rate:>5.0%}  │ {avg_steps:>5.1f}   │{delta}")
    print("└─────────────────┴──────────┴─────────┴─────────┘")

    # Per-repo breakdown
    for repo in repos:
        repo_results = [r for r in results if r["repo"] == repo]
        if len(set(r["condition"] for r in repo_results)) < 2:
            continue
        print(f"\n  {repo}:")
        for condition in conditions:
            cr = [r for r in repo_results if r["condition"] == condition]
            if not cr:
                continue
            patches = sum(1 for r in cr if r.get("has_patch"))
            print(f"    {condition:<15} {patches}/{len(cr)}")

    print("\n  Note: 'Patches' = agent produced a diff. For actual resolution,")
    print("  run predictions through: python -m swebench.harness.run_evaluation")


def print_nightly_timeseries():
    """Show EM/ES trends across nightly benchmark runs."""
    pattern = str(RESULTS_DIR / "nightly_*.jsonl")
    files = sorted(glob.glob(pattern))
    if len(files) < 2:
        return

    print("\n┌─────────────────────────────────────────────────────┐")
    print("│         Nightly Trend                               │")
    print("└─────────────────────────────────────────────────────┘\n")
    print(f"  {'Date':<12} {'EM (base)':>10} {'EM (tgraph)':>12} {'Delta':>8} {'n':>5}")
    print(f"  {'─' * 52}")

    for f in files:
        date = Path(f).stem.replace("nightly_", "")
        results = load_results(f)
        if not results:
            continue
        conditions = sorted(set(r.get("condition", "") for r in results))
        n = len(results) // max(len(conditions), 1)
        row = {"date": date, "n": n}
        for cond in conditions:
            cond_r = [r for r in results if r["condition"] == cond]
            if cond_r:
                agg = aggregate_metrics(cond_r)
                row[cond] = agg["em"]

        base = row.get("no_context", 0)
        tgraph = row.get("tempograph", 0)
        delta = tgraph - base
        print(f"  {date:<12} {base:>9.1%} {tgraph:>11.1%} {delta:>+7.1%} {n:>5}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark results report")
    parser.add_argument("--file", default=None, help="Results JSONL file")
    args = parser.parse_args()

    path = args.file or find_latest_results()
    if not path:
        print("No results found. Run a benchmark first:", file=sys.stderr)
        print("  python -m bench.crosscode.run --subset 10", file=sys.stderr)
        sys.exit(1)

    print(f"Loading: {path}", file=sys.stderr)
    results = load_results(path)
    if not results:
        print("Empty results file", file=sys.stderr)
        sys.exit(1)

    # Detect benchmark type
    if results[0].get("instance_id"):
        print_swebench_report(results)
    elif results[0].get("condition"):
        print_crosscode_report(results)
    else:
        print("Unknown results format", file=sys.stderr)
        sys.exit(1)

    # Show nightly trends if available
    print_nightly_timeseries()


if __name__ == "__main__":
    main()
