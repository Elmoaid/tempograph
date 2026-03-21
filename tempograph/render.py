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

    # Recently active: top commit-hot SOURCE files (excludes docs/config/tests)
    _SRC_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
                 ".rb", ".cpp", ".c", ".h", ".cs", ".swift", ".kt", ".php"}
    try:
        from .git import file_commit_counts as _file_commit_counts
        _commit_counts = _file_commit_counts(graph.root)
        _active = sorted(
            [(fp, c) for fp, c in _commit_counts.items()
             if fp in graph.files and not _is_test_file(fp)
             and Path(fp).suffix in _SRC_EXTS],
            key=lambda x: -x[1],
        )[:3]
        if _active:
            segs_list = [fp.replace("\\", "/").split("/") for fp, _ in _active]
            short = ["/".join(s[-2:]) if len(s) > 1 else s[-1] for s in segs_list]
            _act_str = ", ".join(f"{sh} ({c})" for sh, (_, c) in zip(short, _active))
            lines.append("")
            lines.append(f"recently active: {_act_str}")
    except Exception:
        _commit_counts = {}

    # High-risk files: high-churn source files with no matching test file.
    # Actively changing code with no test coverage — most likely to introduce regressions.
    # Only shown when test files exist (otherwise the whole project lacks tests).
    _test_fps_for_risk = {fp for fp in graph.files if _is_test_file(fp)}
    if _commit_counts and _test_fps_for_risk:
        _high_risk = sorted(
            [
                (fp, c) for fp, c in _commit_counts.items()
                if fp in graph.files
                and not _is_test_file(fp)
                and c >= 5
                and graph.files[fp].symbols
                and Path(fp).suffix in _SRC_EXTS
                and not any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _test_fps_for_risk)
            ],
            key=lambda x: -x[1],
        )
        if _high_risk:
            _hr_parts = [f"{fp.rsplit('/', 1)[-1]} ({c})" for fp, c in _high_risk[:3]]
            lines.append(f"high risk (no tests): {', '.join(_hr_parts)}")

    # Hot symbols: top 3 source functions by unique cross-file caller files.
    # Helps agents immediately identify the highest-traffic API surfaces.
    _hot_syms: list[tuple[int, str, str]] = []  # (unique_caller_files, name, file)
    for sym in graph.symbols.values():
        if sym.kind.value not in ("function", "method") or _is_test_file(sym.file_path):
            continue
        cross_files = {
            c.file_path for c in graph.callers_of(sym.id)
            if c.file_path != sym.file_path and not _is_test_file(c.file_path)
        }
        if len(cross_files) >= 3:
            _hot_syms.append((len(cross_files), sym.qualified_name, sym.file_path))
    if _hot_syms:
        _hot_syms.sort(key=lambda x: -x[0])
        _hot_parts = [f"{name} ({n})" for n, name, _ in _hot_syms[:3]]
        lines.append("")
        lines.append(f"hot symbols: {', '.join(_hot_parts)}")

    # Function size distribution: tiny/small/medium/large/huge counts across source functions.
    # One-line style signal — "large: 3" means 3 functions >50L each; agents know to grep not read.
    _fn_sizes = {"tiny": 0, "small": 0, "medium": 0, "large": 0, "huge": 0}
    for sym in graph.symbols.values():
        if sym.kind.value not in ("function", "method") or _is_test_file(sym.file_path):
            continue
        lc = sym.line_count
        if lc <= 5:
            _fn_sizes["tiny"] += 1
        elif lc <= 20:
            _fn_sizes["small"] += 1
        elif lc <= 50:
            _fn_sizes["medium"] += 1
        elif lc <= 150:
            _fn_sizes["large"] += 1
        else:
            _fn_sizes["huge"] += 1
    _fn_total = sum(_fn_sizes.values())
    if _fn_total >= 5:
        _fs_parts = [f"{k}: {v}" for k, v in _fn_sizes.items() if v > 0]
        lines.append(f"fn sizes: {', '.join(_fs_parts)}")

    # Top imported: files most imported by other source files — true infrastructure files.
    # Distinct from hot symbols (call frequency) and hot files (commit count).
    _importer_counts: dict[str, int] = {}
    for fp in graph.files:
        if _is_test_file(fp):
            continue
        importers = [
            i for i in graph.importers_of(fp)
            if i in graph.files and not _is_test_file(i) and i != fp
        ]
        if importers:
            _importer_counts[fp] = len(set(importers))
    if _importer_counts:
        _top_imported = sorted(_importer_counts.items(), key=lambda x: -x[1])[:3]
        _min_importers = 3
        _top_imported = [(fp, n) for fp, n in _top_imported if n >= _min_importers]
        if _top_imported:
            _ti_parts = [
                f"{fp.rsplit('/', 1)[-1]} ({n})" for fp, n in _top_imported
            ]
            lines.append("")
            lines.append(f"top imported: {', '.join(_ti_parts)}")

    # Test coverage ratio: source files with a matching test file (name-pattern match).
    # Signals overall project health — agents use this to identify undertested areas.
    # Only count code files with symbols (excludes docs, config, markdown).
    _src_fps = [fp for fp in graph.files if not _is_test_file(fp) and graph.files[fp].symbols]
    _test_fps = {fp for fp in graph.files if _is_test_file(fp)}
    if _src_fps and _test_fps:
        _covered = sum(
            1 for fp in _src_fps
            if any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _test_fps)
        )
        _test_pct = int(_covered / len(_src_fps) * 100)
        lines.append(f"test coverage: {_covered}/{len(_src_fps)} source files ({_test_pct}%)")

    # API surface health: exported symbols with 0 cross-file callers = potentially dead API.
    # Quick fraction for agents: "35% of exports unused → dead code problem worth investigating."
    # Only shown when >= 5 exported non-test symbols exist (avoids noise on tiny repos).
    _exported_src = [
        sym for sym in graph.symbols.values()
        if sym.exported and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method", "class", "interface", "variable", "constant")
    ]
    if len(_exported_src) >= 5:
        _unused_exp = [
            sym for sym in _exported_src
            if not any(c.file_path != sym.file_path for c in graph.callers_of(sym.id))
        ]
        _unused_pct = int(len(_unused_exp) / len(_exported_src) * 100)
        _ap_line = f"API surface: {len(_exported_src)} exported"
        if _unused_exp:
            _ap_line += f", {len(_unused_exp)} unused ({_unused_pct}%)"
        lines.append(_ap_line)

    # Potentially unused modules: source files with 0 source importers AND no test coverage.
    # Flags entire floating modules that nothing depends on and nothing tests — either dead
    # features or undiscovered entry points agents should investigate.
    # Only shown when project has 10+ source files AND has tests (otherwise too noisy).
    _ENTRY_BASENAMES = {
        "__init__.py", "__main__.py", "main.py", "app.py", "manage.py",
        "cli.py", "server.py", "wsgi.py", "asgi.py", "run.py", "start.py",
        "index.js", "index.ts", "index.tsx", "main.ts", "main.tsx", "app.ts",
        "main.go", "main.rs", "lib.rs", "mod.rs",  # Rust crate/module roots
        "main.swift", "Program.cs",
    }
    if len(_src_fps) >= 10 and _test_fps:
        _unused_modules: list[str] = []
        for _fp in _src_fps:
            _basename = _fp.rsplit("/", 1)[-1]
            if _basename in _ENTRY_BASENAMES:
                continue  # skip known entry points and package markers
            # Skip TypeScript type-only files (.types.ts, .d.ts) — we skip `import type`
            # statements, so these always appear as having 0 importers (false positive).
            if _basename.endswith(".types.ts") or _basename.endswith(".d.ts"):
                continue
            if len(graph.files[_fp].symbols) < 3:
                continue  # too minimal to flag
            _src_importers_fp = [i for i in graph.importers_of(_fp) if not _is_test_file(i)]
            _test_importers_fp = [i for i in graph.importers_of(_fp) if _is_test_file(i)]
            _test_callers_fp = any(
                _is_test_file(c.file_path)
                for sid in graph.files[_fp].symbols
                for c in graph.callers_of(sid)
            )
            if not _src_importers_fp and not _test_importers_fp and not _test_callers_fp:
                _unused_modules.append(_fp)
        if len(_unused_modules) >= 2:
            _parts = _fp.rsplit("/", 2)
            def _short_fp(fp: str) -> str:
                parts = fp.rsplit("/", 2)
                return "/".join(parts[-2:]) if len(parts) >= 2 else fp
            _um_names = [_short_fp(fp) for fp in _unused_modules[:4]]
            _um_str = ", ".join(_um_names)
            if len(_unused_modules) > 4:
                _um_str += f" +{len(_unused_modules) - 4} more"
            lines.append(f"potentially unused ({len(_unused_modules)}): {_um_str}")

    # Tech debt markers: TODO/FIXME/HACK/XXX comment counts in source files.
    # Quick signal for known issues, shortcuts, and incomplete work.
    # Only source-code files with symbols; capped to avoid I/O cost on huge repos.
    import re as _re  # noqa: PLC0415
    # Only match markers that appear in comment lines (after # or //).
    # Avoids false positives from regex strings, test fixtures, and scanner code itself.
    _TD_PAT = _re.compile(r'(?:#|//)[^\n]*\b(TODO|FIXME|HACK|XXX)\b')
    _td_counts: dict[str, int] = {}
    _td_file_count = 0
    for _fp in _src_fps[:200]:  # cap at 200 to keep I/O bounded
        if Path(_fp).suffix not in _SRC_EXTS:
            continue
        try:
            _content = (Path(graph.root) / _fp).read_text(errors="replace")
            _matches = _TD_PAT.findall(_content)
            if _matches:
                _td_file_count += 1
                for _m in _matches:
                    _td_counts[_m] = _td_counts.get(_m, 0) + 1
        except Exception:
            pass
    if _td_counts:
        _td_total = sum(_td_counts.values())
        if _td_total >= 3:
            _td_parts = [
                f"{_td_counts[k]} {k}s"
                for k in ("TODO", "FIXME", "HACK", "XXX")
                if _td_counts.get(k, 0) > 0
            ]
            lines.append(f"tech debt: {_td_total} markers in {_td_file_count} files ({', '.join(_td_parts)})")

    # Deepest import chain: longest path from any source file through import edges.
    # High depth = deep coupling = hard to refactor. Only shown when depth >= 5.
    # Uses iterative DFS on the import graph; stops early at depth 12 to stay fast.
    # Skips test files and considers only source files with symbols.
    _MAX_CHAIN = 12
    _best_chain: list[str] = []
    _import_adj: dict[str, list[str]] = {}  # file → files it imports
    for _edge in graph.edges:
        if _edge.kind == EdgeKind.IMPORTS:
            # IMPORTS edges use file paths directly as source_id/target_id
            _src_fp = _edge.source_id
            _tgt_fp = _edge.target_id
            if (
                _src_fp in graph.files and _tgt_fp in graph.files
                and not _is_test_file(_src_fp) and not _is_test_file(_tgt_fp)
            ):
                _import_adj.setdefault(_src_fp, [])
                if _tgt_fp not in _import_adj[_src_fp]:
                    _import_adj[_src_fp].append(_tgt_fp)
    # DFS from each file with symbols, find longest non-cyclic chain
    _src_imp_fps = [fp for fp in _import_adj if fp in graph.files and graph.files[fp].symbols]
    for _start in _src_imp_fps[:100]:  # cap to 100 starts for performance
        # Iterative DFS: (file, chain)
        _stack = [(_start, [_start])]
        while _stack:
            _cur, _chain = _stack.pop()
            if len(_chain) > len(_best_chain):
                _best_chain = _chain
            if len(_chain) >= _MAX_CHAIN:
                continue
            for _nxt in _import_adj.get(_cur, []):
                if _nxt not in _chain:  # avoid cycles
                    _stack.append((_nxt, _chain + [_nxt]))
    if len(_best_chain) >= 5:
        def _short(fp: str) -> str:
            parts = fp.split("/")
            return "/".join(parts[-2:]) if len(parts) > 2 else fp
        _chain_names = [_short(fp) for fp in _best_chain]
        lines.append(f"dep depth: {len(_best_chain)} ({' → '.join(_chain_names[:5])}{'...' if len(_best_chain) > 5 else ''})")

    # Module structure -- just the shape, no noisy import counts
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


