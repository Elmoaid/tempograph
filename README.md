# tempograph

Your codebase is a living thing. Every commit reshapes it — new functions appear, old ones decay, dependencies tangle and untangle. Most tools show you code as it is right now. Tempograph shows you how it got here and where it's headed.

Tempograph parses source files with [tree-sitter](https://tree-sitter.github.io/), extracts every symbol and relationship, and builds a semantic graph — a structural snapshot of your entire codebase at a point in time. Each snapshot captures what exists, what connects to what, and what's drifting toward trouble. Run it again after changes and only the delta is recomputed.

## How It Works

```
commit a1b2c3 ──→ snapshot ──→ 847 symbols, 2,031 edges
commit d4e5f6 ──→ snapshot ──→ 851 symbols, 2,044 edges  (+4 symbols, +13 edges)
commit g7h8i9 ──→ snapshot ──→ 849 symbols, 2,067 edges  (-2 symbols, +23 edges ← coupling growing)
```

Each snapshot is a content-hashed graph stored in `.tempograph/cache.json`. Only files that actually changed are re-parsed — a 10,000-file repo re-indexes in seconds, not minutes. The cache is keyed by file content, not timestamps, so switching branches and coming back doesn't trigger a full rebuild.

This means you can:
- **See blast radius before you push** — know exactly what breaks if `auth.py` changes
- **Catch dead code as it forms** — symbols that lost their last caller since the previous snapshot
- **Track complexity drift** — hotspots that are gaining edges faster than the rest of the graph
- **Give AI agents structural memory** — the MCP server lets Claude (or any agent) query the graph mid-conversation instead of grep-and-pray

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

| Mode | What it answers |
|------|----------------|
| `overview` | How big is this codebase? How interconnected? |
| `map` | What's in each file? (tree with top symbols) |
| `symbols` | Full symbol index with signatures and locations |
| `focus` | What's relevant to *this specific task*? |
| `lookup` | Answer a natural-language question about the code |
| `blast` | If I touch this file, what else is affected? |
| `diff` | What changed and what does it connect to? |
| `hotspots` | Where is complexity concentrating? |
| `deps` | How do files depend on each other? |
| `dead` | What can I safely delete? |
| `arch` | What are the architecture layers? Any violations? |
| `stats` | Raw numbers — files, symbols, edges, tokens |

### Examples

```bash
# Structural overview
tempograph ./my-project --mode overview

# What's the riskiest code?
tempograph ./my-project --mode hotspots

# I'm about to refactor auth — what will break?
tempograph ./my-project --mode blast --target src/auth.py

# I need to understand the payment flow
tempograph ./my-project --mode focus --query "payment processing"

# What's dead weight?
tempograph ./my-project --mode dead
```

## MCP Server

Tempograph ships an MCP server that gives AI agents structural awareness of your codebase. Instead of pattern-matching over raw text, agents can query the actual dependency graph.

```bash
tempograph-server
```

| Tool | What the agent gets |
|------|-------------------|
| `index_repo` | Build or refresh the graph |
| `overview` | Structural summary to orient itself |
| `focus` | Relevant subgraph for its current task |
| `hotspots` | Where to be careful |
| `blast_radius` | Impact analysis before suggesting changes |
| `diff_context` | Understanding of what just changed |
| `dead_code` | Candidates for cleanup |

Add to your Claude settings (`~/.claude/settings.json`):

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

for sym in graph.symbols:
    print(f"{sym.kind.name} {sym.name} ({sym.file}:{sym.line})")

for edge in graph.edges:
    print(f"{edge.source} -> {edge.target} [{edge.kind.name}]")
```

## Incremental by Default

Tempograph hashes file contents, not modification times. The cache (`.tempograph/cache.json`) maps each file's content hash to its parsed symbols and edges. On re-index:

1. Hash every file in the repo
2. Skip files whose hash matches the cache
3. Re-parse only what actually changed
4. Merge results into the full graph

Switch branches, rebase, cherry-pick — if the bytes haven't changed, the work isn't repeated.

## License

MIT
