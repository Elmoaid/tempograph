"""Render a Tempo into agent-consumable context.

Multiple rendering modes, each optimized for different agent needs:
- overview: high-level repo summary (cheapest)
- map: file tree + top symbols per file
- symbols: full symbol index with signatures
- focused: task-specific subgraph based on a query
- lookup: answer a specific question about the codebase
"""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import tiktoken

from .types import Tempo, EdgeKind, FileInfo, Language, Symbol, SymbolKind

_ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def _find_entry_points(graph: Tempo) -> list[str]:
    """Find actual execution entry points — where the program starts.
    These are what agents need to understand the architecture."""
    entries: list[str] = []
    files = set(graph.files.keys())

    # Config/manifest files (tells you what kind of project)
    for pattern, label in [
        ("package.json", "package.json"), ("Cargo.toml", "Cargo.toml"),
        ("go.mod", "go.mod"), ("pyproject.toml", "pyproject.toml"),
        ("setup.py", "setup.py"),
    ]:
        for f in files:
            if f.endswith(pattern):
                entries.append(f)
                break

    # Main entry points
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base in ("main.py", "main.ts", "main.tsx", "main.rs", "main.go",
                     "index.ts", "index.tsx", "index.js", "app.py", "app.ts",
                     "lib.rs", "mod.rs", "__main__.py", "server.py", "cli.py"):
            entries.append(f)

    # Symbols named main/run/app at top level
    for sym in graph.symbols.values():
        if sym.parent_id:
            continue
        if sym.name in ("main", "app", "run_server", "create_app", "cli"):
            entries.append(f"{sym.file_path}::{sym.name}")

    # Deduplicate: if file already listed at file level, skip redundant file::symbol entry.
    # e.g. "tempo/cli.py" and "tempo/cli.py::main" → keep "tempo/cli.py::main" (more specific).
    seen_files: set[str] = set()
    deduped: list[str] = []
    # Two passes: symbol entries (more informative) take precedence over bare file entries
    symbol_entries = [e for e in entries if "::" in e]
    file_entries = [e for e in entries if "::" not in e]
    for e in symbol_entries:
        file_part = e.split("::")[0]
        seen_files.add(file_part)
        deduped.append(e)
    for e in file_entries:
        if e not in seen_files:
            deduped.append(e)
    return sorted(set(deduped), key=lambda e: ("::" not in e, e))[:15]  # files first, capped


def render_overview(graph: Tempo) -> str:
    """Cheapest mode: repo orientation — stats, entry points, key files, structure."""
    stats = graph.stats
    lines = [f"repo: {graph.root.rsplit('/', 1)[-1]}"]

    # One-line stats
    lang_items = sorted(stats["languages"].items(), key=lambda x: -x[1])
    lang_str = ", ".join(f"{lang}({n})" for lang, n in lang_items)
    lines.append(f"{stats['files']} files, {stats['symbols']} symbols, {stats['total_lines']:,} lines | {lang_str}")

    # Entry points — what agents need to understand "where does this start"
    entries = _find_entry_points(graph)
    if entries:
        lines.append("")
        lines.append("entry points:")
        for e in entries:
            lines.append(f"  {e}")

    # Top files by combined size + complexity (not two separate lists)
    # Exclude non-code files: JSON schemas, markdown docs, CSS, TOML configs, etc.
    # These have cx=0 but large line counts (e.g. auto-generated Tauri schemas at 2564L),
    # which dominate the ranking and mislead agents about which files actually matter.
    _CODE_LANGS = {
        "python", "typescript", "tsx", "javascript", "jsx",
        "rust", "go", "java", "csharp", "ruby",
    }
    file_scores: list[tuple[float, FileInfo]] = []
    for fi in graph.files.values():
        if fi.language.value not in _CODE_LANGS:
            continue  # skip markdown, json, toml, yaml, html, css — never "key" for coding
        cx = sum(graph.symbols[sid].complexity for sid in fi.symbols if sid in graph.symbols)
        # Score: lines matter, complexity matters more
        score = fi.line_count + cx * 3
        file_scores.append((score, fi))
    file_scores.sort(key=lambda x: -x[0])
    lines.append("")
    lines.append("key files (by size + complexity):")
    for score, fi in file_scores[:12]:
        cx = sum(graph.symbols[sid].complexity for sid in fi.symbols if sid in graph.symbols)
        parts = []
        parts.append(f"{fi.line_count:,}L")
        if cx > 0:
            parts.append(f"cx={cx}")
        parts.append(fi.language.value)
        lines.append(f"  {fi.path} ({', '.join(parts)})")

    # Module structure — just the shape, no noisy import counts
    modules: dict[str, list[str]] = {}
    for fp in graph.files:
        parts = fp.split("/")
        mod = parts[0] if len(parts) > 1 else "."
        modules.setdefault(mod, []).append(fp)
    if len(modules) > 1:
        lines.append("")
        lines.append("structure: " + ", ".join(
            f"{mod}/({len(fs)})" for mod, fs in
            sorted(modules.items(), key=lambda x: -len(x[1]))
        ))

    # Circular imports — these are real problems worth flagging
    cycles = graph.detect_circular_imports()
    if cycles:
        lines.append("")
        lines.append(f"CIRCULAR IMPORTS ({len(cycles)}):")
        for cycle in cycles[:3]:
            chain = " → ".join(c.rsplit("/", 1)[-1] for c in cycle)
            lines.append(f"  {chain}")

    # Suggest directories to exclude — detect likely noise
    noisy = _detect_noisy_dirs(graph, modules)
    if noisy:
        lines.append("")
        lines.append("SUGGESTED EXCLUDES (use exclude_dirs to filter):")
        for dir_name, reason in noisy[:3]:
            lines.append(f"  {dir_name}/ — {reason}")

    return "\n".join(lines)


