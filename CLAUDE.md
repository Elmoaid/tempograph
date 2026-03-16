# Tempo

Agent effectiveness engine. Python 3.11+, tree-sitter kernel, plugin architecture.

## Commands

```bash
pip install -e .            # Install (editable)
pip install -e ".[dev]"     # Install with test deps
pip install -e ".[bench]"   # Install with benchmark deps
pytest                      # Run tests (92 tests)
```

## Architecture

```
tempograph/                  ← core package
  __main__.py   — CLI entry point, arg parsing, mode dispatch, feedback subcommand
  builder.py    — build_graph(): orchestrates parsing + caching
  parser.py     — tree-sitter extraction (10 languages: Python, TS, TSX, JS, JSX, Rust, Go, Java, C#, Ruby)
  cache.py      — content-hashed snapshot storage (.tempograph/cache.json)
  render.py     — all mode renderers + dead code confidence scoring
  types.py      — Symbol, Edge, Tempo, FileInfo dataclasses
  git.py        — git diff helpers for diff_context mode
  server.py     — MCP server (15 tools, JSON output, error codes, exclude_dirs)
  telemetry.py  — usage + feedback JSONL logging (local + ~/.tempograph/global/)
  report.py     — usage/feedback report generator (filters internal stats noise)
```

## MCP Server (15 tools)

All tools accept `output_format="json"` → `{"status":"ok","data":"...","tokens":N,"duration_ms":N}`
All tools accept `exclude_dirs="archive,vendor"` to filter noise.
Error codes: `REPO_NOT_FOUND`, `NOT_GIT_REPO`, `NO_MATCH`, `BUILD_FAILED`, `INVALID_PARAMS`, `LEARN_UNAVAILABLE`

Tools: index_repo, overview, focus, lookup, blast_radius, hotspots, diff_context, dead_code,
symbols, file_map, dependencies, architecture, stats, report_feedback, learn_recommendation

See AGENT_GUIDE.md for full agent integration docs.

## Key Patterns

- All modes share one `Tempo` — build once, render many views
- Cache is content-hashed (not timestamp), so branch-switching is free
- `parser.py` has per-language handlers: `_handle_python`, `_handle_js_ts`, `_handle_go`, `_handle_java`, `_handle_csharp`, `_handle_ruby`
- `parser.py` skips `import type` statements in TS (no runtime impact, prevents false circular imports)
- `parser.py` detects dynamic `import()` via regex after tree-sitter walk (for React.lazy, import().then)
- `_scan_calls()` handles `call_expression`, `call`, `method_invocation` (Java), `invocation_expression` (C#)
- `render.py` functions are `render_<mode>(graph, ...) -> str`
- `search_symbols()` ranks by text match + exported status + cross-file callers + symbol kind
- Focus mode: BFS depth 3, detail at depth 0-1, file context section, overflow counts
- Token budgets controlled via tiktoken; `--max-tokens` caps focus/diff output
- Dead code confidence scoring penalizes single-component files (-20, likely lazy-loaded)
- Telemetry writes to both local `.tempograph/` and `~/.tempograph/global/`
- `_scan_calls()` traverses `spread_element` nodes (for Zustand-style `...createSlice()`)

## Entry Points

- CLI: `tempograph.__main__:main`
- Feedback: `python3 -m tempograph feedback <repo> <mode> <true|false> [note]`
- MCP server: `tempograph.server:run_server`
- Python API: `from tempograph import build_graph, Tempo`

## Tempo Desktop App

Tauri v2 + React + Vite. Dev port: 4902.

```bash
cd tempo/ui && pnpm tauri dev
```

## Roadmap

See `.claude.local.md` for full roadmap. Currently in Phase 1: Plugin Kernel restructure.
