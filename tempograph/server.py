"""MCP server — 23 tools for agent codebase understanding.

Each tool returns structured JSON (status/data/tokens/duration) or plain text.
Standardized error codes: REPO_NOT_FOUND, NOT_GIT_REPO, NO_MATCH, BUILD_FAILED, BUILD_TIMEOUT, RENDER_FAILED.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .builder import build_graph
from .prepare import render_prepare
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

# File watchers — one per repo
_watchers: dict[str, "GraphWatcher"] = {}

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

    # Check prefetch cache first
    cache_key = f"{p}:{tool_name}"
    if cache_key in _prefetch_cache:
        output = _prefetch_cache.pop(cache_key)
        start = time.time()
        elapsed = 0.001  # ~instant from cache
        tokens = count_tokens(output)
        _log_tool(tool_name, p, output, elapsed, prefetch_hit=True, **log_extra)
        _prefetch_next(tool_name, p, _resolve_excludes(p, exclude_dirs))
        return _success(output, tokens, elapsed, output_format)

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

    # Speculative prefetch: pre-warm the predicted next mode in background
    _prefetch_next(tool_name, p, excludes)

    return _success(output, tokens, elapsed, output_format)


# Prefetch cache: store pre-computed results for predicted next modes
_prefetch_cache: dict[str, str] = {}  # key = f"{repo}:{mode}", value = rendered output


def _prefetch_next(current_tool: str, repo_path: str, exclude_dirs: list[str] | None) -> None:
    """Pre-warm the most likely next mode based on session prediction."""
    import threading
    try:
        from .predict import suggest_prefetch
        suggestions = suggest_prefetch(repo_path, current_tool, threshold=0.4)
        if not suggestions:
            return
        # Only prefetch the single most likely mode
        next_mode = suggestions[0]
        cache_key = f"{repo_path}:{next_mode}"
        if cache_key in _prefetch_cache:
            return  # already cached

        def _do_prefetch():
            try:
                graph = _get_or_build_graph(repo_path, exclude_dirs=exclude_dirs)
                if isinstance(graph, str):
                    return
                _prefetch_renderers = {
                    "overview": lambda g: render_overview(g),
                    "hotspots": lambda g: render_hotspots(g),
                    "focus": None,  # needs query, can't prefetch
                    "dead_code": lambda g: render_dead_code(g),
                    "stats": None,  # cheap enough, skip
                    "architecture": lambda g: render_architecture(g),
                    "file_map": lambda g: render_map(g),
                    "dependencies": lambda g: render_dependencies(g),
                }
                fn = _prefetch_renderers.get(next_mode)
                if fn:
                    _prefetch_cache[cache_key] = fn(graph)
            except Exception:
                pass

        t = threading.Thread(target=_do_prefetch, daemon=True)
        t.start()
    except (ImportError, Exception):
        pass


@mcp.tool()
def suggest_next(
    repo_path: str, current_tool: str = "", prev_tool: str = "", output_format: str = "text"
) -> str:
    """Suggest the most useful next tool based on learned session patterns.

    Analyzes historical usage events to predict what tool agents typically call next.
    When prev_tool is provided, uses second-order Markov (prev→current→next) which is
    significantly more accurate than first-order on repeated workflows.

    Example: suggest_next(current_tool='focus', prev_tool='overview') returns
    'hotspots (100%)' instead of the less certain 'hotspots (58%)' from first-order.

    repo_path: absolute path to repository
    current_tool: the tool you just called (e.g. 'focus', 'overview')
    prev_tool: the tool called before current_tool (optional; enables second-order prediction)
    """
    start = time.time()
    p, err = _validate_repo(repo_path)
    if err:
        return err

    try:
        if prev_tool:
            from .predict import predict_next_2nd as _predict_2nd
            predictions = _predict_2nd(p, prev_tool, current_tool, top_k=5)
            header = f"After '{prev_tool}' → '{current_tool}', agents typically call:"
        else:
            from .predict import predict_next as _predict
            predictions = _predict(p, current_tool, top_k=5)
            header = f"After '{current_tool}', agents typically call:"

        if not predictions:
            output = f"No predictions for '{current_tool}' — not enough usage data yet."
        else:
            lines = [header]
            for mode, prob in predictions:
                bar = "#" * int(prob * 20)
                lines.append(f"  {mode:25s} {prob:5.0%}  {bar}")
            output = "\n".join(lines)
    except (ImportError, Exception) as e:
        output = f"Prediction unavailable: {e}"

    elapsed = time.time() - start
    return _success(output, count_tokens(output), elapsed, output_format)


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

    # Pre-warm cochange matrix so the first focus/blast call doesn't pay the git log cost.
    # cochange_matrix_recency has lru_cache(maxsize=4) — one call primes it for the session.
    try:
        from .git import cochange_matrix_recency, is_git_repo
        if is_git_repo(str(p)):
            cochange_matrix_recency(str(p))
    except Exception:
        pass

    # Add storage and embedding status via graph_stats
    db_status = ""
    if hasattr(result, '_db') and result._db is not None:
        db = result._db
        try:
            db.init_vectors()
            stats = db.graph_stats()
            db_mb = stats["db_size_bytes"] / (1024 * 1024)
            db_status = f"\nStorage: SQLite ({db_mb:.1f}MB) | {stats['symbols']} symbols, {stats['edges']} edges, {stats['files']} files"
            if stats["vectors"] > 0:
                db_status += f" | {stats['vectors']} vectors (semantic search active)"
            else:
                db_status += " | run embed_repo to enable semantic search"
            top_langs = ", ".join(f"{k}({v})" for k, v in list(stats["languages"].items())[:4])
            db_status += f"\nLanguages: {top_langs}"
        except Exception:
            db_status = f"\nStorage: SQLite ({db.symbol_count()} symbols, {db.file_count()} files)"
    output = f"Indexed in {elapsed:.1f}s{db_status}\n\n{render_overview(result)}"
    tokens = count_tokens(output)
    _log_tool("index_repo", p, output, elapsed)
    return _success(output, tokens, elapsed, output_format)


@mcp.tool()
def embed_repo(repo_path: str, exclude_dirs: str = "", output_format: str = "text") -> str:
    """Generate embeddings for semantic search across all symbols in the codebase.

    Run after index_repo to enable hybrid search (FTS5 + vector similarity).
    Uses BAAI/bge-small-en-v1.5 (33MB, runs locally on CPU, no API keys).
    Only embeds symbols without existing vectors — fast on subsequent runs.

    Requires: pip install tempograph[semantic]

    repo_path: absolute path to repository
    """
    start = time.time()
    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    graph = _get_or_build_graph(p, _resolve_excludes(p, exclude_dirs) or None)
    if isinstance(graph, str):
        return _error(graph.partition(":")[0], "Graph build failed", output_format)

    if not hasattr(graph, '_db') or graph._db is None:
        return _error("BUILD_FAILED", "No SQLite DB — rebuild with use_db=True", output_format)

    try:
        from .embeddings import embed_symbols
        count = embed_symbols(graph._db)
        elapsed = time.time() - start
        output = f"Embedded {count} symbols in {elapsed:.1f}s. Semantic search now active."
        _log_tool("embed_repo", p, output, elapsed, embedded=count)
        return _success(output, count_tokens(output), elapsed, output_format)
    except ImportError:
        return _error("INVALID_PARAMS", "fastembed not installed. Run: pip install tempograph[semantic]", output_format)


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
def dead_code(repo_path: str, max_tokens: int = 8000, exclude_dirs: str = "", output_format: str = "text", include_low: bool = False) -> str:
    """Find exported symbols never referenced by other files.
    Potential cleanup targets — unused exports, orphaned functions,
    dead interfaces. Respects Python __all__ for precise export tracking.

    max_tokens: cap output size (default 8000) to prevent context overflow
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json" for structured response
    include_low: include low-confidence (likely false positive) symbols (default False,
        saves ~47% tokens — ~1,300 tokens on a typical repo)"""
    return _run_tool("dead_code", repo_path, output_format,
                     lambda g: render_dead_code(g, max_tokens=max_tokens, include_low=include_low),
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
                    definition_first: bool = True,
                    output_format: str = "text") -> str:
    """One-shot context preparation for a task. Runs the optimal combination of
    tools and returns a single, token-budgeted response. Use this instead of
    calling index_repo → focus → blast_radius manually.

    task: describe what you're working on. Two modes are auto-selected:
      - PR/commit titles ("Merge pull request #123 from org/fix-auth-bug",
        "fix: prevent null pointer in handler", "Fix teardown callbacks (#5928)")
        → keyword extraction from branch name → per-keyword symbol focus → KEY FILES list
        → proven +7% file prediction improvement on real PRs (canonical n=159, p=0.035*)
      - General coding tasks ("add pagination to user list", "refactor database layer")
        → fuzzy symbol search → overview fallback if no match
    task_type: optional hint — "changelocal" forces keyword-extraction path regardless
               of task format; also accepts "debug", "feature", "refactor", "review"
    max_tokens: total token budget for the response (default 6000)
    exclude_dirs: comma-separated directory prefixes to skip
    baseline_predicted_files: optional list of files already predicted by the model
      (for adaptive injection). Two skip conditions:
      1. If len(baseline) ≥ 3 → returns "" (model is highly confident with 3+ predictions;
         any context disagrees more than it helps). Evidence: falcon bl=1.000, 3 correct preds
         → av2 without this guard injected anyway → F1 1.0→0.5 (commit 988960b/d4eb3c8).
      2. If overlap(baseline ∩ KEY FILES) ≥ 50% → returns "" (model already knows the files).
      Otherwise: returns full context (model needs the structural graph bridge).
      Bench (canonical): python3 -m bench.changelocal.analyze --canonical --conditions baseline,tempograph_adaptive
      Canonical result (n=159 Python+JS): +6.9% F1 (p=0.035*). Cost: 2× inference for ~37% of tasks.
    precision_filter: if True, skip context when >4 key files are found (topic too broad).
      Canonical bench: python3 -m bench.changelocal.analyze --canonical --conditions baseline,tempograph_precision
      Canonical result (n=159 Python+JS): +3.7% F1 (p=0.21, ns). Default False (plain tempograph = +6.0%
      outperforms precision_filter on canonical corpus). Enable only for high-baseline repos.
    definition_first: if True, when a keyword produces too-broad focus (>10 files) and no path match,
      fall back to the *defining file* of the top-ranked symbol (requires score≥10 and ≤2 defining files).
      Handles "redirect" → flask/helpers.py instead of injecting nothing.
      Phase 5.31 bench: +16.0% F1 (p=0.012*, n=93). Default True (enabled).
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
                                precision_filter=precision_filter,
                                definition_first=definition_first)
    except Exception as exc:
        return _error("RENDER_FAILED", f"prepare_context render error: {exc}", output_format)
    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool("prepare_context", p, output, elapsed, task=task, task_type=task_type)
    if output_format == "json":
        m = _re.search(r'KEY FILES[^:]*:\n((?:  \S+\n?)+)', output)
        # Strip :start-end line range annotations — key_files are bare paths for use
        # as baseline_predicted_files. Line ranges are available in result["data"] text.
        key_files = [_re.sub(r':\d+(-\d+)?$', '', ln.strip())
                     for ln in m.group(1).split('\n') if ln.strip()] if m else []
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