def _detect_noisy_dirs(graph: Tempo, modules: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Detect directories that are likely noise — archived code, generated files, etc."""
    if len(modules) <= 2:
        return []

    total_files = sum(len(fs) for fs in modules.values())
    if total_files < 20:
        return []

    # Known noise patterns
    noise_names = {"archive", "archived", "old", "backup", "deprecated", "legacy",
                   "generated", "gen", "dist", "build", "out", "output", ".cache"}

    # Build cross-directory edge counts
    cross_dir_edges: dict[str, int] = {mod: 0 for mod in modules}
    for edge in graph.edges:
        if edge.kind == EdgeKind.CALLS or edge.kind == EdgeKind.IMPORTS:
            src_file = graph.symbols[edge.source_id].file_path if edge.source_id in graph.symbols else ""
            tgt_file = graph.symbols[edge.target_id].file_path if edge.target_id in graph.symbols else ""
            if not src_file or not tgt_file:
                continue
            src_dir = src_file.split("/")[0] if "/" in src_file else "."
            tgt_dir = tgt_file.split("/")[0] if "/" in tgt_file else "."
            if src_dir != tgt_dir:
                cross_dir_edges[src_dir] = cross_dir_edges.get(src_dir, 0) + 1
                cross_dir_edges[tgt_dir] = cross_dir_edges.get(tgt_dir, 0) + 1

    suggestions: list[tuple[str, str]] = []
    for mod, files in modules.items():
        if mod == ".":
            continue
        file_count = len(files)
        pct = file_count / total_files * 100
        cross_edges = cross_dir_edges.get(mod, 0)

        # Count files with actual code symbols (skip docs-only dirs)
        code_files = sum(1 for fp in files if fp in graph.files and len(graph.files[fp].symbols) > 0)
        if code_files == 0:
            continue  # no code — docs/notes/config dir, not worth excluding

        # Heuristic 1: name matches known noise patterns
        if mod.lower() in noise_names and code_files >= 5:
            suggestions.append((mod, f"{file_count} files ({code_files} with code), likely archived/generated"))
            continue

        # Heuristic 2: large directory with zero cross-dir connections
        if code_files >= 10 and cross_edges == 0:
            suggestions.append((mod, f"{file_count} files ({pct:.0f}%), no cross-directory connections"))
            continue

        # Heuristic 3: large directory with very few cross-dir connections relative to size
        if code_files >= 20 and cross_edges < 3 and pct > 20:
            suggestions.append((mod, f"{file_count} files ({pct:.0f}%), only {cross_edges} cross-dir edges"))

    suggestions.sort(key=lambda x: -len(x[1]))
    return suggestions


def render_map(graph: Tempo, *, max_symbols_per_file: int = 8, max_tokens: int = 0) -> str:
    """File tree with top symbols per file. Good for orientation."""
    lines = []
    token_count = 0

    # Group files by directory
    dirs: dict[str, list[FileInfo]] = defaultdict(list)
    for fi in sorted(graph.files.values(), key=lambda f: f.path):
        parts = fi.path.rsplit("/", 1)
        dir_path = parts[0] if len(parts) > 1 else "."
        dirs[dir_path].append(fi)

    truncated = False
    for dir_path in sorted(dirs):
        files = dirs[dir_path]
        dir_block = [f"[{dir_path}/]"]
        for fi in files:
            fname = fi.path.rsplit("/", 1)[-1]
            sym_count = len(fi.symbols)
            tag = f" ({fi.line_count} lines, {sym_count} sym)" if sym_count else f" ({fi.line_count} lines)"
            dir_block.append(f"  {fname}{tag}")

            # Show top symbols
            symbols = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols]
            symbols.sort(key=lambda s: (
                0 if s.kind in (SymbolKind.COMPONENT, SymbolKind.HOOK) else
                1 if s.kind in (SymbolKind.CLASS, SymbolKind.STRUCT, SymbolKind.TRAIT, SymbolKind.INTERFACE) else
                2 if s.kind == SymbolKind.FUNCTION and s.exported else
                3 if s.kind == SymbolKind.FUNCTION else
                4 if s.kind == SymbolKind.COMMAND else
                5,
                s.line_start,
            ))
            shown = symbols[:max_symbols_per_file]
            for sym in shown:
                kind_tag = sym.kind.value[:4]
                line_info = f"L{sym.line_start}"
                if sym.line_count > 5:
                    line_info = f"L{sym.line_start}-{sym.line_end}"
                sig = f" — {sym.signature}" if sym.signature and len(sym.signature) < 80 else ""
                dir_block.append(f"    {kind_tag} {sym.qualified_name} ({line_info}){sig}")
            if len(symbols) > max_symbols_per_file:
                dir_block.append(f"    ... +{len(symbols) - max_symbols_per_file} more")
        dir_block.append("")

        block_text = "\n".join(dir_block)
        if max_tokens > 0:
            block_tokens = count_tokens(block_text)
            if token_count + block_tokens > max_tokens:
                remaining_dirs = len(dirs) - len([l for l in lines if l.startswith("[")])
                lines.append(f"... truncated ({remaining_dirs} more directories)")
                truncated = True
                break
            token_count += block_tokens
        lines.extend(dir_block)

    return "\n".join(lines)


def render_symbols(graph: Tempo, *, max_tokens: int = 0) -> str:
    """Full symbol index — signatures, locations, relationships."""
    lines = []
    token_count = 0
    by_file: dict[str, list[Symbol]] = defaultdict(list)
    for sym in graph.symbols.values():
        by_file[sym.file_path].append(sym)

    for file_path in sorted(by_file):
        symbols = sorted(by_file[file_path], key=lambda s: s.line_start)
        file_block = [f"── {file_path} ──"]
        for sym in symbols:
            parts = [f"{sym.kind.value} {sym.qualified_name}"]
            parts.append(f"L{sym.line_start}-{sym.line_end}")
            if sym.signature:
                parts.append(sym.signature[:120])
            if sym.doc:
                parts.append(f'"{sym.doc[:80]}"')
            callers = graph.callers_of(sym.id)
            if callers:
                caller_names = [c.qualified_name for c in callers[:5]]
                parts.append(f"← {', '.join(caller_names)}")
            callees = graph.callees_of(sym.id)
            if callees:
                callee_names = [c.qualified_name for c in callees[:5]]
                parts.append(f"→ {', '.join(callee_names)}")
            file_block.append("  " + " | ".join(parts))
        file_block.append("")

        if max_tokens > 0:
            block_text = "\n".join(file_block)
            block_tokens = count_tokens(block_text)
            if token_count + block_tokens > max_tokens:
                remaining = len(by_file) - len([l for l in lines if l.startswith("──")])
                lines.append(f"... truncated ({remaining} more files, {sum(len(v) for k, v in by_file.items() if k >= file_path)} more symbols)")
                break
            token_count += block_tokens
        lines.extend(file_block)

    return "\n".join(lines)


_MONOLITH_THRESHOLD = 1000


def _extract_focus_files(focus_output: str, task_keywords: list[str] | None = None) -> list[str]:
    """Extract unique file paths from a render_focused output string.

    Returns up to 15 paths sorted by: (1) source vs example/test tier,
    (2) hub penalty (files dominating >30% of mentions w/o keyword match → demoted),
    (3) task keyword match (filename stem contains a keyword), (4) frequency.

    Hub detection: files like fastify.js appear in 63% of fastify focus outputs
    regardless of task, polluting KEY FILES. Evidence: hub removal cut -0.061 F1
    harm on fastify corpus (n=30). Keyword-matched files are exempt from hub penalty.
    """
    import re
    pattern = r'\b(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|rb)\b'
    all_paths = re.findall(pattern, focus_output)
    freq: dict[str, int] = {}
    for p in all_paths:
        freq[p] = freq.get(p, 0) + 1

    kw_lower = [k.lower() for k in (task_keywords or [])]
    total_mentions = sum(freq.values())

    def _is_hub(path: str, stem: str) -> bool:
        if total_mentions <= 6:
            return False
        has_kw = any(kw in stem for kw in kw_lower if len(kw) > 3)
        return not has_kw and (freq[path] / total_mentions) > 0.30

    def _sort_key(path: str) -> tuple[int, int, int]:
        lower = path.lower()
        if any(x in lower for x in ("example", "tutorial", "demo", "sample")):
            tier = 2
        elif any(x in lower for x in ("test", "spec", "fixture")):
            tier = 1
        else:
            tier = 0
        stem = lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        hub_penalty = 1 if _is_hub(path, stem) else 0
        kw_rank = 0 if kw_lower and any(kw in stem for kw in kw_lower if len(kw) > 3) else 1
        return (tier + hub_penalty, kw_rank, -freq[path])

    return sorted(freq.keys(), key=_sort_key)[:15]


def _extract_cl_keywords(task: str) -> list[str]:
    """Extract code-symbol keywords from a change-localization task (PR title/commit message).

    Ported from bench/changelocal/context.py::_extract_keywords.
    This is the PR-title-specific extractor used for change localization tasks.
    For general coding tasks, use the fuzzy search in search_symbols_scored directly.
    """
    import re
    _TRUNK_BRANCHES = frozenset({"master", "main", "develop", "development", "stable", "release"})
    m = (re.search(r'Merge pull request #\d+ from [^/\s]+/(\S+)', task)
         or re.search(r'Merge pull request \S+#\d+ from [^/\s]+/(\S+)', task))
    if m:
        branch = m.group(1)
        leaf = branch.lower().split('/')[-1]
        if leaf in _TRUNK_BRANCHES:
            return []
        _DOC_PREFIXES = ("docs-", "doc-", "readme-", "changelog-", "documentation-")
        _DOC_SUFFIXES = ("-docs", "-doc", "-readme", "-changelog")
        branch_lower = branch.lower()
        if (any(leaf.startswith(p) for p in _DOC_PREFIXES)
                or any(leaf.endswith(s) for s in _DOC_SUFFIXES)
                or branch_lower.startswith("docs/")
                or branch_lower.startswith("doc/")):
            return []
        body = task[task.find('\n')+1:].strip() if '\n' in task else ''
        body = re.sub(r'https?://\S+', '', body)
        body = re.sub(r'^[\w-]+:\s+.*$', '', body, flags=re.MULTILINE)
        body = body.strip()
        # Mine the PR body for additional keywords when the branch name is not self-describing:
        # - Ticket-reference branches (issue12345, fix-1587) have numeric names → body is the task.
        # - Pure snake_case branches with no CamelCase transitions (e.g. "support_forwardred_in_python36")
        #   may have typos or version suffixes that hide the real identifier; body often names it.
        # Skip body mining for branches that already produce CamelCase compounds (hyphenated segments
        # like "reply-not-found" → "ReplyNotFound", or explicit camelCase like "partII" with "tI").
        # Those branches are self-describing and body mining adds noise.
        _is_ticket = bool(
            re.match(r'^(?:issue|ticket|bug|patch|pr|fix)[-_]?\d+', leaf) or
            re.match(r'^\d+[-_]', leaf)
        )
        _branch_has_compound = bool(
            re.search(r'\b[a-z]{2,}-[a-z]{2,}', branch)  # hyphenated alpha-alpha ("reply-not")
            or re.search(r'[a-z][A-Z]', branch)           # explicit camelCase ("partII")
        )
        # "Username-master" / "OrgName-master": external contributor PR'd from their fork's master.
        # The compound (camelCase username) is meaningless as a task keyword; mine body instead.
        # Evidence: falcon "CygnusNetworks-master" / "hooblei-master" → body has "byte ranges" / "context_type".
        _is_fork_master = leaf.endswith("-master") or leaf.endswith("_master")
        task = branch + ('\n' + body if (_is_ticket or not _branch_has_compound or _is_fork_master) and body else '')
    else:
        task = re.sub(r'^Merge (?:branch|pull request)[^\n]*\n?', '', task, flags=re.IGNORECASE)
        # Strip conventional commit type prefix (feat:, fix:, chore:, refactor(scope):, etc.)
        # before keyword extraction — these prefixes are commit metadata, not code identifiers.
        task = re.sub(r'^(?:feat|fix|chore|refactor|style|perf|ci|build|docs|test|revert)(?:\([^)]*\))?!?:\s*', '', task, flags=re.IGNORECASE)

    skip = {
        "the", "and", "for", "from", "with", "this", "that", "fix", "add",
        "update", "remove", "change", "bug", "feature", "merge", "pull",
        "request", "branch", "commit", "issue", "use", "make", "new",
        "when", "not", "all", "can", "should", "would", "into", "also",
        # Short English articles/prepositions/conjunctions (never code identifiers)
        "are", "its", "via", "any", "but", "has", "was", "had", "yet",
        "nor", "per", "due", "let", "now", "old", "raw", "off", "out",
        "non", "sub", "pre", "too",
        "pass", "through", "methods", "method", "function", "class", "file",
        "code", "test", "tests", "type", "types", "value", "values", "data",
        "object", "objects", "item", "items", "list", "dict", "set", "get",
        "put", "call", "calls", "return", "returns", "allow", "allows",
        "handle", "handles", "check", "checks", "run", "runs", "create",
        "support", "include", "includes", "avoid", "prevent", "ensure",
        "apply", "improve", "move", "moved", "part", "parts", "some",
        "error", "errors", "option", "options", "response", "config",
        "enable", "enabled", "disable", "disabled", "default", "global",
        "log", "logger", "logging", "ticket", "docs", "readme",
        "fixed", "improved", "updated", "added", "removed", "changed",
        "fixes", "improves", "updates", "usage", "internal", "external",
        "fork", "syncing", "sync", "backport", "rebase", "cherry", "pick",
        # Conventional commit type tokens (belt-and-suspenders for any that slip through)
        "feat", "chore", "refactor", "revert", "perf", "style",
    }
    seen: set[str] = set()
    priority: list[str] = []
    general: list[str] = []

    def _record(ident: str, bucket: list[str]) -> None:
        lower = ident.lower()
        if re.match(r'^(?:issue|ticket|bug|pr|patch|fix)\d+$', lower):
            return
        if lower not in skip and lower not in seen and len(ident) > 2:
            seen.add(lower)
            bucket.append(ident)

    # Backtick-quoted identifiers are highest-priority: explicitly named symbols.
    # E.g. "deprecate `should_ignore_error`" → extract "should_ignore_error" first.
    for backtick_id in re.findall(r'`([a-zA-Z_][a-zA-Z0-9_]{2,})`', task):
        _record(backtick_id, priority)

    lines = task.split('\n', 1)
    branch_text = lines[0]
    body_text = lines[1].strip() if len(lines) > 1 else ''

    def _extract_from(source: str, strict_camel: bool = False) -> None:
        for hyphenated in re.findall(r'\b[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)+\b', source):
            camel = "".join(part.capitalize() for part in hyphenated.split("-"))
            _record(camel, priority)
        for ident in re.findall(r'(?<![A-Z_])\b[A-Z][A-Z0-9_]{2,}\b', source):
            if '_' in ident:
                _record(ident, priority)
        camel_pat = (r'\b(?:[A-Z][a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]+|[A-Z][a-zA-Z0-9]{6,})\b'
                     if strict_camel else r'\b[A-Z][a-zA-Z0-9]+\b')
        for ident in re.findall(camel_pat, source):
            _record(ident, priority)
        for ident in re.findall(r'\b[a-z_][a-z0-9_]{2,}\b', source):
            _record(ident, general)
            if ident.count('_') >= 3 and len(ident) > 20:
                parts = ident.split('_')
                for i, part in enumerate(parts):
                    if len(part) > 2:
                        _record(part, general)
                    if i + 1 < len(parts):
                        compound = f"{parts[i]}_{parts[i+1]}"
                        if len(compound) > 4:
                            _record(compound, general)

    # For fork-master branches ("Username-master"), the branch is a meaningless GitHub username.
    # Extract body keywords FIRST so they get priority over the useless branch name.
    # E.g. "CygnusNetworks-master\nAdd arbitrary byte ranges" → body gives "byte", "ranges" first.
    if body_text and (branch_text.endswith('-master') or branch_text.endswith('_master')):
        _extract_from(body_text, strict_camel=True)
    else:
        _extract_from(branch_text, strict_camel=False)
        if body_text:
            _extract_from(body_text, strict_camel=True)

    return priority + general


def _is_docs_branch_task(task: str) -> bool:
    """Return True when the PR branch name is a docs-specific path.

    Docs branches (docs-javascript, docs/#4574, readme-fix, changelog-update) often change
    both docs AND code, but the overview fallback incorrectly focuses the model on generic
    structure (conf.py, README) rather than the actual code paths changed.
    Suppressing overview lets the model use training knowledge → tie instead of regression.

    Evidence: flask "docs-javascript" overview → F1 0.556→0.154 (-0.402 delta, n=71 dataset).
    """
    import re
    m = re.search(r'Merge pull request \S+ from [^/\s]+/(\S+)', task)
    if not m:
        return False
    branch = m.group(1).lower()
    leaf = branch.split('/')[-1]
    _DOC_PREFIXES = ("docs-", "doc-", "readme-", "changelog-", "documentation-")
    _DOC_SUFFIXES = ("-docs", "-doc", "-readme", "-changelog")
    return (
        any(leaf.startswith(p) for p in _DOC_PREFIXES)
        or any(leaf.endswith(s) for s in _DOC_SUFFIXES)
        or branch.startswith("docs/")
        or branch.startswith("doc/")
    )


def _is_change_localization(task: str, task_type: str) -> bool:
    """Detect if a task is a change-localization task (PR title, commit message, issue ref).

    Change-localization tasks benefit from the per-keyword focus algorithm.
    General coding tasks ("add login feature") should use the default multi-token approach.
    """
    import re
    if task_type in ("changelocal", "debug", "bugfix"):
        return True
    lower = task.lower()
    if "merge pull request" in lower or re.match(r"merge branch '", lower):
        return True
    # Conventional commit prefix
    if re.match(r'^(fix|feat|refactor|chore|perf|style|build|ci|docs|test)(\(.+\))?:', lower):
        return True
    # PR title with issue reference: "Fix #1234" or "Fix teardown (#5928)"
    if re.search(r'\(#\d+\)\s*$', task) or re.search(r'^\w.*#\d+', task):
        return True
    return False


def _suggest_alternatives(graph: Tempo, query: str, max_suggestions: int = 5) -> str:
    """Build a 'did you mean?' hint when a focus query has no matches.

    Splits the query into tokens and searches for each, returning the
    top-scoring symbols as suggestions to try instead.
    """
    import re
    tokens = [t for t in re.split(r'[^a-zA-Z0-9]+', query) if len(t) > 2]
    if not tokens:
        return ""
    seen_ids: set[str] = set()
    candidates: list[tuple[float, Symbol]] = []
    for token in tokens:
        for score, sym in graph.search_symbols_scored(token)[:10]:
            if sym.id not in seen_ids:
                seen_ids.add(sym.id)
                candidates.append((score, sym))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: -x[0])
    lines = [f"No exact match for '{query}'. Closest symbols:"]
    for _, sym in candidates[:max_suggestions]:
        file_short = sym.file_path.split("/")[-1] if "/" in sym.file_path else sym.file_path
        lines.append(f"  {sym.name} ({sym.kind.value}) — {file_short}:{sym.line_start}")
    lines.append(f"\nTry: focus('{candidates[0][1].name}')")
    return "\n".join(lines)


def render_focused(graph: Tempo, query: str, *, max_tokens: int = 4000) -> str:
    """Task-focused rendering with BFS graph traversal.
    Starts from search results, then follows call/render/import edges
    to build a connected subgraph relevant to the query.

    For monolith files (>1000 lines), adds intra-file neighborhood context
    and biases BFS toward cross-file edges to avoid getting trapped in one file."""
    scored = graph.search_symbols_scored(query)
    if not scored:
        return _suggest_alternatives(graph, query) or f"No symbols matching '{query}'"

    # Quality gate: drop seeds with much lower scores than the best match
    top_score = scored[0][0]
    threshold = max(top_score * 0.3, 2.0)  # at least 30% of best, minimum 2.0
    seeds = [sym for score, sym in scored if score >= threshold][:10]

    # Determine seed files to detect monolith bias
    seed_files: set[str] = set()
    for s in seeds:
        fi = graph.files.get(s.file_path)
        if fi and fi.line_count >= _MONOLITH_THRESHOLD:
            seed_files.add(s.file_path)

    # BFS: expand from seed symbols following edges
    # Wider expansion: depth 3, more callers/callees at depth 0-1
    seen_ids: set[str] = set()
    queue: list[tuple[Symbol, int]] = [(s, 0) for s in seeds]
    ordered: list[tuple[Symbol, int]] = []

    def _enqueue(candidate: Symbol, depth: int) -> None:
        if candidate.id in seen_ids:
            return
        # Cross-file edges get priority when seeds are in monolith files
        if seed_files and candidate.file_path not in seed_files:
            queue.insert(0, (candidate, depth))
        else:
            queue.append((candidate, depth))

    while queue and len(ordered) < 50:
        sym, depth = queue.pop(0)
        if sym.id in seen_ids:
            continue
        seen_ids.add(sym.id)
        ordered.append((sym, depth))

        if depth < 3:
            # More context at shallow depths, less at deeper
            caller_limit = 8 if depth == 0 else 5 if depth == 1 else 3
            callee_limit = 8 if depth == 0 else 5 if depth == 1 else 3
            for caller in graph.callers_of(sym.id)[:caller_limit]:
                _enqueue(caller, depth + 1)
            for callee in graph.callees_of(sym.id)[:callee_limit]:
                _enqueue(callee, depth + 1)
            if depth < 2:
                for child in graph.children_of(sym.id)[:5]:
                    _enqueue(child, depth + 1)

    lines = [f"Focus: {query}", ""]
    seen_files: set[str] = set()
    token_count = 0

    for sym, depth in ordered:
        indent = "  " * depth if depth > 0 else ""
        prefix = ["●", "  →", "    ·", "      "][min(depth, 3)]
        # Core info
        loc = f"{sym.file_path}:{sym.line_start}-{sym.line_end}"
        block_lines = [f"{prefix} {sym.kind.value} {sym.qualified_name} — {loc}"]
        if sym.signature and depth < 2:
            block_lines.append(f"{indent}  sig: {sym.signature[:150]}")
        if sym.doc and depth == 0:
            block_lines.append(f"{indent}  doc: {sym.doc}")
        # Detail at depth 0 and 1 (not just 0)
        if depth <= 1:
            warnings = []
            if sym.line_count > 500:
                warnings.append(f"LARGE ({sym.line_count} lines — use grep, don't read)")
            if sym.complexity > 50:
                warnings.append(f"HIGH COMPLEXITY (cx={sym.complexity})")
            if warnings:
                block_lines.append(f"{indent}  ⚠ {', '.join(warnings)}")
            callers = graph.callers_of(sym.id)
            if callers:
                shown = 8 if depth == 0 else 5
                block_lines.append(f"{indent}  called by: {', '.join(c.qualified_name for c in callers[:shown])}")
                if len(callers) > shown:
                    block_lines[-1] += f" (+{len(callers) - shown} more)"
            callees = graph.callees_of(sym.id)
            if callees:
                shown = 8 if depth == 0 else 5
                block_lines.append(f"{indent}  calls: {', '.join(c.qualified_name for c in callees[:shown])}")
                if len(callees) > shown:
                    block_lines[-1] += f" (+{len(callees) - shown} more)"
            if depth == 0:
                children = graph.children_of(sym.id)
                if children:
                    block_lines.append(f"{indent}  contains: {', '.join(f'{c.kind.value[:4]} {c.name}' for c in children[:10])}")

        block = "\n".join(block_lines)
        block_tokens = count_tokens(block)
        if token_count + block_tokens > max_tokens:
            remaining = len(ordered) - len([l for l in lines if l and not l.startswith("...")])
            if remaining > 0:
                lines.append(f"... truncated ({remaining} more symbols)")
            break
        lines.append(block)
        token_count += block_tokens
        seen_files.add(sym.file_path)

    # File context: for each file touched, show key co-located symbols
    file_context: list[str] = []
    for fp in sorted(seen_files):
        fi = graph.files.get(fp)
        if not fi or len(fi.symbols) < 3:
            continue
        file_syms = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols and sid not in seen_ids]
        # Show exported symbols the agent might need
        important = [s for s in file_syms if s.exported and s.kind in (
            SymbolKind.FUNCTION, SymbolKind.CLASS, SymbolKind.COMPONENT, SymbolKind.HOOK
        )][:5]
        if important:
            names = ", ".join(f"{s.name} L{s.line_start}" for s in important)
            file_context.append(f"  {fp}: also has {names}")

    if file_context:
        ctx_block = "\nAlso in these files:\n" + "\n".join(file_context)
        ctx_tokens = count_tokens(ctx_block)
        if token_count + ctx_tokens <= max_tokens:
            lines.append(ctx_block)
            token_count += ctx_tokens

    # Monolith neighborhood: for seed symbols in large files, show nearby symbols
    for sym, depth in ordered:
        if depth > 0:
            break  # only seeds
        fi = graph.files.get(sym.file_path)
        if not fi or fi.line_count < _MONOLITH_THRESHOLD:
            continue
        neighborhood = _monolith_neighborhood(graph, sym)
        if neighborhood:
            nb_block = "\n".join(neighborhood)
            nb_tokens = count_tokens(nb_block)
            if token_count + nb_tokens <= max_tokens:
                lines.append("")
                lines.extend(neighborhood)
                token_count += nb_tokens

    # Related files with size warnings
    related = _find_related_files(graph, [s for s, _ in ordered[:10]])
    if related - seen_files:
        lines.append("")
        lines.append("Related files:")
        for fp in sorted(related - seen_files)[:10]:
            fi = graph.files.get(fp)
            if fi:
                tag = " [grep-only]" if fi.line_count > 500 else ""
                lines.append(f"  {fp} ({fi.line_count} lines){tag}")

    # Blast radius hint for high-impact seed symbols
    high_impact = [s for s, d in ordered[:5] if d == 0
                   and len(graph.callers_of(s.id)) >= 3
                   and any(c.file_path != s.file_path for c in graph.callers_of(s.id))]
    if high_impact and token_count < max_tokens - 50:
        names = ", ".join(s.qualified_name for s in high_impact[:3])
        lines.append(f"\nBefore modifying: run blast_radius(query=\"{high_impact[0].qualified_name}\") to check downstream impact.")

    return "\n".join(lines)


def _monolith_neighborhood(graph: Tempo, seed: Symbol) -> list[str]:
    """For a symbol in a large file, show its local neighborhood:
    parent scope, siblings, and nearby symbols by line proximity."""
    all_syms = graph.symbols_in_file(seed.file_path)
    if len(all_syms) < 3:
        return []

    fi = graph.files.get(seed.file_path)
    lines = [f"Neighborhood in {seed.file_path} ({fi.line_count if fi else '?'} lines):"]

    # Parent scope
    if seed.parent_id and seed.parent_id in graph.symbols:
        parent = graph.symbols[seed.parent_id]
        lines.append(f"  parent: {parent.kind.value} {parent.qualified_name} (L{parent.line_start}-{parent.line_end})")

    # Siblings: same parent, sorted by line
    siblings = [s for s in all_syms if s.parent_id == seed.parent_id and s.id != seed.id]
    siblings.sort(key=lambda s: s.line_start)
    if siblings:
        # Show nearest siblings (up to 5 before and 5 after by line number)
        before = [s for s in siblings if s.line_start < seed.line_start][-5:]
        after = [s for s in siblings if s.line_start > seed.line_start][:5]
        nearby = before + after
        if nearby:
            lines.append(f"  siblings ({len(siblings)} total, showing nearest):")
            for s in nearby:
                rel = "↑" if s.line_start < seed.line_start else "↓"
                dist = abs(s.line_start - seed.line_start)
                lines.append(f"    {rel} {s.kind.value} {s.name} L{s.line_start} ({dist}L away)")

    # Top-level symbols by size (helps orient in the file).
    # Suppress for top-of-file imports/variables: they sit at L1-20, so all landmarks
    # are "below" and semantically unrelated — showing them misleads the model into
    # predicting files associated with the landmark instead of the matched symbol.
    file_lines = fi.line_count if fi else 1
    is_top_import = (
        seed.kind.value in ("variable", "imports")
        and seed.line_start <= max(20, file_lines * 0.05)
    )
    if not is_top_import:
        top_level = sorted(
            [s for s in all_syms if s.parent_id is None and s.line_count > 20],
            key=lambda s: -s.line_count,
        )[:8]
        if top_level:
            lines.append(f"  landmarks:")
            for s in top_level:
                marker = " ← YOU" if s.id == seed.id else ""
                lines.append(f"    {s.kind.value} {s.name} L{s.line_start}-{s.line_end} ({s.line_count}L){marker}")

    return lines


def render_lookup(graph: Tempo, question: str) -> str:
    """Answer a specific question about the codebase."""
    q = question.lower()

    # "where is X defined?"
    if any(w in q for w in ("where is", "find", "locate", "definition of")):
        name = _extract_name_from_question(question)
        if name:
            symbols = graph.find_symbol(name)
            if symbols:
                lines = [f"'{name}' found in {len(symbols)} location(s):"]
                for sym in symbols[:10]:
                    lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.kind.value} {sym.qualified_name}")
                    if sym.signature:
                        lines.append(f"    {sym.signature[:150]}")
                    callers = graph.callers_of(sym.id)
                    if callers:
                        lines.append(f"    called by: {', '.join(c.qualified_name for c in callers[:5])}")
                return "\n".join(lines)
            else:
                # Fuzzy search
                results = graph.search_symbols(name)
                if results:
                    lines = [f"No exact match for '{name}'. Similar:"]
                    for sym in results[:5]:
                        lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.qualified_name}")
                    return "\n".join(lines)
                return f"'{name}' not found in the codebase."

    # "what calls X?" / "who uses X?"
    if any(w in q for w in ("what calls", "who calls", "who uses", "callers of", "references to")):
        name = _extract_name_from_question(question)
        if name:
            symbols = graph.find_symbol(name)
            if symbols:
                lines = []
                for sym in symbols[:3]:
                    callers = graph.callers_of(sym.id)
                    if callers:
                        lines.append(f"'{sym.qualified_name}' is called by:")
                        for c in callers[:15]:
                            lines.append(f"  {c.file_path}:{c.line_start} — {c.qualified_name}")
                    else:
                        lines.append(f"'{sym.qualified_name}' has no recorded callers.")
                return "\n".join(lines) if lines else f"'{name}' not found."
            return f"'{name}' not found."

    # "what does X call?" / "dependencies of X"
    if any(w in q for w in ("what does", "calls what", "dependencies", "callees")):
        name = _extract_name_from_question(question)
        if name:
            symbols = graph.find_symbol(name)
            if symbols:
                lines = []
                for sym in symbols[:3]:
                    callees = graph.callees_of(sym.id)
                    if callees:
                        lines.append(f"'{sym.qualified_name}' calls:")
                        for c in callees[:15]:
                            lines.append(f"  {c.file_path}:{c.line_start} — {c.qualified_name}")
                    else:
                        lines.append(f"'{sym.qualified_name}' has no recorded callees.")
                return "\n".join(lines) if lines else f"'{name}' not found."
            return f"'{name}' not found."

    # "what files import X?" / "who imports X?"
    if any(w in q for w in ("imports", "imported by", "who imports")):
        name = _extract_name_from_question(question)
        if name:
            # Search in file paths
            matching_files = [fp for fp in graph.files if name.lower() in fp.lower()]
            if matching_files:
                lines = []
                for fp in matching_files[:5]:
                    importers = graph.importers_of(fp)
                    if importers:
                        lines.append(f"'{fp}' is imported by:")
                        for imp in importers[:10]:
                            lines.append(f"  {imp}")
                    else:
                        lines.append(f"'{fp}' has no recorded importers.")
                return "\n".join(lines)

    # "what renders X?" / "where is X rendered?"
    if any(w in q for w in ("renders", "rendered", "jsx", "component tree")):
        name = _extract_name_from_question(question)
        if name:
            render_edges = [e for e in graph.edges if e.kind == EdgeKind.RENDERS and name.lower() in e.target_id.lower()]
            if render_edges:
                lines = [f"'{name}' is rendered by:"]
                for e in render_edges[:10]:
                    src = graph.symbols.get(e.source_id)
                    if src:
                        lines.append(f"  {src.file_path}:{e.line} — {src.qualified_name}")
                return "\n".join(lines)

    # "what implements X?" / "what extends X?" / "subtypes of X"
    if any(w in q for w in ("implements", "extends", "subtype", "subclass", "inherits from")):
        name = _extract_name_from_question(question)
        if name:
            subtypes = graph.subtypes_of(name)
            if subtypes:
                lines = [f"'{name}' is implemented/extended by:"]
                for sym in subtypes[:15]:
                    edge_kind = "implements" if any(
                        e.kind == EdgeKind.IMPLEMENTS and e.target_id == name and e.source_id == sym.id
                        for e in graph.edges
                    ) else "extends"
                    lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.qualified_name} ({edge_kind})")
                return "\n".join(lines)

    # Fallback: treat as search
    results = graph.search_symbols(question)
    if results:
        lines = [f"Search results for '{question}':"]
        for sym in results[:15]:
            lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.kind.value} {sym.qualified_name}")
            if sym.signature:
                lines.append(f"    {sym.signature[:120]}")
        return "\n".join(lines)

    return f"No results for '{question}'."


def render_blast_radius(graph: Tempo, file_path: str, query: str = "") -> str:
    """Show what might break if a file or symbol is modified.

    If query is given, shows blast radius for matching symbols instead of
    the whole file — much more useful for monolith files."""
    if query:
        return _render_symbol_blast(graph, query)

    fi = graph.files.get(file_path)
    if not fi:
        return f"File '{file_path}' not found."

    lines = [f"Blast radius for {file_path}:", ""]

    # Direct importers
    importers = graph.importers_of(file_path)
    if importers:
        lines.append(f"Directly imported by ({len(importers)}):")
        for imp in sorted(importers):
            lines.append(f"  {imp}")
        lines.append("")

    # Symbols in this file that are called externally
    symbols = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols]
    external_callers: dict[str, list[str]] = {}
    for sym in symbols:
        callers = graph.callers_of(sym.id)
        ext = [c for c in callers if c.file_path != file_path]
        if ext:
            external_callers[sym.qualified_name] = [f"{c.file_path}:{c.line_start}" for c in ext]

    if external_callers:
        lines.append("Externally called symbols:")
        for name, locations in sorted(external_callers.items()):
            lines.append(f"  {name}:")
            for loc in locations[:5]:
                lines.append(f"    {loc}")
        lines.append("")

    # Render edges (components that render components from this file)
    render_targets = set()
    for sym in symbols:
        for renderer in graph.renderers_of(sym.id):
            if renderer.file_path != file_path:
                render_targets.add(f"{renderer.file_path}:{renderer.line_start} renders {sym.name}")

    if render_targets:
        lines.append("Component render relationships:")
        for rt in sorted(render_targets):
            lines.append(f"  {rt}")

    if not importers and not external_callers and not render_targets:
        lines.append("No external dependencies found — safe to modify in isolation.")

    return "\n".join(lines)


def _render_symbol_blast(graph: Tempo, query: str) -> str:
    """Blast radius for specific symbols — targeted alternative to whole-file blast."""
    matches = graph.search_symbols(query)
    if not matches:
        return f"No symbols matching '{query}'"

    lines = [f"Symbol blast radius for '{query}':", ""]
    for sym in matches[:5]:
        loc = f"{sym.file_path}:{sym.line_start}-{sym.line_end}"
        lines.append(f"● {sym.kind.value} {sym.qualified_name} — {loc}")

        callers = graph.callers_of(sym.id)
        if callers:
            ext = [c for c in callers if c.file_path != sym.file_path]
            local = [c for c in callers if c.file_path == sym.file_path]
            if ext:
                lines.append(f"  external callers ({len(ext)}):")
                for c in ext[:8]:
                    lines.append(f"    {c.file_path}:{c.line_start} — {c.qualified_name}")
            if local:
                lines.append(f"  same-file callers ({len(local)}):")
                for c in local[:5]:
                    lines.append(f"    L{c.line_start} — {c.qualified_name}")
        else:
            lines.append("  no callers")

        renderers = graph.renderers_of(sym.id)
        if renderers:
            lines.append(f"  rendered by ({len(renderers)}):")
            for r in renderers[:5]:
                lines.append(f"    {r.file_path}:{r.line_start} — {r.qualified_name}")

        children = graph.children_of(sym.id)
        if children:
            lines.append(f"  contains {len(children)} child symbol(s)")

        lines.append("")

    return "\n".join(lines)



def _find_related_files(graph: Tempo, symbols: list[Symbol]) -> set[str]:
    """Find files related to a set of symbols via edges."""
    files: set[str] = set()
    for sym in symbols:
        for caller in graph.callers_of(sym.id):
            files.add(caller.file_path)
        for callee in graph.callees_of(sym.id):
            files.add(callee.file_path)
        # Include __init__.py importers — package re-exports are often changed
        # alongside the module they expose (key source of over-narrowing)
        for importer_fp in graph.importers_of(sym.file_path):
            if importer_fp.endswith("__init__.py"):
                files.add(importer_fp)
    return files


def render_diff_context(graph: Tempo, changed_files: list[str], *, max_tokens: int = 6000) -> str:
    """Given changed files, render everything an agent needs: affected symbols,
    external callers, importers, component tree impact, and blast radius."""
    lines = [f"Diff context for {len(changed_files)} changed file(s):", ""]

    # Normalize paths
    normalized = set()
    for f in changed_files:
        if f in graph.files:
            normalized.add(f)
        else:
            for fp in graph.files:
                if fp.endswith(f) or fp.endswith("/" + f):
                    normalized.add(fp)
                    break

    if not normalized:
        return f"None of the changed files found in graph: {changed_files}"

    affected_symbols: list[Symbol] = []
    for fp in sorted(normalized):
        fi = graph.files[fp]
        syms = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols]
        affected_symbols.extend(syms)

    lines.append("Changed files:")
    for fp in sorted(normalized):
        fi = graph.files[fp]
        lines.append(f"  {fp} ({fi.line_count} lines, {len(fi.symbols)} symbols)")
    lines.append("")

    # Exported symbols with external callers (breaking change risk)
    external_deps: list[tuple[Symbol, list[Symbol]]] = []
    for sym in affected_symbols:
        if not sym.exported:
            continue
        callers = graph.callers_of(sym.id)
        ext_callers = [c for c in callers if c.file_path not in normalized]
        if ext_callers:
            external_deps.append((sym, ext_callers))

    token_count = count_tokens("\n".join(lines))

    if external_deps and token_count < max_tokens - 200:
        lines.append("EXTERNAL DEPENDENCIES (breaking change risk):")
        for sym, callers in external_deps[:10]:
            entry = f"  {sym.kind.value} {sym.qualified_name} ({sym.file_path}:{sym.line_start})"
            for c in callers[:3]:
                entry += f"\n    <- {c.qualified_name} ({c.file_path}:{c.line_start})"
            if len(callers) > 3:
                entry += f"\n    ... +{len(callers) - 3} more callers"
            et = count_tokens(entry)
            if token_count + et > max_tokens - 100:
                break
            lines.append(entry)
            token_count += et
        lines.append("")

    # Files that import the changed files
    all_importers: set[str] = set()
    for fp in normalized:
        all_importers.update(graph.importers_of(fp))
    all_importers -= normalized

    if all_importers and token_count < max_tokens - 100:
        lines.append(f"Files importing changed code ({len(all_importers)}):")
        for imp in sorted(all_importers)[:10]:
            lines.append(f"  {imp}")
        if len(all_importers) > 10:
            lines.append(f"  ... +{len(all_importers) - 10} more")
        lines.append("")
        token_count = count_tokens("\n".join(lines))

    # Component tree impact
    if token_count < max_tokens - 100:
        render_impact: list[str] = []
        for sym in affected_symbols:
            if sym.kind == SymbolKind.COMPONENT:
                for renderer in graph.renderers_of(sym.id):
                    if renderer.file_path not in normalized:
                        render_impact.append(f"  {renderer.qualified_name} ({renderer.file_path}) renders {sym.name}")

        if render_impact:
            lines.append("Component tree impact:")
            for ri in render_impact[:5]:
                lines.append(ri)
            lines.append("")
            token_count = count_tokens("\n".join(lines))
    if max_tokens - token_count > 500:
        lines.append("Key symbols in changed files:")
        for sym in affected_symbols:
            if sym.kind in (SymbolKind.VARIABLE, SymbolKind.CONSTANT):
                continue
            if sym.parent_id and sym.kind == SymbolKind.FUNCTION:
                continue
            entry = f"  {sym.kind.value} {sym.qualified_name} L{sym.line_start}-{sym.line_end}"
            if sym.signature:
                entry += f"\n    {sym.signature[:120]}"
            entry_tokens = count_tokens(entry)
            if token_count + entry_tokens > max_tokens:
                lines.append(f"  ... truncated ({len(affected_symbols)} total)")
                break
            lines.append(entry)
            token_count += entry_tokens

    return "\n".join(lines)


def render_hotspots(graph: Tempo, *, top_n: int = 20) -> str:
    """Find the most interconnected, complex, high-risk symbols."""
    # Pre-build renders-from index to avoid O(symbols*edges) scan
    renders_from: dict[str, int] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.RENDERS:
            renders_from[edge.source_id] = renders_from.get(edge.source_id, 0) + 1

    scores: list[tuple[float, Symbol]] = []

    for sym in graph.symbols.values():
        if sym.kind in (SymbolKind.VARIABLE, SymbolKind.CONSTANT,
                        SymbolKind.ENUM_MEMBER, SymbolKind.FIELD):
            continue

        score = 0.0
        callers = graph.callers_of(sym.id)
        callees = graph.callees_of(sym.id)
        children = graph.children_of(sym.id)

        score += len(callers) * 3.0
        score += len(callees) * 1.5
        score += min(sym.line_count / 10, 50)
        score += len(children) * 2.0
        cross_file = len(set(c.file_path for c in callers) - {sym.file_path})
        score += cross_file * 5.0
        render_count = renders_from.get(sym.id, 0)
        score += render_count * 2.0
        # Cyclomatic complexity: log scale to avoid dominating
        if sym.complexity > 1:
            score += math.log2(sym.complexity) * 3.0

        if score > 0:
            scores.append((score, sym))

    scores.sort(key=lambda x: -x[0])

    lines = [f"Top {top_n} hotspots (highest coupling + complexity):", ""]
    for i, (score, sym) in enumerate(scores[:top_n], 1):
        callers = graph.callers_of(sym.id)
        callees = graph.callees_of(sym.id)
        children = graph.children_of(sym.id)
        cross_files = len(set(c.file_path for c in callers) - {sym.file_path})

        lines.append(
            f"{i:2d}. {sym.kind.value} {sym.qualified_name} "
            f"[risk={score:.0f}] ({sym.file_path}:{sym.line_start})"
        )
        details = []
        if callers:
            details.append(f"{len(callers)} callers ({cross_files} cross-file)")
        if callees:
            details.append(f"{len(callees)} callees")
        if children:
            details.append(f"{len(children)} children")
        details.append(f"{sym.line_count} lines")
        if sym.complexity > 1:
            details.append(f"cx={sym.complexity}")
        lines.append(f"    {', '.join(details)}")

        # Actionable guidance
        warnings = []
        if sym.line_count > 500:
            warnings.append("grep-only (too large to read)")
        if cross_files > 5:
            warnings.append("high blast radius — changes here break many files")
        if sym.complexity > 100:
            warnings.append("refactor candidate — extreme complexity")
        elif sym.complexity > 50 and sym.line_count > 200:
            warnings.append("consider splitting — complex and large")
        if warnings:
            lines.append(f"    → {'; '.join(warnings)}")

    return "\n".join(lines)


def render_dependencies(graph: Tempo) -> str:
    """Render dependency analysis: circular imports and layer structure."""
    lines = ["Dependency Analysis:", ""]

    cycles = graph.detect_circular_imports()
    if cycles:
        lines.append(f"CIRCULAR IMPORTS ({len(cycles)} cycles):")
        for i, cycle in enumerate(cycles[:10], 1):
            chain = " → ".join(c.rsplit("/", 1)[-1] for c in cycle)
            lines.append(f"  {i}. {chain}")
        if len(cycles) > 10:
            lines.append(f"  ... +{len(cycles) - 10} more")
        lines.append("")
    else:
        lines.append("No circular imports detected.")
        lines.append("")

    layers = graph.dependency_layers()
    lines.append(f"Dependency layers ({len(layers)} levels):")
    for i, layer in enumerate(layers):
        if len(layer) > 10:
            shown = ", ".join(f.rsplit("/", 1)[-1] for f in layer[:8])
            lines.append(f"  Layer {i}: {shown} ... +{len(layer) - 8} more ({len(layer)} total)")
        else:
            shown = ", ".join(f.rsplit("/", 1)[-1] for f in layer)
            lines.append(f"  Layer {i}: {shown}")

    return "\n".join(lines)


def render_architecture(graph: Tempo) -> str:
    """High-level architecture view: modules, their roles, and inter-module dependencies."""
    # Group files into modules (top-level directories)
    modules: dict[str, list[str]] = {}
    for fp in sorted(graph.files):
        parts = fp.split("/")
        module = parts[0] if len(parts) > 1 else "."
        modules.setdefault(module, []).append(fp)

    # Build inter-module import edges
    import_edges: dict[str, dict[str, int]] = {}  # source_module → {target_module: count}
    for edge in graph.edges:
        if edge.kind == EdgeKind.IMPORTS:
            src_parts = edge.source_id.split("/")
            tgt_parts = edge.target_id.split("/")
            src_mod = src_parts[0] if len(src_parts) > 1 else "."
            tgt_mod = tgt_parts[0] if len(tgt_parts) > 1 else "."
            if src_mod != tgt_mod:
                import_edges.setdefault(src_mod, {})
                import_edges[src_mod][tgt_mod] = import_edges[src_mod].get(tgt_mod, 0) + 1

    # Cross-module call edges
    call_edges: dict[str, dict[str, int]] = {}
    for edge in graph.edges:
        if edge.kind in (EdgeKind.CALLS, EdgeKind.RENDERS):
            src_file = graph.symbols[edge.source_id].file_path if edge.source_id in graph.symbols else ""
            tgt_file = graph.symbols[edge.target_id].file_path if edge.target_id in graph.symbols else ""
            if src_file and tgt_file:
                src_parts = src_file.split("/")
                tgt_parts = tgt_file.split("/")
                src_mod = src_parts[0] if len(src_parts) > 1 else "."
                tgt_mod = tgt_parts[0] if len(tgt_parts) > 1 else "."
                if src_mod != tgt_mod:
                    call_edges.setdefault(src_mod, {})
                    call_edges[src_mod][tgt_mod] = call_edges[src_mod].get(tgt_mod, 0) + 1

    lines = ["Architecture Overview:", ""]

    # Module summary
    lines.append("Modules:")
    for mod in sorted(modules, key=lambda m: -len(modules[m])):
        files = modules[mod]
        # Gather stats for this module
        total_lines = sum(graph.files[f].line_count for f in files if f in graph.files)
        sym_count = sum(len(graph.files[f].symbols) for f in files if f in graph.files)
        langs = set(graph.files[f].language.value for f in files if f in graph.files)
        lang_str = ", ".join(sorted(langs))
        lines.append(f"  {mod}/ — {len(files)} files, {sym_count} symbols, {total_lines:,} lines [{lang_str}]")

        # Top exported symbols in this module
        top_syms = []
        for f in files:
            for sid in graph.files.get(f, FileInfo("", Language.UNKNOWN, 0, 0)).symbols:
                sym = graph.symbols.get(sid)
                if sym and sym.exported and sym.parent_id is None:
                    top_syms.append(sym)
        top_syms.sort(key=lambda s: -s.line_count)
        if top_syms:
            shown = top_syms[:5]
            names = ", ".join(f"{s.name}({s.kind.value})" for s in shown)
            extra = f" +{len(top_syms) - 5}" if len(top_syms) > 5 else ""
            lines.append(f"    exports: {names}{extra}")
    lines.append("")

    # Inter-module dependencies
    all_deps = {}
    for src in set(list(import_edges.keys()) + list(call_edges.keys())):
        targets: dict[str, int] = {}
        for tgt, n in import_edges.get(src, {}).items():
            targets[tgt] = targets.get(tgt, 0) + n
        for tgt, n in call_edges.get(src, {}).items():
            targets[tgt] = targets.get(tgt, 0) + n
        if targets:
            all_deps[src] = targets

    if all_deps:
        lines.append("Module dependencies:")
        for src in sorted(all_deps, key=lambda s: -sum(all_deps[s].values())):
            targets = sorted(all_deps[src].items(), key=lambda x: -x[1])
            dep_str = ", ".join(f"{tgt}({n})" for tgt, n in targets[:6])
            extra = f" +{len(targets) - 6}" if len(targets) > 6 else ""
            lines.append(f"  {src} → {dep_str}{extra}")
    else:
        lines.append("No cross-module dependencies detected.")

    return "\n".join(lines)


_DISPATCH_PATTERNS = ("handle_", "on_", "test_", "route", "command", "hook", "middleware", "plugin")


def _dead_code_confidence(sym: Symbol, graph: Tempo) -> int:
    """Score 0-100: how confident we are this symbol is truly dead."""
    score = 0

    # No callers at all (even same-file) — strong signal
    if not graph.callers_of(sym.id):
        score += 30

    # Parent file has no importers — nothing depends on this file
    if not graph.importers_of(sym.file_path):
        score += 25

    # No render relationships
    if not graph.renderers_of(sym.id):
        score += 10

    # Larger symbols are higher-value cleanup targets
    if sym.line_count > 50:
        score += 15

    # Name looks like a dispatch target — likely wired at runtime
    name_lower = sym.name.lower()
    if any(name_lower.startswith(p) or p in name_lower for p in _DISPATCH_PATTERNS):
        score -= 20

    # Plugin entrypoint: function named 'run' in a plugins/ directory (called via dynamic dispatch)
    if sym.name == "run" and "/plugins/" in sym.file_path:
        score -= 30

    # Tauri command — invoked via IPC from frontend, static analysis can't see callers
    if sym.kind == SymbolKind.COMMAND:
        score -= 40

    # Has docstring — suggests intentional public API
    if sym.doc:
        score -= 15

    # Parent is not cross-file referenced — parent already dead, this is redundant noise
    if sym.parent_id and not graph.callers_of(sym.parent_id):
        score -= 10

    # Single-component file — likely lazy-loaded, lower confidence
    if sym.kind == SymbolKind.COMPONENT and sym.exported:
        siblings = [
            s for s in graph.symbols.values()
            if s.file_path == sym.file_path and s.kind == SymbolKind.COMPONENT
        ]
        if len(siblings) == 1:
            score -= 20

    return max(0, min(100, score))


def render_dead_code(graph: Tempo, *, max_symbols: int = 50, max_tokens: int = 8000) -> str:
    """Find exported symbols that appear to be unused (never referenced externally)."""
    dead = graph.find_dead_code()
    if not dead:
        return "No dead code detected — all exported symbols are referenced."

    # Score each symbol
    scored = [(sym, _dead_code_confidence(sym, graph)) for sym in dead]
    scored.sort(key=lambda x: (-x[1], -x[0].line_count))

    high = [(s, c) for s, c in scored if c >= 70]
    medium = [(s, c) for s, c in scored if 40 <= c < 70]
    low = [(s, c) for s, c in scored if c < 40]

    lines = [f"Potential dead code ({len(dead)} symbols):", ""]
    total_lines = 0

    for label, tier in [("HIGH CONFIDENCE (safe to remove)", high),
                        ("MEDIUM CONFIDENCE (review before removing)", medium),
                        ("LOW CONFIDENCE (likely false positives)", low)]:
        if not tier:
            continue
        shown = tier[:max_symbols]
        lines.append(f"{label}:")
        lines.append("")
        by_file: dict[str, list[tuple[Symbol, int]]] = {}
        for sym, conf in shown:
            by_file.setdefault(sym.file_path, []).append((sym, conf))
        for fp in sorted(by_file):
            lines.append(f"  {fp}:")
            for sym, conf in sorted(by_file[fp], key=lambda x: x[0].line_start):
                lc = sym.line_count
                total_lines += lc
                lines.append(f"    {sym.kind.value} {sym.qualified_name} (L{sym.line_start}-{sym.line_end}, {lc} lines) [confidence: {conf}]")
            lines.append("")

    lines.append(f"Total: {len(dead)} unused symbols (~{total_lines:,} lines shown)")
    lines.append(f"  {len(high)} high, {len(medium)} medium, {len(low)} low confidence")

    result = "\n".join(lines)
    if max_tokens and count_tokens(result) > max_tokens:
        truncated: list[str] = []
        token_count = 0
        for line in lines:
            lt = count_tokens(line)
            if token_count + lt > max_tokens - 50:
                truncated.append(f"\n... truncated ({len(dead)} total, use max_tokens to see more)")
                break
            truncated.append(line)
            token_count += lt
        return "\n".join(truncated)
    return result


def _extract_name_from_question(question: str) -> str:
    """Extract the likely symbol/file name from a natural language question."""
    q = question.strip().rstrip("?")
    for prefix in (
        "where is", "find", "locate", "definition of",
        "what calls", "who calls", "who uses", "callers of", "references to",
        "what does", "dependencies of", "callees of",
        "who imports", "what imports", "imported by", "what files import",
        "what renders", "show me",
        "what implements", "what extends", "subtypes of", "subclasses of",
        "what inherits from", "who inherits",
    ):
        if q.lower().startswith(prefix):
            q = q[len(prefix):].strip()
            break
    for suffix in ("defined", "called", "used", "rendered", "call", "import",
                    "class", "function", "method", "module", "interface", "type"):
        if q.lower().endswith(suffix):
            q = q[:-(len(suffix))].strip()
    # Strip articles and noise words
    for article in ("the", "a", "an"):
        if q.lower().startswith(article + " "):
            q = q[len(article) + 1:]
    q = q.strip("'\"` ")
    return q



def render_prepare(graph: Tempo, task: str, max_tokens: int = 6000, task_type: str = "",
                   baseline_predicted_files: list[str] | None = None) -> str:
    """Batch context preparation: overview + focus + hotspots + diff in one token-budgeted output.

    If L2 learned insights exist for task_type, includes extra modes (dead code, quality)
    that the data shows are helpful for that task category.
    """
    from .git import changed_files_unstaged, is_git_repo
    sections: list[str] = []
    token_count = 0

    # Load L2 insights to customize which supplemental modes to include
    l2_best_modes: set[str] = set()
    try:
        from tempo.plugins.learn import TaskMemory
        mem = TaskMemory(str(Path(graph.root).resolve()))
        if task_type:
            rec = mem.get_recommendation(task_type)
            if rec and rec.get("sample_size", 0) >= 2:
                l2_best_modes = set(rec["best_modes"])
    except Exception:
        pass

    s = graph.stats
    sections.append(f"## Repo: {s['files']} files, {s['symbols']} symbols, {s['total_lines']:,} lines")
    token_count += 20

    if _is_change_localization(task, task_type):
        # Change-localization path: per-keyword focus + breadth filter + selective overview.
        # Bench evidence (n=111, Phase 5.26): +9-12% F1 improvement vs raw task passthrough.
        # Key differences from general path:
        #   - Extract code-symbol keywords from PR title/branch name (not raw task string)
        #   - Run separate focus per keyword (up to 3) → better symbol targeting
        #   - Breadth filter: skip keyword if focus returns >10 files (too generic)
        #   - Selective overview: only inject when keywords=[] (vague task, no code signal)
        #   - "Keywords found but focus failed" → inject nothing (model uses training knowledge)
        keywords = _extract_cl_keywords(task)
        focus_budget = max_tokens // 2
        focus_parts: list[str] = []
        path_fallback_files: list[str] = []  # collected when symbol focus is too broad
        # Skip keywords shorter than 4 chars before taking the top-3 cap.
        # Short tokens can't trigger path fallback (which requires len>=4) and rarely match
        # specific symbols. This prevents "req" (len=3) from blocking "resp" (len=4).
        effective_keywords = [kw for kw in keywords if len(kw) >= 4][:3]
        for kw in effective_keywords:
            focused = render_focused(graph, kw, max_tokens=focus_budget)
            no_match = not focused or "No symbols matching" in focused or "No exact match" in focused
            if not no_match:
                kw_files = _extract_focus_files(focused)
            too_broad = not no_match and len(kw_files) > 10
            if no_match or too_broad:
                # No symbol match OR too broad — try path-based fallback.
                # Handles: (a) directory/module keywords (e.g. "demo" → demos/),
                # (b) keyword is a module name but not a symbol (e.g. "config" → sanic/config.py).
                # Evidence: tornado "demo" → 15 symbol files (skipped) → path match → demos/ (2-4 files).
                # Evidence: sanic "config" → 0 symbol matches → path → sanic/config.py (correct).
                if len(kw) >= 4 and not path_fallback_files:
                    kw_lower = kw.lower()
                    path_hits = [
                        sym.file_path for sym in graph.symbols.values()
                        if kw_lower in sym.file_path.lower()
                    ]
                    unique_paths = sorted(set(path_hits))
                    if unique_paths:
                        path_fallback_files = unique_paths[:8]
                    elif "_" in kw:
                        # Snake_case keyword: try individual components as path keywords.
                        # E.g. "config_from_object" → try "config" → sanic/config.py.
                        # Only use if <= 5 paths (conservative to avoid false positives).
                        for part in kw.split("_"):
                            if len(part) >= 4:
                                part_lower = part.lower()
                                part_hits = sorted(set(
                                    sym.file_path for sym in graph.symbols.values()
                                    if part_lower in sym.file_path.lower()
                                ))
                                if part_hits and len(part_hits) <= 5:
                                    path_fallback_files = part_hits
                                    break
                continue
            focus_parts.append(focused)

        if focus_parts:
            for fp in focus_parts:
                sections.append(fp)
                token_count += count_tokens(fp)
            key_files = _extract_focus_files("\n\n".join(focus_parts), task_keywords=keywords)
            # Adaptive gating: if baseline already predicts the key files, skip injection.
            # Bench evidence (Phase 5.27, n=83): overlap>=0.5 → model already knows the files
            # → skip saves tokens with 0 F1 loss; overlap<0.5 → avg +0.30–0.45 F1 gain per case.
            if baseline_predicted_files is not None and key_files:
                overlap = len(set(baseline_predicted_files) & set(key_files)) / len(key_files)
                if overlap >= 0.5:
                    return sections[0]  # model already predicts the key files — skip injection
            if key_files:
                kf_section = "KEY FILES REFERENCED ABOVE:\n" + "\n".join(f"  {f}" for f in key_files[:5])
                sections.append(kf_section)
                token_count += count_tokens(kf_section)
        elif path_fallback_files:
            # All symbol searches were too broad, but path matching found specific files.
            # E.g. "demo" fails symbol focus (15+ matches) but path match → demos/ directory.
            kf_section = "KEY FILES (path match):\n" + "\n".join(f"  {f}" for f in path_fallback_files[:5])
            sections.append(kf_section)
            token_count += count_tokens(kf_section)
        elif not keywords:
            # Truly vague task (no keywords extracted) — overview provides structure, UNLESS the
            # task is a docs-named branch (docs-javascript, docs/#4574, readme-fix, etc.).
            # Docs branches often change both docs AND code; overview focuses the model on generic
            # structure (conf.py, README) instead of the actual code paths changed.
            # Evidence: flask "docs-javascript" overview → F1 0.556→0.154 (-0.402 delta).
            # Without overview, model uses training knowledge → ties (~0 delta, not regression).
            # Low-baseline repos (requests, django) use trunk-branch tasks for overview, not doc branches.
            if not _is_docs_branch_task(task):
                overview_fallback = render_overview(graph)
                sections.append(overview_fallback)
                token_count += count_tokens(overview_fallback)
        # else: keywords exist but focus found nothing → inject nothing; model uses training knowledge.
        # Evidence: overview hurts high-baseline repos (pydantic -40%, starlette -11%) when
        # focus fails on non-empty keywords.

    else:
        # General coding task path: multi-token fuzzy search + always-overview fallback.
        # Suitable for: "add login feature", "fix broken test", "explain this function".
        focus_output = render_focused(graph, task, max_tokens=int(max_tokens * 0.6))

        # Large-scope heuristic: bench data shows focused context hurts for 8+ file tasks.
        _BROAD_SCOPE_MARKERS = {"all", "every", "entire", "throughout", "global", "across",
                                "everywhere", "whole", "each"}
        _BROAD_ACTION_MARKERS = {"refactor", "migrate", "update", "port", "convert", "rename",
                                 "replace", "remove", "delete", "rewrite"}
        _task_set = set(task.lower().split())
        _is_large_scope = bool(
            _task_set & _BROAD_SCOPE_MARKERS
            and _task_set & _BROAD_ACTION_MARKERS
        )
        if _is_large_scope:
            sections.append(
                "⚠ LARGE SCOPE: task appears to span many files. "
                "Bench data shows focused context hurts F1 for 8+ file changes. "
                "Use `overview` for orientation; skip focused context injection."
            )
            token_count += 25

        _no_match = not focus_output or "No symbols matching" in focus_output or "No exact match" in focus_output
        if _no_match and not _is_large_scope:
            overview_fallback = render_overview(graph)
            sections.append(overview_fallback)
            token_count += count_tokens(overview_fallback)
        else:
            sections.append(focus_output)
            token_count += count_tokens(focus_output)

            if not _no_match:
                key_files = _extract_focus_files(focus_output)
                if key_files:
                    if len(key_files) > 10:
                        sections.append(
                            "⚠ BROAD MATCH: query matched many files — results may include "
                            "loosely related code. Consider re-querying with a more specific "
                            "symbol name or function for a tighter focus."
                        )
                        token_count += 20
                    kf_section = "KEY FILES REFERENCED ABOVE:\n" + "\n".join(f"  {f}" for f in key_files[:5])
                    sections.append(kf_section)
                    token_count += count_tokens(kf_section)

    hotspot_budget = int(max_tokens * 0.15)
    if token_count < max_tokens - 100:
        hotspot_output = render_hotspots(graph, top_n=5)
        ht = count_tokens(hotspot_output)
        if ht <= hotspot_budget:
            sections.append("\n## Hotspots (top 5 riskiest)")
            sections.append(hotspot_output)
            token_count += ht + 10

    # L2-guided: include dead code analysis if learned to be useful for this task_type
    if token_count < max_tokens - 200 and "dead" in l2_best_modes:
        dead_budget = min(1500, max_tokens - token_count - 100)
        dead_output = render_dead_code(graph, max_symbols=10, max_tokens=dead_budget)
        dt = count_tokens(dead_output)
        if dt <= dead_budget:
            sections.append("\n## Dead Code (L2: relevant for this task type)")
            sections.append(dead_output)
            token_count += dt + 10

    # Skills: include coding conventions for feature/refactor tasks so agents write convention-native code
    if token_count < max_tokens - 200 and task_type in ("feature", "refactor"):
        skills_budget = min(800, max_tokens - token_count - 100)
        skills_output = render_skills(graph, max_tokens=skills_budget)
        st = count_tokens(skills_output)
        if st <= skills_budget + 50:
            sections.append("\n## Coding Conventions (follow these when writing new code)")
            sections.append(skills_output)
            token_count += st + 10

    if token_count < max_tokens - 100:
        is_change = any(w in task.lower() for w in (
            "fix", "bug", "change", "modify", "update", "refactor", "add", "remove",
            "delete", "rename", "move", "migrate",
        ))
        if is_change or task_type in ("debug", "refactor", "feature"):
            repo_path = str(Path(graph.root).resolve())
            if is_git_repo(repo_path):
                try:
                    changed = changed_files_unstaged(repo_path)
                    if changed:
                        diff_budget = max_tokens - token_count
                        diff_output = render_diff_context(graph, changed, max_tokens=diff_budget)
                        dt = count_tokens(diff_output)
                        if dt <= diff_budget + 100:  # allow small overflow
                            sections.append(f"\n## Uncommitted changes ({len(changed)} files)")
                            sections.append(diff_output)
                            token_count += dt + 10
                except Exception:
                    pass

    sections.append("---\nCall report_feedback after using this context to improve future recommendations.")
    return "\n\n".join(sections)


def render_skills(graph: Tempo, query: str = "", *, max_tokens: int = 4000) -> str:
    """Return a catalog of coding patterns and conventions for this codebase.

    Useful for agents that need to write new code following project conventions
    (naming, plugin structure, module roles, repeated idioms).
    """
    try:
        from tempo.plugins.skills import get_patterns
        return get_patterns(graph, query=query, max_tokens=max_tokens)
    except ImportError:
        return "Skills plugin not available. Install tempo package."
