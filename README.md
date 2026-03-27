# tempograph

Tempograph is an agent effectiveness engine — a code graph tool that makes AI coding agents measurably better at understanding and navigating codebases.

**Bench result**: Adaptive context gating improves AI file-prediction F1 by **+18.6% (p=0.049)** on low-baseline repos with **zero harm rate** (n=45, qwen2.5-coder:32b, Phase 5.39). Definition-first ordering: **+16.0% F1 (p=0.012)** across n=100 pairs. No other code context tool publishes statistically validated retrieval results.

```bash
pip install tempograph
```

It parses source files with tree-sitter, extracts symbols and relationships, and builds a semantic graph. Run it as an MCP server — your AI agent calls `prepare_context` and gets exactly the right context before making code changes.

## How It Works

```
commit a1b2c3 ──→ snapshot ──→ 847 symbols, 2,031 edges
commit d4e5f6 ──→ snapshot ──→ 851 symbols, 2,044 edges  (+4 symbols, +13 edges)
commit g7h8i9 ──→ snapshot ──→ 849 symbols, 2,067 edges  (‑2 symbols, +23 edges ← coupling growing)
```

The graph is stored in `.tempograph/graph.db` (SQLite with WAL mode). Only files whose contents changed are re-parsed, so a 10,000-file repo re-indexes in seconds. Content-hashed — switching branches and back doesn't force a rebuild. Includes FTS5 full-text search, optional vector embeddings (sqlite-vec), and Reciprocal Rank Fusion for hybrid structural+semantic search.

## Supported Languages

**170+ languages** via tree-sitter-language-pack. Custom handlers for Python, TypeScript, TSX, JavaScript, JSX, Rust, Go, Java, C#, Ruby. Generic handler for PHP, Swift, Kotlin, Dart, Scala, Lua, C, C++, Zig, Haskell, and 160+ more.

```bash
pip install tempograph[languages]  # 170+ languages
pip install tempograph[semantic]   # vector search + embeddings
pip install tempograph[watch]      # live file watching
pip install tempograph[full]       # everything
```

## Install

```bash
pip install tempograph
```

Requires Python 3.11+. Tree-sitter grammars auto-install on first parse.

## MCP Server (primary use — for AI agents)

Add to your `.mcp.json`:

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

Then in your agent system prompt or CLAUDE.md:

```
prepare_context(repo_path="/path/to/repo", task="fix auth bug in login flow")
```

The tool auto-selects the right context type (symbol focus, overview, or nothing for vague tasks) and returns KEY FILES within a token budget. See [AGENT_GUIDE.md](AGENT_GUIDE.md) for full agent integration docs.

## CLI

```bash
tempograph <path> --mode <mode> [options]
```

Global options:
- `--query` / `-q` — search query (for `focus` and `lookup`)
- `--file` / `-f` — file path (for `blast`), or comma-separated paths (for `diff`)
- `--max-tokens` — token budget for output (default: 4000; affects `focus` and `diff`)
- `--json` — dump the raw graph as JSON instead of rendering
- `--tokens` — print token count to stderr after output

Every mode works from the same graph. Tempograph either builds the graph or loads it from cache, then renders the requested view. Build timing is written to stderr. Rendered output is written to stdout.

---

### `overview`

First-pass orientation in an unfamiliar repository. Reports size, languages, likely entry points, the heaviest files, and top-level structure.

**Input:** Repository path only.

```
repo: my-project
249 files, 3830 symbols, 42,891 lines | TypeScript(187), Rust(31), CSS(12)

entry points:
  src/main.tsx
  src-tauri/src/main.rs

key files (by size + complexity):
  src/components/Canvas.tsx (17,631L, cx=847, TypeScript)
  src/components/Scratchpad.tsx (24,158L, cx=1203, TypeScript)
  src-tauri/src/lib.rs (672L, cx=89, Rust)

structure: src/(187), src-tauri/(31), public/(3)
```

Internally: scans all `FileInfo` objects, scores files with `line_count + complexity * 3`, identifies likely entry points from main functions and common entry patterns, and summarizes the repository without graph traversal.

```bash
tempograph ./my-project --mode overview
```

---

### `map`

Directory tree plus the most important symbols in each file. Like `tree`, but with a view into what each file actually contains.

**Input:** Repository path only.

```
[src/components/]
  Canvas.tsx (17631 lines, 142 sym)
    comp Canvas (L45-17631) — Canvas(): JSX.Element
    func handleKeyDown (L892-1204)
    func handleCommand (L1205-1890)
    ... +139 more

[src/lib/]
  db.ts (312 lines, 18 sym)
    func initDatabase (L12-89) — initDatabase(): Promise<void>
    func loadSettings (L91-145)
```

