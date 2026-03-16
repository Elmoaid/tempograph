# Tempograph — Agent Handoff Document

**Last updated:** 2026-03-16
**Last agent:** Claude Opus 4.6
**Owner:** Elmoaid (elmoaidali)
**Repo:** /Users/elmoaidali/Desktop/tempograph

---

## 1. What Is Tempograph

A semantic code graph toolkit. Parses source files with tree-sitter, extracts symbols (functions, classes, components, hooks, types) and relationships (calls, imports, renders, inherits), builds a queryable graph. Cache is content-hashed so branch-switching is free.

**Two systems live here:**

1. **`tempograph/`** — The core Python CLI & MCP server (the product)
2. **`tempo/`** — A Tauri + React desktop app that wraps tempograph with a GUI

---

## 2. Project Structure

```
tempograph/
├── tempograph/               # Core package (Python 3.11+)
│   ├── __init__.py
│   ├── __main__.py           # CLI entry (argparse, 14 modes + serve)
│   ├── builder.py            # build_graph() — walks repo, dispatches to parser
│   ├── parser.py             # tree-sitter extraction (35 methods, 7 languages)
│   ├── render.py             # All render_<mode>() functions (~810 lines)
│   ├── types.py              # CodeGraph, Symbol, Edge, FileInfo, Language
│   ├── cache.py              # Content-hash cache (.tempograph/cache.json)
│   ├── server.py             # MCP server (tempograph-server)
│   ├── git.py                # Git diff helpers
│   ├── report.py             # Report generation
│   └── telemetry.py          # Usage + feedback tracking
│
├── tempo/                    # Desktop app
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                # CLI for tempo (wraps tempograph)
│   ├── kernel/               # Plugin-based kernel architecture
│   │   ├── builder.py
│   │   ├── cache.py
│   │   ├── config.py
│   │   ├── git.py
│   │   ├── graph.py
│   │   ├── parser.py
│   │   ├── registry.py
│   │   └── telemetry.py
│   ├── plugins/              # One plugin per analysis mode
│   │   ├── arch/
│   │   ├── blast/
│   │   ├── context/
│   │   ├── dead_code/
│   │   ├── deps/
│   │   ├── diff/
│   │   ├── focus/
│   │   ├── hotspots/
│   │   ├── learn/
│   │   ├── map/
│   │   ├── overview/
│   │   ├── symbols/
│   │   └── tokens/
│   └── ui/                   # Tauri v2 + React + Vite + TypeScript
│       ├── src/
│       │   ├── App.tsx                   # Root — workspace state, localStorage
│       │   ├── App.css                   # All styles (~528 lines, dark theme)
│       │   ├── index.css                 # CSS variables
│       │   ├── main.tsx                  # React entry
│       │   └── components/
│       │       ├── SinglePage.tsx        # Main UI (~607 lines) — 3-column grid
│       │       ├── ClaudePanel.tsx        # Claude Code config viewer/editor (~352 lines)
│       │       └── tempo.ts              # Tauri IPC bridge (~171 lines)
│       ├── src-tauri/
│       │   ├── src/lib.rs                # Rust backend — 14 IPC commands (~423 lines)
│       │   ├── src/main.rs               # Tauri main entry
│       │   ├── Cargo.toml
│       │   ├── Cargo.lock
│       │   └── tauri.conf.json           # App config (1200x800, "Tempo")
│       ├── package.json
│       ├── pnpm-lock.yaml
│       ├── tsconfig.json
│       └── vite.config.ts
│
├── bench/                    # Benchmark harnesses
│   ├── crosscode/            # CrossCodeEval (completed, n=50)
│   ├── swebench/             # SWE-bench Lite (built, not run)
│   ├── changelocal/          # ChangeLocal benchmark
│   ├── continuous.py         # Continuous benchmark runner
│   ├── nightly.sh            # Nightly benchmark script
│   ├── report.py             # Results formatter
│   └── RESULTS.md            # Benchmark results
│
├── notes/                    # Session notes from autonomous tasks
├── tests/                    # Test files
├── CLAUDE.md                 # Project instructions for Claude Code
├── HANDOFF.md                # THIS FILE
├── README.md                 # Public docs
└── pyproject.toml            # Python package config
```

