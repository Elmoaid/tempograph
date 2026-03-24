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


def head_sha(repo: str) -> str | None:
    """Return the current HEAD commit SHA, or None if not a git repo.

    Reads directly from the filesystem (.git/HEAD -> refs) to avoid subprocess
    overhead (~0.2ms vs ~15ms for ``git rev-parse HEAD``).  Handles both
    regular repos and worktrees.  Falls back to subprocess on any error.
    """
    git_path = Path(repo) / ".git"
    try:
        if git_path.is_file():
            # Worktree: .git is a file containing "gitdir: <path>"
            content = git_path.read_text().strip()
            if not content.startswith("gitdir: "):
                return _run_git(repo, "rev-parse", "HEAD")
            git_dir = Path(content[8:])
            if not git_dir.is_absolute():
                git_dir = (Path(repo) / git_dir).resolve()
        elif git_path.is_dir():
            git_dir = git_path
        else:
            return None

        head_content = (git_dir / "HEAD").read_text().strip()
        if head_content.startswith("ref: "):
            ref = head_content[5:]
            ref_path = git_dir / ref
            if ref_path.exists():
                return ref_path.read_text().strip()
            # Worktrees store refs in the common git dir
            commondir_file = git_dir / "commondir"
            if commondir_file.exists():
                commondir = (git_dir / commondir_file.read_text().strip()).resolve()
                ref_path = commondir / ref
                if ref_path.exists():
                    return ref_path.read_text().strip()
            return _run_git(repo, "rev-parse", "HEAD")
        # Detached HEAD — content is the SHA itself
        if len(head_content) == 40:
            return head_content
    except OSError:
        pass
    return _run_git(repo, "rev-parse", "HEAD")


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


def batch_file_modification_map(repo: str) -> dict[str, int | None]:
    """Return a {relative_file_path: days_since_last_commit} map for all tracked files.

    Runs one ``git log --format=%ct --name-only -n 1000`` and parses the output
    to build a complete staleness map.  Used to pre-populate staleness caches so
    render passes avoid spawning one subprocess per unique file path.

    Files not seen in the last 1000 commits map to ``None``.
    """
    # Use COMMIT_SEP marker so we can split on commit boundaries reliably.
    # Two-year window covers all "recent" staleness checks; files older than 2 years
    # are definitively stale (>730 days > 30-day threshold) so None is safe there.
    out = _run_git(
        repo, "log", "--pretty=format:COMMIT_SEP%ct", "--name-only",
        "--since=2.years.ago",
    )
    if not out:
        return {}

    result: dict[str, int | None] = {}
    current_ct: int | None = None
    now = time.time()

    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("COMMIT_SEP"):
            ts = line[len("COMMIT_SEP"):]
            current_ct = int(ts) if ts.isdigit() else None
        elif current_ct is not None and line not in result:
            result[line] = int((now - current_ct) / 86400)

    return result


# Module-level batch prime: populated by prime_file_age_cache() from render passes.
# Checked before the subprocess in file_last_modified_days to avoid per-file git calls.
_file_age_prime: dict[tuple[str, str], int | None] = {}


def prime_file_age_cache(repo: str) -> None:
    """Pre-populate _file_age_prime for all files in ``repo`` using one git call.

    Render functions call this once before their main loop so that subsequent
    ``file_last_modified_days`` calls are prime-cache hits rather than subprocess
    spawns.  Safe to call multiple times — later calls overwrite earlier values.
    """
    batch = batch_file_modification_map(repo)
    for fp, days in batch.items():
        _file_age_prime[(repo, fp)] = days


@functools.lru_cache(maxsize=512)
def file_last_modified_days(repo: str, file_path: str) -> int | None:
    """Return the number of days since `file_path` was last committed.

    Checks _file_age_prime (pre-populated via prime_file_age_cache) before
    spawning a subprocess.  Falls back to ``git log -1 --format=%ct`` for
    files not in the prime cache.
    """
    if (repo, file_path) in _file_age_prime:
        return _file_age_prime[(repo, file_path)]
    out = _run_git(repo, "log", "-1", "--format=%ct", "--", file_path)
    if not out:
        return None
    try:
        ct = int(out.strip())
    except ValueError:
        return None
    return int((time.time() - ct) / 86400)


def symbol_last_modified_days(repo: str, file_path: str, line_start: int) -> int | None:
    """Return days since the line at ``line_start`` in ``file_path`` was last committed.

    Tries line-level ``git log -L`` first (precise), falls back to file-level
    ``git log -- <file>`` (fast).  Returns None when git is unavailable or the
    file has no history.
    """
    # Line-level attempt
    out = _run_git(
        repo, "log", "-1", "--format=%ct",
        f"-L{line_start},{line_start}:{file_path}",
    )
    if out:
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int((time.time() - int(line)) / 86400)

    # Fallback: file-level (faster, always works if file is tracked)
    return file_last_modified_days(repo, file_path)


def recent_file_commits(repo: str, file_path: str, n: int = 3) -> list[dict]:
    """Return the last n commits that touched file_path.

    Each entry: {"days_ago": int, "message": str} (newest first).
    Returns [] on error, empty output, or no git history.
    """
    out = _run_git(repo, "log", f"-n", str(n), "--format=%ct|%s", "--", file_path)
    if not out:
        return []
    results = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        try:
            ct = int(parts[0])
        except ValueError:
            continue
        days_ago = int((time.time() - ct) / 86400)
        message = parts[1][:60]
        results.append({"days_ago": days_ago, "message": message})
    return results


def cochange_pairs(
    repo_path: str, file_path: str, n: int = 3, min_count: int = 3
) -> list[dict]:
    """Return top N source files that co-change with file_path, by raw commit count.

    Scans the last 200 commits. For each commit touching file_path, counts
    which other non-test files also appeared. Returns [{"path": str, "count": int}, ...]
    sorted by count desc. Excludes test files and the file itself.
    Returns [] on error, no git history, or no co-change partners above min_count.
    """
    if not repo_path or not is_git_repo(repo_path):
        return []
    out = _run_git(
        repo_path, "log", "--max-count=200", "--name-only", "--pretty=format:COMMIT_SEP"
    )
    if not out:
        return []

    from collections import Counter
    counts: Counter[str] = Counter()

    for commit_block in out.split("COMMIT_SEP"):
        files = [f.strip() for f in commit_block.strip().splitlines() if f.strip()]
        if len(files) < 2 or len(files) > 50:
            continue
        if file_path not in files:
            continue
        for f in files:
            if f != file_path:
                counts[f] += 1

    def _is_test(p: str) -> bool:
        name = p.rsplit("/", 1)[-1]
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or name.endswith(".test.ts")
            or name.endswith(".test.js")
            or name.endswith(".spec.ts")
            or name.endswith(".spec.js")
        )

    return [
        {"path": fp, "count": cnt}
        for fp, cnt in counts.most_common()
        if cnt >= min_count and not _is_test(fp)
    ][:n]


