# Tempograph — Agent Guide

Drop this into your CLAUDE.md, system prompt, or agent instructions.

## Quick Start

For most tasks, call `prepare_context` once — it combines overview + focus + hotspots + diff in one
token-budgeted response:

```
prepare_context(repo_path="/my/repo", task="fix auth bug in login flow", task_type="debug")
```

## Workflow (manual)

1. **Session start**: Call `index_repo` with the repo path. Builds the graph and returns orientation (~500 tokens).
2. **Before changes**: Call `focus` with what you're working on (e.g. "authentication middleware") to get relevant symbols, callers, and complexity warnings.
3. **Before modifying a file**: Call `blast_radius` with `query="SymbolName"` to see what breaks.
4. **After git changes**: Call `diff_context` with `scope="unstaged"` to see impact of your changes.
5. **After using any tool**: Call `report_feedback` with the mode name, whether it helped, and a short note.

## All 16 Tools

| Tool | Use When | Token Cost |
|------|----------|------------|
| `index_repo` | Session start — builds graph + orientation | ~600 |
| `overview` | Quick repo orientation (no rebuild) | ~500 |
| `focus` | Working on a specific feature/area | 2-4K |
| `lookup` | "where is X?", "what calls X?", "who imports X?" | 100-500 |
| `blast_radius` | Before changing a file or symbol | 1-5K |
| `hotspots` | Find riskiest, most-coupled code | ~2.5K |
| `diff_context` | Impact analysis of changed files | 3-6K |
| `dead_code` | Find unused exports for cleanup | variable |
| `symbols` | Full symbol inventory (use sparingly) | ~30K |
| `file_map` | File tree with top symbols | ~20K |
| `dependencies` | Circular imports + layer structure | ~1K |
| `architecture` | Module-level view + cross-module edges | ~2K |
| `stats` | Token budget planner for all modes | ~100 |
| `learn_recommendation` | Data-driven mode suggestions | ~200 |
| `prepare_context` | **One-shot context for a task** (recommended) | 2-6K |
| `report_feedback` | Log whether output was helpful | — |

## JSON Mode

All tools accept `output_format="json"` for structured responses:

```json
{"status": "ok", "data": "...", "tokens": 579, "duration_ms": 81}
```

Errors return machine-readable codes:

```json
{"status": "error", "code": "REPO_NOT_FOUND", "message": "..."}
```

Error codes: `REPO_NOT_FOUND`, `NOT_GIT_REPO`, `NO_MATCH`, `BUILD_FAILED`, `INVALID_PARAMS`, `LEARN_UNAVAILABLE`

## Filtering with exclude_dirs

All tools accept `exclude_dirs` — a comma-separated string of directory prefixes to skip:

```
index_repo(repo_path="/my/repo", exclude_dirs="archive,vendor,dist")
```

This filters out noise from archived code, vendored dependencies, build output, etc. Dramatically improves signal on repos with legacy directories.

Also reads from `.tempo/config.json`:
```json
{"exclude_dirs": ["archive", "vendor"]}
```

Both sources are merged — explicit parameter + config file.

## Key Rules

- Always pass the full repo path to every tool (server caches graphs in-process).
- `blast_radius`: if you pass both `file_path` and `query`, query takes precedence.
- `diff_context`: requires a git repo when using `scope`. Pass `changed_files` explicitly to skip git.
- Avoid `symbols` and `file_map` on large repos — use `focus` or `lookup` instead.
- Use `exclude_dirs` to filter out archive/vendor/dist directories for cleaner results.
- Call `report_feedback` after every tool use to improve recommendations.
- `import type` statements in TypeScript are ignored — they have no runtime impact.

## Setup

```bash
pip install -e .  # install tempograph
```

MCP config (add to `.mcp.json`):

```json
{
  "mcpServers": {
    "tempograph": {
      "command": "tempograph-server",
      "args": []
    }
  }
}
```

Requires Python 3.11+. Tree-sitter grammars auto-install on first parse.
