"""Git integration for tempograph — auto-detect changed files, branches, diffs."""
from __future__ import annotations

import subprocess

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


