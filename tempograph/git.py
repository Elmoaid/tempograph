"""Git integration for tempograph — auto-detect changed files, branches, diffs."""
from __future__ import annotations

import subprocess
from pathlib import Path


def is_git_repo(repo: str) -> bool:
    """Check if a directory is a git repository."""
    return (Path(repo) / ".git").exists()

def _run_git(repo: str, *args: str) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def changed_files_unstaged(repo: str) -> list[str]:
    """Get files with unstaged changes (working tree vs index)."""
    out = _run_git(repo, "diff", "--name-only")
    return out.split("\n") if out else []


def changed_files_staged(repo: str) -> list[str]:
    """Get files staged for commit."""
    out = _run_git(repo, "diff", "--cached", "--name-only")
    return out.split("\n") if out else []


def changed_files_since(repo: str, ref: str = "HEAD~1") -> list[str]:
    """Get files changed since a git ref (default: last commit)."""
    out = _run_git(repo, "diff", "--name-only", ref)
    return out.split("\n") if out else []


def changed_files_branch(repo: str, base: str = "main") -> list[str]:
    """Get all files changed on the current branch vs base."""
    merge_base = _run_git(repo, "merge-base", base, "HEAD")
    if not merge_base:
        return []
    out = _run_git(repo, "diff", "--name-only", merge_base)
    return out.split("\n") if out else []


def current_branch(repo: str) -> str | None:
    """Get the current branch name."""
    return _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def cochange_matrix(repo: str, n_commits: int = 200) -> dict[str, list[tuple[str, float]]]:
    """Build a co-change matrix from git history.

    Files that frequently change together in commits are "logically coupled."
    Returns {file_path: [(coupled_file, frequency), ...]} sorted by frequency desc.

    Used for speculative context prefetching: if the agent is looking at file A,
    pre-compute context for A's top co-changed files.

    Based on software engineering research on logical coupling (Adam Tornhill, code-maat).
    """
    out = _run_git(repo, "log", f"--max-count={n_commits}", "--name-only", "--pretty=format:COMMIT_SEP")
    if not out:
        return {}

    # Parse commits into file groups
    from collections import Counter
    pair_counts: Counter[tuple[str, str]] = Counter()
    file_counts: Counter[str] = Counter()

    for commit_block in out.split("COMMIT_SEP"):
        files = [f.strip() for f in commit_block.strip().splitlines() if f.strip()]
        if len(files) < 2 or len(files) > 50:  # skip trivial and bulk commits
            continue
        for f in files:
            file_counts[f] += 1
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                key = tuple(sorted([f1, f2]))
                pair_counts[key] += 1

    # Build adjacency: for each file, its top co-changed partners
    result: dict[str, list[tuple[str, float]]] = {}
    for (f1, f2), count in pair_counts.items():
        if count < 2:  # need at least 2 co-changes to be meaningful
            continue
        # Frequency = co-changes / min(individual changes) — Jaccard-like
        freq = count / max(min(file_counts[f1], file_counts[f2]), 1)
        result.setdefault(f1, []).append((f2, freq))
        result.setdefault(f2, []).append((f1, freq))

    # Sort each file's partners by frequency desc, keep top 10
    for f in result:
        result[f] = sorted(result[f], key=lambda x: -x[1])[:10]

    return result


def recently_modified_files(repo: str, n_commits: int = 5) -> set[str]:
    """Return set of file paths (relative to repo root) touched in the last n_commits.

    Used for temporal weighting: symbols in recently-modified files are more
    likely relevant to the current task in an active development session.
    Gracefully returns empty set if not a git repo or git unavailable.
    """
    out = _run_git(repo, "log", f"--max-count={n_commits}", "--name-only", "--format=")
    if not out:
        return set()
    return {line for line in out.splitlines() if line.strip()}


