"""Generate tempograph context for Change Localization examples.

Given a repo at a base commit and a task description, produce
structural context that should help identify which files need changing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tempograph.builder import build_graph
from tempograph.render import render_focused, render_blast_radius, render_overview


def checkout_base(repo_path: Path, base_sha: str) -> bool:
    """Checkout repo to the base commit (state before PR)."""
    try:
        subprocess.run(
            ["git", "checkout", "--quiet", base_sha],
            capture_output=True, cwd=repo_path, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def restore_default_branch(repo_path: Path) -> None:
    """Return repo to default branch."""
    for branch in ("main", "master"):
        result = subprocess.run(
            ["git", "checkout", "--quiet", branch],
            capture_output=True, cwd=repo_path,
        )
        if result.returncode == 0:
            return


def get_tempograph_context(repo_path: Path, task_description: str, max_tokens: int = 3000) -> str:
    """Build tempograph context for a change localization task.

    Strategy: overview + focused search on key terms from the task.
    This gives the model structural awareness of the repo.
    """
    try:
        graph = build_graph(str(repo_path))
    except Exception:
        return ""

    parts = []

    # Overview for repo structure
    overview = render_overview(graph)
    parts.append(overview)

    # Focused search on task keywords
    keywords = _extract_keywords(task_description)
    for kw in keywords[:3]:
        focused = render_focused(graph, kw, max_tokens=max_tokens // 3)
        if focused and "No symbols matching" not in focused:
            parts.append(focused)

    return "\n\n".join(parts)


def _extract_keywords(text: str) -> list[str]:
    """Extract likely symbol names from a PR title/description."""
    import re
    # Find CamelCase, snake_case, and dotted identifiers
    identifiers = re.findall(r'\b[A-Z][a-zA-Z0-9]+\b|\b[a-z_][a-z0-9_]{2,}\b', text)
    skip = {
        "the", "and", "for", "from", "with", "this", "that", "fix", "add",
        "update", "remove", "change", "bug", "feature", "merge", "pull",
        "request", "branch", "commit", "issue", "use", "make", "new",
        "when", "not", "all", "can", "should", "would", "into", "also",
    }
    seen = set()
    result = []
    for ident in identifiers:
        lower = ident.lower()
        if lower not in skip and lower not in seen:
            seen.add(lower)
            result.append(ident)
    return result