# ── Tool 18: Run kit ─────────────────────────────────────────────

@mcp.tool()
def run_kit(repo_path: str, kit: str, query: str = "", max_tokens: int = 4000,
            exclude_dirs: str = "", output_format: str = "text") -> str:
    """Run a composable kit — a named multi-mode workflow that combines tempograph
    modes into a single token-budgeted response.

    kit: name of the kit to run, or "list" to show all available kits.
      Built-in kits:
        explore     — overview + hotspots (orient to a new codebase)
        deep_dive   — focus + blast (deep-dive into a symbol)
        change_prep — diff + focus (prepare for a code change)
        code_review — dead + hotspots + focus (code review workflow)
        health      — hotspots + dead (codebase health check)
      Custom kits can be defined in .tempo/kits.json.
    query: optional symbol or topic for focus/blast steps
    max_tokens: total token budget across all kit steps (default 4000)
    exclude_dirs: comma-separated directory prefixes to skip
    output_format: "text" (default) or "json"

    Examples:
    - run_kit(".", "explore")                         → overview + hotspots
    - run_kit(".", "deep_dive", query="render_focused") → focus + blast on symbol
    - run_kit(".", "health")                          → hotspots + dead code
    - run_kit(".", "list")                            → show all available kits
    """
    from .kits import execute_kit, get_all_kits, list_kits

    p, err = _validate_repo(repo_path)
    if err:
        return _error(err, f"Directory not found: {repo_path}", output_format)

    if kit == "list":
        kits = list_kits(p)
        lines = ["Available kits:", ""]
        for name, desc in sorted(kits.items()):
            lines.append(f"  {name:15s} — {desc}")
        output = "\n".join(lines)
        tokens = count_tokens(output)
        return _success(output, tokens, 0.0, output_format)

    all_kits = get_all_kits(p)
    if kit not in all_kits:
        available = ", ".join(sorted(all_kits.keys()))
        return _error(INVALID_PARAMS, f"Unknown kit '{kit}'. Available: {available}", output_format)

    kit_def = all_kits[kit]
    excludes = _resolve_excludes(p, exclude_dirs)
    start = time.time()
    result = _get_or_build_graph(p, exclude_dirs=excludes or None)
    if isinstance(result, str):
        code, _, msg = result.partition(":")
        return _error(code, msg or "Graph build failed", output_format)

    try:
        output = execute_kit(result, kit_def, query=query, max_tokens=max_tokens)
    except Exception as exc:
        return _error("RENDER_FAILED", f"run_kit render error: {exc}", output_format)

    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool("run_kit", p, output, elapsed, kit=kit, query=query)
    return _success(output, tokens, elapsed, output_format)