---

## 3. Tempo Desktop App — Architecture Deep Dive

### How to Run

```bash
cd tempo/ui
pnpm install          # first time only
pnpm tauri dev        # launches dev server + native window
```

Requires: Node 20+, pnpm, Rust (via rustup). Tauri v2.

**Kill old processes first:**
```bash
lsof -ti :4902 | xargs kill -9 2>/dev/null
pkill -f "tempo-ui" 2>/dev/null
```

### Architecture

```
[React Frontend]  ←→  [Tauri IPC bridge]  ←→  [Rust Backend]  ←→  [tempograph CLI / filesystem]
   SinglePage.tsx        tempo.ts                lib.rs              python3 -m tempograph
   ClaudePanel.tsx
   App.tsx
```

### Frontend Components

#### `App.tsx` (84 lines)
- Manages workspace list (`workspaces: string[]`) and `activeIdx`
- Persists to localStorage under key `"tempo-workspaces"`
- Default workspaces:
  - `/Users/elmoaidali/Desktop/Final NeedSpec Production Review`
  - `/Users/elmoaidali/Desktop/NeedEnd - Production Review`
- Renders `<SinglePage>` with workspace props

#### `SinglePage.tsx` (607 lines)
The main UI. Single-page 3-column grid layout.

**State management:**
- `cacheRef = useRef<Record<string, WorkspaceData>>({})` — per-workspace data cache
- Each workspace loads 9 data sources in parallel via `Promise.all`
- `WorkspaceData` includes: overview, quality, learning, tokens, plugins, notes, telemetry, config, git

**Layout:**
- **Topbar**: Brand, repo path display, Refresh/Claude buttons
- **Workspace strip**: Tab bar with close buttons, "+" to add workspace
- **3-column grid** (or hidden when Claude panel is open):
  - Column 1: Mode list (14 modes) + Run output panel
  - Column 2: Stats (overview, quality, learning, token stats) + Git status
  - Column 3: Plugins + Notes + File browser + Config

**Features:**
- Mode runner with args input field, "Run" button, copy/save output
- Plugin toggles that write to `.tempo/config.json`
- File browser with directory navigation
- Note creation (name + content → saves to `notes/`)
- Git panel (status, branch, recent commits)
- Claude panel toggle

#### `ClaudePanel.tsx` (352 lines)
Full Claude Code configuration viewer/editor.

**Layout:** 2-panel — file tree (left, 280px) + viewer/editor (right)

**9 Sections:**
1. Settings — `settings.json`, `settings.local.json`
2. Global CLAUDE.md — `~/.claude/CLAUDE.md`
3. Project CLAUDE.md — per-workspace `CLAUDE.md` and `.claude.local.md` files
4. MCP Servers — `.mcp.json`
5. Hooks — all files in `~/.claude/hooks/`
6. Skills — directories in `~/.claude/skills/`
7. Plugins — manifests from `~/.claude/plugins/.install-manifests/`
8. Scheduled Tasks — directories in `~/.claude/scheduled-tasks/`
9. Plans — `.md` files in `~/.claude/plans/`
10. Project Memory — directories in `~/.claude/projects/`

**Editing:** Click file → view in `<pre>`. Click "Edit" → textarea. Click "Save" → writes directly to real file path via `writeFile` IPC command. `isEditable()` checks extensions: `.json`, `.md`, `.sh`, `.toml`, `.yaml`, `.yml`, `.txt`.

#### `tempo.ts` (171 lines)
Tauri IPC bridge. All functions use dynamic `import("@tauri-apps/api/core")` with fallback for non-Tauri environments.

**14 functions:** `runTempo`, `readConfig`, `writeConfig`, `listNotes`, `readFile`, `readTelemetry`, `getRepoInfo`, `detectRepo`, `gitInfo`, `listDir`, `writeNote`, `saveOutput`, `getHomeDir`, `writeFile`

