# tempograph

Semantic code graph toolkit. Python 3.11+, tree-sitter-based.

## Commands

```bash
pip install -e .            # Install (editable)
pip install -e ".[dev]"     # Install with test deps
pip install -e ".[bench]"   # Install with benchmark deps
pytest                      # Run tests
```

## Architecture

```
tempograph/
  __main__.py   — CLI entry point, arg parsing, mode dispatch
  builder.py    — build_graph(): orchestrates parsing + caching
  parser.py     — tree-sitter extraction (35 methods, 7 languages)
  cache.py      — content-hashed snapshot storage (.tempograph/cache.json)
  render.py     — all mode renderers (overview, map, focus, blast, etc.)
  types.py      — Symbol, Edge, CodeGraph, FileInfo dataclasses
  git.py        — git diff helpers for diff_context mode
  server.py     — MCP server (tempograph-server)
```

## Key Patterns

- All modes share one `CodeGraph` — build once, render many views
- Cache is content-hashed (not timestamp), so branch-switching is free
- `parser.py` has per-language handlers: `_extract_python_*`, `_extract_ts_*`, etc.
- `render.py` functions are `render_<mode>(graph, ...) -> str`
- Token budgets controlled via tiktoken; `--max-tokens` caps focus/diff output

## Gotchas

- Decorator-dispatched symbols (@mcp.tool, @app.route) show as dead code — false positives
- tree-sitter grammars are compiled on first use; cold start is slower
- `bench/` requires `pip install -e ".[bench]"` and an ANTHROPIC_API_KEY
- MCP server auto-detects git diff for `diff_context` tool — needs a git repo

## Entry Points

- CLI: `tempograph.__main__:main`
- MCP server: `tempograph.server:run_server`
- Python API: `from tempograph import build_graph, CodeGraph`