@mcp.tool()
def search_semantic(repo_path: str, query: str, limit: int = 10,
                    exclude_dirs: str = "", output_format: str = "text") -> str:
    """Hybrid semantic + structural search across all symbols in a codebase.

    Combines FTS5 keyword matching with vector similarity (if embeddings exist)
    using Reciprocal Rank Fusion. Finds symbols by meaning, not just exact name match.

    Example: search_semantic(repo, "handle user authentication") finds auth-related
    functions even if they're named validate_token or check_credentials.

    Run `python3 -m tempograph <repo> --embed` first to enable semantic vectors.

    repo_path: absolute path to repository
    query: natural language description of what you're looking for
    limit: max results (default 10)
    """
    start = time.time()
    p, err = _validate_repo(repo_path)
    if err:
        return err
    graph = _get_or_build_graph(p, _resolve_excludes(exclude_dirs))
    if isinstance(graph, str):
        return graph

    results = graph.search_symbols_scored(query)[:limit]
    lines = []
    for score, sym in results:
        callers = len(graph.callers_of(sym.id))
        hot = " [hot]" if graph.hot_files and sym.file_path in graph.hot_files else ""
        lines.append(f"{score:6.1f}  {sym.kind.value:10s}  {sym.qualified_name:40s}  {sym.file_path}:{sym.line_start}  ({callers} callers){hot}")

    output = "\n".join(lines) if lines else "No matches found."
    elapsed = time.time() - start
    tokens = count_tokens(output)
    _log_tool("search_semantic", p, output, elapsed, query=query)
    return _success(output, tokens, elapsed, output_format)


