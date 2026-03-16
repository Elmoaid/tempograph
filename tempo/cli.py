"""Tempo CLI — plugin-driven entry point."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .kernel.registry import Registry
from .kernel.config import Config
from .kernel.builder import build_graph
from .kernel.telemetry import log_feedback, log_usage, is_empty_result


def _run_feedback(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tempo feedback")
    parser.add_argument("repo")
    parser.add_argument("mode")
    parser.add_argument("helpful", choices=("true", "false"))
    parser.add_argument("note", nargs="?", default="")
    args = parser.parse_args(argv)

    log_feedback(
        str(Path(args.repo).resolve()),
        mode=args.mode,
        helpful=args.helpful == "true",
        note=args.note,
    )
    print(f"Feedback recorded for '{args.mode}' (helpful={args.helpful}).")
    return 0


def _run_plugins(reg: Registry) -> int:
    """Show plugin status."""
    status = reg.status()
    for name, info in status["plugins"].items():
        dot = "●" if info["enabled"] else "○"
        deps = f" (needs: {', '.join(info['depends'])})" if info['depends'] else ""
        print(f"  {dot} {name:<14} {info['description']}{deps}")
    print(f"\n{status['enabled_count']}/{status['total_count']} enabled")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]

    if raw and raw[0] == "feedback":
        return _run_feedback(raw[1:])

    # Init registry
    reg = Registry()
    reg.discover()

    # Build mode choices from registered plugins
    plugin_modes = sorted(reg.status()["modes"].keys())
    all_modes = plugin_modes + ["stats", "report", "serve", "plugins"]

    parser = argparse.ArgumentParser(
        prog="tempo",
        description="Agent effectiveness engine — structural code intelligence.",
    )
    parser.add_argument("repo", help="Path to the repository root")
    parser.add_argument("--mode", "-m", choices=all_modes, default="overview")
    parser.add_argument("--query", "-q", help="Query for focus/lookup modes")
    parser.add_argument("--file", "-f", help="File path for blast/diff modes")
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--json", action="store_true", help="Output raw graph as JSON")
    parser.add_argument("--tokens", action="store_true", help="Show token count")
    parser.add_argument("--no-log", action="store_true", help="Disable usage logging")
    parser.add_argument("--enable", help="Enable a plugin")
    parser.add_argument("--disable", help="Disable a plugin")
    parser.add_argument("--exclude", "-x", help="Comma-separated directory prefixes to exclude (e.g. archive,bench/results)")
    parser.add_argument("--task-type", help="Explicit task type for L2 learning (e.g. refactor, debug, feature, review)")
    args = parser.parse_args(raw)

    repo = str(Path(args.repo).resolve())
    cfg = Config(repo)

    # Handle plugin toggling
    if args.enable:
        enabled = reg.enable(args.enable)
        print(f"Enabled: {', '.join(enabled)}")
        return 0
    if args.disable:
        ok, blockers = reg.disable(args.disable)
        if not ok:
            print(f"Cannot disable '{args.disable}' — required by: {', '.join(blockers)}")
            return 1
        print(f"Disabled: {args.disable}")
        return 0

    # Modes that don't need graph
    if args.mode == "report":
        from tempograph.report import generate_report
        print(generate_report(repo))
        return 0
    if args.mode == "plugins":
        return _run_plugins(reg)

    # Build graph — merge CLI --exclude with config-persisted exclude_dirs
    cli_exclude = [p.strip() for p in args.exclude.split(",")] if args.exclude else []
    cfg_exclude = cfg.get("exclude_dirs") or []
    exclude_dirs = list(dict.fromkeys(cfg_exclude + cli_exclude)) or None  # deduplicate, preserve order

    print(f"Building graph for {repo}...", file=sys.stderr)
    start = time.time()
    graph = build_graph(repo, exclude_dirs=exclude_dirs)
    elapsed = time.time() - start
    stats = graph.stats
    print(f"Done in {elapsed:.1f}s — {stats['files']} files, {stats['symbols']} symbols, {stats['edges']} edges", file=sys.stderr)

    if args.json:
        data = {
            "root": graph.root,
            "stats": stats,
            "files": {fp: {"language": fi.language.value, "line_count": fi.line_count} for fp, fi in graph.files.items()},
            "symbols": {sid: {"name": s.name, "kind": s.kind.value, "file_path": s.file_path, "line_start": s.line_start} for sid, s in graph.symbols.items()},
            "edges": [{"kind": e.kind.value, "source": e.source_id, "target": e.target_id} for e in graph.edges],
        }
        print(json.dumps(data, indent=2))
        return 0

    if args.mode == "serve":
        from tempograph.server import run_server
        run_server()
        return 0

    if args.mode == "stats":
        from tempograph.render import count_tokens, render_overview, render_map
        ov = render_overview(graph)
        mp = render_map(graph)
        print(f"Build: {elapsed:.1f}s\nFiles: {stats['files']}, Symbols: {stats['symbols']}, Edges: {stats['edges']}\nLines: {stats['total_lines']:,}\n\nToken costs:\n  overview:  {count_tokens(ov):,}\n  map:       {count_tokens(mp):,}")
        return 0

    # Run via plugin registry
    runner = reg.get_runner(args.mode)
    if not runner:
        print(f"Mode '{args.mode}' is not available (plugin disabled or missing).", file=sys.stderr)
        return 1

    kwargs = {"query": args.query or "", "file": args.file or "", "max_tokens": args.max_tokens}
    if args.mode == "diff":
        kwargs["changed_files"] = [f.strip() for f in (args.file or "").split(",") if f.strip()]

    output = runner(graph, **kwargs)
    print(output)

    if args.tokens:
        from tempograph.render import count_tokens
        print(f"\n[{count_tokens(output):,} tokens]", file=sys.stderr)

    if not args.no_log and args.mode not in ("stats", "report", "plugins"):
        from tempograph.render import count_tokens
        log_usage(repo, source="cli", mode=args.mode, query=args.query, file=args.file,
                  symbols=stats["symbols"], tokens=count_tokens(output),
                  duration_ms=int(elapsed * 1000), empty=is_empty_result(output),
                  task_type=args.task_type)

    return 0


if __name__ == "__main__":
    sys.exit(main())
