"""CLI entry point: python3 -m tempograph <repo_path> [options]"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _repo_info(repo: str) -> str:
    """Generate a status dashboard for a repo's tempograph state."""
    from .storage import GraphDB
    from .git import is_git_repo, cochange_matrix

    rp = Path(repo)
    db_path = rp / ".tempograph" / "graph.db"
    cache_path = rp / ".tempograph" / "cache.json"
    config_path = rp / ".tempo" / "config.json"
    lines = [f"Tempograph v0.6.0 — {repo}", ""]

    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        db = GraphDB(repo)
        lines.append(f"Storage:    SQLite ({size_mb:.1f} MB)")
        lines.append(f"Symbols:    {db.symbol_count()}")
        lines.append(f"Files:      {db.file_count()}")
        has_vec = db.init_vectors()
        if has_vec:
            try:
                vc = db._conn.execute("SELECT COUNT(*) FROM symbol_vectors").fetchone()[0]
            except Exception:
                vc = 0
            if vc > 0:
                lines.append(f"Vectors:    {vc} embeddings (semantic search active)")
            else:
                lines.append(f"Vectors:    none (run --embed to enable)")
        else:
            lines.append(f"Vectors:    sqlite-vec not installed (pip install tempograph[semantic])")
        db.close()
    elif cache_path.exists():
        size_kb = cache_path.stat().st_size / 1024
        lines.append(f"Storage:    JSON cache ({size_kb:.0f} KB)")
    else:
        lines.append(f"Storage:    not indexed")

    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            exc = cfg.get("exclude_dirs", [])
            lines.append(f"Config:     .tempo/config.json (exclude: {', '.join(exc) if exc else 'none'})")
        except Exception:
            lines.append(f"Config:     .tempo/config.json (parse error)")
    else:
        lines.append(f"Config:     defaults")

    if is_git_repo(repo):
        matrix = cochange_matrix(repo, n_commits=100)
        lines.append(f"Co-change:  {len(matrix)} files with coupling data")
    else:
        lines.append(f"Co-change:  not a git repo")

    try:
        from .kits import get_all_kits as _gak
        _k = _gak(repo)
        lines.append(f"Kits:       {len(_k)} ({', '.join(sorted(_k.keys()))})")
    except Exception:
        pass

    try:
        from .predict import build_transition_matrix
        matrix = build_transition_matrix(repo, min_count=5)
        if matrix:
            top_pred = max(matrix.items(), key=lambda x: x[1][0][1] if x[1] else 0)
            lines.append(f"Prediction: {len(matrix)} mode transitions learned")
        else:
            lines.append(f"Prediction: no usage data yet")
    except Exception:
        pass

    return "\n".join(lines)

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


def _run_feedback(argv: list[str]) -> int:
    """Handle: python3 -m tempograph feedback <repo> <mode> <helpful> [note]"""
    parser = argparse.ArgumentParser(
        prog="tempograph feedback",
        description="Report whether tempograph output was helpful.",
    )
    parser.add_argument("repo", help="Path to the repository that was analyzed")
    parser.add_argument("mode", help="Which mode was used (overview, focus, blast, dead, hotspots, diff, etc.)")
    parser.add_argument("helpful", choices=("true", "false"), help="Was the output helpful?")
    parser.add_argument("note", nargs="?", default="", help="1-2 sentences: what worked, what was missing")
    args = parser.parse_args(argv)

    from .telemetry import log_feedback
    log_feedback(
        str(Path(args.repo).resolve()),
        mode=args.mode,
        helpful=args.helpful == "true",
        note=args.note,
    )
    print(f"Feedback recorded for '{args.mode}' (helpful={args.helpful}). Thanks!")
    return 0


def _run_snapshot(argv: list[str]) -> int:
    """Handle: python3 -m tempograph snapshot [--list | --repo ORG/REPO]"""
    parser = argparse.ArgumentParser(
        prog="tempograph snapshot",
        description="Manage pre-built graph snapshots for popular OSS repos.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List available snapshots")
    group.add_argument("--repo", metavar="ORG/REPO", help="Download snapshot for this repo")
    args = parser.parse_args(argv)

    from .snapshots import list_snapshots, download_snapshot, is_downloaded

    if args.list:
        repos = list_snapshots()
        print("Available snapshots:")
        for repo in repos:
            status = "downloaded" if is_downloaded(repo) else "not downloaded"
            print(f"  {repo} ({status})")
        return 0

    return 0 if download_snapshot(args.repo) else 1


__version__ = "0.6.0"