Internally: groups files by directory, retrieves symbols for each file, and ranks them by importance — components and hooks first, then classes and structs, then exported functions, then everything else. Line ranges and signatures are shown when available.

```bash
tempograph ./my-project --mode map
```

---

### `symbols`

Full symbol inventory. Functions, classes, components, hooks, variables, and types, with signatures, docstrings, locations, and caller/callee context.

**Input:** Repository path only.

```
── src/lib/db.ts ──
  function initDatabase | L12-89 | initDatabase(): Promise<void> | ← main, App | → loadSettings, migrateSchema
  function loadSettings | L91-145 | loadSettings(db: Database): Settings | ← initDatabase
  function saveSetting | L147-162 | saveSetting(key: string, value: string) | ← SettingsPanel, handleCommand
```

Internally: iterates every symbol in the graph, groups them by file, and uses `callers_of()` and `callees_of()` to render relationship context inline. Signatures are truncated at 120 characters, docstrings at 80.

```bash
tempograph ./my-project --mode symbols
```

---

### `focus`

Build a connected subgraph around a topic, symbol, or concept. This is the mode to use before you start modifying code.

**Input:** `--query "authentication"` (or any search term — function names, module names, concepts).

```
Focus: authentication

● function handleLogin — src/auth/login.ts:45-120
  sig: handleLogin(credentials: Credentials): Promise<Session>
  called by: LoginForm.onSubmit, AuthProvider.refresh
  calls: validateCredentials, createSession, setToken
  contains: func validateEmail, func hashPassword

  → function validateCredentials — src/auth/validators.ts:12-67
  → function createSession — src/auth/session.ts:23-89
    · function setToken — src/lib/storage.ts:34-41

Related files:
  src/auth/middleware.ts (234 lines)
  src/auth/types.ts (45 lines)
```

Internally:
1. Fuzzy-searches the symbol index for the query and selects the top seed matches.
2. Expands breadth-first through callers, callees, and child symbols up to depth 2.
3. Caps the result at 40 symbols to keep output readable.
4. Applies a token budget (default: 4000) and truncates when necessary.
5. Appends related files that appear in edges but were not rendered directly, with `[grep-only]` warnings for files over 500 lines.

```bash
tempograph ./my-project --mode focus --query "payment processing"
```

---

### `lookup`

Answer common natural-language questions about the codebase through structured graph queries. This is query dispatch, not an LLM feature.

**Input:** `--query "where is handleLogin defined?"` or `--query "what calls saveDocument?"` or `--query "who imports db.ts?"`

| Pattern | What it does |
|---------|-------------|
| `where is X` / `find X` / `locate X` | Exact + fuzzy symbol search, then shows locations and callers |
| `what calls X` / `who uses X` | Lists all callers of a symbol with file:line locations |
| `what does X call` / `dependencies of X` | Lists all callees of a symbol |
| `who imports X` | Lists all files that import a given file |
| `what renders X` | Lists components that render a given component (JSX/TSX) |
| *(anything else)* | Falls back to fuzzy symbol search |

```
'saveDocument' is called by:
  src/components/Scratchpad.tsx:1204 — handleCommand
  src/scratchpad/hooks/useAutoSave.ts:34 — useAutoSave
  src/lib/docBridge.ts:78 — syncToCloud
```

```bash
tempograph ./my-project --mode lookup --query "what calls saveDocument"
```

---

### `blast`

Show the blast radius of changing a file. Reports direct importers, cross-file symbol usage, and component render relationships where relevant.

**Input:** `--file src/lib/db.ts` (path relative to repo root).

```
Blast radius for src/lib/db.ts:

Directly imported by (7):
  src/components/Canvas.tsx
  src/components/Scratchpad.tsx
  src/lib/settings.ts
  ...

Externally called symbols:
  initDatabase:
    src/main.tsx:12
    src/lib/settings.ts:34
  saveSetting:
    src/components/SettingsPanel.tsx:89
    src/components/Scratchpad.tsx:1204

Component render relationships:
  (none — this is a utility file)
```

If no external dependencies are found, Tempograph says so directly:
`No external dependencies found — safe to modify in isolation.`

Internally: checks `importers_of()` for the file itself, then walks every symbol in the target file and filters `callers_of()` down to external callers only. `renderers_of()` is used to add component-level impact when applicable.

