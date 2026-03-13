"""CLI entry point: python3 -m tempograph <repo_path> [options]"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

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
    render_symbols,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tempograph",
        description="Build and query a semantic code graph for any repository.",
    )
    parser.add_argument("repo", help="Path to the repository root")
    parser.add_argument(
        "--mode", "-m",
        choices=("overview", "map", "symbols", "focus", "lookup", "blast", "diff", "hotspots", "deps", "dead", "arch", "stats", "report", "serve"),
        default="overview",
        help="Rendering mode (default: overview)",
    )
    parser.add_argument("--query", "-q", help="Query for focus/lookup modes")
    parser.add_argument("--file", "-f", help="File path for blast radius mode, or comma-separated files for diff mode")
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--json", action="store_true", help="Output raw graph as JSON")
    parser.add_argument("--tokens", action="store_true", help="Show token count")
    parser.add_argument("--no-log", action="store_true", help="Disable usage logging")

    args = parser.parse_args(argv)
    repo = str(Path(args.repo).resolve())

    # Report mode: no graph needed
    if args.mode == "report":
        from .report import generate_report
        print(generate_report(repo))
        return 0

    print(f"Building graph for {repo}...", file=sys.stderr)
    start = time.time()
    graph = build_graph(repo)
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

    mode_map = {
        "overview": lambda: render_overview(graph),
        "map": lambda: render_map(graph),
        "symbols": lambda: render_symbols(graph),
        "focus": lambda: render_focused(graph, args.query or "main", max_tokens=args.max_tokens),
        "lookup": lambda: render_lookup(graph, args.query or ""),
        "blast": lambda: render_blast_radius(graph, args.file or "", query=args.query or ""),
        "diff": lambda: render_diff_context(graph, [f.strip() for f in (args.file or "").split(",") if f.strip()], max_tokens=args.max_tokens),
        "hotspots": lambda: render_hotspots(graph),
        "deps": lambda: render_dependencies(graph),
        "dead": lambda: render_dead_code(graph),
        "arch": lambda: render_architecture(graph),
        "stats": lambda: _render_stats(graph, elapsed),
    }

    output = mode_map[args.mode]()
    print(output)

    if args.tokens:
        tokens = count_tokens(output)
        print(f"\n[{tokens:,} tokens]", file=sys.stderr)

    # Usage logging
    if not args.no_log:
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