def render_symbols(graph: Tempo, *, max_tokens: int = 8000) -> str:
    """Full symbol index — signatures, locations, relationships.

    max_tokens: cap output to prevent context overflow (default 8000; 0 = no limit)"""
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
                rendered_files = sum(1 for l in lines if l.startswith("──"))
                remaining_files = len(by_file) - rendered_files
                remaining_symbols = sum(len(v) for k, v in by_file.items() if k >= file_path)
                lines.append(f"... and {remaining_symbols} more symbols in {remaining_files} files (increase max_tokens to see all)")
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
    Primary-match files (from direct ● symbol lines) are also exempt from hub penalty:
    the file that contains the directly-searched symbol must always be considered relevant.
    Evidence: fastify reply-not-found — setNotFoundHandler is in fastify.js (14/25 = 56%
    mentions → hub), but fastify.js IS a changed file. Hub penalty incorrectly demoted it.
    """
    import re
    pattern = r'\b(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|rb)\b'
    all_paths = re.findall(pattern, focus_output)
    freq: dict[str, int] = {}
    for p in all_paths:
        freq[p] = freq.get(p, 0) + 1

    # Primary-match files: files referenced on direct symbol lines (● symbol — file:N-M).
    # These are the files that actually contain the searched symbol → never apply hub penalty.
    primary_files: set[str] = set()
    for line in focus_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("●"):
            m = re.search(r'—\s+(\S+\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|rb)):\d', stripped)
            if m:
                primary_files.add(m.group(1))

    kw_lower = [k.lower() for k in (task_keywords or [])]
    total_mentions = sum(freq.values())

    def _is_hub(path: str, stem: str) -> bool:
        if path in primary_files:
            return False  # Never penalize directly-matched symbol files
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



from .keywords import _extract_cl_keywords  # noqa: F401 (re-exported for backward compat)

def _is_docs_branch_task(task: str) -> bool:
    """Return True when the PR is a docs/version/infra PR that should skip overview injection.

    Three cases where overview injection misleads (changes files outside the code graph):
    1. Docs branches: docs-javascript, docs/#4574, readme-fix → README/conf.py changes
    2. Version-release branches: version-0.1.5, v1.2.3 → changelog/pyproject changes
    3. Pure-infra body: "Pin versions of dependencies" → requirements.txt changes

    Evidence: flask "docs-javascript" overview → F1 0.556→0.154 (-0.402 delta).
              fastapi "fix-10" (Pin versions) overview → F1 0.500→0.286 (-0.214 delta).
              fastapi "docs/edit-timer-in-middleware" (Merge branch into docs/X) → overview injected
              because "Merge branch" format wasn't matched — overview misleads to docs_src/ files.
    """
    import re
    # "Merge pull request #N from user/branch-name" format
    m = re.search(r'Merge pull request \S+ from [^/\s]+/(\S+)', task)
    if not m:
        # "Merge branch 'name' into target" format — also check for docs branch in target
        m2 = re.search(r"[Mm]erge branch '([^']+)' into ([^\s]+)", task)
        if m2:
            # The TARGET branch (after "into") may be the docs branch
            target = m2.group(2).lower().strip("'\"")
            source = m2.group(1).lower().strip("'\"")
            # Use the more specific branch (the one that isn't 'master'/'main')
            branch = target if target not in ("master", "main", "develop") else source
        else:
            return False
    else:
        branch = m.group(1).lower()
    branch = branch.strip("'\"")  # strip any trailing quote chars
    leaf = branch.split('/')[-1]
    # Docs branches: "docs" as a hyphen/underscore/slash-separated component anywhere.
    # Matches: docs-javascript, auth-docs, 5309-docs-viewset (DRF-style mid-name).
    # Does NOT match: docstring-update (component is "docstring", not "docs").
    _DOC_COMPONENT = re.compile(r'(?:^|[-_/])docs?(?:[-_/]|$)')
    if (bool(_DOC_COMPONENT.search(leaf))
            or any(re.search(r'(?:^|[-_/])' + kw + r'(?:[-_/]|$)', leaf)
                   for kw in ("readme", "changelog", "documentation"))
            or branch.startswith("docs/")
            or branch.startswith("doc/")):
        return True
    # Version-release branches: "version-X.Y.Z", "v1.2.3", "release-1.0" in branch leaf.
    # These change pyproject.toml / CHANGELOG, not source files.
    if (re.search(r'(?:^|[-_/])v?\d+\.\d+', leaf)
            or re.search(r'(?:^|[-_/])version(?:[-_/]|$)', leaf)
            or re.search(r'(?:^|[-_/])release(?:[-_/]|$)', leaf)):
        return True
    # Pure-infrastructure body: ticket-ref branch where body ONLY contains infra words.
    # "fix-10" + "Pin versions of dependencies and bump version" → requirements.txt.
    # Keyword extraction already returns [] for these; overview adds no code-graph signal.
    _is_ticket = bool(
        re.match(r'^(?:issue|ticket|bug|patch|pr|fix|hotfix)[-_]?\d+', leaf)
        or re.match(r'^\d+[-_]', leaf)
    )
    if _is_ticket:
        body = task[task.find('\n') + 1:].strip() if '\n' in task else ''
        _INFRA_ONLY = frozenset({
            "pin", "pinned", "pinning", "bump", "bumped", "bumping",
            "version", "versions", "versioning", "release", "releases",
            "dependency", "dependencies", "deps", "package", "packages",
            "upgrade", "upgraded", "upgrading", "downgrade", "downgraded",
            "install", "installation", "requirements", "freeze", "frozen",
            "and", "the", "a", "an", "of", "to", "for", "in", "with", "from",
        })
        body_words = set(re.findall(r'[a-zA-Z]+', body.lower()))
        if body_words and body_words <= _INFRA_ONLY:
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


def _cochange_orbit(
    repo_root: str, seed_files: list[str], seen_files: set[str], n: int = 3
) -> list[tuple[str, float, int]]:
    """Return top N co-change partners for seed_files not already in seen_files.

    Returns (file_path, decayed_score, days_since_last_cochange).
    Uses recency-weighted scoring: recent co-changes outrank stale ones.
    Empty list if repo has no git history or no meaningful coupling.
    """
    try:
        from .git import cochange_matrix_recency, is_git_repo
        if not repo_root or not is_git_repo(repo_root):
            return []
        matrix = cochange_matrix_recency(repo_root, n_commits=200)
    except Exception:
        return []

    seed_set = set(seed_files)
    partners: dict[str, tuple[float, int]] = {}
    for sf in seed_files:
        for partner, score, days in matrix.get(sf, []):
            if partner not in seed_set and partner not in seen_files:
                if partner not in partners or score > partners[partner][0]:
                    partners[partner] = (score, days)

    return [(fp, score, days) for fp, (score, days) in
            sorted(partners.items(), key=lambda x: -x[1][0])[:n]]


def _find_orbit_seeds(
    graph: "Tempo",
    query_tokens: list[str],
    orbit_pairs: list[tuple[str, float, int]],
) -> list[tuple["Symbol", float]]:
    """Find the best-matching symbol in each orbit file by query token overlap.

    Returns (symbol, coupling_freq) pairs — up to 1 per orbit file, max 3 total.
    Only files with at least one symbol matching a query token are included.
    This is how git-coupled files that aren't in the call graph become BFS seeds."""
    if not query_tokens:
        return []

    results: list[tuple["Symbol", float]] = []
    for fp, freq, _days in orbit_pairs:
        syms = graph.symbols_in_file(fp)
        best_sym: "Symbol | None" = None
        best_score = 0
        for sym in syms:
            name_lower = sym.name.lower()
            score = sum(1 for tok in query_tokens if tok in name_lower)
            if score > best_score:
                best_score = score
                best_sym = sym
        if best_sym and best_score > 0:
            results.append((best_sym, freq))

    return results[:3]


