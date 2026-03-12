"""Generate tempograph context for SWE-bench instances."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tempograph.builder import build_graph
from tempograph.render import render_overview, render_focused, render_blast_radius


REPO_CACHE_DIR = Path(__file__).parent.parent / "results" / ".repos"


def clone_and_checkout(repo: str, base_commit: str) -> Path | None:
    """Clone a repo and checkout the base commit for a SWE-bench instance."""
    REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = repo.replace("/", "__")
    repo_path = REPO_CACHE_DIR / f"{safe_name}__{base_commit[:8]}"

    if repo_path.exists():
        return repo_path

    # Clone full repo (need history for checkout)
    bare_path = REPO_CACHE_DIR / f"{safe_name}.git"
    if not bare_path.exists():
        url = f"https://github.com/{repo}.git"
        try:
            subprocess.run(
                ["git", "clone", "--bare", "--quiet", url, str(bare_path)],
                capture_output=True, timeout=300, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    # Worktree for this commit
    try:
        subprocess.run(
            ["git", "-C", str(bare_path), "worktree", "add",
             str(repo_path), base_commit],
            capture_output=True, timeout=60, check=True,
        )
        return repo_path
    except subprocess.CalledProcessError:
        return None


def generate_tempograph_context(
    repo_path: Path,
    issue_text: str,
    hints: str = "",
) -> str:
    """Generate tempograph context for a SWE-bench instance.

    Returns overview + focused context based on issue keywords.
    """
    try:
        graph = build_graph(str(repo_path))
    except Exception:
        return ""

    parts = []

    # Overview: high-level repo structure
    overview = render_overview(graph)
    parts.append(overview)

    # Extract keywords from issue for focused search
    keywords = _extract_issue_keywords(issue_text, hints)
    for kw in keywords[:3]:
        focused = render_focused(graph, kw, max_tokens=1500)
        if focused and "No symbols matching" not in focused:
            parts.append(focused)
            break  # One good focused result is enough

    # If hints mention specific files, add blast radius
    file_refs = _extract_file_refs(hints or issue_text)
    for fref in file_refs[:2]:
        if fref in graph.files:
            blast = render_blast_radius(graph, fref)
            if blast and "not found" not in blast:
                parts.append(blast)

    return "\n\n".join(parts)


def _extract_issue_keywords(issue_text: str, hints: str = "") -> list[str]:
    """Pull meaningful search terms from an issue description."""
    combined = f"{issue_text} {hints}"
    # Look for class/function names (CamelCase or snake_case patterns)
    camel = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', combined)
    snake = re.findall(r'\b[a-z]+_[a-z_]+\b', combined)
    # Filter out very common words
    skip = {"the_same", "for_example", "in_order", "at_least", "such_as",
            "does_not", "do_not", "can_not", "is_not", "should_be"}
    snake = [s for s in snake if s not in skip and len(s) > 4]
    return camel[:5] + snake[:5]


def _extract_file_refs(text: str) -> list[str]:
    """Extract file path references from text."""
    # Match common patterns like path/to/file.py or module.submodule
    paths = re.findall(r'[\w/]+\.(?:py|js|ts|java|go|rs)\b', text)
    return list(dict.fromkeys(paths))  # dedupe, preserve order
