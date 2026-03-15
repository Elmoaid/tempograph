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
from .telemetry import is_empty_result, log_feedback, log_usage
from .types import Tempo

mcp = FastMCP("tempograph")

_graphs: dict[str, Tempo] = {}
_build_times: dict[str, float] = {}


def _get_or_build_graph(repo_path: str) -> Tempo:
    p = str(Path(repo_path).resolve())
    if p not in _graphs:
        start = time.time()
        _graphs[p] = build_graph(p)
        _build_times[p] = time.time() - start
    return _graphs[p]


def _log_tool(tool_name: str, repo_path: str, output: str, duration: float, **extra) -> None:
    """Log an MCP tool invocation."""
    p = str(Path(repo_path).resolve())
    log_usage(
        p,
        source="mcp",
        tool=tool_name,
        symbols=_graphs[p].stats["symbols"] if p in _graphs else 0,
        tokens=count_tokens(output),
        duration_ms=int(duration * 1000),
        empty=is_empty_result(output),
        cached=p in _build_times and _build_times[p] == 0,
        **extra,
    )


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
    output = f"Indexed in {elapsed:.1f}s\n\n{render_overview(graph)}"
    _log_tool("index_repo", p, output, elapsed)
    return output


# ── Tool 2: Overview ────────────────────────────────────────────────

@mcp.tool()
def overview(repo_path: str) -> str:
    """Repo orientation: project type, languages, biggest/most complex files,
    module dependencies, circular import warnings. ~500 tokens.
    Use this to understand the codebase before diving in."""
    start = time.time()
    graph = _get_or_build_graph(repo_path)
    output = render_overview(graph)
    _log_tool("overview", repo_path, output, time.time() - start)
    return output


# ── Tool 3: Focus ───────────────────────────────────────────────────

@mcp.tool()
def focus(repo_path: str, query: str, max_tokens: int = 4000) -> str:
    """Get task-scoped context. Describe what you're working on and get back
    the relevant symbols, their callers/callees, complexity warnings,
    and related files — all within a token budget.

    Examples: "authentication middleware", "Canvas command palette",
    "database migrations", "AI assistant toolbar"
    """
    start = time.time()
    graph = _get_or_build_graph(repo_path)
    output = render_focused(graph, query, max_tokens=max_tokens)
    _log_tool("focus", repo_path, output, time.time() - start, query=query)
    return output


# ── Tool 4: Hotspots ────────────────────────────────────────────────

@mcp.tool()
def hotspots(repo_path: str, top_n: int = 15) -> str:
    """Find the riskiest symbols: highest coupling, complexity, and cross-file
    callers. These are where bugs cluster and changes are most dangerous.
    Use before modifying unfamiliar code to know what to be careful around."""
    start = time.time()
    graph = _get_or_build_graph(repo_path)
    output = render_hotspots(graph, top_n=top_n)
    _log_tool("hotspots", repo_path, output, time.time() - start)
    return output


# ── Tool 5: Blast radius ────────────────────────────────────────────

@mcp.tool()
def blast_radius(repo_path: str, file_path: str = "", query: str = "") -> str:
    """What breaks if you change this file or symbol? Shows importers,
    external callers, component render chains, and cross-language bridges.

    Pass file_path for whole-file blast radius: "src/lib/db.ts"
    Pass query for symbol-level blast radius: "Sparkline.max"
    For large monolith files, prefer query over file_path.
    """
    start = time.time()
    graph = _get_or_build_graph(repo_path)
    output = render_blast_radius(graph, file_path, query=query)
    _log_tool("blast_radius", repo_path, output, time.time() - start, file=file_path, query=query)
    return output


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
    start = time.time()
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
    output = header + render_diff_context(graph, files, max_tokens=max_tokens)
    _log_tool("diff_context", repo_path, output, time.time() - start, scope=scope)
    return output


# ── Tool 7: Dead code ───────────────────────────────────────────────

@mcp.tool()
def dead_code(repo_path: str) -> str:
    """Find exported symbols never referenced by other files.
    Potential cleanup targets — unused exports, orphaned functions,
    dead interfaces. Respects Python __all__ for precise export tracking."""
    start = time.time()
    graph = _get_or_build_graph(repo_path)
    output = render_dead_code(graph)
    _log_tool("dead_code", repo_path, output, time.time() - start)
    return output


# ── Tool 8: Feedback ───────────────────────────────────────────────

@mcp.tool()
def report_feedback(repo_path: str, mode: str, helpful: bool, note: str = "") -> str:
    """Report whether tempograph output was helpful for your current task.
    Call after using any tempograph tool. Helps improve the product.

    mode: which tool you used (overview, focus, hotspots, blast_radius, diff_context, dead_code)
    helpful: true if the output helped, false if not
    note: optional — what was missing or what worked well
    """
    log_feedback(
        repo_path,
        mode=mode,
        helpful=helpful,
        note=note,
    )
    return f"Feedback recorded for '{mode}' (helpful={helpful}). Thanks!"


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