def _collect_seeds(
    graph: Tempo, query: str
) -> tuple[list[Symbol], set[str], list[str]]:
    """Tokenize query, search for seed symbols, apply quality gate.

    Returns (seeds, seed_files, query_tokens).
    seeds is empty when there are no matches (caller should return early).
    seed_files contains paths of monolith-sized files (>= _MONOLITH_THRESHOLD lines)
    that host at least one seed symbol — used to bias BFS toward cross-file edges."""
    import re as _re
    # Split query tokens and expand CamelCase: "ReplyNotFound" → ["reply", "not", "found"]
    # so "reply" matches "test/internals/reply.test.js" even when query is CamelCase.
    _raw_tokens = _re.split(r'[^a-zA-Z0-9]+', query)
    _camel_tokens: list[str] = []
    for tok in _raw_tokens:
        parts = _re.sub(r'([A-Z][a-z]+)', r' \1', _re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', tok)).split()
        _camel_tokens.extend(parts if len(parts) > 1 else [tok])
    query_tokens = [t.lower() for t in _camel_tokens if len(t) >= 3]

    scored = graph.search_symbols_scored(query)
    if not scored:
        return [], set(), query_tokens

    # Quality gate: drop seeds with much lower scores than the best match
    top_score = scored[0][0]
    threshold = max(top_score * 0.3, 2.0)  # at least 30% of best, minimum 2.0
    all_seeds = [sym for score, sym in scored if score >= threshold][:10]
    # Prefer non-test seeds when available — test functions named "test_foo_bar"
    # match queries like "foo_bar" but they're not useful as BFS starting points.
    non_test_seeds = [sym for sym in all_seeds if not _is_test_file(sym.file_path)]
    seeds = non_test_seeds if non_test_seeds else all_seeds

    seed_files: set[str] = set()
    for s in seeds:
        fi = graph.files.get(s.file_path)
        if fi and fi.line_count >= _MONOLITH_THRESHOLD:
            seed_files.add(s.file_path)

    return seeds, seed_files, query_tokens


def _sym_importance(graph: "Tempo", sym: Symbol) -> int:
    """Structural importance score for BFS prioritisation.

    Score = cross_file_callers * 2 + (1 if exported) + (2 if hot_file).
    Uses raw ``_callers`` index to avoid creating Symbol objects per lookup."""
    caller_ids = graph._callers.get(sym.id, [])
    cross = sum(
        1 for cid in caller_ids
        if cid in graph.symbols and graph.symbols[cid].file_path != sym.file_path
    )
    return cross * 2 + (1 if sym.exported else 0) + (2 if sym.file_path in graph.hot_files else 0)


def _bfs_expand(
    graph: Tempo,
    seeds: list[Symbol],
    seed_files: set[str],
    secondary_seeds: list[Symbol] | None = None,
    max_depth: int | None = None,
) -> tuple[list[tuple[Symbol, int]], set[str]]:
    """BFS from seed symbols following caller/callee/child edges.

    Returns (ordered, seen_ids).
    ordered is the BFS traversal sequence with (symbol, depth) pairs.
    seen_ids is the full set of visited symbol IDs — callers use it for
    file-context deduplication so already-shown symbols are excluded.

    secondary_seeds are orbit-derived symbols injected at depth 1 — they
    expand the BFS into git-coupled files that aren't reachable via call edges.

    max_depth overrides the hot-seeds heuristic when provided (used by adaptive
    sparse-neighborhood expansion in render_focused).

    Within each depth level, candidates are sorted by structural importance so
    that when the 50-node cap truncates, hub nodes (many cross-file callers,
    exported, in hot files) survive over orphan/internal-only nodes."""
    _hot_seeds = any(s.file_path in graph.hot_files for s in seeds)
    _bfs_max_depth = max_depth if max_depth is not None else (4 if _hot_seeds else 3)
    seen_ids: set[str] = set()
    ordered: list[tuple[Symbol, int]] = []

    # Level-by-level BFS: collect all candidates per depth, sort by importance,
    # then expand.  This guarantees depth ordering (BFS topology unchanged)
    # while ensuring high-importance nodes within a depth are visited first.
    current_level: list[tuple[Symbol, int]] = [(s, 0) for s in seeds]
    if secondary_seeds:
        primary_ids = {s.id for s in seeds}
        for s in secondary_seeds:
            if s.id not in primary_ids:
                current_level.append((s, 1))

    # Pre-compute importance scores once per candidate (cache across levels)
    _imp_cache: dict[str, int] = {}

    def _cached_importance(sym: Symbol) -> int:
        score = _imp_cache.get(sym.id)
        if score is None:
            score = _sym_importance(graph, sym)
            _imp_cache[sym.id] = score
        return score

    while current_level and len(ordered) < 50:
        # Deduplicate and remove already-seen before sorting
        deduped: list[tuple[Symbol, int]] = []
        for sym, d in current_level:
            if sym.id not in seen_ids:
                deduped.append((sym, d))
                seen_ids.add(sym.id)

        # Sort within this batch: lower depth first (preserves BFS layering),
        # then cross-file nodes first, then by descending importance.
        deduped.sort(key=lambda pair: (
            pair[1],                                                  # depth ascending
            pair[0].file_path in seed_files if seed_files else True,  # cross-file first
            -_cached_importance(pair[0]),                              # importance descending
        ))

        next_level: list[tuple[Symbol, int]] = []
        for sym, depth in deduped:
            if len(ordered) >= 50:
                break
            ordered.append((sym, depth))

            if depth < _bfs_max_depth:
                caller_limit = 8 if depth == 0 else 5 if depth == 1 else 3
                callee_limit = 8 if depth == 0 else 5 if depth == 1 else 3
                _imp_key = lambda s: -_cached_importance(s)
                for caller in sorted(graph.callers_of(sym.id), key=_imp_key)[:caller_limit]:
                    if caller.id not in seen_ids:
                        next_level.append((caller, depth + 1))
                for callee in sorted(graph.callees_of(sym.id), key=_imp_key)[:callee_limit]:
                    if callee.id not in seen_ids:
                        next_level.append((callee, depth + 1))
                if depth < 2:
                    for child in graph.children_of(sym.id)[:5]:
                        if child.id not in seen_ids:
                            next_level.append((child, depth + 1))

        current_level = next_level

    return ordered, seen_ids


def _handle_overflow(
    lines: list[str],
    ordered: list[tuple[Symbol, int]],
    block: str,
    token_count: int,
    max_tokens: int,
    *,
    graph: "Tempo | None" = None,
    current_idx: int = 0,
) -> tuple[bool, int]:
    """Check whether adding block would exceed the token budget.

    Returns (should_break, block_tokens).
    When should_break is True a truncation message has already been appended
    to lines — the caller should stop the rendering loop immediately.

    If graph is provided, lists up to 5 high-importance (score >= 3) dropped
    symbol names so agents know which hub symbols were truncated."""
    block_tokens = count_tokens(block)
    if token_count + block_tokens > max_tokens:
        remaining = len(ordered) - len([l for l in lines if l and not l.startswith("...")])
        if remaining > 0:
            hi_names: list[tuple[int, str]] = []
            if graph is not None:
                for sym_d, _depth in ordered[current_idx:]:
                    imp = _sym_importance(graph, sym_d)
                    if imp >= 3:
                        hi_names.append((imp, sym_d.name))
                hi_names.sort(key=lambda x: -x[0])
            if hi_names:
                names = ", ".join(n for _, n in hi_names[:5])
                lines.append(f"... ({remaining} more symbols — high-importance: {names})")
            else:
                lines.append(f"... truncated ({remaining} more symbols)")
        return True, block_tokens
    return False, block_tokens



def _render_cochange_section(graph, seed_file_paths: list[str]) -> str:
    """Build the 'Co-changed with (basename):' section for render_focused."""
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from .git import cochange_pairs
        pairs = cochange_pairs(graph.root, seed_file_paths[0])
        if pairs:
            basename = Path(seed_file_paths[0]).name
            parts = [f"\nCo-changed with ({basename}):"]
            for p in pairs:
                parts.append(f"  \u2022 {p['path']} \u2014 {p['count']} commits together")
            return "\n".join(parts)
    except Exception:
        pass
    return ""


def _render_all_callers_section(
    graph, seeds: list, callsite_lines: dict, token_count: int = 0, max_tokens: int = 0
) -> str:
    """Complete callers section — all callers of seed symbols, grouped by file.

    Shows which source files call the seed symbol and where (line numbers).
    Useful for rename/refactor impact: agents see every call site at once.
    Test callers are excluded (already shown in Tests section).
    Triggered when total source callers >= 2. Capped at 5 files / 3 names each."""

    # Collect all source (non-test) callers across all seeds
    by_file: dict[str, list[tuple[str, str, int]]] = {}  # file → [(caller_name, caller_id, seed_id)]
    for seed in seeds:
        for caller in graph.callers_of(seed.id):
            if _is_test_file(caller.file_path):
                continue
            entry = (caller.name, caller.id, seed.id)
            by_file.setdefault(caller.file_path, []).append(entry)

    if not by_file:
        return ""

    total = sum(len(v) for v in by_file.values())
    if total < 2:
        return ""

    # Sort files by number of callers (most first), take top 5
    sorted_files = sorted(by_file.items(), key=lambda kv: -len(kv[1]))
    n_files_total = len(sorted_files)
    shown_files = sorted_files[:5]
    hidden_files = n_files_total - len(shown_files)

    parts = [f"\nCallers ({total} in {n_files_total} file{'s' if n_files_total != 1 else ''}):"]
    for fp, entries in shown_files:
        # De-duplicate by caller name, preserve order
        seen_names: set[str] = set()
        unique: list[tuple[str, str, str]] = []
        for name, cid, sid in entries:
            if name not in seen_names:
                seen_names.add(name)
                unique.append((name, cid, sid))

        shown = unique[:3]
        overflow = len(unique) - len(shown)

        caller_strs = []
        for name, cid, sid in shown:
            lines_list = callsite_lines.get((cid, sid), [])
            if lines_list:
                loc = f"[line {lines_list[0]}]" if len(lines_list) == 1 else f"[lines {', '.join(str(l) for l in lines_list[:2])}]"
                caller_strs.append(f"{name} {loc}")
            else:
                caller_strs.append(name)

        suffix = f", +{overflow} more" if overflow else ""
        parts.append(f"  {fp}: {', '.join(caller_strs)}{suffix}")

    if hidden_files:
        parts.append(f"  ... and {hidden_files} more file{'s' if hidden_files != 1 else ''}")

    return "\n".join(parts)


def _render_hot_callers_section(
    graph, seeds: list, token_count: int, max_tokens: int
) -> str:
    """Build the 'Hot callers:' section — callers of seed symbols in recently-modified files.

    Helps agents understand which callers are actively being changed, critical
    context for avoiding merge conflicts and understanding in-progress work.
    Does NOT filter by seen_ids: even callers already shown in BFS benefit from
    the focused summary (the inline [hot] tag is easy to miss in long output)."""
    if token_count > max_tokens - 80 or not graph.hot_files:
        return ""

    hot_entries: list[tuple[str, str, int]] = []  # (caller_name, file_path, line)
    seen_files_dedup: set[str] = set()
    for seed in seeds:
        for caller in graph.callers_of(seed.id):
            if caller.file_path in graph.hot_files and caller.file_path not in seen_files_dedup:
                hot_entries.append((caller.name, caller.file_path, caller.line_start))
                seen_files_dedup.add(caller.file_path)

    if not hot_entries:
        return ""

    # Sort by file path for stable output, cap at 5
    hot_entries.sort(key=lambda e: e[1])
    hot_entries = hot_entries[:5]

    parts = ["\nHot callers:"]
    for name, fp, line in hot_entries:
        basename = Path(fp).name
        parts.append(f"  {name} ({basename}:{line}) \u2014 last seen in hot file")
    return "\n".join(parts)


def _render_dependency_files_section(
    graph, ordered: list[tuple], seen_files: set[str], token_count: int, max_tokens: int
) -> str:
    """Build the 'Depends on:' section — outgoing dependency files for seed symbols.

    Collects callees of depth-0 (seed) symbols, groups by file, and shows
    up to 3 callee names per file. Skipped when fewer than 2 dependency files
    remain after filtering the seed's own file."""
    if token_count > max_tokens - 50:
        return ""

    seed_files = {sym.file_path for sym, depth in ordered if depth == 0}
    dep_map: dict[str, list[str]] = {}
    for sym, depth in ordered:
        if depth != 0:
            continue
        for callee in graph.callees_of(sym.id):
            fp = callee.file_path
            if fp in seed_files:
                continue
            if fp not in dep_map:
                dep_map[fp] = []
            if callee.name not in dep_map[fp]:
                dep_map[fp].append(callee.name)

    if len(dep_map) < 2:
        return ""

    sorted_deps = sorted(dep_map.items(), key=lambda x: -len(x[1]))[:6]
    parts = ["\nDepends on:"]
    for fp, names in sorted_deps:
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f", +{len(names) - 3} more"
        parts.append(f"  {fp} ({shown})")
    return "\n".join(parts)


def _render_recent_changes_section(graph, seed_file_paths: list[str]) -> str:
    """Build the 'Recent changes (basename):' section for render_focused."""
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from .git import recent_file_commits
        primary_file = seed_file_paths[0]
        commits = recent_file_commits(graph.root, primary_file)
        if commits:
            basename = Path(primary_file).name
            parts = [f"\nRecent changes ({basename}):"]
            for c in commits:
                parts.append(f"  \u2022 {c['days_ago']}d ago: {c['message']}")
            return "\n".join(parts)
    except Exception:
        pass
    return ""


def _render_volatility_section(graph, seed_file_paths: list[str], token_count: int, max_tokens: int) -> str:
    """Build the 'Volatile:' section for render_focused."""
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from .git import file_commit_counts
        churn = file_commit_counts(graph.root)
        _VOLATILE_THRESHOLD = 10
        volatile = [(fp, churn.get(fp, 0)) for fp in seed_file_paths
                     if churn.get(fp, 0) >= _VOLATILE_THRESHOLD]
        if volatile:
            parts = [f"{fp} ({count}/200 commits)" for fp, count in volatile]
            return f"\nVolatile: {', '.join(parts)} \u2014 high-churn file(s), re-read before editing"
    except Exception:
        pass
    return ""


def _render_cochange_orbit_section(graph, seed_file_paths: list[str], seen_files: set[str],
                                    token_count: int, max_tokens: int) -> str:
    """Build the 'Co-change orbit:' section for render_focused."""
    orbit = _cochange_orbit(graph.root, seed_file_paths, seen_files)
    if not orbit or token_count >= max_tokens - 80:
        return ""

    def _recency_label(days: int) -> str:
        if days < 45:
            return "recent"
        elif days < 120:
            return "aging"
        return "stale"

    orbit_parts = [f"{fp} ({score:.0%} {_recency_label(days)})" for fp, score, days in orbit]
    return (f"\nCo-change orbit: {', '.join(orbit_parts)}\n"
            "  (files that historically change with these \u2014 check if your change affects them)")


def _render_blast_risk_section(graph, ordered: list, token_count: int, max_tokens: int) -> str:
    """Build the 'High impact:' blast risk badge for render_focused."""
    _BLAST_FILE_THRESHOLD = 5
    _blast_hits: list[tuple] = []
    for _bs, _bd in ordered[:5]:
        if _bd != 0:
            continue
        _ext_files = {c.file_path for c in graph.callers_of(_bs.id) if c.file_path != _bs.file_path}
        if len(_ext_files) > _BLAST_FILE_THRESHOLD:
            _blast_hits.append((_bs, len(_ext_files)))
    if _blast_hits and token_count < max_tokens - 60:
        _blast_hits.sort(key=lambda x: -x[1])
        _top_sym, _top_count = _blast_hits[0]
        return f"\nHigh impact: {_top_count} files depend on {_top_sym.qualified_name} \u2014 run blast mode before editing"
    return ""


def _render_related_files_section(graph, ordered: list, seen_files: set[str]) -> str:
    """Build the 'Related files:' section for render_focused."""
    related = _find_related_files(graph, [s for s, _ in ordered[:10]])
    unseen = related - seen_files
    if not unseen:
        return ""
    parts = ["\nRelated files:"]
    for fp in sorted(unseen)[:10]:
        fi = graph.files.get(fp)
        if fi:
            tag = " [grep-only]" if fi.line_count > 500 else ""
            parts.append(f"  {fp} ({fi.line_count} lines){tag}")
    return "\n".join(parts)


def _render_file_context_section(graph, seen_files: set[str], seen_ids: set[str],
                                  token_count: int, max_tokens: int) -> tuple[str, int]:
    """Build the 'Also in these files:' section for render_focused.
    Returns (section_text, token_cost)."""
    file_context: list[str] = []
    for fp in sorted(seen_files):
        fi = graph.files.get(fp)
        if not fi or len(fi.symbols) < 3:
            continue
        file_syms = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols and sid not in seen_ids]
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
            return ctx_block, ctx_tokens
    return "", 0


