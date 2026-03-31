# Tempograph

**Code graph context engine for AI coding agents.**

Tempograph parses your codebase with tree-sitter, builds a structural dependency graph, and gives AI agents exactly the right context before they make code changes. One `prepare_context` call replaces manual file-hunting.

**Statistically validated**: +18.6% F1 improvement on change-localization (p=0.049, n=45) with zero harm rate. No other code context tool publishes retrieval results with statistical significance.

```bash
pip install tempograph
```

## How It Works

```
your repo ──→ tree-sitter parse ──→ symbols + edges ──→ SQLite graph
                                                            │
                    AI agent calls prepare_context ─────────┘
                                                            │
                              ◄── KEY FILES + callers + callees + risk signals
```

Content-hashed graph stored in `.tempograph/graph.db` (SQLite + WAL). Only changed files are re-parsed — a 10,000-file repo re-indexes in seconds. Branch-switching doesn't force a rebuild. Includes FTS5 keyword search, optional vector embeddings (sqlite-vec), and Reciprocal Rank Fusion for hybrid retrieval.

## Quick Start — MCP Server (for AI agents)

Add to your `.mcp.json` or `~/.claude/settings.json`:

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

Then in your agent prompt or CLAUDE.md:

```
prepare_context(repo_path="/path/to/repo", task="fix auth bug in login flow")
```

The tool auto-selects the right context type, extracts keywords, runs symbol search, and returns KEY FILES within a token budget. See [AGENT_GUIDE.md](AGENT_GUIDE.md) for full integration docs and bench evidence.

## Supported Languages

**170+ languages** via tree-sitter-language-pack. Custom handlers with deeper extraction for Python, TypeScript, TSX, JavaScript, JSX, Rust, Go, Java, C#, and Ruby. Generic handler covers PHP, Swift, Kotlin, Dart, Scala, Lua, C, C++, Zig, Haskell, and 160+ more.

```bash
pip install tempograph              # core (10 languages)
pip install tempograph[languages]   # 170+ languages
pip install tempograph[semantic]    # vector search + embeddings
pip install tempograph[watch]       # live file watching
pip install tempograph[full]        # everything
```

Requires Python 3.11+.

## 24 MCP Tools

| Tool | What it does |
|------|-------------|
| **`prepare_context`** | One-shot context for a task — keyword extraction, symbol search, KEY FILES list. Adaptive gating + definition-first ordering. **This is the primary tool for agents.** |
| `overview` | Repository orientation: size, languages, entry points, key files, structure |
| `focus` | Connected subgraph around a symbol or concept — callers, callees, depth 3, BFS |
| `blast_radius` | What breaks if you change this file or symbol — importers, callers, cascade |
| `diff_context` | Impact analysis of changed files — breaking-change risk, affected consumers |
| `hotspots` | Ranked risk list — files where size, complexity, and coupling overlap |
| `dead_code` | Unreferenced exported symbols — cleanup candidates sorted by size |
| `lookup` | Answer "where is X?", "what calls X?", "who imports X?" via graph queries |
| `dependencies` | Circular imports, dependency layers, module structure |
| `architecture` | Module-level view with cross-module dependency counts |
| `symbols` | Full symbol inventory with signatures and relationships |
| `file_map` | File tree with top symbols per file |
| `search_semantic` | Hybrid FTS5 + vector + structural search via Reciprocal Rank Fusion |
| `cochange_context` | Git-correlated speculative context — files that change together |
| `suggest_next` | Markov-chain mode prediction from usage history |
| `run_kit` | Composable multi-mode workflows (5 built-in + custom) |
| `stats` | Token budget planner — output size estimates per mode |
| `get_patterns` | Naming conventions, idioms, module roles |
| `report_feedback` | Log whether output was useful (feeds the learn engine) |
| `learn_recommendation` | Data-driven mode suggestions from feedback history |
| `index_repo` | Build graph and return stats |
| `watch_repo` / `unwatch_repo` | Live file watching for incremental updates |
| `embed_repo` | Generate vector embeddings for semantic search |

All tools accept `output_format="json"` for structured responses and `exclude_dirs` for filtering.

## CLI

```bash
tempograph <path> --mode <mode> [--query <q>] [--file <f>] [--max-tokens 4000]
```

### Modes

