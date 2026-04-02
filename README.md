# Tempograph

<!-- mcp-name: io.github.Elmoaid/tempograph -->

[![CI](https://github.com/Elmoaid/TempoGraph/actions/workflows/ci.yml/badge.svg)](https://github.com/Elmoaid/TempoGraph/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![TempoGraph MCP server](https://glama.ai/mcp/servers/Elmoaid/TempoGraph/badges/score.svg)](https://glama.ai/mcp/servers/Elmoaid/TempoGraph)

**Your AI agent finds the right files. Every time.**

Tempograph builds a dependency graph of your codebase and gives your AI coding agent exactly the files it needs before making changes. One tool call. No guessing.

<p align="center">
  <img src="docs/demo.gif" alt="TempoGraph demo" width="700">
</p>

## The Problem

AI coding agents guess which files to look at. They search by filename, grep for keywords, and hope for the best. In large codebases, they miss critical dependencies, break things downstream, and waste tokens reading irrelevant code.

## The Fix

```bash
pip install tempograph
```

Add to your MCP config (Claude Code, Cursor, Windsurf, or any MCP client):

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

Your agent calls `prepare_context` with a task description. Tempograph returns the exact files that matter — based on real dependency analysis, not text matching.

## Does It Work?

Tested on real PRs from django, flask, httpx, fastapi, requests, and pydantic. Task: predict which files need to change.

| Model | Without Tempograph | With Tempograph | Improvement |
|-------|-------------------|-----------------|-------------|
| GPT-4o | 21.7% F1 | 27.5% F1 | **+27%** |
| GPT-4o-mini | 19.2% F1 | 24.5% F1 | **+28%** |
| qwen2.5-coder:32b | — | — | **+18.6%** (p=0.049) |

Consistent improvement across every model. 2-3x more tasks helped than hurt. No other code context tool publishes retrieval benchmarks with statistical significance.

## How It Works

```
your repo ──→ tree-sitter parse ──→ symbols + edges ──→ SQLite graph
                                                            │
                    AI agent calls prepare_context ─────────┘
                                                            │
                              ◄── KEY FILES + callers + callees + risk signals
```

- Parses your code with tree-sitter into a structural dependency graph
- Content-hashed and stored in SQLite — only changed files get re-parsed
- Warm queries in ~21ms. Branch switching doesn't trigger a rebuild
- Knows when NOT to inject context (adaptive gating avoids harming diffuse commits)

## What Else Can It Do?

Beyond `prepare_context`, Tempograph exposes 24 MCP tools for deeper analysis when your agent needs it:

| Tool | When to use it |
|------|---------------|
| `blast_radius` | "What breaks if I change this file?" |
| `focus` | "Show me everything related to auth" |
| `hotspots` | "Which files are riskiest to change?" |
| `dead_code` | "What can I safely delete?" |
| `diff_context` | "What's the impact of my current changes?" |
| `overview` | "Orient me in this new codebase" |

<details>
<summary>All 24 tools</summary>

| Tool | What it does |
|------|-------------|
| `prepare_context` | One-shot context for a task — the primary tool |
| `overview` | Repository orientation: size, languages, entry points |
| `focus` | Connected subgraph around a symbol — callers, callees |
| `blast_radius` | What breaks if you change this file or symbol |
| `diff_context` | Impact analysis of changed files |
| `hotspots` | Ranked risk list — complexity x coupling x size |
| `dead_code` | Unreferenced symbols — cleanup candidates |
| `lookup` | "Where is X?", "What calls X?" |
| `dependencies` | Circular imports, dependency layers |
| `architecture` | Module-level dependency view |
| `symbols` | Full symbol inventory |
| `file_map` | File tree with top symbols per file |
| `search_semantic` | Hybrid keyword + vector + structural search |
| `cochange_context` | Files that historically change together |
| `suggest_next` | Predicts the next useful tool call |
| `run_kit` | Composable multi-tool workflows |
| `stats` | Token budget estimates |
| `get_patterns` | Codebase conventions and idioms |
| `report_feedback` | Log whether output was useful |
| `learn_recommendation` | Suggestions from feedback history |
| `index_repo` | Build or rebuild the graph |
| `watch_repo` / `unwatch_repo` | Live incremental updates |
| `embed_repo` | Generate vector embeddings |

</details>

## CLI

```bash
# Orient in a new repo
tempograph ./my-project --mode overview

# What's connected to auth?
tempograph ./my-project --mode focus --query "authentication"

# What breaks if I touch db.ts?
tempograph ./my-project --mode blast --file src/lib/db.ts

# Find dead code to clean up
tempograph ./my-project --mode dead
```

## Python API

```python
from tempograph import build_graph

graph = build_graph("./my-project")
results = graph.search_symbols("handleLogin")
importers = graph.importers_of("src/lib/db.ts")
dead = graph.find_dead_code()
```

## Languages

Python, TypeScript, JavaScript, Rust, Go, Java, C#, and Ruby get deep extraction (custom tree-sitter handlers). 170+ additional languages are supported via generic handler. `pip install tempograph[full]` for everything.

## Support & Sponsorship

If TempoGraph saves you time, consider [sponsoring the project](https://github.com/sponsors/Elmoaid). Sponsors get early access to new features.

[![Sponsor](https://img.shields.io/badge/Sponsor-TempoGraph-ea4aaa?logo=github-sponsors)](https://github.com/sponsors/Elmoaid)

## Commercial Licensing

TempoGraph is AGPL-3.0 — free to use, modify, and distribute. If you use TempoGraph in a **network service** (SaaS, hosted IDE, AI coding platform), AGPL requires you to open-source your service code. If that doesn't work for you, commercial licenses are available.

Contact **elmoaid@gmail.com** for commercial licensing terms.

## License

[AGPL-3.0](LICENSE) — free to use. Network service use requires source disclosure, or a [commercial license](mailto:elmoaid@gmail.com).
