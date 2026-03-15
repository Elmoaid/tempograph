"""Change Localization benchmark runner.

Measures whether tempograph helps an LLM correctly identify which files
need to change for a given task.

Two conditions:
  - baseline: repo file listing + task description only
  - tempograph: repo structure via tempograph overview/focus + task description

Usage:
    python -m bench.changelocal.run --subset 10          # quick validation
    python -m bench.changelocal.run --subset 50 --model qwen2.5-coder:32b
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .collect import collect, DATA_DIR, DEFAULT_REPOS
from .context import checkout_base, restore_default_branch, get_tempograph_context
from .evaluate import file_metrics, aggregate

RESULTS_DIR = Path(__file__).parent.parent / "results" / "changelocal"
CONDITIONS = ("baseline", "tempograph")


def _list_repo_files(repo_path: Path, max_files: int = 200) -> str:
    """Get a flat file listing for the baseline condition."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=repo_path,
    )
    files = result.stdout.strip().split("\n")[:max_files]
    return "\n".join(files)


def _build_prompt(task: str, context: str, repo_files: list[str]) -> str:
    """Build the LLM prompt for file localization."""
    return f"""You are a senior software engineer. Given a task description and information about a codebase, identify which files need to be modified to complete the task.

TASK: {task}

REPOSITORY FILES:
{chr(10).join(repo_files[:200])}

{f"STRUCTURAL CONTEXT:{chr(10)}{context}" if context else ""}

Respond with ONLY a JSON array of file paths that need to be modified. Example:
["src/auth.py", "src/models/user.py", "tests/test_auth.py"]

Do not explain. Just output the JSON array."""


def _call_ollama(prompt: str, model: str) -> str:
    """Call local Ollama model."""
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt, capture_output=True, text=True,
            timeout=120,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "[]"


def _parse_file_list(response: str) -> list[str]:
    """Extract file paths from LLM response."""
    # Try to find a JSON array
    match = re.search(r'\[.*?\]', response, re.DOTALL)
    if match:
        try:
            files = json.loads(match.group())
            if isinstance(files, list):
                return [f for f in files if isinstance(f, str)]
        except json.JSONDecodeError:
            pass
    # Fallback: extract quoted paths
    return re.findall(r'"([^"]+\.[a-z]{1,4})"', response)


def run_example(example: dict, condition: str, model: str) -> dict:
    """Run a single example under a condition. Returns result dict."""
    repo_path = Path(example["repo_path"])

    # Checkout to base commit
    if not checkout_base(repo_path, example["base_sha"]):
        return {"error": "checkout_failed"}

    try:
        # Get repo file listing
        file_listing = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, cwd=repo_path,
        ).stdout.strip().split("\n")

        # Build context based on condition
        task = f"{example['title']}\n{example.get('body', '')}".strip()
        if condition == "tempograph":
            context = get_tempograph_context(repo_path, task)
        else:
            context = ""

        prompt = _build_prompt(task, context, file_listing)

        # Call LLM
        t0 = time.time()
        response = _call_ollama(prompt, model)
        duration = time.time() - t0

        predicted = _parse_file_list(response)
        actual = example["files_changed"]
        metrics = file_metrics(predicted, actual)

        return {
            "repo": example["repo"],
            "merge_sha": example["merge_sha"],
            "condition": condition,
            "model": model,
            "task": task[:200],
            "predicted": predicted,
            "actual": actual,
            "duration_s": round(duration, 1),
            "prompt_len": len(prompt),
            **metrics,
        }
    finally:
        restore_default_branch(repo_path)


def run_benchmark(
    examples: list[dict],
    conditions: tuple[str, ...] = CONDITIONS,
    model: str = "qwen2.5-coder:32b",
) -> list[dict]:
    """Run the full benchmark across conditions."""
    results = []
    total = len(examples) * len(conditions)
    done = 0

    for example in examples:
        for condition in conditions:
            done += 1
            repo = example["repo"].split("/")[-1]
            print(f"  [{done}/{total}] {repo} | {condition} | {example['title'][:50]}...")
            result = run_example(example, condition, model)
            results.append(result)

            # Print inline metrics
            if "error" not in result:
                print(f"    R={result['recall']:.0%} P={result['precision']:.0%} F1={result['f1']:.0%}")

    return results


def print_report(results: list[dict]):
    """Print comparison report."""
    print("\n" + "=" * 60)
    print("  Change Localization Benchmark Results")
    print("=" * 60)

    for condition in CONDITIONS:
        cond_results = [r for r in results if r.get("condition") == condition and "error" not in r]
        if not cond_results:
            continue
        agg = aggregate(cond_results)
        print(f"\n  {condition.upper()} (n={agg['n']})")
        print(f"    File Recall:    {agg['recall']:.1%}")
        print(f"    File Precision: {agg['precision']:.1%}")
        print(f"    File F1:        {agg['f1']:.1%}")
        print(f"    Miss Rate:      {agg['miss_rate']:.1%}")
        print(f"    Exact Match:    {agg['exact_match']:.1%}")
        print(f"    Avg predicted:  {agg['avg_predicted']:.1f} files")
        print(f"    Avg actual:     {agg['avg_actual']:.1f} files")

    # Delta
    baseline = [r for r in results if r.get("condition") == "baseline" and "error" not in r]
    tempo = [r for r in results if r.get("condition") == "tempograph" and "error" not in r]
    if baseline and tempo:
        b = aggregate(baseline)
        t = aggregate(tempo)
        print(f"\n  DELTA (tempograph - baseline)")
        print(f"    Recall:      {t['recall'] - b['recall']:+.1%}")
        print(f"    Precision:   {t['precision'] - b['precision']:+.1%}")
        print(f"    F1:          {t['f1'] - b['f1']:+.1%}")
        print(f"    Miss Rate:   {t['miss_rate'] - b['miss_rate']:+.1%}")
        print(f"    Exact Match: {t['exact_match'] - b['exact_match']:+.1%}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Change Localization benchmark")
    parser.add_argument("--subset", type=int, default=10, help="Examples per repo")
    parser.add_argument("--model", default="qwen2.5-coder:32b")
    parser.add_argument("--repos", default=",".join(DEFAULT_REPOS[:3]),
                        help="Comma-separated repos")
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--data", help="Pre-collected examples JSONL (skip collection)")
    args = parser.parse_args()

    conditions = tuple(args.conditions.split(","))

    # Load or collect examples
    if args.data and Path(args.data).exists():
        print(f"Loading examples from {args.data}")
        with open(args.data) as f:
            examples = [json.loads(line) for line in f if line.strip()]
    else:
        repos = [r.strip() for r in args.repos.split(",")]
        examples = collect(repos, per_repo=args.subset)

    if not examples:
        print("No examples found. Check repo access.", file=sys.stderr)
        sys.exit(1)

    print(f"\nRunning {len(examples)} examples x {len(conditions)} conditions")
    print(f"Model: {args.model}\n")

    results = run_benchmark(examples, conditions, args.model)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    outfile = RESULTS_DIR / f"changelocal_{ts}.jsonl"
    with open(outfile, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results saved to {outfile}")

    print_report(results)


if __name__ == "__main__":
    main()