### MCP Server (`server.py`, ~490 lines)

**15 MCP tools** — the primary agent interface:

| Tool | Purpose | Token Cost |
|------|---------|------------|
| `index_repo` | Build graph + orientation | ~600 |
| `overview` | Repo orientation | ~500 |
| `focus` | Task-scoped context | 2-4K |
| `lookup` | "where is X?", "what calls X?" | 100-500 |
| `blast_radius` | Impact analysis for file/symbol | 1-5K |
| `hotspots` | Riskiest symbols by coupling | ~2.5K |
| `diff_context` | Impact of changed files | 3-6K |
| `dead_code` | Unused exports | variable |
| `symbols` | Full symbol inventory | ~30K |
| `file_map` | File tree + top symbols | ~20K |
| `dependencies` | Circular imports + layers | ~1K |
| `architecture` | Module-level view | ~2K |
| `stats` | Token budget planner | ~100 |
| `learn_recommendation` | Data-driven mode suggestions | ~200 |
| `report_feedback` | Log tool effectiveness | — |

**JSON output:** All tools accept `output_format="json"` → `{"status":"ok","data":"...","tokens":N,"duration_ms":N}`

**Error codes:** `REPO_NOT_FOUND`, `NOT_GIT_REPO`, `NO_MATCH`, `BUILD_FAILED`, `INVALID_PARAMS`, `LEARN_UNAVAILABLE`

**Text mode:** Default, backwards compatible. Errors prefixed with `[ERROR:CODE]`.

### Rust Backend (`lib.rs`, 423 lines)

**14 registered Tauri commands:**

| Command | Purpose |
|---------|---------|
| `run_tempo` | Runs `python3 -m tempo <repo> --mode <mode> [args]` |
| `read_config` | Reads `.tempo/config.json` |
| `write_config` | Writes `.tempo/config.json` |
| `list_notes` | Lists files in `notes/` |
| `read_file` | Reads any file path |
| `read_telemetry` | Reads `usage.jsonl` + `feedback.jsonl` |
| `get_repo_info` | Quick repo stats (has_git, has_tempo, has_config) |
| `detect_repo` | Walks up from CWD to find `.git` |
| `git_info` | Runs git status/branch/log |
| `list_dir` | Lists one directory level (dirs-first sort) |
| `write_note` | Creates/updates a note file |
| `save_output` | Saves mode output to a file |
| `get_home_dir` | Returns `$HOME` |
| `write_file` | Writes content to any file path |

### CSS (`App.css`, 528 lines)

Dark theme via CSS custom properties. Key class groups:
- `.topbar`, `.ws-strip`, `.ws-tab` — navigation
- `.grid-shell` — 3-column CSS Grid
- `.cell`, `.cell-head`, `.cell-body` — grid cards
- `.mode-row` — mode buttons
- `.plugin-row`, `.toggle` — plugin switches
- `.claude-panel`, `.claude-tree`, `.claude-viewer`, `.claude-editor` — Claude panel
- `.output`, `.input`, `.btn`, `.btn-ghost` — generic elements

---

## 4. Core Tempograph — Key Technical Details

### Modes (14 total)

overview, focus, blast, dead_code, hotspots, diff, deps, arch, symbols, map, context, quality, learn, token_stats

### Languages Supported

Python, TypeScript, TSX, JavaScript, JSX, Rust, Go

### Key Files

| File | Lines | Role |
|------|-------|------|
| `render.py` | ~810 | All render functions. `render_focused` does BFS (depth 2, max 40 symbols, 4000 tokens) |
| `parser.py` | ~900 | Tree-sitter parsing. `_handle_*` method per language construct |
| `types.py` | ~310 | `CodeGraph` with query methods: callers_of, callees_of, find_dead_code, etc. |
| `builder.py` | ~200 | build_graph() — walks file tree, calls parser, manages cache |
| `__main__.py` | ~150 | CLI with argparse |

### Telemetry

