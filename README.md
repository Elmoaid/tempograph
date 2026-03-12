# tempograph

Semantic code graph for any codebase. Parses source files with [tree-sitter](https://tree-sitter.github.io/), extracts symbols and their relationships, and exposes a queryable graph via CLI, Python API, and MCP server.

## Supported Languages

Python · TypeScript · TSX · JavaScript · JSX · Rust · Go

## Install

```bash
pip install -e .
```

## CLI

```bash
tempograph <path> --mode <mode>
```

| Mode | Description |
|------|-------------|
| `overview` | High-level repo summary — files, symbols, edges, token estimate |
| `map` | File tree with top symbols per file |
| `symbols` | Full symbol index with signatures |
| `focus` | Task-specific subgraph from a search query |
| `lookup` | Answer a question about the codebase |
| `blast` | Blast radius — what breaks if a file changes |
| `diff` | Context for changed files (unstaged/staged/committed) |
| `hotspots` | Most complex and connected symbols |
| `deps` | Dependency graph between files |
| `dead` | Unreferenced symbols (potential dead code) |
| `arch` | Architecture layers and cross-layer edges |
| `stats` | Raw counts and metrics |

### Examples

```bash
# Repo overview
tempograph ./my-project --mode overview

# Find hotspots
tempograph ./my-project --mode hotspots

# Blast radius for a file
tempograph ./my-project --mode blast --target src/auth.py

# Focus on a topic
tempograph ./my-project --mode focus --query "authentication"

# Dead code detection
tempograph ./my-project --mode dead
```

## MCP Server

Tempograph ships an MCP server for Claude and other AI agents.

```bash
tempograph-server
```

**Tools exposed:**

| Tool | Description |
|------|-------------|
| `index_repo` | Build/refresh the code graph for a repo |
| `overview` | High-level summary |
| `focus` | Task-specific subgraph |
| `hotspots` | Most complex symbols |
| `blast_radius` | What depends on a given file |
| `diff_context` | Context for recent changes |
| `dead_code` | Unreferenced symbols |

Add to your Claude settings (e.g. `~/.claude/settings.json`):

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

## Python API

```python
from tempograph import build_graph, CodeGraph

graph: CodeGraph = build_graph("./my-project")

# Symbols
for sym in graph.symbols:
    print(f"{sym.kind.name} {sym.name} ({sym.file}:{sym.line})")

# Edges (calls, imports, inherits)
for edge in graph.edges:
    print(f"{edge.source} -> {edge.target} [{edge.kind.name}]")
```

## Caching

Tempograph uses content-hash caching (`.tempograph/cache.json`) for incremental rebuilds. Only changed files are re-parsed.

## License

MIT