@mcp.tool()
def watch_repo(repo_path: str, exclude_dirs: str = "") -> str:
    """Start watching a repository for file changes. Incrementally updates the graph DB
    when files are added, modified, or deleted. Uses Rust-backed file watcher for performance.

    repo_path: absolute path to the repository root
    exclude_dirs: comma-separated directories to ignore (e.g. "node_modules,dist")
    """
    from .watcher import GraphWatcher

    p, err = _validate_repo(repo_path)
    if err:
        return err

    if p in _watchers and _watchers[p].is_running:
        return f"Already watching {p}"

    excludes = [d.strip() for d in exclude_dirs.split(",") if d.strip()] if exclude_dirs else []

    def on_update(files: list[str]) -> None:
        # Invalidate cached graph so next tool call rebuilds from fresh DB
        _graphs.pop(p, None)
        _graph_timestamps.pop(p, None)

    watcher = GraphWatcher(p, exclude_dirs=excludes, on_update=on_update)
    watcher.start()
    _watchers[p] = watcher

    return f"Watching {p} for changes (incremental graph updates enabled)"


@mcp.tool()
def unwatch_repo(repo_path: str) -> str:
    """Stop watching a repository for file changes.

    repo_path: absolute path to the repository root
    """
    p, err = _validate_repo(repo_path)
    if err:
        return err

    watcher = _watchers.pop(p, None)
    if watcher:
        watcher.stop()
        return f"Stopped watching {p}"
    return f"Not currently watching {p}"


@mcp.tool()
def cochange_context(repo_path: str, file_path: str, n_commits: int = 200,
                     output_format: str = "text") -> str:
    """Files that historically co-change with a given file (logical coupling).

    Uses git history to find files frequently changed in the same commits.
    Useful for discovering hidden dependencies: if A and B co-change 80% of
    the time, a change to A likely requires reviewing B.

    file_path: path relative to repo root (e.g., "tempograph/render.py")
    n_commits: how many recent commits to analyze (default 200)
    output_format: "text" (default) or "json"
    """
    t0 = time.time()
    p, err = _validate_repo(repo_path)
    if err:
        return err
    if not _is_git_repo(p):
        return _error(NOT_GIT_REPO, f"Not a git repository: {repo_path}", output_format)

    from .git import cochange_matrix
    matrix = cochange_matrix(p, n_commits=n_commits)

    # Normalize: strip leading ./ and try both the given path and as-given
    fp = file_path.lstrip("./")
    partners = matrix.get(fp) or matrix.get(file_path)
    if partners is None:
        # Suffix match for partial paths (e.g. "render.py" → "tempograph/render.py")
        for key, val in matrix.items():
            if key.endswith("/" + fp) or key == fp:
                fp, partners = key, val
                break

    if not partners:
        return _error(NO_MATCH,
                      f"No co-change data for '{file_path}'. It may not appear in "
                      f"the last {n_commits} commits or has no co-change partners.",
                      output_format)

    lines = [f"Co-change partners for {fp} (last {n_commits} commits):"]
    for coupled_file, freq in partners:
        pct = int(freq * 100)
        lines.append(f"  {coupled_file}  {pct}%")

    output = "\n".join(lines)
    elapsed = time.time() - t0
    _log_tool("cochange_context", p, output, elapsed)
    return _success(output, count_tokens(output), elapsed, output_format)


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