- `tempograph/telemetry.py` tracks usage and feedback
- Every tempograph invocation should be followed by a feedback call:
  ```bash
  python3 -m tempograph feedback <repo> <mode> <true|false> "<note>"
  ```

---

## 5. Benchmark Status

### CrossCodeEval (completed, n=50)

```
              EM      ES      ID-F1
no_context   14.0%   28.5%   30.8%
tempograph   20.0%   35.2%   35.3%
             +6.0%   +6.7%   +4.5%
```

Model: qwen2.5-coder:32b (local Ollama). Not statistically significant at n=50.

### SWE-bench Lite — built, not run. Needs ANTHROPIC_API_KEY.
### ChangeLocal — harness in `bench/changelocal/`.

---

## 6. Git State

**Branch:** `main` (only branch)
**Ahead of origin:** ~8 commits (not pushed)

Recent commits (newest first):
```
644c8b0 feat: add changelocal benchmark harness
2925c63 fix: add missing tauri.conf.json
808a946 feat: add bench continuous runner, nightly script, and session notes
e591351 feat: add Tempo desktop app — Tauri + React single-page UI
c2e5003 feat: improve core — telemetry, report, parser, monolith support
fb762e4 feat: kernel wiring, real tests, quality scorer fix
455dee6 feat: monolith file support for focus and blast modes
7e44ddf feat: add telemetry, feedback tool, dead code scoring, report mode
```

**Working tree should be clean** after this handoff.

---

## 7. Environment

- macOS, zsh, Homebrew
- Node 20+, pnpm
- Python 3.11+ (`python3` not `python`)
- Rust via rustup
- Tauri v2: `pnpm tauri dev` / `pnpm tauri build`
- PostgreSQL 17: `/opt/homebrew/opt/postgresql@17/bin/`

---

## 8. What Needs Doing Next

### High Priority
1. **Push to remote** — many unpushed commits on main. Ask Elmo before pushing.
2. **Scale CrossCodeEval to 200 examples** — for statistical significance
3. **Run SWE-bench Lite** — needs ANTHROPIC_API_KEY
4. **Monitor agent feedback** — agents now report via `report_feedback`. Check `~/.tempograph/global/feedback.jsonl` for patterns.

### Medium Priority
5. **Add tests for Tempo UI** — no frontend tests yet
6. **Per-project `.claude/` directory browsing** — ClaudePanel shows per-project CLAUDE.md files from workspaces but doesn't yet browse per-project `.claude/` subdirectories deeply
7. **Add project-specific `.mcp.json`** — some projects have their own `.mcp.json` at root; could surface these in ClaudePanel
8. **Publish to PyPI** — `pyproject.toml` is ready

### Low Priority
9. **Add C/C++ language support**
10. **Cross-snapshot diffing** — diff mode only compares within a single snapshot
11. **GitHub Actions CI** — tests exist (50 passing) but no CI pipeline

---

## 9. Gotchas

- `render_hotspots` scoring formula is in `render.py:580-592` — changing weights changes benchmark results
- tree-sitter grammars compiled on first use; cold start is slower
- MCP server rebuilds graph on every `index_repo` call (cache makes it fast)
- `archive/` is gitignored — exists locally with v0/v1 history
- `bench/results/` is gitignored — only `bench/RESULTS.md` and named JSONL tracked
- The Tempo UI default workspaces reference paths on Elmo's machine — change them in `App.tsx` if running elsewhere
- Dev port is 4902 (changed from 5173 to avoid NeedYeet conflicts)
- Decorator-dispatched symbols (@mcp.tool, @app.route) show as dead code — false positives

---

## 10. User Preferences (from CLAUDE.md)

- "fix"/"build"/"implement" = end-to-end + verify. No partial work.
- Code > explanation. No narration, no apologies.
- Minimal changes only. Don't touch code not asked about.
- Never read files >500 lines in full — Grep then Read ±50 lines.
- Stage specific files. Never `git add -A`. Don't push unless asked.
- Run `python3 -m tempograph --mode overview <repo>` at session start for orientation.
- Provide feedback after every tempograph use (see CLAUDE.md for syntax).
