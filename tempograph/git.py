"""Git integration for tempograph — auto-detect changed files, branches, diffs."""
from __future__ import annotations

import functools
import subprocess
import time
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


def changed_files_vs_head(repo: str) -> list[str]:
    """Get all files that differ from HEAD (staged + unstaged, one subprocess).

    Equivalent to union of changed_files_staged() and changed_files_unstaged()
    but uses a single git call instead of two.
    """
    out = _run_git(repo, "diff", "HEAD", "--name-only")
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


@functools.lru_cache(maxsize=4)
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


@functools.lru_cache(maxsize=4)
def cochange_matrix_recency(repo: str, n_commits: int = 200) -> dict[str, list[tuple[str, float, int]]]:
    """Like cochange_matrix but with recency-weighted scores.

    Returns {file_path: [(coupled_file, decayed_score, days_since_last_cochange), ...]}
    sorted by decayed_score desc.

    Decay: score = jaccard_freq * exp(-0.01 * days)
    Half-life ~69 days — coupling from 2 years ago weighs ~7% of today's coupling.
    This surfaces files that are CURRENTLY moving together, not just historically.
    """
    import math
    import time
    from collections import Counter

    now = time.time()
    # Include unix timestamp per commit: "COMMIT_SEP <ts>"
    out = _run_git(repo, "log", f"--max-count={n_commits}", "--name-only", "--pretty=format:COMMIT_SEP %ct")
    if not out:
        return {}

    pair_counts: Counter[tuple[str, str]] = Counter()
    file_counts: Counter[str] = Counter()
    pair_last_ts: dict[tuple[str, str], float] = {}

    for commit_block in out.split("COMMIT_SEP "):
        commit_block = commit_block.strip()
        if not commit_block:
            continue
        lines = commit_block.splitlines()
        if not lines:
            continue
        try:
            ts = float(lines[0].strip())
        except (ValueError, IndexError):
            continue
        files = [f.strip() for f in lines[1:] if f.strip()]
        if len(files) < 2 or len(files) > 50:
            continue
        for f in files:
            file_counts[f] += 1
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                key = tuple(sorted([f1, f2]))
                pair_counts[key] += 1
                if key not in pair_last_ts or ts > pair_last_ts[key]:
                    pair_last_ts[key] = ts

    LAMBDA = 0.01  # decay constant; half-life = ln(2)/0.01 ≈ 69 days

    result: dict[str, list[tuple[str, float, int]]] = {}
    for (f1, f2), count in pair_counts.items():
        if count < 2:
            continue
        freq = count / max(min(file_counts[f1], file_counts[f2]), 1)
        last_ts = pair_last_ts.get((f1, f2), now)
        days_since = max(0.0, (now - last_ts) / 86400.0)
        decayed = freq * math.exp(-LAMBDA * days_since)
        days_int = int(days_since)
        result.setdefault(f1, []).append((f2, decayed, days_int))
        result.setdefault(f2, []).append((f1, decayed, days_int))

    for f in result:
        result[f] = sorted(result[f], key=lambda x: -x[1])[:10]

    return result


@functools.lru_cache(maxsize=4)
def file_commit_counts(repo: str, n_commits: int = 200) -> dict[str, int]:
    """Count how many of the last n_commits touched each file.

    Returns {filepath: count} for any file that appeared at least once.
    Files not in the result have never appeared in those commits (count = 0).
    Cached — cheap after first call per session.
    """
    from collections import Counter
    out = _run_git(repo, "log", f"--max-count={n_commits}", "--name-only", "--format=")
    if not out:
        return {}
    counts: Counter[str] = Counter()
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] += 1
    return dict(counts)


@functools.lru_cache(maxsize=4)
def file_change_velocity(repo: str, recent_days: int = 7) -> dict[str, float]:
    """Return {filepath: commits_per_week} for files touched in the last recent_days.

    Counts raw commit frequency — how many commits touched each file in the
    recent window, normalized to commits-per-week. Files not in the result had
    zero recent activity (implicit 0.0).
    Cached — single git call per session.
    """
    from collections import Counter
    out = _run_git(repo, "log", f"--since={recent_days} days ago", "--name-only", "--format=")
    if not out:
        return {}
    counts: Counter[str] = Counter()
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] += 1
    # Normalize to commits/week
    scale = 7.0 / max(recent_days, 1)
    return {fp: count * scale for fp, count in counts.items()}


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


def file_last_modified_days(repo: str, file_path: str) -> int | None:
    """Return the number of days since `file_path` was last committed.

    Uses ``git log -1 --format=%ct`` for the file.  Returns None if the file
    has no git history or git is unavailable.  Callers should cache the result
    per file_path to avoid repeated subprocess invocations within a single
    render call.
    """
    out = _run_git(repo, "log", "-1", "--format=%ct", "--", file_path)
    if not out:
        return None
    try:
        ct = int(out.strip())
    except ValueError:
        return None
    return int((time.time() - ct) / 86400)