def _render_monolith_section(graph, ordered: list, token_count: int, max_tokens: int) -> tuple[str, int]:
    """Build monolith neighborhood sections for render_focused.
    Returns (section_text, token_cost)."""
    parts: list[str] = []
    total_tokens = 0
    for sym, depth in ordered:
        if depth > 0:
            break
        fi = graph.files.get(sym.file_path)
        if not fi or fi.line_count < _MONOLITH_THRESHOLD:
            continue
        neighborhood = _monolith_neighborhood(graph, sym)
        if neighborhood:
            nb_block = "\n".join(neighborhood)
            nb_tokens = count_tokens(nb_block)
            if token_count + total_tokens + nb_tokens <= max_tokens:
                parts.append("")
                parts.extend(neighborhood)
                total_tokens += nb_tokens
    if parts:
        return "\n".join(parts), total_tokens
    return "", 0


def _build_symbol_block_lines(
    sym: "Symbol",
    depth: int,
    orbit_note: str,
    graph: "Tempo",
    query_tokens: list[str],
    staleness_cache: dict,
    callsite_lines: "dict[tuple[str,str], list[int]] | None" = None,
) -> list[str]:
    """Render one BFS symbol into display lines (header + annotations).

    Returns a list of lines; caller joins them and checks token overflow."""
    indent = "  " * depth if depth > 0 else ""
    prefix = ["●", "  →", "    ·", "      "][min(depth, 3)]
    loc = f"{sym.file_path}:{sym.line_start}-{sym.line_end}"
    # Blast annotation for depth-0 seed: number of unique files that call this symbol.
    # Gives agents immediate risk context — "[blast: 7 files]" = 7 files need review.
    _blast_ann = ""
    _age_ann = ""
    if depth == 0:
        _blast_files = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}
        if len(_blast_files) >= 3:
            _blast_ann = f" [blast: {len(_blast_files)} files]"
        elif len(_blast_files) == 1:
            # Exactly 1 external caller file → tightly owned by that file.
            # Agents can safely change this without reviewing other files.
            _sole_file = next(iter(_blast_files))
            _blast_ann = f" [owned by: {_sole_file.rsplit('/', 1)[-1]}]"
        # Symbol-level age: when was this specific function last changed?
        # Uses git log -L for per-line precision; falls back to file-level.
        # Skipped for symbols changed < 8 days ago (not actionable — treat as "fresh").
        try:
            from .git import symbol_last_modified_days as _sld  # noqa: PLC0415
            _days = _sld(graph.root, sym.file_path, sym.line_start)
            if _days is not None and _days >= 8:
                if _days >= 365:
                    _age_ann = " [age: 1y+]"
                elif _days >= 30:
                    _age_ann = f" [age: {_days // 30}m]"
                else:
                    _age_ann = f" [age: {_days}d]"
        except Exception:
            pass
    block_lines = [f"{prefix} {sym.kind.value} {sym.qualified_name}{_blast_ann}{_age_ann} — {loc}{orbit_note}"]
    # Container annotation for methods: show parent class with caller count.
    # Helps agents understand the class context of the focused method.
    if depth == 0 and sym.kind.value == "method" and "::" in sym.id:
        _name_part = sym.id.split("::", 1)[1]
        if "." in _name_part:
            _class_id = sym.id.rsplit(".", 1)[0]  # "file.py::ClassName"
            _class_sym = graph.symbols.get(_class_id)
            if _class_sym:
                _c_callers = len(graph.callers_of(_class_id))
                _c_methods = len([c for c in graph.children_of(_class_id) if c.kind.value == "method"])
                _c_ann = f"{_c_callers} callers" if _c_callers else "no callers"
                block_lines.append(f"{indent}  container: {_class_sym.kind.value} {_class_sym.name} ({_c_ann}, {_c_methods} methods)")
    # Recent commit messages: last 2 commits that touched the seed symbol's file.
    # Gives agents instant "why was this last changed" context without running git log.
    if depth == 0 and graph.root:
        try:
            from .git import recent_file_commits as _rfc  # noqa: PLC0415
            _commits = _rfc(graph.root, sym.file_path, n=2)
            if _commits:
                _commit_parts = [f"{c['days_ago']}d \"{c['message']}\"" for c in _commits]
                block_lines.append(f"{indent}  recent: {', '.join(_commit_parts)}")
        except Exception:
            pass
    # Callee drift: seed is >=30d old but calls things changed in the last 14d.
    # Flags potential "stale wrapper" — function may not reflect its dependency changes.
    # Uses file-level age for callees (fast — no per-line git log overhead).
    if depth == 0 and graph.root:
        try:
            from .git import symbol_last_modified_days as _sld_cd  # noqa: PLC0415
            from .git import file_last_modified_days as _fld_cd    # noqa: PLC0415
            _seed_days = _sld_cd(graph.root, sym.file_path, sym.line_start)
            if _seed_days is not None and _seed_days >= 30:
                _callees_cd = graph.callees_of(sym.id)
                _drifted: list[tuple[int, str]] = []
                for _c in _callees_cd[:15]:  # cap to avoid subprocess spam
                    if _c.file_path == sym.file_path:
                        continue  # same-file callees usually updated together
                    _c_days = _fld_cd(graph.root, _c.file_path)
                    if _c_days is not None and _c_days < 14:
                        _drifted.append((_c_days, _c.name))
                if _drifted:
                    _drifted.sort()  # most recently changed first
                    _drift_strs = [f"{n} ({d}d)" for d, n in _drifted[:3]]
                    _drift_overflow = f" +{len(_drifted) - 3} more" if len(_drifted) > 3 else ""
                    block_lines.append(
                        f"{indent}  ⚠ callee drift: {len(_drifted)} dep(s) changed after your last edit"
                        f" — {', '.join(_drift_strs)}{_drift_overflow}"
                    )
        except Exception:
            pass
    if sym.signature and depth < 2:
        block_lines.append(f"{indent}  sig: {sym.signature[:150]}")
    if sym.doc and depth == 0:
        block_lines.append(f"{indent}  doc: {sym.doc}")
    if depth <= 1:
        warnings = []
        if sym.line_count > 500:
            warnings.append(f"LARGE ({sym.line_count} lines — use grep, don't read)")
        if sym.complexity > 50:
            warnings.append(f"HIGH COMPLEXITY (cx={sym.complexity})")
        if depth == 0 and not sym.exported and not graph.callers_of(sym.id):
            if _dead_code_confidence(sym, graph) >= 40:
                warnings.append("POSSIBLY DEAD — 0 callers, not exported (run dead_code mode to confirm)")
        # Test-only callers: symbol has callers but ALL are test files.
        # Production code never calls this — likely test helper or fixture, not real API.
        if depth == 0:
            _callers = graph.callers_of(sym.id)
            if len(_callers) >= 2 and all(_is_test_file(c.file_path) for c in _callers):
                warnings.append("TEST-ONLY CALLERS — not called from production code")
        # Circular import: if the seed's file is in a circular import chain, flag it.
        # Agents need to know this to avoid making the cycle worse or getting confused
        # about why re-imports behave unexpectedly.
        if depth == 0 and graph.root:
            _cycles = graph.detect_circular_imports()
            for _cycle in _cycles:
                if sym.file_path in _cycle:
                    _names = [fp.rsplit("/", 1)[-1] for fp in _cycle]
                    warnings.append(f"CIRCULAR IMPORT — {' → '.join(_names)}")
                    break
        if warnings:
            block_lines.append(f"{indent}  ⚠ {', '.join(warnings)}")

        def _caller_priority(c: "Symbol") -> int:
            path_lower = c.file_path.lower()
            return 0 if query_tokens and any(tok in path_lower for tok in query_tokens) else 1

        from .git import file_last_modified_days as _fld  # noqa: PLC0415

        def _stale_annotation(file_path: str) -> str:
            if file_path not in staleness_cache:
                staleness_cache[file_path] = _fld(graph.root, file_path)
            days = staleness_cache[file_path]
            if days is None or days <= 30:
                return ""
            if days > 180:
                return " [stale: 6m+]"
            return f" [stale: {days}d]"

        callers = graph.callers_of(sym.id)
        if callers:
            callers_sorted = sorted(callers, key=_caller_priority)
            kw_callers = [c for c in callers_sorted if _caller_priority(c) == 0]
            other_callers = [c for c in callers_sorted if _caller_priority(c) != 0]
            hot_other = [c for c in other_callers if c.file_path in graph.hot_files]
            cold_other = [c for c in other_callers if c.file_path not in graph.hot_files]
            max_other = 3 if kw_callers else (8 if depth == 0 else 5)
            shown_other = (hot_other + cold_other)[:max_other]
            shown_callers = kw_callers + shown_other
            shown_count = len(kw_callers) + max_other
            caller_strs = []
            for c in shown_callers:
                _cl = (callsite_lines or {}).get((c.id, sym.id), [])
                if len(_cl) == 1:
                    _line_ann = f" [line {_cl[0]}]"
                elif len(_cl) >= 2:
                    _line_ann = f" [lines {_cl[0]}, {_cl[1]}]"
                else:
                    _line_ann = ""
                if c.file_path in graph.hot_files:
                    caller_strs.append(f"{c.qualified_name}{_line_ann} [hot]")
                else:
                    caller_strs.append(c.qualified_name + _line_ann + _stale_annotation(c.file_path))
            block_lines.append(f"{indent}  called by: {', '.join(caller_strs)}")
            if len(callers) > shown_count:
                block_lines[-1] += f" (+{len(callers) - shown_count} more)"
        callees = graph.callees_of(sym.id)
        if callees:
            shown = 8 if depth == 0 else 5
            hot_callees = [c for c in callees if c.file_path in graph.hot_files]
            cold_callees = [c for c in callees if c.file_path not in graph.hot_files]
            ordered_callees = (hot_callees + cold_callees)[:shown]
            callee_strs = []
            for c in ordered_callees:
                _hot_ann = " [hot]" if c.file_path in graph.hot_files else ""
                _cb_ann = ""
                if depth == 0:
                    _cb_files = len({
                        cr.file_path for cr in graph.callers_of(c.id)
                        if cr.file_path != c.file_path
                    })
                    if _cb_files >= 3:
                        _cb_ann = f" [blast: {_cb_files}]"
                callee_strs.append(f"{c.qualified_name}{_hot_ann}{_cb_ann}")
            block_lines.append(f"{indent}  calls: {', '.join(callee_strs)}")
            if len(callees) > shown:
                block_lines[-1] += f" (+{len(callees) - shown} more)"
        if depth == 0:
            children = graph.children_of(sym.id)
            if children:
                _child_strs = []
                for c in children[:10]:
                    _c_callers = len(graph.callers_of(c.id))
                    _c_ann = f" ({_c_callers})" if _c_callers >= 1 else ""
                    _child_strs.append(f"{c.kind.value[:4]} {c.name}{_c_ann}")
                block_lines.append(f"{indent}  contains: {', '.join(_child_strs)}")

            # Implementors: classes/traits that extend or implement this symbol.
            # Shown only for CLASS/INTERFACE seeds to surface the inheritance fanout.
            if sym.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE):
                _subtypes = graph.subtypes_of(sym.name)
                if _subtypes:
                    _sub_strs = [
                        f"{s.qualified_name} ({s.file_path.rsplit('/', 1)[-1]}:{s.line_start})"
                        for s in _subtypes[:8]
                    ]
                    _overflow_sub = len(_subtypes) - 8
                    _sub_line = f"{indent}  implementors: {', '.join(_sub_strs)}"
                    if _overflow_sub > 0:
                        _sub_line += f" (+{_overflow_sub} more)"
                    block_lines.append(_sub_line)

            # Similar functions: other functions sharing ≥2 callees with this seed.
            # Helps agents find related implementations that may need parallel changes.
            if sym.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                # Exclude class/type constructors — ubiquitous and create false positives
                _seed_callees = {
                    cid for cid in graph._callees.get(sym.id, [])
                    if cid in graph.symbols and graph.symbols[cid].kind.value not in ("class", "type_alias", "enum")
                }
                if len(_seed_callees) >= 2:
                    _overlap: dict[str, int] = {}
                    for _callee_id in _seed_callees:
                        for _sibling_id in graph._callers.get(_callee_id, []):
                            if _sibling_id != sym.id:
                                _sib = graph.symbols.get(_sibling_id)
                                if _sib and _sib.kind.value in ("function", "method"):
                                    _overlap[_sibling_id] = _overlap.get(_sibling_id, 0) + 1
                    _similar = [
                        (cnt, graph.symbols[sid])
                        for sid, cnt in _overlap.items()
                        if cnt >= 2 and sid in graph.symbols
                    ]
                    if _similar:
                        _similar.sort(key=lambda x: -x[0])
                        _sim_strs = [
                            f"{s.qualified_name} ({s.file_path.rsplit('/', 1)[-1]}:{s.line_start}, {n} shared)"
                            for n, s in _similar[:4]
                        ]
                        block_lines.append(f"{indent}  similar: {', '.join(_sim_strs)}")
    return block_lines


