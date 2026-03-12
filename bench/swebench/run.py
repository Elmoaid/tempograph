"""SWE-bench Lite benchmark harness.

Compares bug-fix resolution rates with and without tempograph context.

Conditions:
  - baseline: agent sees issue description only
  - tempograph: agent sees issue + structural overview + focused context

Usage:
    python -m bench.swebench.run --subset 10        # dry run
    python -m bench.swebench.run --subset 50         # real run
    python -m bench.swebench.run --conditions baseline
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from .agent import run_agent
from .context import clone_and_checkout, generate_tempograph_context

RESULTS_DIR = Path(__file__).parent.parent / "results" / "swebench"
CONDITIONS = ("baseline", "tempograph")

# SWE-bench Lite repos (Python only, most popular)
PRIORITY_REPOS = {
    "django/django",
    "pallets/flask",
    "psf/requests",
    "scikit-learn/scikit-learn",
    "pytest-dev/pytest",
    "sympy/sympy",
    "matplotlib/matplotlib",
    "astropy/astropy",
    "sphinx-doc/sphinx",
    "pydata/xarray",
    "pylint-dev/pylint",
}


def load_swebench_lite(subset: int) -> list[dict]:
    """Load SWE-bench Lite dataset from HuggingFace."""
    try:
        from datasets import load_dataset
    except ImportError:
        print("Install datasets: pip install datasets", file=sys.stderr)
        sys.exit(1)

    print("Loading SWE-bench Lite from HuggingFace...", file=sys.stderr)
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    items = list(ds)

    # Deterministic shuffle
    items.sort(key=lambda x: hashlib.md5(x["instance_id"].encode()).hexdigest())

    # Stratify: try to pick from diverse repos
    by_repo: dict[str, list[dict]] = {}
    for item in items:
        repo = item["repo"]
        by_repo.setdefault(repo, []).append(item)

    selected = []
    # Round-robin across repos
    per_repo = max(1, subset // len(by_repo))
    for repo, repo_items in sorted(by_repo.items()):
        take = min(per_repo, len(repo_items))
        selected.extend(repo_items[:take])
        if len(selected) >= subset:
            break

    # Fill remainder if needed
    if len(selected) < subset:
        remaining = [i for i in items if i not in selected]
        selected.extend(remaining[:subset - len(selected)])

    selected = selected[:subset]
    print(f"Selected {len(selected)} instances from {len(set(i['repo'] for i in selected))} repos", file=sys.stderr)
    return selected


async def run_instance(
    instance: dict,
    condition: str,
    model: str,
    tempograph_ctx: str = "",
) -> dict:
    """Run agent on a single SWE-bench instance."""
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    instance_id = instance["instance_id"]
    issue_text = instance["problem_statement"]

    print(f"    {instance_id} [{condition}]...", file=sys.stderr)

    # Clone and checkout
    repo_path = clone_and_checkout(repo, base_commit)
    if not repo_path:
        print(f"    Skip {instance_id} (clone failed)", file=sys.stderr)
        return {
            "instance_id": instance_id,
            "condition": condition,
            "repo": repo,
            "resolved": False,
            "patch": "",
            "steps": 0,
            "error": "clone_failed",
        }

    # Build context
    ctx = ""
    if condition == "tempograph":
        ctx = tempograph_ctx or generate_tempograph_context(
            repo_path, issue_text, instance.get("hints_text", "")
        )

    # Run agent
    result = await run_agent(
        repo_path,
        issue_text,
        context=ctx,
        model=model,
    )

    return {
        "instance_id": instance_id,
        "condition": condition,
        "repo": repo,
        "patch": result["patch"],
        "steps": result["steps"],
        "has_patch": bool(result["patch"]),
        # Note: actual resolution requires running tests in Docker.
        # We record has_patch as a proxy; full eval needs swebench harness.
    }


async def run_condition(
    instances: list[dict],
    condition: str,
    model: str,
    concurrency: int = 3,
) -> list[dict]:
    """Run all instances for one condition."""
    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def process_one(inst: dict) -> dict:
        async with semaphore:
            return await run_instance(inst, condition, model)

    tasks = [process_one(inst) for inst in instances]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        done = len(results)
        if done % 5 == 0 or done == len(instances):
            patches = sum(1 for r in results if r.get("has_patch"))
            print(f"  [{condition}] {done}/{len(instances)} ({patches} patches)", file=sys.stderr)

    return results


def print_summary(all_results: list[dict], conditions: list[str]):
    """Print results summary."""
    print("\n" + "=" * 60)
    print("SWE-bench Lite Results")
    print("=" * 60)

    for condition in conditions:
        cond_results = [r for r in all_results if r["condition"] == condition]
        if not cond_results:
            continue
        total = len(cond_results)
        patches = sum(1 for r in cond_results if r.get("has_patch"))
        errors = sum(1 for r in cond_results if r.get("error"))

        print(f"\n{condition} ({total} instances):")
        print(f"  Patches generated: {patches}/{total} ({patches/total:.0%})")
        print(f"  Errors: {errors}")

        # Per-repo breakdown
        repos = sorted(set(r["repo"] for r in cond_results))
        for repo in repos:
            repo_results = [r for r in cond_results if r["repo"] == repo]
            repo_patches = sum(1 for r in repo_results if r.get("has_patch"))
            print(f"    {repo}: {repo_patches}/{len(repo_results)}")

    print("\n  Note: 'has_patch' means the agent produced a diff.")
    print("  For actual resolution rates, run patches through SWE-bench eval:")
    print("  python -m swebench.harness.run_evaluation --predictions <file>")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Lite benchmark for tempograph")
    parser.add_argument("--subset", type=int, default=10, help="Number of instances (default: 10)")
    parser.add_argument("--conditions", default=",".join(CONDITIONS), help="Comma-separated conditions")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="LLM model ID")
    parser.add_argument("--concurrency", type=int, default=3, help="Max concurrent agents")
    parser.add_argument("--output", default=None, help="Output JSONL path")
    args = parser.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",")]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    # Load dataset
    instances = load_swebench_lite(args.subset)
    if not instances:
        print("No instances loaded", file=sys.stderr)
        sys.exit(1)

    # Run each condition
    all_results = []
    for condition in conditions:
        print(f"\nRunning condition: {condition} ({len(instances)} instances)...", file=sys.stderr)
        start = time.time()
        results = asyncio.run(run_condition(
            instances, condition, args.model, args.concurrency,
        ))
        elapsed = time.time() - start
        all_results.extend(results)
        print(f"  Done in {elapsed:.1f}s", file=sys.stderr)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = args.output or str(RESULTS_DIR / f"results_{int(time.time())}.jsonl")
    with open(output_path, "w") as f:
        for r in all_results:
            f.write(json.dumps(r) + "\n")
    print(f"\nResults saved to {output_path}", file=sys.stderr)

    # Also save predictions in SWE-bench format for optional eval
    predictions_path = str(RESULTS_DIR / f"predictions_{int(time.time())}.jsonl")
    with open(predictions_path, "w") as f:
        for r in all_results:
            if r.get("patch"):
                f.write(json.dumps({
                    "instance_id": r["instance_id"],
                    "model_name_or_path": f"tempograph_{r['condition']}",
                    "model_patch": r["patch"],
                }) + "\n")
    print(f"Predictions (SWE-bench format) saved to {predictions_path}", file=sys.stderr)

    print_summary(all_results, conditions)


if __name__ == "__main__":
    main()
