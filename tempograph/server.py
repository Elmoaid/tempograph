"""MCP server — 16 tools for agent codebase understanding.

Each tool returns structured JSON (status/data/tokens/duration) or plain text.
Standardized error codes: REPO_NOT_FOUND, NOT_GIT_REPO, NO_MATCH, BUILD_FAILED, BUILD_TIMEOUT, RENDER_FAILED.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .builder import build_graph
from .render import (
    count_tokens,
    render_architecture,
    render_blast_radius,
    render_dead_code,
    render_dependencies,
    render_diff_context,
    render_focused,
    render_hotspots,
    render_lookup,
    render_map,
    render_overview,
    render_prepare,
    render_skills,
    render_symbols,
)
from .telemetry import is_empty_result, log_feedback, log_usage

try:
    from tempo.plugins.learn import TaskMemory, infer_from_telemetry
    _LEARN_AVAILABLE = True
except ImportError:
    _LEARN_AVAILABLE = False
from .types import Tempo

_L3_INSIGHTS_PATH = Path.home() / ".tempograph" / "global" / "l3_insights.json"


def _load_l3_insights() -> dict | None:
    """Load L3 cross-repo insights, or None if unavailable."""
    try:
        if _L3_INSIGHTS_PATH.exists():
            return json.loads(_L3_INSIGHTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return None


# Navigation modes useful for general tasks (not meta/specialized tools)
_GENERAL_MODES = {"overview", "focus", "hotspots", "dead", "diff", "blast", "arch", "deps", "map", "symbols", "lookup"}


_L3_STALE_DAYS = 7  # warn if insights older than this


def _l3_age_note(l3: dict) -> str:
    """Return a staleness note if L3 insights are old, empty string otherwise."""
    from datetime import datetime, timezone
    generated_at = l3.get("generated_at", "")
    if not generated_at:
        return ""
    try:
        gen_ts = datetime.fromisoformat(generated_at)
        age_days = (datetime.now(timezone.utc) - gen_ts).total_seconds() / 86400
        if age_days >= _L3_STALE_DAYS:
            return f" [STALE: {age_days:.0f}d old — run analyze_cross_repo_patterns to refresh]"
        if age_days >= 1:
            return f" [{age_days:.1f}d ago]"
    except (ValueError, TypeError):
        pass
    return ""


def _format_l3_section(l3: dict, task_type: str = "", fallback: bool = False) -> str:
    """Format L3 cross-repo insights as an appendable section.

    fallback=True: filter to general navigation modes only (not meta-tools like quality/learn).
    """
    sessions = l3.get("sessions_analyzed", 0)
    repos = l3.get("repos_seen", 0)
    effectiveness = l3.get("mode_effectiveness", [])
    if not effectiveness:
        return ""

    age_note = _l3_age_note(l3)
    header = f"Cross-repo context ({sessions} sessions, {repos} repos){age_note}:"
    if fallback:
        header = f"Cross-repo suggestion ({sessions} sessions, {repos} repos){age_note}:"
        # Fallback: show only general navigation modes sorted by success rate then token cost
        candidates = [e for e in effectiveness if e["mode"] in _GENERAL_MODES and e["success_rate"] >= 0.9]
        candidates.sort(key=lambda x: (-x["success_rate"], x["avg_tokens"]))
        top = candidates[:4]
    else:
        top = [e for e in effectiveness if e["success_rate"] >= 0.9][:5]

    if not top:
        top = effectiveness[:3]

    mode_strs = [
        f"{e['mode']}({e['success_rate']*100:.0f}%, {e['avg_tokens']:,}t)"
        for e in top
    ]
    return header + "\n  " + " | ".join(mode_strs)

mcp = FastMCP("tempograph")

# Cache key includes exclude_dirs so different configs get different graphs
_graphs: dict[str, Tempo] = {}
_build_times: dict[str, float] = {}
_graph_excludes: dict[str, list[str]] = {}  # repo_path → exclude_dirs used
_graph_timestamps: dict[str, float] = {}  # repo_path → time.time() when built
_CACHE_TTL = 30  # seconds — rebuild if graph is older than this

# ── Error codes ───────────────────────────────────────────────────

REPO_NOT_FOUND = "REPO_NOT_FOUND"
NOT_GIT_REPO = "NOT_GIT_REPO"
NO_MATCH = "NO_MATCH"
BUILD_FAILED = "BUILD_FAILED"
BUILD_TIMEOUT = "BUILD_TIMEOUT"
LEARN_UNAVAILABLE = "LEARN_UNAVAILABLE"
INVALID_PARAMS = "INVALID_PARAMS"


def _error(code: str, message: str, output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps({"status": "error", "code": code, "message": message})
    return f"[ERROR:{code}] {message}"


def _success(data: str, tokens: int, duration: float, output_format: str = "text", **extra) -> str:
    if output_format == "json":
        result = {"status": "ok", "data": data, "tokens": tokens, "duration_ms": int(duration * 1000)}
        result.update(extra)
        return json.dumps(result)
    return data


def _validate_repo(repo_path: str) -> tuple[str, str | None]:
    """Resolve repo path and check it exists. Returns (resolved_path, error_or_None)."""
    p = str(Path(repo_path).resolve())
    if not Path(p).is_dir():
        return p, REPO_NOT_FOUND
    return p, None


def _is_git_repo(repo_path: str) -> bool:
    return (Path(repo_path) / ".git").exists()


def _read_config_excludes(repo_path: str) -> list[str]:
    """Read exclude_dirs from .tempo/config.json if it exists."""
    config_path = Path(repo_path) / ".tempo" / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            return config.get("exclude_dirs", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _resolve_excludes(repo_path: str, exclude_dirs: str = "") -> list[str]:
    """Merge explicit excludes with config-file excludes. Returns deduped list."""
    excludes = set()
    # From .tempo/config.json
    excludes.update(_read_config_excludes(repo_path))
    # From explicit parameter
    if exclude_dirs:
        excludes.update(d.strip() for d in exclude_dirs.split(",") if d.strip())
    return sorted(excludes) if excludes else []


def _get_or_build_graph(repo_path: str, exclude_dirs: list[str] | None = None,
                        timeout: int = 120) -> Tempo | str:
    """Build or retrieve cached graph. Returns Tempo on success, error code string on failure."""
    p = str(Path(repo_path).resolve())
    # Reuse cached graph if same excludes and within TTL
    if (p in _graphs
            and _graph_excludes.get(p) == (exclude_dirs or [])
            and time.time() - _graph_timestamps.get(p, 0) < _CACHE_TTL):
        return _graphs[p]
    try:
        start = time.time()
        _graphs[p] = build_graph(p, exclude_dirs=exclude_dirs)
        _graph_excludes[p] = exclude_dirs or []
        _graph_timestamps[p] = time.time()
        elapsed = time.time() - start
        _build_times[p] = elapsed
        return _graphs[p]
    except Exception as exc:
        return f"{BUILD_FAILED}:{exc}"


def _log_tool(tool_name: str, repo_path: str, output: str, duration: float, **extra) -> None:
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


def _run_tool(tool_name: str, repo_path: str, output_format: str, render_fn,
              exclude_dirs: str = "", **log_extra) -> str:
    """Common wrapper: validate repo → build graph → render → format → log."""
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    excludes = _resolve_excludes(p, exclude_dirs)
    start = time.time()
    result = _get_or_build_graph(p, exclude_dirs=excludes or None)
    if isinstance(result, str):
        code, _, msg = result.partition(":")
        return _error(code, msg or "Graph build failed", output_format)

    graph = result
    try:
        output = render_fn(graph)
    except Exception as exc:
        elapsed = time.time() - start
        return _error("RENDER_FAILED", f"{tool_name} render error: {exc}", output_format)
    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool(tool_name, p, output, elapsed, **log_extra)
    return _success(output, tokens, elapsed, output_format)


# ── Tool 1: Build + orient ──────────────────────────────────────────

@mcp.tool()
def index_repo(repo_path: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Build the semantic index and return a full orientation.
    Run this once at session start. Returns project type, stats,
    top files, complexity hotspots, and module dependency map.
    ~500-700 tokens — everything an agent needs to begin.

    exclude_dirs: comma-separated directory prefixes to skip (e.g. "archive,vendor,dist").
      Also reads from .tempo/config.json "exclude_dirs" array. Both sources are merged.
    output_format: "text" (default) or "json" for structured response with
    {status, data, tokens, duration_ms} fields."""
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    excludes = _resolve_excludes(p, exclude_dirs)
    _graphs.pop(p, None)
    _graph_excludes.pop(p, None)
    start = time.time()
    result = _get_or_build_graph(p, exclude_dirs=excludes or None)
    if isinstance(result, str):
        code, _, msg = result.partition(":")
        return _error(code, msg or "Graph build failed", output_format)

    elapsed = time.time() - start
    output = f"Indexed in {elapsed:.1f}s\n\n{render_overview(result)}"
    tokens = count_tokens(output)
    _log_tool("index_repo", p, output, elapsed)
    return _success(output, tokens, elapsed, output_format)