def render_focused(graph: Tempo, query: str, *, max_tokens: int = 4000) -> str:
    """Task-focused rendering with BFS graph traversal.
    Starts from search results, then follows call/render/import edges
    to build a connected subgraph relevant to the query.

    For monolith files (>1000 lines), adds intra-file neighborhood context
    and biases BFS toward cross-file edges to avoid getting trapped in one file.

    Supports multi-symbol focus via '|' separator: "authMiddleware | loginHandler"
    merges seeds from each query and runs a single combined BFS."""
    # Multi-symbol: split on '|', collect seeds for each query, merge results.
    _parts = [p.strip() for p in query.split("|") if p.strip()] if "|" in query else [query]
    if len(_parts) > 1:
        _seen_seed_ids: set[str] = set()
        seeds: list[Symbol] = []
        seed_files: set[str] = set()
        query_tokens: list[str] = []
        for _part in _parts:
            _s, _sf, _qt = _collect_seeds(graph, _part)
            for _sym in _s:
                if _sym.id not in _seen_seed_ids:
                    seeds.append(_sym)
                    _seen_seed_ids.add(_sym.id)
            seed_files |= _sf
            query_tokens.extend(t for t in _qt if t not in query_tokens)
        if not seeds:
            return _suggest_alternatives(graph, query) or f"No symbols matching '{query}'"
    else:
        seeds, seed_files, query_tokens = _collect_seeds(graph, query)
        if not seeds:
            return _suggest_alternatives(graph, query) or f"No symbols matching '{query}'"

    # Orbit-BFS seeding: inject symbols from git-coupled files as depth-1 seeds.
    # Call graph misses test files (they call INTO src, not tracked as callers).
    # Git orbit catches those — if tests always change with render.py, seed them too.
    orbit_seed_meta: dict[str, tuple[str, float]] = {}  # sym.id → (orbit_file, coupling_freq)
    orbit_secondary: list[Symbol] = []
    if graph.root:
        _primary_fps = [s.file_path for s in seeds]
        _primary_fp_set = {s.file_path for s in seeds}
        _orbit_pairs = _cochange_orbit(graph.root, _primary_fps, _primary_fp_set, n=3)
        for sym, freq in _find_orbit_seeds(graph, query_tokens, _orbit_pairs):
            orbit_secondary.append(sym)
            orbit_seed_meta[sym.id] = (sym.file_path, freq)

    # Determine initial BFS depth: 4 for hot seeds, 3 otherwise.
    _hot_seeds = any(s.file_path in graph.hot_files for s in seeds)
    _initial_depth = 4 if _hot_seeds else 3
    ordered, seen_ids = _bfs_expand(
        graph, seeds, seed_files, secondary_seeds=orbit_secondary or None,
        max_depth=_initial_depth,
    )

    # Sparse-neighborhood adaptive expansion: if BFS returned fewer than 20 nodes
    # and didn't saturate the 50-node cap, the neighborhood is small enough that
    # going one level deeper costs little and gives agents genuine extra context.
    _depth_extended = False
    _SPARSE_THRESHOLD = 20
    _MAX_ADAPTIVE_DEPTH = 5
    if len(ordered) < _SPARSE_THRESHOLD and _initial_depth < _MAX_ADAPTIVE_DEPTH:
        _ext_depth = _initial_depth + 1
        _ext_ordered, _ext_seen = _bfs_expand(
            graph, seeds, seed_files, secondary_seeds=orbit_secondary or None,
            max_depth=_ext_depth,
        )
        if len(_ext_ordered) > len(ordered):
            ordered, seen_ids = _ext_ordered, _ext_seen
            _depth_extended = True

    _focus_header = f"Focus: {' | '.join(_parts)}" if len(_parts) > 1 else f"Focus: {query}"
    if _depth_extended:
        _focus_header += f"  [depth +1 — sparse ({len(ordered)} nodes)]"
    lines = [_focus_header, ""]
    seen_files: set[str] = set()
    token_count = 0

    # Callsite line index: (caller_id, callee_id) → sorted non-zero line numbers.
    _callsite_lines: dict[tuple[str, str], list[int]] = {}
    for _edge in graph.edges:
        if _edge.kind is EdgeKind.CALLS and _edge.line > 0:
            _key = (_edge.source_id, _edge.target_id)
            _callsite_lines.setdefault(_key, []).append(_edge.line)
    for _key in _callsite_lines:
        _callsite_lines[_key] = sorted(set(_callsite_lines[_key]))

    _staleness_cache: dict[str, int | None] = {}

    for _sym_idx, (sym, depth) in enumerate(ordered):
        orbit_note = ""
        if sym.id in orbit_seed_meta and depth == 1:
            _orb_fp, _orb_freq = orbit_seed_meta[sym.id]
            orbit_note = f"  [orbit {_orb_freq:.0%}]"
        block_lines = _build_symbol_block_lines(sym, depth, orbit_note, graph, query_tokens, _staleness_cache, _callsite_lines)
        block = "\n".join(block_lines)
        should_break, block_tokens = _handle_overflow(lines, ordered, block, token_count, max_tokens, graph=graph, current_idx=_sym_idx)
        if should_break:
            break
        lines.append(block)
        token_count += block_tokens
        seen_files.add(sym.file_path)

    # File context: for each file touched, show key co-located symbols
    ctx_block, ctx_tokens = _render_file_context_section(graph, seen_files, seen_ids, token_count, max_tokens)
    if ctx_block:
        lines.append(ctx_block)
        token_count += ctx_tokens

    # Monolith neighborhood: for seed symbols in large files, show nearby symbols
    mono_block, mono_tokens = _render_monolith_section(graph, ordered, token_count, max_tokens)
    if mono_block:
        lines.append(mono_block)
        token_count += mono_tokens

    # Related files with size warnings
    related_section = _render_related_files_section(graph, ordered, seen_files)
    if related_section:
        lines.append(related_section)
        token_count += count_tokens(related_section)

    # Blast risk badge: count unique downstream files for seed symbols.
    blast_section = _render_blast_risk_section(graph, ordered, token_count, max_tokens)
    if blast_section:
        lines.append(blast_section)
        token_count += count_tokens(blast_section)

    # Co-change orbit: git history reveals which files change together with seed files.
    seed_file_paths = [s.file_path for s, d in ordered if d == 0]
    orbit_section = _render_cochange_orbit_section(graph, seed_file_paths, seen_files, token_count, max_tokens)
    if orbit_section:
        lines.append(orbit_section)
        token_count += count_tokens(orbit_section)

    # File volatility: flag seed files that are actively changing.
    volatile_section = _render_volatility_section(graph, seed_file_paths, token_count, max_tokens)
    if volatile_section:
        lines.append(volatile_section)
        token_count += count_tokens(volatile_section)

    # Recent changes: show last 3 commits for the primary seed file.
    recent_section = _render_recent_changes_section(graph, seed_file_paths)
    if recent_section:
        lines.append(recent_section)
        token_count += count_tokens(recent_section)

    # Co-change suggestions: which source files historically move with the primary file?
    cochange_section = _render_cochange_section(graph, seed_file_paths)
    if cochange_section:
        lines.append(cochange_section)
        token_count += count_tokens(cochange_section)

    # Test coverage section: which test files call the primary seed symbols?
    # Only consider depth-0 (seed) symbols to avoid noise from BFS expansion.
    if token_count < max_tokens - 40:
        _test_callers: dict[str, int] = {}
        _has_source_callers = False
        for _ts, _td in ordered:
            if _td != 0:
                continue
            for caller in graph.callers_of(_ts.id):
                if _is_test_file(caller.file_path):
                    _test_callers[caller.file_path] = _test_callers.get(caller.file_path, 0) + 1
                else:
                    _has_source_callers = True
        if _test_callers:
            _tcov = "\nTests:\n" + "\n".join(f"  {_tfp} ({_tcount} caller{'s' if _tcount != 1 else ''})" for _tfp, _tcount in sorted(_test_callers.items()))
            lines.append(_tcov)
            token_count += count_tokens(_tcov)
        elif _has_source_callers:
            lines.append("\nTests: none")
            token_count += 4

    # All callers: complete caller list grouped by file (for rename/refactor impact).
    _seed_syms = [sym for sym, d in ordered if d == 0]
    callers_section = _render_all_callers_section(graph, _seed_syms, _callsite_lines, token_count, max_tokens)
    if callers_section:
        lines.append(callers_section)
        token_count += count_tokens(callers_section)

    # Outgoing dependency files: what files do the seed symbols depend on?
    dep_section = _render_dependency_files_section(graph, ordered, seen_files, token_count, max_tokens)
    if dep_section:
        lines.append(dep_section)
        token_count += count_tokens(dep_section)

    # Hot callers: callers of seed symbols that live in recently-modified files.
    hot_section = _render_hot_callers_section(graph, _seed_syms, token_count, max_tokens)
    if hot_section:
        lines.append(hot_section)
        token_count += count_tokens(hot_section)

    # Similar symbols: functions/methods that share ≥2 callees with the seed.
    # Surfaces parallel implementations that likely need the same change.
    # Only applies to FUNCTION/METHOD seeds (not classes, not test files).
    if _seed_syms and token_count < max_tokens - 60:
        _prim_s = _seed_syms[0]
        if _prim_s.kind.value in ("function", "method") and not _is_test_file(_prim_s.file_path):
            # Exclude class/type constructors — they're shared ubiquitously and
            # create false positives (every handler that creates Symbol/Edge looks
            # "similar" to every other handler, which is meaningless noise).
            _seed_callees = {
                c.id for c in graph.callees_of(_prim_s.id)
                if c.kind.value not in ("class", "type_alias", "enum")
            }
            if len(_seed_callees) >= 2:
                _shared_counts: dict[str, int] = {}  # sym_id → shared callee count
                for _callee_id in _seed_callees:
                    for _caller in graph.callers_of(_callee_id):
                        if (
                            _caller.id != _prim_s.id
                            and not _is_test_file(_caller.file_path)
                            and _caller.kind.value in ("function", "method")
                        ):
                            _shared_counts[_caller.id] = _shared_counts.get(_caller.id, 0) + 1
                _similar = [
                    (cnt, graph.symbols[sid])
                    for sid, cnt in _shared_counts.items()
                    if cnt >= 2 and sid in graph.symbols
                ]
                if _similar:
                    _similar.sort(key=lambda x: -x[0])
                    _sim_parts = [
                        f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]}, {cnt} shared)"
                        for cnt, sym in _similar[:3]
                    ]
                    _sim_line = f"\nsimilar: {', '.join(_sim_parts)}"
                    lines.append(_sim_line)
                    token_count += count_tokens(_sim_line)

    # File siblings: other notable symbols in the primary seed's file.
    # Shows agents what else is in the file without requiring a blast query.
    # Only shown when token budget allows and siblings have callers (i.e. are live code).
    if _seed_syms and token_count < max_tokens - 80:
        _prim = _seed_syms[0]
        _fi = graph.files.get(_prim.file_path)
        if _fi and len(_fi.symbols) > 2:
            _prim_children = {c.id for c in graph.children_of(_prim.id)}
            _sibs: list[tuple[int, "Symbol"]] = []
            for _sid in _fi.symbols:
                if _sid == _prim.id or _sid in _prim_children or _sid not in graph.symbols:
                    continue
                _s = graph.symbols[_sid]
                _nc = len(graph.callers_of(_sid))
                if _nc >= 1:
                    _sibs.append((_nc, _s))
            _sibs.sort(key=lambda x: -x[0])
            if _sibs[:4]:
                _sb_parts = [f"{s.name} ({n})" for n, s in _sibs[:4]]
                lines.append(f"\nIn {_prim.file_path.rsplit('/', 1)[-1]}: {', '.join(_sb_parts)}")

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
        if file_path and Path(file_path).exists():
            parent_dir = Path(file_path).parent.name
            exclude_hint = (
                f" (e.g. '{parent_dir}' may be in your --exclude list)" if parent_dir else ""
            )
            return (
                f"⚠  '{file_path}' exists on disk but is not in the graph{exclude_hint}.\n"
                "   Re-run without --exclude to index it, or run "
                "`tempograph . --mode overview` to see what is currently indexed."
            )
        return f"File '{file_path}' not found."

    lines = [f"Blast radius for {file_path}:", ""]

    # Direct importers
    importers = graph.importers_of(file_path)
    if importers:
        lines.append(f"Directly imported by ({len(importers)}):")
        # Build index: importer_file → [caller symbol names that call INTO blast target]
        _target_sym_ids = set(fi.symbols)
        _importer_users: dict[str, list[str]] = {}
        for edge in graph.edges:
            if edge.kind is EdgeKind.CALLS and edge.target_id in _target_sym_ids:
                caller = graph.symbols.get(edge.source_id)
                if caller and caller.file_path in set(importers):
                    _importer_users.setdefault(caller.file_path, []).append(caller.name)
        _all_test_fps = {fp for fp in graph.files if _is_test_file(fp)}
        _src_importers = [imp for imp in importers if not _is_test_file(imp)]
        # Sort by call count descending: most-dependent callers appear first.
        # Ties broken by file path for stable output.
        _sorted_importers = sorted(
            importers, key=lambda imp: (-len(_importer_users.get(imp, [])), imp)
        )
        for imp in _sorted_importers:
            users = _importer_users.get(imp, [])
            unique_users = list(dict.fromkeys(users))[:3]  # deduplicate, cap at 3
            if unique_users:
                lines.append(f"  {imp} — used by: {', '.join(unique_users)}")
            else:
                lines.append(f"  {imp}")
        # Refactor safety: how many source importers have test coverage?
        if _src_importers and _all_test_fps:
            _tested = sum(
                1 for imp in _src_importers
                if any(imp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _all_test_fps)
            )
            _pct = int(_tested / len(_src_importers) * 100)
            lines.append(f"  refactor safety: {_tested}/{len(_src_importers)} caller files tested ({_pct}%)")
        # S51: recently active callers — importers modified in last 30 days
        if _src_importers and graph.root:
            try:
                from .git import file_last_modified_days as _fld  # noqa: PLC0415
                _recent = [(imp, _fld(graph.root, imp)) for imp in _src_importers]
                _recent = [(imp, d) for imp, d in _recent if d is not None and d <= 30]
                if len(_recent) >= 2:
                    _recent.sort(key=lambda x: x[1])
                    _rec_parts = [f"{imp.rsplit('/', 1)[-1]} ({d}d ago)" for imp, d in _recent[:3]]
                    lines.append(f"  recently active: {', '.join(_rec_parts)}")
            except Exception:
                pass
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

    # Transitive import cascade — BFS over import graph (cap: 5 levels, 200 files)
    if importers:
        _visited: set[str] = {file_path}
        _by_depth: dict[int, int] = {}
        _queue: list[tuple[str, int]] = [(fp, 1) for fp in importers]
        while _queue:
            fp, depth = _queue.pop(0)
            if fp in _visited or depth > 5:
                continue
            if sum(_by_depth.values()) >= 200:
                break
            _visited.add(fp)
            _by_depth[depth] = _by_depth.get(depth, 0) + 1
            _queue.extend((nfp, depth + 1) for nfp in graph.importers_of(fp) if nfp not in _visited)
        if len(_by_depth) > 1:  # only show when cascade goes beyond direct importers
            _total = sum(_by_depth.values())
            _max_d = max(_by_depth.keys())
            _depth_str = ", ".join(f"d{d}:{_by_depth[d]}" for d in sorted(_by_depth.keys()))
            lines.append(f"Transitive cascade: {_total} file(s) up to depth {_max_d} ({_depth_str})")
            lines.append("")

    # Test files: collect test files that directly call symbols in this file
    # or directly import this file. Shows agents exactly which tests to run.
    _blast_tests: dict[str, int] = {}  # test_file_path → symbol call count
    for sym in symbols:
        for caller in graph.callers_of(sym.id):
            if caller.file_path != file_path and _is_test_file(caller.file_path):
                _blast_tests[caller.file_path] = _blast_tests.get(caller.file_path, 0) + 1
    # Also include test files that directly import this file
    for imp in importers:
        if _is_test_file(imp):
            _blast_tests.setdefault(imp, 0)
    if _blast_tests:
        _sorted_tests = sorted(_blast_tests.items(), key=lambda x: -x[1])
        _shown = _sorted_tests[:5]
        lines.append(f"Tests to run ({len(_blast_tests)}):")
        for _tfp, _cnt in _shown:
            _lbl = f" ({_cnt} call{'s' if _cnt != 1 else ''})" if _cnt else ""
            lines.append(f"  {_tfp}{_lbl}")
        if len(_blast_tests) > 5:
            lines.append(f"  ... and {len(_blast_tests) - 5} more")
        lines.append("")

    # Co-change partners: files that historically changed together with this file.
    # Based on git history — not code structure. Helps agents know what else needs
    # updating when this file changes, even if there's no import/call relationship.
    _cc_orbit = _cochange_orbit(graph.root, [file_path], {file_path})
    if _cc_orbit:
        _cc_parts = []
        for _cc_fp, _cc_score, _cc_days in _cc_orbit[:4]:
            _cc_age = "recent" if _cc_days < 45 else ("aging" if _cc_days < 120 else "stale")
            _cc_parts.append(f"{_cc_fp.rsplit('/', 1)[-1]} ({_cc_score:.0%} {_cc_age})")
        lines.append(f"Co-change partners: {', '.join(_cc_parts)}")
        lines.append("")

    # Recent callers: importer files modified within the last 14 days.
    # Proxy for "blast radius may be growing" — recently touched importers signal
    # active coupling growth. Needs git repo; silently skipped otherwise.
    if importers and graph.root:
        try:
            from .git import file_last_modified_days as _fld  # noqa: PLC0415
            _recent_callers = [
                imp for imp in importers
                if not _is_test_file(imp)
                and (_fld(graph.root, imp) or 9999) <= 14
            ]
            if len(_recent_callers) >= 2:
                _rc_names = [fp.rsplit("/", 1)[-1] for fp in _recent_callers[:4]]
                _rc_str = ", ".join(_rc_names)
                if len(_recent_callers) > 4:
                    _rc_str += f" +{len(_recent_callers) - 4} more"
                lines.append(f"Recent callers (14d): {_rc_str} — blast radius growing")
                lines.append("")
        except Exception:
            pass

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

    # Load per-file velocity for annotation (graceful fallback if not a git repo).
    _vel: dict[str, float] = {}
    _churn_counts: dict[str, int] = {}
    if graph.root:
        try:
            from .git import file_change_velocity as _fcv, file_commit_counts as _fcc, is_git_repo as _igr
            _vel = _fcv(graph.root)
            if _igr(graph.root):
                _churn_counts = _fcc(graph.root)
        except Exception:
            pass

    lines.append("Changed files:")
    for fp in sorted(normalized):
        fi = graph.files[fp]
        _v = _vel.get(fp, 0.0)
        _vel_ann = f" [{_v:.0f}x/wk]" if _v >= 2.0 else ""
        # Blast count: how many external files import this changed file.
        # Inline signal — agents see risk per file without reading the importer list.
        _blast_n = len({i for i in graph.importers_of(fp) if i != fp and i in graph.files})
        _blast_ann = f" [blast: {_blast_n}]" if _blast_n >= 2 else ""
        lines.append(f"  {fp} ({fi.line_count} lines, {len(fi.symbols)} symbols){_vel_ann}{_blast_ann}")
        # Change risk score: callers (blast radius) + churn (commit frequency)
        _callers_count = sum(
            len({c.file_path for c in graph.callers_of(sid) if c.file_path != fp})
            for sid in fi.symbols if sid in graph.symbols
        )
        _churn = _churn_counts.get(fp, 0)
        _risk = _callers_count + _churn * 2
        if _risk >= 12:
            lines.append(f"  change risk: HIGH (callers: {_callers_count}, churn: {_churn})")
        elif _risk >= 6:
            lines.append(f"  change risk: MEDIUM (callers: {_callers_count}, churn: {_churn})")
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
    # Tests to run: test files that directly call symbols from the changed file(s).
    # Sorted by call count — most-covered test files first.
    if token_count < max_tokens - 60:
        _test_caller_counts: dict[str, int] = {}
        for sym in affected_symbols:
            for caller in graph.callers_of(sym.id):
                if _is_test_file(caller.file_path):
                    _test_caller_counts[caller.file_path] = _test_caller_counts.get(caller.file_path, 0) + 1
        if _test_caller_counts:
            _sorted_tests = sorted(_test_caller_counts.items(), key=lambda x: -x[1])
            _test_parts = [f"{fp.rsplit('/', 1)[-1]} ({n})" for fp, n in _sorted_tests[:5]]
            _overflow = len(_test_caller_counts) - 5
            _tests_line = f"Tests to run ({len(_test_caller_counts)}): {', '.join(_test_parts)}"
            if _overflow > 0:
                _tests_line += f" +{_overflow} more"
            lines.append(_tests_line)
            lines.append("")
            token_count = count_tokens("\n".join(lines))

    # Co-change partners missing from diff.
    # Warns the agent when a file that historically co-changes with a changed file is absent —
    # classic sign of an incomplete changeset (e.g. touched auth.py but not session.py).
    if graph.root and token_count < max_tokens - 80:
        try:
            from .git import cochange_pairs as _cpairs
            _missing: dict[str, int] = {}  # partner_path → count (deduped)
            for fp in sorted(normalized):
                for p in _cpairs(graph.root, fp, n=5, min_count=5):
                    partner = p["path"]
                    if partner not in normalized and partner not in _missing and partner in graph.files:
                        _missing[partner] = p["count"]
            if _missing:
                _sorted_missing = sorted(_missing.items(), key=lambda x: -x[1])
                _warn_parts = [f"{p.rsplit('/', 1)[-1]} ({c}x)" for p, c in _sorted_missing[:3]]
                _overflow_warn = len(_sorted_missing) - 3
                _warn_line = f"Co-change warning: {', '.join(_warn_parts)} often change with this diff — missing from changeset"
                if _overflow_warn > 0:
                    _warn_line += f" (+{_overflow_warn} more)"
                lines.append(_warn_line)
                lines.append("")
                token_count = count_tokens("\n".join(lines))
        except Exception:
            pass

    if max_tokens - token_count > 500:
        lines.append("Key symbols in changed files:")
        for sym in affected_symbols:
            if sym.kind in (SymbolKind.VARIABLE, SymbolKind.CONSTANT):
                continue
            if sym.parent_id and sym.kind == SymbolKind.FUNCTION:
                continue
            # Cross-file caller count: tells agents how widely this symbol is used.
            # Changes to high-caller symbols need broader review + testing.
            _cross_callers = len({c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path})
            _caller_ann = f" [callers: {_cross_callers}]" if _cross_callers > 0 else ""
            entry = f"  {sym.kind.value} {sym.qualified_name}{_caller_ann} L{sym.line_start}-{sym.line_end}"
            if sym.signature:
                entry += f"\n    {sym.signature[:120]}"
            entry_tokens = count_tokens(entry)
            if token_count + entry_tokens > max_tokens:
                lines.append(f"  ... truncated ({len(affected_symbols)} total)")
                break
            lines.append(entry)
            token_count += entry_tokens

    return "\n".join(lines)


