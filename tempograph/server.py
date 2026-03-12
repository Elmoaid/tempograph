"""Lean MCP server — 7 high-value tools for agent codebase understanding.

Each tool is designed to give agents maximum signal per token spent.
No dump-everything modes — every response is scoped and actionable.
"""
from __future__ import annotations

import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .builder import build_graph
from .render import (
    count_tokens,
    render_blast_radius,
    render_dead_code,
    render_diff_context,
    render_focused,
    render_hotspots,
    render_overview,
)
from .types import CodeGraph

mcp = FastMCP("tempograph")

_graphs: dict[str, CodeGraph] = {}
_build_times: dict[str, float] = {}


def _get_or_build_graph(repo_path: str) -> CodeGraph:
    p = str(Path(repo_path).resolve())
    if p not in _graphs:
        start = time.time()
        _graphs[p] = build_graph(p)
        _build_times[p] = time.time() - start
    return _graphs[p]


# ── Tool 1: Build + orient ──────────────────────────────────────────

@mcp.tool()
def index_repo(repo_path: str) -> str:
    """Build the semantic index and return a full orientation.
    Run this once at session start. Returns project type, stats,
    top files, complexity hotspots, and module dependency map.
    ~500-700 tokens — everything an agent needs to begin."""
    p = str(Path(repo_path).resolve())
    _graphs.pop(p, None)
    start = time.time()
    graph = _get_or_build_graph(p)
    elapsed = time.time() - start
    return f"Indexed in {elapsed:.1f}s\n\n{render_overview(graph)}"


# ── Tool 2: Overview ────────────────────────────────────────────────

@mcp.tool()
def overview(repo_path: str) -> str:
    """Repo orientation: project type, languages, biggest/most complex files,
    module dependencies, circular import warnings. ~500 tokens.
    Use this to understand the codebase before diving in."""
    graph = _get_or_build_graph(repo_path)
    return render_overview(graph)


# ── Tool 3: Focus ───────────────────────────────────────────────────

@mcp.tool()
def focus(repo_path: str, query: str, max_tokens: int = 4000) -> str:
    """Get task-scoped context. Describe what you're working on and get back
    the relevant symbols, their callers/callees, complexity warnings,
    and related files — all within a token budget.

    Examples: "authentication middleware", "Canvas command palette",
    "database migrations", "AI assistant toolbar"
    """
    graph = _get_or_build_graph(repo_path)
    return render_focused(graph, query, max_tokens=max_tokens)


# ── Tool 4: Hotspots ────────────────────────────────────────────────

@mcp.tool()
def hotspots(repo_path: str, top_n: int = 15) -> str:
    """Find the riskiest symbols: highest coupling, complexity, and cross-file
    callers. These are where bugs cluster and changes are most dangerous.
    Use before modifying unfamiliar code to know what to be careful around."""
    graph = _get_or_build_graph(repo_path)
    return render_hotspots(graph, top_n=top_n)


# ── Tool 5: Blast radius ────────────────────────────────────────────

@mcp.tool()
def blast_radius(repo_path: str, file_path: str) -> str:
    """What breaks if you change this file? Shows importers, external callers,
    component render chains, and cross-language bridges.

    Use the relative path from repo root: "src/lib/db.ts"
    """
    graph = _get_or_build_graph(repo_path)
    return render_blast_radius(graph, file_path)


# ── Tool 6: Diff context ────────────────────────────────────────────

@mcp.tool()
def diff_context(repo_path: str, changed_files: str = "", scope: str = "unstaged", max_tokens: int = 6000) -> str:
    """Impact analysis for changed files. Pass comma-separated paths OR
    use scope to auto-detect from git:
    - "unstaged" (default): working tree changes
    - "staged": files staged for commit
    - "commit": last commit
    - "branch": all changes vs main

    Shows external callers, importers, component tree impact, key symbols.
    """
    graph = _get_or_build_graph(repo_path)

    if changed_files.strip():
        files = [f.strip() for f in changed_files.split(",") if f.strip()]
    else:
        from .git import (
            changed_files_unstaged, changed_files_staged,
            changed_files_since, changed_files_branch, current_branch,
        )
        p = str(Path(repo_path).resolve())
        if scope == "staged":
            files = changed_files_staged(p)
        elif scope == "commit":
            files = changed_files_since(p, "HEAD~1")
        elif scope == "branch":
            files = changed_files_branch(p, "main")
        else:
            files = changed_files_unstaged(p)
        files = [f for f in files if f]
        if not files:
            branch = current_branch(p) or "unknown"
            return f"No changed files (scope={scope}, branch={branch})."

    header = f"Impact of {len(files)} changed file{'s' if len(files) != 1 else ''}:\n"
    return header + render_diff_context(graph, files, max_tokens=max_tokens)


# ── Tool 7: Dead code ───────────────────────────────────────────────

@mcp.tool()
def dead_code(repo_path: str) -> str:
    """Find exported symbols never referenced by other files.
    Potential cleanup targets — unused exports, orphaned functions,
    dead interfaces. Respects Python __all__ for precise export tracking."""
    graph = _get_or_build_graph(repo_path)
    return render_dead_code(graph)


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