def main(argv: list[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]

    if raw and raw[0] in ("--version", "-V"):
        print(f"tempograph {__version__}")
        return 0

    # Intercept 'feedback' subcommand before argparse (avoids graph build)
    if raw and raw[0] == "feedback":
        return _run_feedback(raw[1:])

    # Intercept 'snapshot' subcommand before argparse (no repo needed)
    if raw and raw[0] == "snapshot":
        return _run_snapshot(raw[1:])

    parser = argparse.ArgumentParser(
        prog="tempograph",
        description="Build and query a semantic code graph for any repository.",
    )
    parser.add_argument("repo", help="Path to the repository root")
    parser.add_argument(
        "--mode", "-m",
        choices=("overview", "map", "symbols", "focus", "lookup", "blast", "diff", "hotspots", "deps", "dead", "arch", "stats", "prepare", "skills", "report", "serve"),
        default="overview",
        help="Rendering mode (default: overview)",
    )
    parser.add_argument("--query", "-q", help="Query for focus/lookup modes")
    parser.add_argument("--file", "-f", help="File path for blast radius mode, or comma-separated files for diff mode")
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--json", action="store_true", help="Output raw graph as JSON")
    parser.add_argument("--tokens", action="store_true", help="Show token count")
    parser.add_argument("--no-log", action="store_true", help="Disable usage logging")
    parser.add_argument("--exclude", "-x", help="Comma-separated directory prefixes to exclude (e.g. archive,bench/results)")
    parser.add_argument("--task-type", help="Explicit task type for L2 learning (e.g. refactor, debug, feature, review)")
    parser.add_argument("--kit", "-k", metavar="KIT",
                        help="Run a composable kit workflow. Use --kit list to show all kits.")
    parser.add_argument("--embed", action="store_true",
                        help="Generate embeddings for semantic search (requires fastembed)")
    parser.add_argument("--search", metavar="QUERY",
                        help="Hybrid semantic+structural search for symbols")
    parser.add_argument("--info", action="store_true",
                        help="Show tempograph status dashboard for this repo")
    parser.add_argument("--from-snapshot", metavar="ORG/REPO",
                        help="Load a pre-built snapshot instead of indexing. "
                             "Use 'python3 -m tempograph snapshot --list' to see available snapshots.")

    args = parser.parse_args(raw)
    repo = str(Path(args.repo).resolve())

    # Info: show repo tempograph status dashboard
    if args.info:
        print(_repo_info(repo))
        return 0

    # Embed: generate embeddings for semantic search
    if args.embed:
        from .embeddings import embed_symbols as _embed_symbols
        _g = build_graph(repo, exclude_dirs=args.exclude.split(",") if args.exclude else None)
        count = _embed_symbols(_g._db if hasattr(_g, '_db') else None)
        print(f"Embedded {count} symbols for semantic search")
        return 0

    # Search: hybrid semantic+structural search
    if args.search:
        _g = build_graph(repo, exclude_dirs=args.exclude.split(",") if args.exclude else None)
        results = _g.search_symbols_scored(args.search)[:20]
        for score, sym in results:
            print(f"  {score:6.1f}  {sym.kind.value:10s}  {sym.qualified_name:40s}  {sym.file_path}:{sym.line_start}")
        return 0

    # Kit list: no graph needed
    if args.kit == "list":
        from .kits import list_kits
        kits = list_kits(repo)
        print("Available kits:")
        print()
        for name, desc in sorted(kits.items()):
            print(f"  {name:15s} — {desc}")
        return 0

    # Report mode: no graph needed
    if args.mode == "report":
        from .report import generate_report
        print(generate_report(repo))
        return 0

    # From-snapshot: load a pre-built graph.db instead of indexing
    if getattr(args, "from_snapshot", None):
        from .builder import load_from_snapshot
        try:
            print(f"Loading snapshot for {args.from_snapshot}...", file=sys.stderr)
            start = time.time()
            graph = load_from_snapshot(args.from_snapshot)
            elapsed = time.time() - start
            stats = graph.stats
            print(
                f"Done in {elapsed:.1f}s — {stats['files']} files, "
                f"{stats['symbols']} symbols, {stats['edges']} edges",
                file=sys.stderr,
            )
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"error loading snapshot: {exc}", file=sys.stderr)
            return 1
    else:
        # Merge CLI --exclude with config-file exclude_dirs
        cli_exclude = [p.strip() for p in args.exclude.split(",")] if args.exclude else []
        cfg_path = Path(repo) / ".tempo" / "config.json"
        cfg_exclude: list[str] = []
        if cfg_path.exists():
            try:
                cfg_exclude = json.loads(cfg_path.read_text()).get("exclude_dirs", [])
            except (json.JSONDecodeError, OSError):
                pass
        exclude_dirs = list(dict.fromkeys(cfg_exclude + cli_exclude)) or None

        print(f"Building graph for {repo}...", file=sys.stderr)
        start = time.time()
        graph = build_graph(repo, exclude_dirs=exclude_dirs)
        elapsed = time.time() - start
        stats = graph.stats
        print(
            f"Done in {elapsed:.1f}s — {stats['files']} files, "
            f"{stats['symbols']} symbols, {stats['edges']} edges",
            file=sys.stderr,
        )

    if args.json:
        data = {
            "root": graph.root,
            "stats": stats,
            "files": {
                fp: {
                    "language": fi.language.value,
                    "line_count": fi.line_count,
                    "symbols": fi.symbols,
                    "imports": fi.imports,
                }
                for fp, fi in graph.files.items()
            },
            "symbols": {
                sid: {
                    "name": sym.name,
                    "qualified_name": sym.qualified_name,
                    "kind": sym.kind.value,
                    "file_path": sym.file_path,
                    "line_start": sym.line_start,
                    "line_end": sym.line_end,
                    "signature": sym.signature,
                    "doc": sym.doc,
                    "exported": sym.exported,
                }
                for sid, sym in graph.symbols.items()
            },
            "edges": [
                {"kind": e.kind.value, "source": e.source_id, "target": e.target_id, "line": e.line}
                for e in graph.edges
            ],
        }
        print(json.dumps(data, indent=2))
        return 0

    if args.mode == "serve":
        from .server import run_server
        run_server()
        return 0

    # Kit execution (--kit <name>)
    if args.kit and args.kit != "list":
        from .kits import execute_kit, get_all_kits
        all_kits = get_all_kits(repo)
        if args.kit not in all_kits:
            available = ", ".join(sorted(all_kits.keys()))
            print(f"[ERROR] Unknown kit '{args.kit}'. Available: {available}", file=sys.stderr)
            return 1
        kit_def = all_kits[args.kit]
        output = execute_kit(graph, kit_def, query=args.query or "", max_tokens=args.max_tokens)
        print(output)
        if args.tokens:
            tokens = count_tokens(output)
            print(f"\n[{tokens:,} tokens]", file=sys.stderr)
        if not args.no_log:
            from .telemetry import log_usage, is_empty_result
            tokens = count_tokens(output) if not args.tokens else tokens
            log_usage(repo, source="cli", mode=f"kit:{args.kit}", query=args.query,
                      symbols=stats["symbols"], tokens=tokens,
                      duration_ms=int(elapsed * 1000), empty=is_empty_result(output))
        return 0

    mode_map = {
        "overview": lambda: render_overview(graph),
        "map": lambda: render_map(graph, max_tokens=args.max_tokens),
        "symbols": lambda: render_symbols(graph, max_tokens=args.max_tokens),
        "focus": lambda: render_focused(graph, args.query or "main", max_tokens=args.max_tokens),
        "lookup": lambda: render_lookup(graph, args.query or ""),
        "blast": lambda: render_blast_radius(graph, args.file or "", query=args.query or ""),
        "diff": lambda: render_diff_context(graph, [f.strip() for f in (args.file or "").split(",") if f.strip()], max_tokens=args.max_tokens),
        "hotspots": lambda: render_hotspots(graph),
        "deps": lambda: render_dependencies(graph),
        "dead": lambda: render_dead_code(graph),
        "arch": lambda: render_architecture(graph),
        "stats": lambda: _render_stats(graph, elapsed),
        "prepare": lambda: render_prepare(graph, args.query or "understand this codebase", args.max_tokens, args.task_type or ""),
        "skills": lambda: render_skills(graph, args.query or "", max_tokens=args.max_tokens),
    }

    output = mode_map[args.mode]()
    print(output)

    if args.tokens:
        tokens = count_tokens(output)
        print(f"\n[{tokens:,} tokens]", file=sys.stderr)

    # Usage logging — skip stats/report modes (diagnostic only, not real usage signal)
    if not args.no_log and args.mode not in ("stats", "report"):
        from .telemetry import log_usage, is_empty_result
        tokens = count_tokens(output) if not args.tokens else tokens  # reuse if already computed
        log_usage(
            repo,
            source="cli",
            mode=args.mode,
            query=args.query,
            file=args.file,
            symbols=stats["symbols"],
            tokens=tokens,
            duration_ms=int(elapsed * 1000),
            empty=is_empty_result(output),
            task_type=args.task_type,
        )

    return 0


def _render_stats(graph, build_time: float) -> str:
    from .render import render_map, render_overview, count_tokens
    s = graph.stats
    ov = render_overview(graph)
    mp = render_map(graph)
    lines = [
        f"Build: {build_time:.1f}s",
        f"Files: {s['files']}, Symbols: {s['symbols']}, Edges: {s['edges']}",
        f"Lines: {s['total_lines']:,}",
        "",
        f"Token costs:",
        f"  overview:  {count_tokens(ov):,}",
        f"  map:       {count_tokens(mp):,}",
        f"  symbols:   ~{s['symbols'] * 15:,} (est)",
        f"  focused:   ~2,000-4,000 (query-dep)",
        f"  lookup:    ~100-500 (question-dep)",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