def _classify_file(path: str) -> str:
    """Classify a file as 'test', 'config', or 'source' by filename patterns."""
    import os
    name = os.path.basename(path)
    if (name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py" or ".test." in name or ".spec." in name):
        return "test"
    _CONFIG_NAMES = {
        "setup.py", "setup.cfg", "pyproject.toml", "package.json",
        "package-lock.json", "yarn.lock", "Makefile", "makefile",
        "CMakeLists.txt", "tox.ini", "pytest.ini", ".flake8",
        "requirements.txt", "Cargo.toml", "go.mod", "pom.xml",
        "build.gradle", "Gemfile", "tsconfig.json",
    }
    if name in _CONFIG_NAMES or ".config." in name:
        return "config"
    return "source"


def _file_blast_info(graph: Tempo, file_path: str) -> dict[str, int]:
    """Count external dependent files categorized as source/test/config.

    Returns dict with keys: "total", "source", "test", "config".
    This is the file-level blast radius with category context: agents care whether
    their change breaks prod code (source), test infrastructure (test), or build
    tooling (config). Same total, different risk profile.
    """
    fi = graph.files.get(file_path)
    if not fi:
        return {"total": 0, "source": 0, "test": 0, "config": 0}
    dependent_files: set[str] = set()
    # Direct importers
    for imp in graph.importers_of(file_path):
        if imp != file_path:
            dependent_files.add(imp)
    # Files that call symbols in this file from outside
    for sym_id in fi.symbols:
        if sym_id not in graph.symbols:
            continue
        for caller in graph.callers_of(sym_id):
            if caller.file_path and caller.file_path != file_path:
                dependent_files.add(caller.file_path)
    counts: dict[str, int] = {"source": 0, "test": 0, "config": 0}
    for f in dependent_files:
        counts[_classify_file(f)] += 1
    counts["total"] = len(dependent_files)
    return counts


def _file_blast_count(graph: Tempo, file_path: str) -> int:
    """Count unique external files that depend on this file (importers + external callers).

    This is the file-level blast radius: if file_path changes, how many other
    files are directly affected? Captures both import-level and call-level coupling
    that per-symbol cross_file misses (a file with 10 small helpers each called
    once from different files has high file-blast but low per-symbol cross_file).
    """
    return _file_blast_info(graph, file_path)["total"]


def render_hotspots(graph: Tempo, *, top_n: int = 20) -> str:
    """Find the most interconnected, complex, high-risk symbols."""
    # Pre-build renders-from index to avoid O(symbols*edges) scan
    renders_from: dict[str, int] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.RENDERS:
            renders_from[edge.source_id] = renders_from.get(edge.source_id, 0) + 1

    # Load change velocity: files in active churn carry coordination risk
    velocity: dict[str, float] = {}
    velocity_14: dict[str, float] = {}
    try:
        from .git import file_change_velocity
        velocity = file_change_velocity(graph.root)
        velocity_14 = file_change_velocity(graph.root, recent_days=14)
    except Exception:
        pass

    # Blast info cache: file_path → categorized dependent file counts
    # Computed once per file, not per symbol, to avoid redundant traversal
    blast_cache: dict[str, dict[str, int]] = {}

    # Pre-check for test files once; avoids O(symbols × files) per-symbol check
    _any_tests_in_project = any(_is_test_file(fp) for fp in graph.files)

    scores: list[tuple[float, Symbol]] = []

    for sym in graph.symbols.values():
        if sym.kind in (SymbolKind.VARIABLE, SymbolKind.CONSTANT,
                        SymbolKind.ENUM_MEMBER, SymbolKind.FIELD):
            continue

        score = 0.0
        callers = graph.callers_of(sym.id)
        callees = graph.callees_of(sym.id)
        children = graph.children_of(sym.id)

        caller_files = set(c.file_path for c in callers)
        score += len(caller_files) * 3.0
        score += len(callees) * 1.5
        score += min(sym.line_count / 10, 50)
        score += len(children) * 2.0
        cross_file = len(caller_files - {sym.file_path})
        score += cross_file * 5.0
        render_count = renders_from.get(sym.id, 0)
        score += render_count * 2.0
        # Cyclomatic complexity: log scale to avoid dominating
        if sym.complexity > 1:
            score += math.log2(sym.complexity) * 3.0

        # Change velocity multiplier: log-scale boost for actively churning files
        # A symbol in a file with 10 commits/week gets ~1.72x score boost
        if velocity and sym.file_path:
            rel = sym.file_path
            cpw = velocity.get(rel, 0.0)
            if cpw > 0:
                score *= 1.0 + math.log2(1.0 + cpw) * 0.2

        # File blast count multiplier: files with many external dependents are riskier.
        # A file with 50 dependents gets ~1.56x; 10 dependents → ~1.35x; 5 → ~1.26x
        # Cached per file since many symbols share the same file_path.
        if sym.file_path:
            if sym.file_path not in blast_cache:
                blast_cache[sym.file_path] = _file_blast_info(graph, sym.file_path)
            bc = blast_cache[sym.file_path]["total"]
            if bc > 0:
                score *= 1.0 + math.log2(1.0 + bc) * 0.1

        if score > 0:
            scores.append((score, sym))

    scores.sort(key=lambda x: -x[0])

    lines = [f"Top {top_n} hotspots (highest coupling + complexity):", ""]
    for i, (score, sym) in enumerate(scores[:top_n], 1):
        callers = graph.callers_of(sym.id)
        callees = graph.callees_of(sym.id)
        children = graph.children_of(sym.id)
        caller_files_display = set(c.file_path for c in callers)
        cross_files = len(caller_files_display - {sym.file_path})

        lines.append(
            f"{i:2d}. {sym.kind.value} {sym.qualified_name} "
            f"[risk={score:.0f}] ({sym.file_path}:{sym.line_start})"
        )
        details = []
        if callers:
            details.append(f"{len(caller_files_display)} caller files ({cross_files} cross-file)")
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
        # Change velocity warning: active churn = coordination hazard
        if velocity and sym.file_path:
            cpw = velocity.get(sym.file_path, 0.0)
            if cpw >= 5.0:
                cpw14 = velocity_14.get(sym.file_path, 0.0)
                if cpw14 > 0 and cpw >= cpw14 * 1.5:
                    _trend = " ↑"
                elif cpw14 > 1.0 and cpw < cpw14 * 0.5:
                    _trend = " ↓"
                else:
                    _trend = ""
                warnings.append(
                    f"active churn: {cpw:.0f} commits/week{_trend} — re-read before editing"
                )
        # File blast count warning: many external dependents = high coordination cost
        if sym.file_path and sym.file_path in blast_cache:
            binfo = blast_cache[sym.file_path]
            bc = binfo["total"]
            if bc >= 20:
                parts = [f"{binfo[cat]} {cat}" for cat in ("source", "test", "config") if binfo.get(cat, 0) > 0]
                breakdown = f" ({', '.join(parts)})" if parts else ""
                warnings.append(f"blast: {bc} files depend{breakdown} — changes need broad review")
        # Test coverage warning: high-blast symbols with no test coverage at all.
        # Only flag when: (a) project has tests, (b) symbol is widely used cross-file,
        # (c) no test file imports or calls this symbol's file.
        # Avoids noise: if tests import the file, at least some coverage exists.
        if cross_files >= 5 and _any_tests_in_project and sym.file_path:
            _test_importers = [i for i in graph.importers_of(sym.file_path) if _is_test_file(i)]
            _test_callers_sym = [c for c in graph.callers_of(sym.id) if _is_test_file(c.file_path)]
            if not _test_importers and not _test_callers_sym:
                warnings.append("no test coverage — high blast, no safety net")
        if warnings:
            lines.append(f"    → {'; '.join(warnings)}")

    # High-complexity summary: top symbols by raw cyclomatic complexity.
    # Separate from overall hotspot rank — a rarely-called function with cx=200
    # is still a refactor target even if it doesn't score high by coupling.
    _cx_syms = [
        (sym.complexity, sym)
        for _, sym in scores
        if sym.complexity >= 20 and not _is_test_file(sym.file_path)
    ]
    if len(_cx_syms) >= 2:
        _cx_syms.sort(key=lambda x: -x[0])
        _cx_parts = [f"{sym.qualified_name} (cx={cx})" for cx, sym in _cx_syms[:3]]
        lines.append("")
        lines.append(f"Most complex: {', '.join(_cx_parts)}")

    # Untested hotspots: high-scoring symbols in files with no test coverage.
    # The riskiest code to modify: high coupling/complexity AND no safety net.
    # Only shown when test files exist in the project (otherwise whole project lacks tests).
    _all_test_fps_hs = {fp for fp in graph.files if _is_test_file(fp)}
    if _all_test_fps_hs and scores:
        _untested: list[tuple[float, Symbol]] = []
        for _sc, _sym in scores[:top_n]:
            if _is_test_file(_sym.file_path):
                continue
            # Only flag symbols with real cross-file exposure (≥2 cross-file callers)
            _cross = len({
                c.file_path for c in graph.callers_of(_sym.id)
                if c.file_path != _sym.file_path
            })
            if _cross < 2:
                continue
            _base = _sym.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if not any(_base in t for t in _all_test_fps_hs):
                _untested.append((_sc, _sym))
        if len(_untested) >= 1:
            _uh_parts = [
                f"{sym.qualified_name} ({sym.file_path.rsplit('/', 1)[-1]})"
                for _, sym in _untested[:3]
            ]
            lines.append("")
            lines.append(f"Untested hotspots: {', '.join(_uh_parts)}")

    # Churn risk: symbols that are BOTH complex (cx≥15) AND actively churning (≥3/wk).
    # These are the highest-priority refactor targets — changing frequently AND hard to reason about.
    # Separate from hotspot rank (which weights coupling) — a standalone complex churner still matters.
    if velocity and scores:
        _churn_risk: list[tuple[float, Symbol, float]] = []
        for _, _sym in scores:
            if _is_test_file(_sym.file_path):
                continue
            _cx = _sym.complexity
            if _cx < 15:
                continue
            _cpw = velocity.get(_sym.file_path, 0.0)
            if _cpw < 3.0:
                continue
            _danger = _cx * (_cpw ** 0.5)
            _churn_risk.append((_danger, _sym, _cpw))
        if len(_churn_risk) >= 1:
            _churn_risk.sort(key=lambda x: -x[0])
            _cr_parts = [
                f"{sym.qualified_name} (cx={sym.complexity}, {cpw:.0f}/wk)"
                for _, sym, cpw in _churn_risk[:3]
            ]
            lines.append("")
            lines.append(f"Churn risk: {', '.join(_cr_parts)}")

    # File concentration: which files dominate the hotspot list.
    # If one file has 5+ hotspots, agents should read it first — it's the bottleneck.
    if len(scores) >= 5:
        _file_counts: dict[str, int] = {}
        for _, sym in scores[:top_n]:
            _file_counts[sym.file_path] = _file_counts.get(sym.file_path, 0) + 1
        _top_conc = sorted(_file_counts.items(), key=lambda x: -x[1])[:2]
        _conc_parts = [
            f"{fp.rsplit('/', 1)[-1]} ({n}/{min(len(scores), top_n)})"
            for fp, n in _top_conc if n >= 3
        ]
        if _conc_parts:
            lines.append("")
            lines.append(f"Hotspot concentration: {', '.join(_conc_parts)}")

    # Coupled pairs: hotspot files that always change together (high co-change count).
    # Hidden coupling not visible in the call graph — agents must update both when touching one.
    # Only shown when git history is available and at least 1 pair qualifies.
    if graph.root and scores:
        try:
            from .git import cochange_pairs as _hspot_cpairs
            # Get the top-5 hotspot file paths (source only)
            _hs_fps = list(dict.fromkeys(
                sym.file_path for _, sym in scores[:top_n]
                if not _is_test_file(sym.file_path)
            ))[:5]
            _seen_pairs: set[frozenset] = set()
            _coupled: list[tuple[int, str, str]] = []  # (count, fp_a, fp_b)
            for _fp in _hs_fps:
                for _p in _hspot_cpairs(graph.root, _fp, n=3, min_count=5):
                    _partner = _p["path"]
                    if _partner in graph.files and not _is_test_file(_partner):
                        _pair_key = frozenset((_fp, _partner))
                        if _pair_key not in _seen_pairs:
                            _seen_pairs.add(_pair_key)
                            _coupled.append((_p["count"], _fp, _partner))
            if _coupled:
                _coupled.sort(key=lambda x: -x[0])
                _cp_parts = [
                    f"{a.rsplit('/', 1)[-1]} ↔ {b.rsplit('/', 1)[-1]} ({n}x)"
                    for n, a, b in _coupled[:2]
                ]
                lines.append("")
                lines.append(f"Coupled pairs: {', '.join(_cp_parts)}")
        except Exception:
            pass

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


_TEST_FILE_SUFFIXES = (".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")


def _is_test_file(file_path: str) -> bool:
    """Return True if file_path looks like a test/spec file."""
    name = Path(file_path).name
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or any(name.endswith(sfx) for sfx in _TEST_FILE_SUFFIXES)
    )