```bash
tempograph ./my-project --mode blast --file src/lib/db.ts
```

---

### `diff`

Add structural context to a code diff. Shows affected symbols, breaking-change risk, import impact, and component-tree impact for a set of changed files.

**Input:** `--file src/lib/db.ts,src/lib/settings.ts` (comma-separated file paths).

```
Diff context for 2 changed file(s):

Changed files:
  src/lib/db.ts (312 lines, 18 symbols)
  src/lib/settings.ts (145 lines, 8 symbols)

EXTERNAL DEPENDENCIES (breaking change risk):
  function initDatabase (src/lib/db.ts:12)
    <- main (src/main.tsx:5)
    <- App (src/App.tsx:23)

Files importing changed code (4):
  src/components/Canvas.tsx
  src/components/Scratchpad.tsx
  src/components/SettingsPanel.tsx
  src/main.tsx

Key symbols in changed files:
  function initDatabase L12-89
    initDatabase(): Promise<void>
  function loadSettings L91-145
    loadSettings(db: Database): Settings
```

Internally: normalizes paths, collects symbols from the changed files, flags exported symbols with external callers as breaking-change risk, finds importers of the changed files, checks render-tree impact, and renders key symbols until the token budget is exhausted.

```bash
tempograph ./my-project --mode diff --file src/lib/db.ts,src/lib/settings.ts
```

---

### `hotspots`

Rank the parts of the codebase where size, complexity, and coupling overlap. These are the places most likely to hide bugs or make changes expensive.

**Input:** Repository path only.

```
Top 20 hotspots (highest coupling + complexity):

 1. component Canvas [risk=847] (src/components/Canvas.tsx:45)
    23 callers (12 cross-file), 45 callees, 18 children, 17631 lines, cx=312
    → grep-only (too large to read); high blast radius — changes here break many files

 2. component Scratchpad [risk=623] (src/components/Scratchpad.tsx:38)
    8 callers (5 cross-file), 67 callees, 24 children, 24158 lines, cx=445
    → grep-only (too large to read); refactor candidate — extreme complexity

 3. function handleCommand [risk=234] (src/components/Canvas.tsx:1205)
    12 callers (4 cross-file), 34 callees, 0 children, 685 lines, cx=89
    → consider splitting — complex and large
```

Scoring formula:
- `callers × 3` (how many things depend on this)
- `callees × 1.5` (how many things this depends on)
- `min(line_count / 10, 50)` (size, capped)
- `children × 2` (internal complexity)
- `cross_file_callers × 5` (blast radius)
- `render_count × 2` (component tree coupling)
- `log₂(cyclomatic_complexity) × 3` (branching complexity)

Actionable warnings are appended automatically: `grep-only` for files over 500 lines, `high blast radius` for more than 5 cross-file callers, `refactor candidate` for extreme complexity.

```bash
tempograph ./my-project --mode hotspots
```

---

### `deps`

Inspect the file-level dependency graph. Finds circular imports and arranges files into dependency layers.

**Input:** Repository path only.

```
Dependency Analysis:

CIRCULAR IMPORTS (2 cycles):
  1. db.ts → settings.ts → db.ts
  2. Canvas.tsx → shortcuts.ts → Canvas.tsx

Dependency layers (5 levels):
  Layer 0: types.ts, constants.ts, crypto.ts
  Layer 1: db.ts, settings.ts, tauri.ts
  Layer 2: profiles.ts, ai.ts, pipelines.ts
  Layer 3: Canvas.tsx, Scratchpad.tsx, CommandPalette.tsx ... +12 more (15 total)
  Layer 4: App.tsx, main.tsx
```

Internally: builds a directed graph of file-level imports, runs cycle detection, then computes a topological layering. Files in the same layer do not depend on one another.

```bash
tempograph ./my-project --mode deps
```

---

### `dead`

Find exported symbols that are not referenced anywhere else in the repository. Sorted by size so the biggest cleanup opportunities appear first.

**Input:** Repository path only.

```
Potential dead code (23 symbols, showing top 23 by size):

src/components/GraphView.tsx:
  component GraphView (L1-342, 342 lines)

src/lib/formulaEngine.ts:
  function evaluateFormula (L45-189, 144 lines)
  function parseExpression (L191-267, 76 lines)

src/components/OutputInspector.tsx:
  component OutputInspector (L1-156, 156 lines)

Total: 23 unused symbols (~1,847 lines shown)
Note: decorator-dispatched symbols (@mcp.tool, @app.route, etc.) may be false positives.
```

