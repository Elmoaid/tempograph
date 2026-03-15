"""Collect multi-file PRs from GitHub repos for Change Localization benchmark.

Mines merged PRs that touch 3+ files from popular open-source repos.
Extracts: PR title/body, files changed, diff stats, base commit.

Usage:
    python -m bench.changelocal.collect --repos flask,fastapi,httpx --per-repo 50
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_CACHE = Path(__file__).parent.parent / "results" / ".repos"
DATA_DIR = Path(__file__).parent.parent / "results" / "changelocal"

# Repos with good PR hygiene (clear titles, multi-file changes, diverse patterns)
DEFAULT_REPOS = [
    "pallets/flask",
    "tiangolo/fastapi",
    "encode/httpx",
    "psf/requests",
    "pydantic/pydantic",
    "django/django",
    "expressjs/express",
    "vercel/next.js",
    "microsoft/TypeScript",
    "rust-lang/rust-analyzer",
]


def clone_repo(repo: str) -> Path | None:
    """Shallow clone with full history for PR mining."""
    REPO_CACHE.mkdir(parents=True, exist_ok=True)
    safe = repo.replace("/", "__")
    path = REPO_CACHE / safe
    if path.exists():
        return path
    url = f"https://github.com/{repo}.git"
    try:
        subprocess.run(
            ["git", "clone", "--quiet", url, str(path)],
            capture_output=True, timeout=600, check=True,
        )
        return path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(f"  Failed to clone {repo}", file=sys.stderr)
        return None


def find_multi_file_merges(repo_path: Path, min_files: int = 3, max_files: int = 15, limit: int = 50) -> list[dict]:
    """Find merge commits that changed 3-15 files. Returns structured PR data."""
    # Get merge commits with their messages
    result = subprocess.run(
        ["git", "log", "--merges", "--format=%H\t%s\t%b", f"-{limit * 5}"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if result.returncode != 0:
        return []

    examples = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        sha, title = parts[0], parts[1]
        body = parts[2] if len(parts) > 2 else ""

        # Get files changed in this merge (first parent diff = the PR's changes)
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", f"{sha}^1", sha],
            capture_output=True, text=True, cwd=repo_path,
        )
        if diff_result.returncode != 0:
            continue

        files = [f for f in diff_result.stdout.strip().split("\n") if f.strip()]
        if not (min_files <= len(files) <= max_files):
            continue

        # Filter: skip if all files are tests, docs, or config
        code_files = [f for f in files if not _is_non_code(f)]
        if len(code_files) < 2:
            continue

        # Get the base commit (state before PR)
        base_sha = subprocess.run(
            ["git", "rev-parse", f"{sha}^1"],
            capture_output=True, text=True, cwd=repo_path,
        ).stdout.strip()

        examples.append({
            "merge_sha": sha,
            "base_sha": base_sha,
            "title": title,
            "body": body[:500],
            "files_changed": files,
            "num_files": len(files),
            "num_code_files": len(code_files),
        })

        if len(examples) >= limit:
            break

    return examples


def _is_non_code(path: str) -> bool:
    """Check if a file is likely non-code (tests, docs, config)."""
    lower = path.lower()
    non_code = (
        "test", "spec", "readme", "changelog", "license",
        ".md", ".txt", ".rst", ".yml", ".yaml", ".toml",
        ".json", ".lock", ".cfg", ".ini", ".gitignore",
    )
    return any(x in lower for x in non_code)


def collect(repos: list[str], per_repo: int = 50) -> list[dict]:
    """Collect examples from multiple repos."""
    all_examples = []
    for repo in repos:
        print(f"Collecting from {repo}...")
        repo_path = clone_repo(repo)
        if not repo_path:
            continue
        examples = find_multi_file_merges(repo_path, limit=per_repo)
        for ex in examples:
            ex["repo"] = repo
            ex["repo_path"] = str(repo_path)
        print(f"  Found {len(examples)} multi-file merges")
        all_examples.extend(examples)
    return all_examples


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Collect multi-file PRs for benchmark")
    parser.add_argument("--repos", default=",".join(DEFAULT_REPOS[:5]),
                        help="Comma-separated repo list (owner/name)")
    parser.add_argument("--per-repo", type=int, default=50)
    parser.add_argument("--output", default=str(DATA_DIR / "examples.jsonl"))
    args = parser.parse_args()

    repos = [r.strip() for r in args.repos.split(",")]
    examples = collect(repos, args.per_repo)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")
    print(f"\nWrote {len(examples)} examples to {args.output}")


if __name__ == "__main__":
    main()