# ── Tool 2: Overview ────────────────────────────────────────────────

@mcp.tool()
def overview(repo_path: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Repo orientation: project type, languages, biggest/most complex files,
    module dependencies, circular import warnings. ~500 tokens.
    Use this to understand the codebase before diving in.

    exclude_dirs: comma-separated directory prefixes to skip (e.g. "archive,vendor")
    output_format: "text" (default) or "json" for structured {status, data, tokens, duration_ms}."""
    return _run_tool("overview", repo_path, output_format, render_overview, exclude_dirs=exclude_dirs)


# ── Tool 3: Focus ───────────────────────────────────────────────────

@mcp.tool()
def focus(repo_path: str, query: str, max_tokens: int = 4000, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Get task-scoped context. Describe what you're working on and get back
    the relevant symbols, their callers/callees, complexity warnings,
    and related files — all within a token budget.

    query: natural language description or symbol name
    max_tokens: cap output length (default 4000)
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response

    Examples: "authentication middleware", "Canvas command palette",
    "database migrations", "AI assistant toolbar"
    """
    if not query.strip():
        return _error(INVALID_PARAMS, "query is required — describe what you're working on", output_format)
    return _run_tool("focus", repo_path, output_format,
                     lambda g: render_focused(g, query, max_tokens=max_tokens),
                     exclude_dirs=exclude_dirs, query=query)


# ── Tool 4: Hotspots ────────────────────────────────────────────────

@mcp.tool()
def hotspots(repo_path: str, top_n: int = 15, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Find the riskiest symbols: highest coupling, complexity, and cross-file
    callers. These are where bugs cluster and changes are most dangerous.
    Use before modifying unfamiliar code to know what to be careful around.

    top_n: how many hotspots to return (default 15)
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    return _run_tool("hotspots", repo_path, output_format,
                     lambda g: render_hotspots(g, top_n=top_n),
                     exclude_dirs=exclude_dirs)


# ── Tool 5: Blast radius ────────────────────────────────────────────

@mcp.tool()
def blast_radius(repo_path: str, file_path: str = "", query: str = "", exclude_dirs: str = "", output_format: str = "text") -> str:
    """What breaks if you change this file or symbol? Shows importers,
    external callers, component render chains, and cross-language bridges.

    Parameter priority: if BOTH file_path and query are provided, query wins.
    - file_path: whole-file blast radius, e.g. "src/lib/db.ts"
    - query: symbol-level blast radius (more precise), e.g. "Sparkline.max"
    For large monolith files, prefer query over file_path.
    At least one of file_path or query must be provided.

    output_format: "text" (default) or "json" for structured response"""
    if not file_path and not query:
        return _error(INVALID_PARAMS, "Provide file_path or query (or both — query takes precedence).", output_format)
    return _run_tool("blast_radius", repo_path, output_format,
                     lambda g: render_blast_radius(g, file_path, query=query),
                     exclude_dirs=exclude_dirs, file=file_path, query=query)


# ── Tool 6: Diff context ────────────────────────────────────────────

@mcp.tool()
def diff_context(repo_path: str, changed_files: str = "", scope: str = "unstaged",
                 max_tokens: int = 6000, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Impact analysis for changed files. Pass comma-separated paths OR
    use scope to auto-detect from git.

    changed_files: comma-separated file paths (overrides scope if provided)
    scope: git detection mode — "unstaged" (default), "staged", "commit", "branch"
    max_tokens: cap output length (default 6000)
    output_format: "text" (default) or "json" for structured response

    NOTE: When using scope (git auto-detect), the repo must be a git repository.
    Returns a NOT_GIT_REPO error if it isn't. If changed_files is provided,
    git is not required.
    """
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    excludes = _resolve_excludes(p, exclude_dirs)
    start = time.time()
    result = _get_or_build_graph(p, exclude_dirs=excludes or None)
    if isinstance(result, str):
        code, _, msg = result.partition(":")
        return _error(code, msg or "Graph build failed", output_format)

    graph = result

    if changed_files.strip():
        files = [f.strip() for f in changed_files.split(",") if f.strip()]
    else:
        if not _is_git_repo(p):
            return _error(NOT_GIT_REPO,
                          f"Not a git repository: {repo_path}. Pass changed_files explicitly or use a git repo.",
                          output_format)
        from .git import (
            changed_files_unstaged, changed_files_staged,
            changed_files_since, changed_files_branch, current_branch,
        )
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
            msg = f"No changed files (scope={scope}, branch={branch})."
            elapsed = time.time() - start
            _log_tool("diff_context", p, msg, elapsed, scope=scope)
            return _success(msg, count_tokens(msg), elapsed, output_format)

    header = f"Impact of {len(files)} changed file{'s' if len(files) != 1 else ''}:\n"
    output = header + render_diff_context(graph, files, max_tokens=max_tokens)
    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool("diff_context", p, output, elapsed, scope=scope)
    return _success(output, tokens, elapsed, output_format)


# ── Tool 7: Dead code ───────────────────────────────────────────────

@mcp.tool()
def dead_code(repo_path: str, max_tokens: int = 8000, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Find exported symbols never referenced by other files.
    Potential cleanup targets — unused exports, orphaned functions,
    dead interfaces. Respects Python __all__ for precise export tracking.

    max_tokens: cap output size (default 8000) to prevent context overflow
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    return _run_tool("dead_code", repo_path, output_format,
                     lambda g: render_dead_code(g, max_tokens=max_tokens),
                     exclude_dirs=exclude_dirs)


# ── Tool 8: Lookup ───────────────────────────────────────────────────

@mcp.tool()
def lookup(repo_path: str, question: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Answer a specific question about the codebase. Understands patterns like:
    - "where is X defined?"
    - "what calls X?" / "who uses X?"
    - "what does X call?" / "dependencies of X"
    - "what files import X?"
    - "what renders X?" (JSX/component tree)
    - "what implements X?" / "what extends X?"

    Falls back to fuzzy symbol search if no pattern matches.
    Typically ~100-500 tokens.

    question: natural language question about the codebase
    output_format: "text" (default) or "json" for structured response"""
    return _run_tool("lookup", repo_path, output_format,
                     lambda g: render_lookup(g, question),
                     exclude_dirs=exclude_dirs, query=question)


# ── Tool 9: Symbols ──────────────────────────────────────────────────

@mcp.tool()
def symbols(repo_path: str, max_tokens: int = 8000, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Full symbol index — every function, class, component, hook, type in the repo
    with signatures, locations, and relationships.

    WARNING: Can be very large. Default max_tokens=8000 prevents context window overflow.
    For scoped queries, use focus or lookup instead — they're much cheaper.

    max_tokens: cap output (default 8000; 0 = use default)
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    cap = max_tokens if max_tokens > 0 else 8000
    return _run_tool("symbols", repo_path, output_format,
                     lambda g: render_symbols(g, max_tokens=cap),
                     exclude_dirs=exclude_dirs)


# ── Tool 10: Map ──────────────────────────────────────────────────────

@mcp.tool()
def file_map(repo_path: str, max_symbols_per_file: int = 8, max_tokens: int = 4000,
             exclude_dirs: str = "", output_format: str = "text") -> str:
    """File tree with top symbols per file. Good for orientation and understanding
    project structure. Shows directory groupings, file sizes, and key symbols.

    Use overview for a cheaper orientation, or focus for task-specific context.

    max_symbols_per_file: how many symbols to show per file (default 8)
    max_tokens: cap output (default 4000; 0 = use default)
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    cap = max_tokens if max_tokens > 0 else 4000
    return _run_tool("file_map", repo_path, output_format,
                     lambda g: render_map(g, max_symbols_per_file=max_symbols_per_file, max_tokens=cap),
                     exclude_dirs=exclude_dirs)


# ── Tool 11: Dependencies ────────────────────────────────────────────

@mcp.tool()
def dependencies(repo_path: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Dependency analysis: circular imports and layer structure.
    Shows import cycles and which files depend on which layers.
    Use before refactoring to understand the dependency graph.

    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    return _run_tool("dependencies", repo_path, output_format, render_dependencies, exclude_dirs=exclude_dirs)


# ── Tool 12: Architecture ────────────────────────────────────────────

@mcp.tool()
def architecture(repo_path: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """High-level architecture view: modules, their roles, and inter-module
    dependencies. Groups files into top-level directories, shows import and
    call edges between modules. Use for understanding how the codebase is
    organized at a macro level.

    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    return _run_tool("architecture", repo_path, output_format, render_architecture, exclude_dirs=exclude_dirs)


# ── Tool 13: Stats ────────────────────────────────────────────────────

@mcp.tool()
def stats(repo_path: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Quick repo statistics: file count, symbol count, edge count, line count,
    and estimated token costs for each mode. Use to plan your token budget.

    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response"""
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    excludes = _resolve_excludes(p, exclude_dirs)
    start = time.time()
    result = _get_or_build_graph(p, exclude_dirs=excludes or None)
    if isinstance(result, str):
        code, _, msg = result.partition(":")
        return _error(code, msg or "Graph build failed", output_format)

    graph = result
    s = graph.stats
    elapsed = time.time() - start

    ov = render_overview(graph)
    mp = render_map(graph)
    output_lines = [
        f"Build: {elapsed:.1f}s",
        f"Files: {s['files']}, Symbols: {s['symbols']}, Edges: {s['edges']}",
        f"Lines: {s['total_lines']:,}",
        "",
        "Token costs:",
        f"  overview:  {count_tokens(ov):,}",
        f"  map:       {count_tokens(mp):,}",
        f"  symbols:   ~{s['symbols'] * 15:,} (est)",
        f"  focused:   ~2,000-4,000 (query-dep)",
        f"  lookup:    ~100-500 (question-dep)",
    ]
    output = "\n".join(output_lines)
    tokens = count_tokens(output)
    _log_tool("stats", p, output, elapsed)
    return _success(output, tokens, elapsed, output_format)


# ── Tool 14: Feedback ───────────────────────────────────────────────

@mcp.tool()
def report_feedback(repo_path: str, mode: str, helpful: bool, note: str = "") -> str:
    """Report whether tempograph output was helpful for your current task.
    Call after using any tempograph tool. Helps improve the product.

    mode: which tool you used (index_repo, overview, focus, hotspots, blast_radius, diff_context, dead_code, lookup, symbols, file_map, dependencies, architecture, stats, prepare_context, learn_recommendation)
    helpful: true if the output helped, false if not
    note: optional — what was missing or what worked well
    """
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", "text")
    log_feedback(
        p,
        mode=mode,
        helpful=helpful,
        note=note,
    )
    return f"Feedback recorded for '{mode}' (helpful={helpful}). Thanks!"


# ── Tool 15: Learn recommendation ───────────────────────────────────

@mcp.tool()
def learn_recommendation(repo_path: str, task_type: str = "", output_format: str = "text") -> str:
    """Get a data-driven context strategy recommendation from learned usage patterns.

    Returns the best modes to use, expected token cost, and success rate for a given task type.
    Known task types: debug, feature, refactor, code_navigation, orientation, cleanup,
    architecture, dependency_audit, code_review, task_preparation, output_review,
    learning, patterns.

    Leave task_type empty to see all learned strategies for this repo.

    NOTE: Requires the tempo package to be installed. Returns a LEARN_UNAVAILABLE
    error if not installed — install with: pip install -e .

    output_format: "text" (default) or "json" for structured response
    """
    if not _LEARN_AVAILABLE:
        return _error(LEARN_UNAVAILABLE,
                      "Learning engine not available. Install tempo package: pip install -e .",
                      output_format)

    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    start = time.time()
    infer_from_telemetry(p)
    mem = TaskMemory(p)
    l3 = _load_l3_insights()

    if task_type:
        rec = mem.get_recommendation(task_type)
        if rec:
            modes = ", ".join(rec["best_modes"])
            output = (
                f"Recommendation for '{task_type}':\n"
                f"  Use modes: [{modes}]\n"
                f"  Avg tokens: ~{rec['avg_tokens']:,}\n"
                f"  Success rate: {rec['success_rate']:.0%} (n={rec['sample_size']})"
            )
            if l3:
                l3_section = _format_l3_section(l3, task_type=task_type)
                if l3_section:
                    output += "\n\n" + l3_section
        else:
            output = f"No learned strategy for '{task_type}' yet. Run more sessions to build data."
            if l3:
                l3_section = _format_l3_section(l3, task_type=task_type, fallback=True)
                if l3_section:
                    output += "\n\n" + l3_section
    else:
        output = mem.summary()
        if l3:
            sessions = l3.get("sessions_analyzed", 0)
            repos = l3.get("repos_seen", 0)
            effectiveness = l3.get("mode_effectiveness", [])
            if effectiveness:
                top = effectiveness[:5]
                mode_strs = [
                    f"{e['mode']}({e['success_rate']*100:.0f}%)"
                    for e in top
                ]
                age_note = _l3_age_note(l3)
                output += f"\n\nL3 cross-repo ({sessions} sessions, {repos} repos){age_note}: {' | '.join(mode_strs)}"

    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool("learn_recommendation", repo_path, output, elapsed, task_type=task_type)
    return _success(output, tokens, elapsed, output_format)


# ── Tool 16: Prepare context (batch) ─────────────────────────────

@mcp.tool()
def prepare_context(repo_path: str, task: str, task_type: str = "",
                    max_tokens: int = 6000, exclude_dirs: str = "",
                    baseline_predicted_files: list[str] | None = None,
                    precision_filter: bool = False,
                    output_format: str = "text") -> str:
    """One-shot context preparation for a task. Runs the optimal combination of
    tools and returns a single, token-budgeted response. Use this instead of
    calling index_repo → focus → blast_radius manually.

    task: describe what you're working on. Two modes are auto-selected:
      - PR/commit titles ("Merge pull request #123 from org/fix-auth-bug",
        "fix: prevent null pointer in handler", "Fix teardown callbacks (#5928)")
        → keyword extraction from branch name → per-keyword symbol focus → KEY FILES list
        → proven +9-15% file prediction improvement on real PRs (n=111, bench data)
      - General coding tasks ("add pagination to user list", "refactor database layer")
        → fuzzy symbol search → overview fallback if no match
    task_type: optional hint — "changelocal" forces keyword-extraction path regardless
               of task format; also accepts "debug", "feature", "refactor", "review"
    max_tokens: total token budget for the response (default 6000)
    exclude_dirs: comma-separated directory prefixes to skip
    baseline_predicted_files: optional list of files already predicted by the model
      (for adaptive injection). If overlap(baseline ∩ KEY FILES) ≥ 50%, returns ""
      (model already knows the relevant files — skip re-prediction, save tokens).
      If overlap < 50%, returns full context (model needs the structural graph bridge).
      Bench evidence (Phase 5.27, n=83): overlap<0.5 → avg +0.08 F1 gain per injected case.
    precision_filter: if True, skip context when >4 key files are found (topic too broad).
      Bench evidence (n=160 pairs, verified 2026-03-18): +13.4% (p=0.022, significant).
      Default False for backward compatibility.
    output_format: "text" (default) or "json" for structured response

    Returns: overview summary + focused context + KEY FILES + hotspot warnings,
    all within the token budget. JSON format adds `key_files` (parsed list) and `injected` (bool).
    """
    import re as _re
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)
    excludes = _resolve_excludes(p, exclude_dirs)
    start = time.time()
    result = _get_or_build_graph(p, exclude_dirs=excludes or None)
    if isinstance(result, str):
        code, _, msg = result.partition(":")
        return _error(code, msg or "Graph build failed", output_format)
    try:
        output = render_prepare(result, task, max_tokens=max_tokens, task_type=task_type,
                                baseline_predicted_files=baseline_predicted_files,
                                precision_filter=precision_filter)
    except Exception as exc:
        return _error("RENDER_FAILED", f"prepare_context render error: {exc}", output_format)
    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool("prepare_context", p, output, elapsed, task=task, task_type=task_type)
    if output_format == "json":
        m = _re.search(r'KEY FILES[^:]*:\n((?:  \S+\n?)+)', output)
        key_files = [ln.strip() for ln in m.group(1).split('\n') if ln.strip()] if m else []
        return _success(output, tokens, elapsed, output_format,
                        key_files=key_files, injected=bool(output.strip()))
    return output


# ── Tool 17: Skills / pattern catalog ────────────────────────────

@mcp.tool()
def get_patterns(repo_path: str, query: str = "", max_tokens: int = 4000,
                 exclude_dirs: str = "", output_format: str = "text") -> str:
    """Get coding patterns and conventions for this codebase.

    Returns a catalog of naming conventions, structural patterns, module roles,
    and repeated idioms. Use this before writing new code to ensure you follow
    the project's existing conventions.

    query: optional filter (e.g. "render", "plugin", "test", "handler")
    max_tokens: cap output size (default 4000)
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json"

    Examples:
    - get_patterns(".")                          → full convention catalog
    - get_patterns(".", query="plugin")          → plugin-related patterns
    - get_patterns(".", query="render")          → rendering conventions
    """
    return _run_tool("get_patterns", repo_path, output_format,
                     lambda g: render_skills(g, query, max_tokens=max_tokens),
                     exclude_dirs=exclude_dirs, query=query)


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