Internally: calls `graph.find_dead_code()`, which checks exported symbols for incoming calls, renders, and imports. Results are grouped by file and sorted by line count descending. Decorator-dispatched symbols such as route handlers or MCP tools may appear as false positives.

```bash
tempograph ./my-project --mode dead
```

---

### `arch`

Summarize the repository as modules rather than individual files. Shows each module's size, language mix, exported surface area, and dependencies on other modules.

**Input:** Repository path only.

```
Architecture Overview:

Modules:
  src/ — 187 files, 3201 symbols, 38,441 lines [TypeScript]
    exports: Canvas(component), Scratchpad(component), App(component), initDatabase(function), buildGraph(function) +42
  src-tauri/ — 31 files, 489 symbols, 4,200 lines [Rust]
    exports: main(function), http_fetch(function), type_text(function) +18

Module dependencies:
  src → src-tauri(47)
  src-tauri → src(0)
```

Internally: groups files by first path segment, builds inter-module import counts plus call/render counts, then merges them into a single dependency summary. Top exported symbols are shown per module.

```bash
tempograph ./my-project --mode arch
```

---

### `stats`

Repository totals plus token-cost estimates for each mode. Useful when Tempograph output is going to be passed to an LLM.

**Input:** Repository path only.

```
Build: 0.3s
Files: 249, Symbols: 3830, Edges: 8241
Lines: 42,891

Token costs:
  overview:  342
  map:       2,847
  symbols:   ~57,450 (est)
  focus:     ~2,000–4,000 (query-dep)
  lookup:    ~100–500 (question-dep)
```

Internally: runs `render_overview` and `render_map` to get actual token counts via tiktoken. Estimates symbols mode at roughly 15 tokens per symbol. `focus` and `lookup` are query-dependent, so Tempograph reports ranges instead of fixed counts.

```bash
tempograph ./my-project --mode stats
```

---

## MCP Server

Tempograph includes an MCP server so AI agents can query the same structural model used by the CLI instead of scraping raw files repeatedly.

```bash
tempograph-server
```

| Tool | Input | Output |
|------|-------|--------|
| **`prepare_context`** | `repo_path`, `task` | **One-shot context for a PR/commit task — recommended for agents.** Extracts keywords, focuses the graph, returns KEY FILES. Adaptive gating skips injection when it would cause harm. Definition-first ordering (+18.6% F1, p=0.049). |
| `index_repo` | `repo_path` | Builds graph and returns stats (file/symbol/edge counts) |
| `overview` | `repo_path` | Repository orientation: size, languages, entry points, key files |
| `focus` | `repo_path`, `query` | Connected subgraph for a topic or symbol — callers, callees, depth 3 |
| `lookup` | `repo_path`, `query` | Answer "where is X?", "what calls X?", "who imports X?" |
| `blast_radius` | `repo_path`, `query` or `file_path` | What breaks if you change this symbol or file? |
| `hotspots` | `repo_path` | Ranked risk list — most coupled and complex files |
| `diff_context` | `repo_path` | Impact analysis of changed files (staged, unstaged, or explicit list) |
| `dead_code` | `repo_path` | Unreferenced exported symbols — cleanup candidates |
| `dependencies` | `repo_path` | Circular imports and module layer structure |
| `architecture` | `repo_path` | Module-level view with cross-module edges |
| `symbols` | `repo_path` | Full symbol inventory with signatures and relationships |
| `file_map` | `repo_path` | File tree with top symbols per file |
| `stats` | `repo_path` | Token budget planner — output size for every mode |
| `get_patterns` | `repo_path` | Naming conventions, idioms, module roles |
| `learn_recommendation` | `repo_path` | Data-driven mode suggestions based on feedback history |
| `report_feedback` | `repo_path`, `mode`, `helpful`, `note` | Log whether a tool's output was useful (feeds learn engine) |

| `search_semantic` | `repo_path`, `query` | Hybrid semantic+structural symbol search (FTS5 + sqlite-vec + ranking) |
| `cochange_context` | `repo_path`, `file_path` | Git-correlated speculative context — files that historically change together |
| `suggest_next` | `repo_path` | Markov-chain next-mode prediction from usage history |
| `run_kit` | `repo_path`, `kit_name` | Run a composable multi-mode workflow (5 built-in + custom) |
| `watch_repo` / `unwatch_repo` | `repo_path` | Start/stop live file watching for incremental graph updates |
| `embed_repo` | `repo_path` | Generate vector embeddings for semantic search |

All tools accept `output_format="json"` for structured responses and `exclude_dirs` for filtering.