def _dead_code_confidence(sym: Symbol, graph: Tempo) -> int:
    """Score 0-100: how confident we are this symbol is truly dead."""
    score = 0

    # Test files: symbols are test infrastructure discovered by runners, not dead code
    if _is_test_file(sym.file_path):
        score -= 50

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


def render_dead_code(graph: Tempo, *, max_symbols: int = 50, max_tokens: int = 8000, include_low: bool = False) -> str:
    """Find exported symbols that appear to be unused (never referenced externally).

    include_low: include low-confidence (likely false positive) symbols. Off by default
        to reduce token output (~47% savings). Pass include_low=True to see all tiers.
    """
    dead = graph.find_dead_code()
    if not dead:
        return "No dead code detected — all exported symbols are referenced."

    # Score each symbol
    scored = [(sym, _dead_code_confidence(sym, graph)) for sym in dead]
    scored.sort(key=lambda x: (-x[1], -x[0].line_count))

    high = [(s, c) for s, c in scored if c >= 70]
    medium = [(s, c) for s, c in scored if 40 <= c < 70]
    low = [(s, c) for s, c in scored if c < 40]

    lines = [f"Potential dead code ({len(dead)} symbols):"]

    # Quick wins: top files with the most HIGH confidence dead symbols.
    # Shows agents where to start cleanup without reading the full list.
    if high:
        _qw_counts: dict[str, int] = {}
        for sym, _ in high:
            _qw_counts[sym.file_path] = _qw_counts.get(sym.file_path, 0) + 1
        _qw_sorted = sorted(_qw_counts.items(), key=lambda x: -x[1])[:2]
        _qw_parts = [
            f"{fp.rsplit('/', 1)[-1]} ({n} high-conf)" for fp, n in _qw_sorted
        ]
        lines.append(f"Quick wins: {', '.join(_qw_parts)}")

    # Orphan files: files where ALL exported symbols are dead → delete the whole file.
    # More actionable than quick wins: one `rm` instead of N symbol deletions.
    _dead_sym_ids = {sym.id for sym, _ in scored}
    _orphan_files: list[tuple[str, int, int]] = []  # (file_path, sym_count, line_count)
    for _fp in {sym.file_path for sym, _ in scored}:
        if _is_test_file(_fp):
            continue
        _fi = graph.files.get(_fp)
        if not _fi:
            continue
        _exported = [
            graph.symbols[sid] for sid in _fi.symbols
            if sid in graph.symbols and graph.symbols[sid].exported
        ]
        if _exported and all(sym.id in _dead_sym_ids for sym in _exported):
            _orphan_files.append((_fp, len(_exported), sum(sym.line_count for sym in _exported)))
    if _orphan_files:
        _orphan_files.sort(key=lambda x: -x[2])
        _o_parts = [
            f"{fp.rsplit('/', 1)[-1]} ({n} syms, {lc} lines)"
            for fp, n, lc in _orphan_files[:3]
        ]
        lines.append(f"Orphan files (all-dead): {', '.join(_o_parts)}")

    # Recently dead: dead symbols in files touched in the last 30 days.
    # These are most likely accidentally dead (just added but not yet wired up).
    # Only shown when git history is available and at least 2 symbols qualify.
    from .git import file_last_modified_days as _file_last_modified_days  # noqa: PLC0415
    _touched_cache: dict[str, int | None] = {}

    def _file_age(fp: str) -> int | None:
        if fp not in _touched_cache:
            _touched_cache[fp] = _file_last_modified_days(graph.root, fp)
        return _touched_cache[fp]

    _recently_dead = [
        (sym, conf) for sym, conf in scored
        if conf >= 40  # medium+ confidence only
        and (_file_age(sym.file_path) or 9999) <= 30
    ]
    if len(_recently_dead) >= 2:
        _rd_names = [
            f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]})"
            for sym, _ in _recently_dead[:4]
        ]
        _rd_str = ", ".join(_rd_names)
        if len(_recently_dead) > 4:
            _rd_str += f" +{len(_recently_dead) - 4} more"
        lines.append(f"Recently dead ({len(_recently_dead)}): {_rd_str}")

    lines.append("")
    total_lines = 0

    tiers = [("HIGH CONFIDENCE (safe to remove)", high),
             ("MEDIUM CONFIDENCE (review before removing)", medium)]
    if include_low:
        tiers.append(("LOW CONFIDENCE (likely false positives)", low))

    def _last_touched(file_path: str) -> str:
        if file_path not in _touched_cache:
            _touched_cache[file_path] = _file_last_modified_days(graph.root, file_path)
        days = _touched_cache[file_path]
        if days is None:
            return ""
        return f" — last touched: {days} days ago"

    def _format_age(days: int | None) -> str:
        if days is None:
            return ""
        if days >= 365:
            return " [age: 1y+]"
        if days >= 30:
            return f" [age: {days // 30}m]"
        return f" [age: {days}d]"

    def _sym_age(sym: Symbol) -> str:
        if sym.file_path not in _touched_cache:
            _touched_cache[sym.file_path] = _file_last_modified_days(graph.root, sym.file_path)
        return _format_age(_touched_cache[sym.file_path])

    for label, tier in tiers:
        if not tier:
            continue
        shown = tier[:max_symbols]
        lines.append(f"{label}:")
        lines.append("")
        by_file: dict[str, list[tuple[Symbol, int]]] = {}
        for sym, conf in shown:
            by_file.setdefault(sym.file_path, []).append((sym, conf))
        # Sort files: most dead symbols first (most-contaminated first)
        sorted_files = sorted(by_file.items(), key=lambda x: -len(x[1]))
        for fp, file_syms in sorted_files:
            n = len(file_syms)
            sym_label = f"{n} dead symbol{'s' if n != 1 else ''}"
            lines.append(f"  {fp} ({sym_label}){_last_touched(fp)}:")
            by_line = sorted(file_syms, key=lambda x: x[0].line_start)
            shown_syms = by_line[:10]
            for sym, conf in shown_syms:
                lc = sym.line_count
                total_lines += lc
                age = _sym_age(sym)
                # Superseded hint: if name has legacy/old/deprecated suffix, find active replacement.
                _sup_hint = ""
                _STALE_SUFFIXES = ("_old", "_legacy", "_v1", "_v2", "_deprecated", "_backup", "_bak", "_orig")
                _lower = sym.name.lower()
                for _suf in _STALE_SUFFIXES:
                    if _lower.endswith(_suf):
                        _base = sym.name[:-(len(_suf))]
                        _replacement = next(
                            (s for s in graph.symbols.values()
                             if s.name.lower() == _base.lower()
                             and s.id != sym.id
                             and graph.callers_of(s.id)),
                            None
                        )
                        if _replacement:
                            _sup_hint = f" → possibly replaced by: {_replacement.name}"
                        break
                lines.append(f"    {sym.kind.value} {sym.qualified_name} (L{sym.line_start}-{sym.line_end}, {lc} lines) [confidence: {conf}]{age}{_sup_hint}")
            if len(by_line) > 10:
                lines.append(f"    ... and {len(by_line) - 10} more")
            lines.append("")

    lines.append(f"Total: {len(dead)} unused symbols (~{total_lines:,} lines shown)")
    if include_low:
        lines.append(f"  {len(high)} high, {len(medium)} medium, {len(low)} low confidence")
    else:
        lines.append(f"  {len(high)} high, {len(medium)} medium, {len(low)} low confidence (low hidden — pass include_low=True to show)")

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



# _TEST_MARKERS, _ASSET_DIRS, _cl_path_fallback, _is_change_localization,
# _get_cochange_related, _extract_focus_ranges, render_prepare extracted to tempograph/prepare.py


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