| Mode | Input | Output |
|------|-------|--------|
| `overview` | repo path | Size, languages, entry points, key files, structural signals |
| `focus` | `--query "auth"` | Connected subgraph — callers, callees, related files |
| `blast` | `--file src/db.ts` | Importers, external callers, cascade depth, risk signals |
| `diff` | `--file a.ts,b.ts` | Breaking-change risk, affected consumers, key symbols |
| `hotspots` | repo path | Top 20 risk-ranked symbols (coupling x complexity x size) |
| `dead` | repo path | Exported symbols with no incoming references |
| `lookup` | `--query "what calls X"` | Structured graph query dispatch |
| `map` | repo path | File tree + top symbols per file |
| `symbols` | repo path | Full symbol inventory with caller/callee context |
| `deps` | repo path | Circular imports + dependency layers |
| `arch` | repo path | Module-level view |
| `stats` | repo path | Token cost estimates per mode |

```bash
# Orient in a new repo
tempograph ./my-project --mode overview

# Understand auth before modifying it
tempograph ./my-project --mode focus --query "authentication"

# Check what breaks if you touch db.ts
tempograph ./my-project --mode blast --file src/lib/db.ts

# Find dead code to clean up
tempograph ./my-project --mode dead
```

## Desktop App

Native desktop app built with Tauri v2 + React 19.

```bash
cd tempo/ui && pnpm install && pnpm tauri dev
```

- **Interactive code graph** — Force-directed visualization (Cytoscape.js). Directory clusters drill down to files on double-click. Health-colored nodes (green/yellow/red/gray).
- **Dashboard** — Health metrics, top hotspots, async mode runs on open.
- **14-mode runner** — All modes in sidebar, command palette (Cmd+K), run history, output search/filter/save.
- **Keyboard-first** — Cmd+1-9 modes, Cmd+R run, Cmd+F filter, Cmd+S save, ? overlay.
- **Multi-workspace** — Open multiple repos in tabs with independent state.
- **Drag-and-drop** — Drop a folder from Finder to index it.

## Python API

```python
from tempograph import build_graph

graph = build_graph("./my-project")

# Symbol search
results = graph.search_symbols("handleLogin")

# Call graph traversal
for sym in graph.symbols.values():
    callers = graph.callers_of(sym.id)
    callees = graph.callees_of(sym.id)

# File dependencies
importers = graph.importers_of("src/lib/db.ts")
circular = graph.detect_circular_imports()
layers = graph.dependency_layers()
dead = graph.find_dead_code()
```

## Bench Results

| Condition | F1 Delta | p-value | n | Note |
|-----------|----------|---------|---|------|
| v5_defn (production default) | **+18.6%** | 0.049* | 45 | httpx/django/flask, qwen2.5-coder:32b |
| definition_first (Phase 5.31) | **+16.0%** | 0.012* | 100 | 8 repos, pred<3 gate |
| adaptive_v5 (Phase 5.29) | **+8.0%** | 0.026* | 109 | Python+JS combined, 0% harm rate |
| Python-only (Phase 5.26) | **+7.1%** | 0.043* | 111 | 8 Python repos |

All results: canonical runs, temperature=0.7, seed=42, qwen2.5-coder:32b local. Reproduce with:
```bash
pip install tempograph[bench]
python3 -m bench.changelocal.analyze --canonical --conditions baseline,tempograph_adaptive_v5_defn
```

## Why Tempograph

- **Triple search**: structural graph (tree-sitter) + semantic vectors (sqlite-vec) + keyword search (FTS5), fused via RRF
- **Validated results**: Only code context tool publishing statistically significant retrieval F1 improvements
- **21ms warm queries**: Content-hashed SQLite with BLOB caching — no rebuild on branch switch
- **170+ languages**: tree-sitter-language-pack with custom handlers for 10 core languages
- **24 MCP tools**: Purpose-built for AI agents — not a general-purpose search engine
- **Adaptive gating**: Knows when NOT to inject context (avoids harm on diffuse commits)
- **Self-improving**: Telemetry learns which modes work for which tasks and adapts
- **Local-first**: Everything runs on your machine. No API keys, no cloud, no data leaves your laptop
- **4,700+ tests**: Comprehensive coverage across pytest + vitest

## License

[BSL 1.1](LICENSE) — free to use, can't resell as a hosted service. Converts to Apache 2.0 on 2030-03-22.