Add it to Claude settings (`~/.claude/settings.json`):

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

## Desktop App (Tempo)

Tempograph includes a native desktop app built with Tauri v2 + React 19.

```bash
cd tempo/ui
pnpm install
pnpm tauri dev    # dev mode on port 4902
pnpm tauri build  # production binary
```

**Interactive Code Graph** — Force-directed visualization powered by Cytoscape.js. Directory-level clusters drill down to file-level nodes on double-click. Nodes are health-colored: green (healthy), yellow (hotspot), red (dead code), gray (stable). Hover highlights connections, click opens detail panel with file stats and quick analysis buttons.

**Dashboard View** — Health metric cards (files, symbols, lines, dead code %), top 5 hotspots ranked by risk score. Three async mode runs on view open.

**Drag-and-Drop** — Drop a folder from Finder to index it. Native file picker via `tauri-plugin-dialog`. Recent repos persisted. Pre-indexed snapshots for popular OSS repos.

**14-Mode Runner** — Sidebar with all modes, per-mode argument history, command palette (Cmd+K), run history chips. Output with search, filtering, diff view, copy, save, font size control, token counting.

**Keyboard-First** — Cmd+1-9 switch modes, Cmd+K palette, Cmd+R run, Cmd+F filter, Cmd+S save, Cmd+B sidebar toggle, Cmd+L focus args, ? shortcut overlay.

**Multi-Workspace** — Open multiple repos in tabs. Each workspace maintains independent state.

## Python API

The Python API exposes the same graph model used by the CLI and MCP server.

```python
from tempograph import build_graph, CodeGraph

graph: CodeGraph = build_graph("./my-project")

# All symbols
for sym in graph.symbols.values():
    print(f"{sym.kind.value} {sym.qualified_name} ({sym.file_path}:{sym.line_start})")

# Call graph
for sym in graph.symbols.values():
    callers = graph.callers_of(sym.id)
    callees = graph.callees_of(sym.id)
    if callers or callees:
        print(f"{sym.name}: {len(callers)} callers, {len(callees)} callees")

# Edges (calls, imports, inherits, renders)
for edge in graph.edges:
    print(f"{edge.source_id} --{edge.kind.value}--> {edge.target_id}")
```

`CodeGraph` methods:
- `graph.search_symbols(query)` — fuzzy search by name
- `graph.find_symbol(name)` — exact match
- `graph.callers_of(symbol_id)` — who calls this symbol
- `graph.callees_of(symbol_id)` — what this symbol calls
- `graph.children_of(symbol_id)` — nested symbols (methods inside a class, etc.)
- `graph.renderers_of(symbol_id)` — components that render this component
- `graph.importers_of(file_path)` — files that import this file
- `graph.detect_circular_imports()` — find import cycles
- `graph.dependency_layers()` — topological layer sort
- `graph.find_dead_code()` — exported symbols with no incoming references

## Incremental by Default

Tempograph keys its cache to file contents, not modification times. The cache in `.tempograph/cache.json` stores each file's content hash alongside its parsed symbols and edges.

On re-index:

1. Hash every file in the repository.
2. Skip files whose hash matches the cache.
3. Re-parse only the files that changed.
4. Merge the updated results into the full graph.

Switch branches, rebase, cherry-pick — if the file contents are unchanged, Tempograph does not redo the work.

## Why Tempograph?

- **Only tool with triple search**: structural graph (tree-sitter) + semantic vectors (sqlite-vec) + keyword search (FTS5), fused via RRF
- **Only tool publishing retrieval F1**: +18.6% improvement (p=0.049), statistically significant, across 25 OSS repos
- **21ms warm queries**: Content-hashed SQLite with BLOB caching, no rebuild on branch switch
- **170+ languages**: tree-sitter-language-pack with custom handlers for 10 core languages
- **24 MCP tools**: Purpose-built for AI agents — not a general-purpose search engine
- **Interactive code graph**: Desktop app with force-directed visualization (Cytoscape.js, LOD dir→file→symbol)
- **Self-improving**: L1/L2/L3 telemetry learns which modes work for which tasks and adapts
- **Zero-config adaptive gating**: Knows when NOT to inject context (avoids harm on diffuse commits)
- **Local-first**: Everything runs on your machine. No API keys, no cloud, no data leaves your laptop
- **3,948 tests**: 5:1 test-to-code ratio across pytest + vitest

## License

[BSL 1.1](LICENSE) — free to use, can't resell as a hosted service. Converts to Apache 2.0 on 2030-03-22.
