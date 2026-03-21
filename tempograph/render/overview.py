from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..types import Tempo, EdgeKind, FileInfo, Symbol, SymbolKind
from ._utils import count_tokens, _is_test_file


_CODE_LANGS = {
    "python", "typescript", "tsx", "javascript", "jsx",
    "rust", "go", "java", "csharp", "ruby",
}

_SRC_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
             ".rb", ".cpp", ".c", ".h", ".cs", ".swift", ".kt", ".php"}


def _display_path(fp: str, all_fps: list[str]) -> str:
    """Return basename if unique, else parent/basename to disambiguate (e.g. h1/mod.rs)."""
    base = fp.rsplit("/", 1)[-1]
    if sum(1 for p in all_fps if p.rsplit("/", 1)[-1] == base) > 1:
        parts = fp.rsplit("/", 2)
        return f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else base
    return base


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

    # Main entry points (mod.rs excluded — it's a module declaration, not an entry point)
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base in ("main.py", "main.ts", "main.tsx", "main.rs", "main.go",
                     "index.ts", "index.tsx", "index.js", "app.py", "app.ts",
                     "lib.rs", "__main__.py", "server.py", "cli.py"):
            entries.append(f)

    # Symbols named main/run/app at top level (skip examples/ and benchmarks/)
    _EXAMPLE_PATH_PARTS = {"examples", "example", "benchmarks", "bench", "benches", "samples"}
    for sym in graph.symbols.values():
        if sym.parent_id:
            continue
        path_parts = set(sym.file_path.replace("\\", "/").split("/"))
        if path_parts & _EXAMPLE_PATH_PARTS:
            continue
        if _is_test_file(sym.file_path):
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



# ---------------------------------------------------------------------------
# Signal-group helpers: each returns list[str] of lines to append.
# ---------------------------------------------------------------------------

def _signals_complexity(
    graph: Tempo, *, _src_fps: list[str],
    _all_cx_vals: list[int], _src_file_cx: dict[str, int], _total_cx: int,
) -> list[str]:
    """Complexity/size signals."""
    lines: list[str] = []

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
        # S82: Append avg complexity to the fn sizes line.
        # avg cx > 5 = dense; 2-5 = moderate; < 2 = clean.
        _cx_vals = [
            sym.complexity
            for sym in graph.symbols.values()
            if sym.kind.value in ("function", "method")
            and not _is_test_file(sym.file_path)
            and sym.complexity >= 1
        ]
        if _cx_vals:
            _avg_cx = sum(_cx_vals) / len(_cx_vals)
            _fs_parts.append(f"avg cx: {_avg_cx:.1f}")
        lines.append(f"fn sizes: {', '.join(_fs_parts)}")

    # Largest functions: top 3 non-test functions by line count.
    # Agents should avoid reading these in full; grep/focus is safer.
    _large_fns = sorted(
        (
            (sym.line_count, sym.name, sym.file_path)
            for sym in graph.symbols.values()
            if sym.kind.value in ("function", "method")
            and not _is_test_file(sym.file_path)
            and sym.line_count >= 50
        ),
        key=lambda x: -x[0],
    )
    if len(_large_fns) >= 2:
        _lf_parts = [f"{name} ({lc}L)" for lc, name, _ in _large_fns[:3]]
        lines.append(f"largest fns: {', '.join(_lf_parts)}")


    # S93: Complexity concentration — % of total cyclomatic complexity held in top-N files.
    # High concentration = most cognitive burden in few files = clear refactoring targets.
    # Only shown when 5+ source files AND top-3 files hold >= 50% of total complexity.
    _src_file_cx: dict[str, int] = {}
    for _sym in graph.symbols.values():
        if not _is_test_file(_sym.file_path) and _sym.complexity >= 1:
            _src_file_cx[_sym.file_path] = _src_file_cx.get(_sym.file_path, 0) + _sym.complexity
    _total_cx = sum(_src_file_cx.values())
    if _total_cx >= 10 and len(_src_file_cx) >= 5:
        _cx_sorted = sorted(_src_file_cx.items(), key=lambda x: -x[1])
        _top3_cx = sum(cx for _, cx in _cx_sorted[:3])
        _pct = int(_top3_cx / _total_cx * 100)
        if _pct >= 60:
            _cxc_parts = [f"{fp.rsplit('/', 1)[-1]}:{cx}" for fp, cx in _cx_sorted[:3]]
            lines.append(f"cx concentration: {_pct}% in top 3 files ({', '.join(_cxc_parts)})")

    # S100: Median complexity — central tendency for cyclomatic complexity across fns.
    # Complements the avg: if median << avg, a small number of outliers skew the mean.
    # Only shown when 10+ non-test functions have complexity data.
    _all_cx_vals = sorted(
        sym.complexity for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method") and not _is_test_file(sym.file_path) and sym.complexity >= 1
    )
    if len(_all_cx_vals) >= 10:
        _mid = len(_all_cx_vals) // 2
        _median_cx = (
            _all_cx_vals[_mid] if len(_all_cx_vals) % 2 == 1
            else (_all_cx_vals[_mid - 1] + _all_cx_vals[_mid]) // 2
        )
        _mean_cx = sum(_all_cx_vals) / len(_all_cx_vals)
        lines.append(f"median complexity: {_median_cx} (mean: {_mean_cx:.1f}, n={len(_all_cx_vals)} fns)")

    # S125: Most complex function — the single non-test function with highest cyclomatic complexity.
    # Agents refactoring or auditing code need to know the worst-case function by complexity.
    # Only shown when there are 5+ non-test functions AND max complexity >= 15.
    if len(_all_cx_vals) >= 5 and _all_cx_vals and _all_cx_vals[-1] >= 15:
        _cx_max = _all_cx_vals[-1]
        _cx_leader = max(
            (sym for sym in graph.symbols.values()
             if sym.kind.value in ("function", "method") and not _is_test_file(sym.file_path)
             and sym.complexity >= _cx_max),
            key=lambda s: s.complexity,
            default=None,
        )
        if _cx_leader:
            lines.append(
                f"most complex fn: {_cx_leader.name} (cx={_cx_leader.complexity}"
                f" in {_cx_leader.file_path.rsplit('/', 1)[-1]})"
            )


    # S110: File size distribution — avg + median source file line counts.
    # High avg = large files = harder to navigate; agents should use focus mode more.
    # Only shown when 5+ source files exist with known line counts.
    _src_line_counts = sorted(
        fi.line_count for fp, fi in graph.files.items()
        if not _is_test_file(fp) and fi.line_count and fi.line_count > 0
    )
    if len(_src_line_counts) >= 5:
        _lc_avg = int(sum(_src_line_counts) / len(_src_line_counts))
        _lc_mid = len(_src_line_counts) // 2
        _lc_median = (
            _src_line_counts[_lc_mid] if len(_src_line_counts) % 2 == 1
            else (_src_line_counts[_lc_mid - 1] + _src_line_counts[_lc_mid]) // 2
        )
        if _lc_avg >= 50:  # skip trivial repos with tiny stubs
            lines.append(f"avg file size: {_lc_avg} lines (median: {_lc_median}, n={len(_src_line_counts)} files)")


    # S137: Avg fn size — mean line count of all non-test function/method bodies.
    # High average (>= 40 lines) = functions doing too much; poor decomposition signal.
    # Only shown when 10+ source functions exist and avg >= 40.
    _s137_fns = [
        sym for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method")
        and not _is_test_file(sym.file_path)
        and sym.line_count >= 3
    ]
    if len(_s137_fns) >= 10:
        _s137_avg = sum(s.line_count for s in _s137_fns) / len(_s137_fns)
        if _s137_avg >= 40:
            lines.append(f"avg fn size: {_s137_avg:.0f} lines — functions are large, consider decomposition")


    # S226: Monolithic file — a source file containing >= 50 tracked symbols.
    # A file with 50+ symbols is hard to navigate and often violates single-responsibility.
    # Only shown when 1+ non-test source file has >= 50 tracked symbols.
    _s226_mono: list[tuple[int, str]] = []
    for _fp226 in graph.files:
        if _is_test_file(_fp226):
            continue
        _n_syms226 = sum(1 for s in graph.symbols.values() if s.file_path == _fp226)
        if _n_syms226 >= 50:
            _s226_mono.append((_n_syms226, _fp226))
    if _s226_mono:
        _s226_mono.sort(reverse=True)
        _s226_top = _s226_mono[0]
        _s226_base = _s226_top[1].rsplit("/", 1)[-1]
        lines.append(
            f"monolithic file: {_s226_base} has {_s226_top[0]} symbols"
            f" — consider splitting into focused modules"
        )


    return lines


def _signals_coupling(
    graph: Tempo, *, _src_fps: list[str],
    _importer_counts: dict[str, int], _import_adj: dict[str, list[str]],
    modules: dict[str, list[str]],
) -> list[str]:
    """Import/coupling signals."""
    lines: list[str] = []

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
            _ti_fps = [fp for fp, _ in _top_imported]
            _ti_parts = [
                f"{_display_path(fp, _ti_fps)} ({n})" for fp, n in _top_imported
            ]
            lines.append("")
            lines.append(f"top imported: {', '.join(_ti_parts)}")

    # Stable core: widely-imported files (>= 5 source importers) that haven't been
    # modified in 30+ days. These are the infrastructure heart of the codebase —
    # agents can rely on them being stable and well-tested.
    if graph.root and _importer_counts:
        try:
            from ..git import file_last_modified_days as _fld_core  # noqa: PLC0415
            _stable_core: list[tuple[int, str, int]] = []  # (importers, fp, days)
            for _fp, _n_imp in _importer_counts.items():
                if _n_imp < 5:
                    continue
                _days_c = _fld_core(graph.root, _fp)
                if _days_c is not None and _days_c >= 30:
                    _stable_core.append((_n_imp, _fp, _days_c))
            if _stable_core:
                _stable_core.sort(key=lambda x: -x[0])
                _sc_fps = [fp for _, fp, _ in _stable_core[:3]]
                _sc_parts = [
                    f"{_display_path(fp, _sc_fps)} ({n_imp} importers, {d}d)"
                    for n_imp, fp, d in _stable_core[:3]
                ]
                lines.append(f"stable core: {', '.join(_sc_parts)}")
        except Exception:
            pass

    # High-coupling files: non-test source files that import >= 8 distinct source files.
    # High fan-out = many dependencies = fragile integration points. Hard to change safely.
    _import_fanout: dict[str, int] = {}
    for _edge in graph.edges:
        if _edge.kind == EdgeKind.IMPORTS:
            _src_fp = _edge.source_id
            _tgt_fp = _edge.target_id
            if (
                _src_fp in graph.files and _tgt_fp in graph.files
                and not _is_test_file(_src_fp) and not _is_test_file(_tgt_fp)
                and _src_fp != _tgt_fp
            ):
                _import_fanout[_src_fp] = _import_fanout.get(_src_fp, 0) + 1
    _high_coupling = sorted(
        [(n, fp) for fp, n in _import_fanout.items() if n >= 8],
        key=lambda x: -x[0],
    )
    if _high_coupling:
        _hc_parts = [f"{fp.rsplit('/', 1)[-1]} ({n} imports)" for n, fp in _high_coupling[:3]]
        lines.append(f"high-coupling: {', '.join(_hc_parts)}")

    # Co-change pairs: file pairs that frequently change together in git commits.
    # Surfaces edit-coupling that static imports don't capture.
    # e.g. "render.py ↔ test_mcp_server.py (100%)" tells agents what to touch together.
    if graph.root:
        try:
            from ..git import cochange_matrix as _ccm, is_git_repo as _igr  # noqa: PLC0415
            if _igr(graph.root):
                _cc_matrix = _ccm(graph.root)
                _CC_SRC_EXTS = {
                    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
                    ".java", ".kt", ".rb", ".cs", ".cpp", ".c", ".h",
                    ".swift", ".dart", ".scala", ".ex", ".exs",
                }
                def _is_src(_p: str) -> bool:
                    return any(_p.endswith(e) for e in _CC_SRC_EXTS)

                _seen_cc_pairs: set[tuple[str, str]] = set()
                _cc_pairs: list[tuple[float, str, str]] = []
                for _ccfp1, _cc_partners in _cc_matrix.items():
                    if not _is_src(_ccfp1):
                        continue
                    for _ccfp2, _cc_freq in _cc_partners:
                        if not _is_src(_ccfp2):
                            continue
                        _cc_key = (min(_ccfp1, _ccfp2), max(_ccfp1, _ccfp2))
                        if _cc_key in _seen_cc_pairs:
                            continue
                        _seen_cc_pairs.add(_cc_key)
                        if _is_test_file(_ccfp1) and _is_test_file(_ccfp2):
                            continue
                        _cc_pairs.append((_cc_freq, _ccfp1, _ccfp2))
                _cc_pairs.sort(key=lambda x: -x[0])
                if _cc_pairs:
                    _cc_parts2 = [
                        f"{_cp1.rsplit('/', 1)[-1]} ↔ {_cp2.rsplit('/', 1)[-1]} ({_cf:.0%})"
                        for _cf, _cp1, _cp2 in _cc_pairs[:3]
                    ]
                    lines.append(f"co-change pairs: {', '.join(_cc_parts2)}")
        except Exception:
            pass


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


    # S119: Deepest import chain — the source file with the highest import depth from root.
    # Deeply-nested files are fragile: a change anywhere up the chain cascades down.
    # Only shown when there are 5+ source files and max depth >= 4.
    _src_fps_depth = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_fps_depth) >= 5:
        _max_depth = 0
        _deepest_fp = ""
        for _dfp in _src_fps_depth:
            # BFS depth from this file up the importer chain
            _visited: set[str] = set()
            _queue = [(_dfp, 0)]
            _local_max = 0
            while _queue:
                _cur, _d = _queue.pop(0)
                if _cur in _visited:
                    continue
                _visited.add(_cur)
                _local_max = max(_local_max, _d)
                for _imp in graph.importers_of(_cur):
                    if _imp not in _visited and not _is_test_file(_imp):
                        _queue.append((_imp, _d + 1))
            if _local_max > _max_depth:
                _max_depth = _local_max
                _deepest_fp = _dfp
        if _max_depth >= 4:
            lines.append(f"deepest import chain: {_deepest_fp.rsplit('/', 1)[-1]} ({_max_depth} hops from root)")

    # Circular imports: flag immediately in overview so agents don't miss them.
    # Details are in `--mode deps` but overview gives a quick count + first cycle.
    try:
        _cycles = graph.detect_circular_imports()
        if _cycles:
            _first_cycle = " → ".join(fp.rsplit("/", 1)[-1] for fp in _cycles[0])
            _more = f" +{len(_cycles) - 1} more" if len(_cycles) > 1 else ""
            lines.append(f"⚠ circular imports: {len(_cycles)} cycle(s) ({_first_cycle}{_more})")
    except Exception:
        pass


    # S129: Orphan modules — top-level dirs where no file is imported by any other module.
    # These are candidate standalone tools, dead plugins, or forgotten experiments.
    # Only shown when 3+ top-level dirs exist and 2+ are orphans (not "." root files).
    if len(modules) >= 3:
        _orphan_mods: list[str] = []
        for _om, _om_files in modules.items():
            if _om == ".":
                continue
            # A module is orphan if none of its files are imported from outside the module
            _any_importer = any(
                imp in graph.files and not imp.startswith(_om + "/")
                for fp in _om_files
                for imp in graph.importers_of(fp)
            )
            if not _any_importer:
                _orphan_mods.append(_om)
        if len(_orphan_mods) >= 2:
            _om_str = ", ".join(f"{m}/" for m in sorted(_orphan_mods)[:4])
            if len(_orphan_mods) > 4:
                _om_str += f" +{len(_orphan_mods) - 4} more"
            lines.append(f"orphan modules: {_om_str} — no files imported by other modules")


    # S146: Barrel file count — source files that import from 5+ other modules (aggregators).
    # Many barrel files = fragmented architecture; changes to any dependency flow through them.
    # Only shown when 2+ barrel files found.
    from ..types import EdgeKind as _EK146
    _s146_import_counts: dict[str, int] = {}
    for _e146 in graph.edges:
        if _e146.kind != _EK146.IMPORTS:
            continue
        if _e146.source_id not in graph.files or _e146.target_id not in graph.files:
            continue
        if _is_test_file(_e146.source_id) or _is_test_file(_e146.target_id):
            continue
        if _e146.source_id == _e146.target_id:
            continue
        _s146_import_counts[_e146.source_id] = _s146_import_counts.get(_e146.source_id, 0) + 1
    _s146_barrels = [fp for fp, cnt in _s146_import_counts.items() if cnt >= 5]
    if len(_s146_barrels) >= 2:
        _s146_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_s146_barrels)[:3]]
        _s146_str = ", ".join(_s146_names)
        if len(_s146_barrels) > 3:
            _s146_str += f" +{len(_s146_barrels) - 3} more"
        lines.append(f"barrel files: {len(_s146_barrels)} aggregator files ({_s146_str})")


    # S185: Circular deps — pairs of source files that mutually import each other.
    # Circular imports introduce tight coupling and make testing or refactoring harder.
    # Only shown when >= 1 such circular pair is detected.
    _s185_fp_imports: dict[str, set[str]] = {}
    for _fp185b in graph.files:
        if _is_test_file(_fp185b):
            continue
        for _imp185a in graph.importers_of(_fp185b):
            if _imp185a in graph.files and not _is_test_file(_imp185a) and _imp185a != _fp185b:
                _s185_fp_imports.setdefault(_imp185a, set()).add(_fp185b)
    _s185_circular: set[tuple[str, str]] = set()
    for _fp185a, _imports185 in _s185_fp_imports.items():
        for _fp185b in _imports185:
            if _fp185a in _s185_fp_imports.get(_fp185b, set()):
                _s185_circular.add(tuple(sorted([_fp185a, _fp185b])))  # type: ignore[arg-type]
    if _s185_circular:
        _circ_names = [
            f"{a.rsplit('/', 1)[-1]}↔{b.rsplit('/', 1)[-1]}"
            for a, b in list(_s185_circular)[:2]
        ]
        _circ_str = ", ".join(_circ_names)
        lines.append(
            f"circular deps: {len(_s185_circular)} mutual-import pair(s) ({_circ_str})"
            f" — tight coupling, difficult to test in isolation"
        )


    # S227: High coupling — average number of imports per source file >= 5.
    # High coupling means files are tightly interdependent; one change ripples widely.
    # Only shown when avg >= 5 and 5+ source files exist.
    _s227_src_fps = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s227_src_fps) >= 5:
        _s227_import_counts = []
        for _fp227 in _s227_src_fps:
            # Count files this fp imports (using importers API: how many others import THIS file)
            # Instead: count files imported by this file = outgoing edges
            _n_imports227 = sum(
                1 for e in graph.edges
                if e.kind.value == "imports" and e.source_id.split("::")[0] == _fp227
            )
            _s227_import_counts.append(_n_imports227)
        _avg227 = sum(_s227_import_counts) / len(_s227_import_counts)
        if _avg227 >= 5:
            lines.append(
                f"high coupling: avg {_avg227:.1f} imports/file across {len(_s227_src_fps)}"
                f" source files — tightly interdependent modules"
            )


    # S258: High coupling density — average imports-per-file exceeds 5 for non-trivial repos.
    # Dense coupling makes refactoring expensive; each file change can cascade unpredictably.
    # Only shown for repos with 10+ source files and avg fan-in > 5.
    _s258_code_files = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s258_code_files) >= 10:
        _s258_total_imports = sum(
            1 for e in graph.edges
            if e.kind.value == "imports"
            and not _is_test_file(e.source_id)
        )
        _s258_avg = _s258_total_imports / len(_s258_code_files)
        if _s258_avg >= 5:
            lines.append(
                f"high coupling: avg {_s258_avg:.1f} imports/file"
                f" — dense dependency graph; refactors cascade unpredictably"
            )


    # S265: Flat structure — 8+ source files all at root level with no subdirectories.
    # Flat codebases are harder to navigate as they grow; grouping by feature/layer
    # into subdirectories reduces cognitive load and enables selective imports.
    _s259_root_src = [
        fp for fp in graph.files
        if "/" not in fp
        and not _is_test_file(fp)
        and graph.files[fp].language.value in _CODE_LANGS
    ]
    _s259_all_src = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s259_root_src) >= 8 and len(_s259_root_src) == len(_s259_all_src):
        lines.append(
            f"flat structure: all {len(_s259_root_src)} source files at root level"
            f" — consider grouping into modules/packages as codebase grows"
        )



    # S286: Shallow module graph — few import edges between source files (avg < 1 per file).
    # Very low coupling may indicate disconnected modules, dead code islands, or
    # a library with very independent components.
    _s286_src = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s286_src) >= 8:
        _s286_import_edges = sum(
            1 for e in graph.edges
            if e.kind.value == "imports" and not _is_test_file(e.source_id)
        )
        _s286_avg = _s286_import_edges / len(_s286_src)
        if _s286_avg < 1.0:
            lines.append(
                f"shallow graph: avg {_s286_avg:.1f} imports/file across {len(_s286_src)} source files"
                f" — low coupling; modules may be disconnected or code is largely dead"
            )



    # S342: Circular imports detected — 2+ files form a circular import chain.
    # Circular imports cause unpredictable initialization order and are hard to refactor;
    # Python lazy-imports them partially, causing AttributeError at runtime for some access patterns.
    _s342_import_edges: dict[str, set[str]] = {}
    for _e342 in graph.edges:
        if _e342.kind.value == "imports":
            _src342 = graph.symbols.get(_e342.source_id)
            _tgt342 = graph.symbols.get(_e342.target_id)
            if _src342 and _tgt342 and _src342.file_path != _tgt342.file_path:
                _s342_import_edges.setdefault(_src342.file_path, set()).add(_tgt342.file_path)
    _s342_cycles: list[tuple[str, str]] = []
    for _a342, _deps342 in _s342_import_edges.items():
        for _b342 in _deps342:
            if _a342 in _s342_import_edges.get(_b342, set()):
                _pair342 = tuple(sorted([_a342, _b342]))
                if _pair342 not in [tuple(sorted(c)) for c in _s342_cycles]:
                    _s342_cycles.append((_a342, _b342))
    if _s342_cycles:
        _cycle_pair342 = _s342_cycles[0]
        _a_name342 = _cycle_pair342[0].rsplit("/", 1)[-1]
        _b_name342 = _cycle_pair342[1].rsplit("/", 1)[-1]
        lines.append(
            f"circular imports: {len(_s342_cycles)} import cycle(s) detected"
            f" (e.g. {_a_name342} ↔ {_b_name342})"
            f" — unpredictable init order; refactor to break cycle before adding new imports"
        )


    return lines


def _signals_coverage(
    graph: Tempo, *, _src_fps: list[str],
    _test_fps: set[str], _commit_counts: dict[str, int],
) -> list[str]:
    """Test coverage signals."""
    lines: list[str] = []

    # Stale tests: test files not in recent commits while their source file IS.
    # Signals test drift — code changed but tests haven't kept up. Needs git repo.
    if graph.root:
        try:
            from ..git import file_change_velocity as _fcv2  # noqa: PLC0415
            _recent_vel = _fcv2(graph.root)
            _stale_tests: list[str] = []
            for _tfp in graph.files:
                if not _is_test_file(_tfp):
                    continue
                # Find likely source file: test_foo.py → foo.py
                _tname = _tfp.rsplit("/", 1)[-1]
                _sname = _tname
                if _sname.startswith("test_"):
                    _sname = _sname[5:]
                elif _sname.endswith("_test.py"):
                    _sname = _sname[:-8] + ".py"
                # Find source file with matching base name
                _src_match = next(
                    (fp for fp in graph.files if not _is_test_file(fp) and fp.rsplit("/", 1)[-1] == _sname),
                    None,
                )
                if _src_match and _src_match in _recent_vel and _tfp not in _recent_vel:
                    _stale_tests.append(_tfp.rsplit("/", 1)[-1])
            if len(_stale_tests) >= 2:
                _st_str = ", ".join(_stale_tests[:3])
                if len(_stale_tests) > 3:
                    _st_str += f" +{len(_stale_tests) - 3} more"
                lines.append(f"stale tests ({len(_stale_tests)}): {_st_str} — source changed, tests didn't")
        except Exception:
            pass

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

        # Per-directory breakdown: show dirs with >=3 source files and <80% coverage.
        # Agents use this to identify which directories are high-risk to edit.
        from collections import defaultdict as _dd
        _dir_src: dict[str, list[str]] = _dd(list)
        for fp in _src_fps:
            _d = fp.rsplit("/", 1)[0] if "/" in fp else "."
            _dir_src[_d].append(fp)
        _dir_breakdown: list[tuple[int, str, str]] = []  # (pct, dir, label)
        for _d, _fps in _dir_src.items():
            if len(_fps) < 3:
                continue
            _d_cov = sum(1 for fp in _fps if any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _test_fps))
            _d_pct = int(_d_cov / len(_fps) * 100)
            if _d_pct < 80:  # only show undertested dirs
                _dname = _d.rsplit("/", 1)[-1] if "/" in _d else _d
                _dir_breakdown.append((_d_pct, _dname, f"{_dname}/ ({_d_pct}%, {_d_cov}/{len(_fps)})"))
        if len(_dir_breakdown) >= 2:
            _dir_breakdown.sort(key=lambda x: x[0])  # worst first
            lines.append(f"  by dir: {', '.join(x[2] for x in _dir_breakdown[:4])}")

    # Orphan test files: test files that name a source file which no longer exists.
    # These linger after source renames/deletions and should be cleaned up.
    # Uses both basename and stem-segment matching to avoid false positives:
    # test_bench_context.py -> "bench_context.py" OR any file ending in "context.py"
    # with a path segment containing "bench".
    if len(_test_fps) >= 2:
        _orphan_tests: list[str] = []
        _src_basenames = {
            fp.rsplit("/", 1)[-1] for fp in graph.files if not _is_test_file(fp)
        }
        # Also build a stem set for partial matching (handles test_bench_ctx -> ctx.py patterns)
        _src_stems = {
            fp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for fp in graph.files if not _is_test_file(fp)
        }
        for _tfp in _test_fps:
            _tname = _tfp.rsplit("/", 1)[-1]
            _sname = _tname
            if _sname.startswith("test_"):
                _sname = _sname[5:]
            elif _sname.endswith("_test.py"):
                _sname = _sname[:-8] + ".py"
            else:
                continue  # no test_ prefix/suffix pattern — skip
            _sstem = _sname.rsplit(".", 1)[0]  # e.g. "bench_context"
            # Direct basename match
            if _sname in _src_basenames:
                continue
            # Partial stem match: any source file whose stem is a suffix of test stem
            # e.g. "bench_context" -> source "context" (bench/changelocal/context.py)
            _partial_match = any(
                _sstem.endswith("_" + s) or _sstem == s
                for s in _src_stems
                if len(s) >= 3
            )
            if not _partial_match:
                _orphan_tests.append(_tname)
        # Suppress if many orphans AND >40% ratio — indicates a naming mismatch
        # between test structure and source structure (e.g. test_bench_foo.py for bench/foo.py).
        # Small counts (<5) always shown regardless of ratio (likely real orphans).
        _orphan_ratio = len(_orphan_tests) / max(len(_test_fps), 1)
        if _orphan_tests and (len(_orphan_tests) < 5 or _orphan_ratio <= 0.40):
            _ot_str = ", ".join(sorted(_orphan_tests)[:3])
            if len(_orphan_tests) > 3:
                _ot_str += f" +{len(_orphan_tests) - 3} more"
            lines.append(
                f"orphan tests ({len(_orphan_tests)}): {_ot_str} — no matching source file"
            )

    # S115: Largest test file — the test file with the most test functions.
    # Identifies the "main test suite" — agents should run it first after any change.
    # Only shown when 2+ test files exist and the largest has 3+ test functions.
    if len(_test_fps) >= 2:
        _test_fn_counts: list[tuple[int, str]] = []
        for _tfp in _test_fps:
            _fi_t = graph.files.get(_tfp)
            if not _fi_t:
                continue
            _n_tests = sum(
                1 for sid in _fi_t.symbols
                if sid in graph.symbols and graph.symbols[sid].name.startswith("test_")
            )
            if _n_tests >= 3:
                _test_fn_counts.append((_n_tests, _tfp))
        if _test_fn_counts:
            _largest_t_n, _largest_t_fp = max(_test_fn_counts, key=lambda x: x[0])
            lines.append(f"largest test file: {_largest_t_fp.rsplit('/', 1)[-1]} ({_largest_t_n} tests)")


    # S84: Test debt — exported functions/methods with real callers but zero test coverage.
    # Different from "API surface unused": these ARE actively called but not tested.
    # The highest-risk category: production code exercised by users but not by tests.
    # Only shown when 3+ qualify (avoids noise on small/well-tested repos).
    _active_exported_fns = [
        sym for sym in graph.symbols.values()
        if sym.exported
        and sym.kind.value in ("function", "method")
        and not _is_test_file(sym.file_path)
        and any(c.file_path != sym.file_path for c in graph.callers_of(sym.id))
        and not any(_is_test_file(c.file_path) for c in graph.callers_of(sym.id))
    ]
    if len(_active_exported_fns) >= 3:
        lines.append(
            f"test debt: {len(_active_exported_fns)} active exports with callers but no tests"
        )

    # S86: Zombie exports — exported functions whose ONLY callers are test files.
    # These are test-scaffolding APIs that should be made private or deleted.
    # Only shown when 2+ qualify; single occurrence could be an intentional test helper.
    _zombie_exports = [
        sym for sym in graph.symbols.values()
        if sym.exported
        and sym.kind.value in ("function", "method")
        and not _is_test_file(sym.file_path)
        and graph.callers_of(sym.id)  # must have at least one caller
        and all(_is_test_file(c.file_path) for c in graph.callers_of(sym.id))
    ]
    if len(_zombie_exports) >= 2:
        _z_names = [s.name for s in _zombie_exports[:4]]
        _z_str = ", ".join(_z_names)
        if len(_zombie_exports) > 4:
            _z_str += f" +{len(_zombie_exports) - 4} more"
        lines.append(f"zombie exports ({len(_zombie_exports)}): {_z_str} — exported but only called from tests")


    # S142: Test coverage gap — source files with no corresponding test file.
    # Helps agents know which areas of the codebase are "flying blind."
    # Only shown when test files exist AND >= 30% of source files lack tests.
    _s142_all_tests = {fp for fp in graph.files if _is_test_file(fp)}
    _s142_src_fps = [fp for fp in graph.files if not _is_test_file(fp)]
    if _s142_all_tests and len(_s142_src_fps) >= 5:
        _s142_untested_fps = [
            fp for fp in _s142_src_fps
            if not any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _s142_all_tests)
        ]
        _s142_gap_pct = int(len(_s142_untested_fps) / len(_s142_src_fps) * 100)
        if _s142_gap_pct >= 30:
            lines.append(
                f"test coverage gap: {len(_s142_untested_fps)}/{len(_s142_src_fps)}"
                f" source files have no tests ({_s142_gap_pct}%)"
            )


    # S151: Impl vs test ratio — ratio of non-test source lines to test file lines.
    # High ratio (>= 5x) = tests are thin relative to implementation.
    # Only shown when test files exist and there are 10+ source lines.
    _s151_src_lines = sum(
        fi.line_count for fp, fi in graph.files.items() if not _is_test_file(fp)
    )
    _s151_test_lines = sum(
        fi.line_count for fp, fi in graph.files.items() if _is_test_file(fp)
    )
    if _s151_test_lines > 0 and _s151_src_lines >= 10:
        _s151_ratio = _s151_src_lines / _s151_test_lines
        if _s151_ratio >= 5.0:
            lines.append(
                f"impl:test ratio: {_s151_ratio:.1f}x ({_s151_src_lines:,}L src / {_s151_test_lines:,}L tests)"
                f" — test coverage is thin"
            )


    # S233: Undertested codebase — source files exist but fewer than 20% of all files are tests.
    # A low test ratio for a non-trivial codebase is a coverage risk signal.
    # Only shown when < 20% of 10+ files are tests (excluding trivial single-file repos).
    _s233_all = [fp for fp in graph.files if graph.files[fp].language.value in _CODE_LANGS]
    _s233_tests = [fp for fp in _s233_all if _is_test_file(fp)]
    _s233_src = [fp for fp in _s233_all if not _is_test_file(fp)]
    if len(_s233_all) >= 10 and _s233_src:
        _s233_ratio = len(_s233_tests) / len(_s233_all) * 100
        if _s233_ratio < 20:
            lines.append(
                f"undertested: {len(_s233_tests)}/{len(_s233_all)} files are tests"
                f" ({_s233_ratio:.0f}%) — consider adding test coverage"
            )


    # S213: High test ratio — more than 60% of source files are test files.
    # Test-heavy repos are healthy, but very high ratios may indicate missing source coverage.
    # Positive signal: only shown when ratio >= 60% and there are 5+ total files.
    _all_files213 = [fp for fp in graph.files if graph.files[fp].language.value in _CODE_LANGS]
    _test_files213 = [fp for fp in _all_files213 if _is_test_file(fp)]
    if len(_all_files213) >= 5:
        _test_ratio213 = len(_test_files213) / len(_all_files213) * 100
        if _test_ratio213 >= 60:
            lines.append(
                f"high test ratio: {len(_test_files213)}/{len(_all_files213)} files are tests"
                f" ({_test_ratio213:.0f}%) — well-tested codebase"
            )


    # S271: Test-heavy codebase — test files outnumber source files by 2×.
    # A very high test-to-code ratio may indicate test duplication, over-testing of
    # trivial code, or test debt accumulated from abandoned features.
    _s271_src = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    _s271_tests = [fp for fp in graph.files if _is_test_file(fp)]
    if len(_s271_src) >= 5 and len(_s271_tests) >= 10 and len(_s271_tests) > 2 * len(_s271_src):
        _s271_ratio = len(_s271_tests) / max(len(_s271_src), 1)
        lines.append(
            f"test-heavy: {len(_s271_tests)} test files vs {len(_s271_src)} source files"
            f" ({_s271_ratio:.1f}× ratio) — unusually high; check for test duplication or dead tests"
        )



    # S322: Test gap — source files outnumber test files by 3× or more.
    # A large source-to-test ratio means most code has no regression safety net;
    # adding tests before refactoring is strongly advised.
    _s322_src_files = [fp for fp in graph.files if not _is_test_file(fp)]
    _s322_test_files = [fp for fp in graph.files if _is_test_file(fp)]
    if len(_s322_src_files) >= 10 and _s322_test_files:
        _s322_ratio = len(_s322_src_files) / max(len(_s322_test_files), 1)
        if _s322_ratio >= 3.0:
            lines.append(
                f"test gap: {len(_s322_src_files)} source files vs {len(_s322_test_files)}"
                f" test files ({_s322_ratio:.1f}×) — low coverage density; add tests before refactoring"
            )
    elif len(_s322_src_files) >= 10 and not _s322_test_files:
        lines.append(
            f"no tests: {len(_s322_src_files)} source files with 0 test files detected"
            f" — add baseline tests before any refactor"
        )


    return lines


def _signals_exports(
    graph: Tempo, *, _src_fps: list[str], _exported_src: list,
) -> list[str]:
    """Export/API signals."""
    lines: list[str] = []

    # God files: source files with unusually many exported symbols (>15).
    # Signal for undivided modules or god objects — high cognitive load, hard to navigate.
    _god_files = sorted(
        (
            (sum(1 for sid in fi.symbols if graph.symbols.get(sid, None) and graph.symbols[sid].exported), fp)
            for fp, fi in graph.files.items()
            if not _is_test_file(fp) and fi.symbols
        ),
        key=lambda x: -x[0],
    )
    _god_files = [(n, fp) for n, fp in _god_files if n >= 15]
    if len(_god_files) >= 1:
        _gf_parts = [f"{fp.rsplit('/', 1)[-1]} ({n} exported)" for n, fp in _god_files[:3]]
        lines.append(f"god files: {', '.join(_gf_parts)}")


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

    # S130: Most-called export — the single exported symbol with the most cross-file source callers.
    # The "heart" of the codebase: changes here have maximum blast radius.
    # Only shown when there are 5+ exported non-test symbols and max callers >= 5.
    if len(_exported_src) >= 5:
        _mc_exp_sym: "Symbol | None" = None
        _mc_exp_count = 0
        for _mce in _exported_src:
            if _mce.kind.value not in ("function", "method"):
                continue
            _mce_cfs = len({
                c.file_path for c in graph.callers_of(_mce.id)
                if c.file_path != _mce.file_path and not _is_test_file(c.file_path)
            })
            if _mce_cfs > _mc_exp_count:
                _mc_exp_count = _mce_cfs
                _mc_exp_sym = _mce
        if _mc_exp_sym and _mc_exp_count >= 5:
            lines.append(
                f"most-called export: {_mc_exp_sym.name}"
                f" ({_mc_exp_count} caller files in {_mc_exp_sym.file_path.rsplit('/', 1)[-1]})"
            )


    # S80: Interface/abstract count — how many pure interface/protocol/abstract-class symbols.
    # Signals codebase abstraction health: 0 interfaces in a 100-class repo = no abstraction layer.
    # Only shown when project has 5+ classes (smaller repos rarely use abstractions).
    _class_count = sum(
        1 for sym in graph.symbols.values()
        if sym.kind.value == "class" and not _is_test_file(sym.file_path) and sym.exported
    )
    if _class_count >= 5:
        _iface_count = sum(
            1 for sym in graph.symbols.values()
            if sym.kind.value == "interface" and not _is_test_file(sym.file_path)
        )
        if _iface_count >= 1:
            lines.append(f"abstractions: {_iface_count} interface(s) across {_class_count} classes")

    # Private API leaking: symbols with _ prefix called from external files.
    # Indicates callers depending on implementation details — fragile coupling.
    _private_leaks: list[str] = []
    for sym in graph.symbols.values():
        if not sym.name.startswith("_") or sym.name.startswith("__"):
            continue
        if _is_test_file(sym.file_path) or sym.kind.value not in ("function", "method"):
            continue
        _ext_callers = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path and not _is_test_file(c.file_path)}
        if _ext_callers:
            _private_leaks.append(sym.name)
    if len(_private_leaks) >= 2:
        _pl_str = ", ".join(_private_leaks[:4])
        if len(_private_leaks) > 4:
            _pl_str += f" +{len(_private_leaks) - 4} more"
        lines.append(f"private leak ({len(_private_leaks)}): {_pl_str} — _ symbols called externally")

    # Quick-win dead code: largest non-test, non-exported functions with 0 callers.
    # Agents get immediate cleanup targets without running full dead_code mode.
    # Threshold: line_count >= 15 to avoid flagging trivial 1-2 line helpers.
    _quick_wins = sorted(
        [
            (sym.line_count, sym)
            for sym in graph.symbols.values()
            if sym.kind.value in ("function", "method")
            and not sym.exported
            and not _is_test_file(sym.file_path)
            and sym.line_count >= 15
            and not graph.callers_of(sym.id)
        ],
        key=lambda x: -x[0],
    )[:3]
    if len(_quick_wins) >= 2:
        _qw_parts = [f"{sym.name} ({lc}L)" for lc, sym in _quick_wins]
        lines.append(f"quick wins: {', '.join(_qw_parts)} — no callers, likely dead")


    # S105: Mono-callers — exported symbols used by exactly 1 external file.
    # These look like "public API" but are secretly coupled to a single consumer.
    # High count = hidden tight coupling disguised as an open interface.
    # Only shown when 3+ mono-caller exports exist (fewer = not a pattern worth flagging).
    _mono_callers = [
        sym for sym in graph.symbols.values()
        if sym.exported and sym.kind.value in ("function", "method")
        and not _is_test_file(sym.file_path)
        and len({c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path and not _is_test_file(c.file_path)}) == 1
    ]
    if len(_mono_callers) >= 3:
        _mc_names = [s.name for s in _mono_callers[:4]]
        _mc_str = ", ".join(_mc_names)
        if len(_mono_callers) > 4:
            _mc_str += f" +{len(_mono_callers) - 4} more"
        lines.append(f"mono-callers: {len(_mono_callers)} exported fns with only 1 caller file ({_mc_str})")


    # S154: Single-caller fns — source functions called by exactly one other function.
    # These are prime inlining candidates; many single-caller fns = over-extracted code.
    # Only shown when 5+ such functions found.
    _s154_single_callers = [
        sym for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method")
        and not _is_test_file(sym.file_path)
        and sym.exported is False
        and len({c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}) == 0
        and len(graph.callers_of(sym.id)) == 1
    ]
    if len(_s154_single_callers) >= 5:
        lines.append(
            f"single-caller fns: {len(_s154_single_callers)} private fns have exactly 1 caller"
            f" — consider inlining"
        )

    # S161: Hub files — source files imported by 10+ other files.
    # High import fan-in = central dependency; a change here blasts everywhere.
    # Only shown when >= 1 non-test file has 10+ unique importing files.
    _s161_hubs = sorted(
        [
            (len([i for i in graph.importers_of(fp) if i in graph.files and not _is_test_file(i) and i != fp]), fp)
            for fp in graph.files
            if not _is_test_file(fp)
        ],
        reverse=True,
    )
    _s161_hubs = [(n, fp) for n, fp in _s161_hubs if n >= 10]
    if _s161_hubs:
        _s161_fps = [fp for _, fp in _s161_hubs[:3]]
        _s161_top3 = [_display_path(fp, _s161_fps) for fp in _s161_fps]
        _s161_str = ", ".join(_s161_top3)
        lines.append(
            f"hub files: {len(_s161_hubs)} files imported by 10+ others ({_s161_str})"
            f" — changes here have wide blast radius"
        )

    # S203: Undocumented exports — high percentage of exported symbols with empty docstrings.
    # Public API without docs is harder to use correctly and harder to review for contracts.
    # Only shown when >= 10 exported non-test symbols exist and >= 50% have no doc.
    _s203_exports = [
        s for s in graph.symbols.values()
        if s.exported and not _is_test_file(s.file_path)
        and s.kind.value in ("function", "method", "class")
    ]
    if len(_s203_exports) >= 10:
        _s203_no_doc = [s for s in _s203_exports if not s.doc]
        _s203_pct = len(_s203_no_doc) / len(_s203_exports) * 100
        if _s203_pct >= 50:
            lines.append(
                f"undocumented exports: {len(_s203_no_doc)}/{len(_s203_exports)}"
                f" exported symbols lack docstrings ({_s203_pct:.0f}%)"
            )


    # S191: API-only files — source files where every tracked symbol is exported.
    # Files where all symbols are public = pure API surface; any change risks breaking callers.
    # Only shown when 2+ non-trivial source files (>= 3 symbols) are fully exported.
    _s191_api_files: list[str] = []
    for _fp191 in graph.files:
        if _is_test_file(_fp191):
            continue
        _syms191 = [
            s for s in graph.symbols.values()
            if s.file_path == _fp191 and s.kind.value in ("function", "method", "class", "constant")
        ]
        if len(_syms191) >= 3 and all(s.exported for s in _syms191):
            _s191_api_files.append(_fp191)
    if len(_s191_api_files) >= 2:
        _s191_names = [fp.rsplit("/", 1)[-1] for fp in _s191_api_files[:3]]
        _s191_str = ", ".join(_s191_names)
        if len(_s191_api_files) > 3:
            _s191_str += f" +{len(_s191_api_files) - 3} more"
        lines.append(
            f"api-only files: {len(_s191_api_files)} files where all symbols are exported"
            f" ({_s191_str}) — pure contract files, high breaking-change risk"
        )


    # S252: God symbol — single non-test symbol called from 10+ distinct files.
    # One function/class/module that everything depends on is an architectural bottleneck.
    # Only shown when 1+ symbol has 10+ distinct non-test caller files.
    _s252_god: list[tuple[int, str, str]] = []
    for _sym252 in graph.symbols.values():
        if _sym252.kind.value not in ("function", "method", "class"):
            continue
        if _is_test_file(_sym252.file_path):
            continue
        _caller_files252 = {
            c.file_path for c in graph.callers_of(_sym252.id)
            if not _is_test_file(c.file_path) and c.file_path != _sym252.file_path
        }
        if len(_caller_files252) >= 10:
            _s252_god.append((len(_caller_files252), _sym252.name, _sym252.file_path))
    if _s252_god:
        _s252_god.sort(key=lambda x: -x[0])
        _n252, _name252, _fp252 = _s252_god[0]
        lines.append(
            f"god symbol: {_name252} ({_fp252.rsplit('/', 1)[-1]}) called from {_n252} files"
            f" — central bottleneck; changes here blast everywhere"
        )

    # S247: API-heavy codebase — 3+ source files with "api", "route", "endpoint", or
    # "view" in their names. Changes often need to update route handlers, serializers, and tests.
    # Only shown when 3+ such files detected (1-2 is typical for small apps).
    _s247_api_stems = {"api", "route", "routes", "router", "routers", "endpoint", "endpoints",
                       "view", "views", "handler", "handlers", "controller", "controllers",
                       "resource", "resources", "schema", "schemas", "serializer", "serializers"}
    _s247_api_files = [
        fp for fp in graph.files
        if not _is_test_file(fp)
        and graph.files[fp].language.value in _CODE_LANGS
        and fp.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in _s247_api_stems
    ]
    if len(_s247_api_files) >= 3:
        _s247_names = sorted({fp.rsplit("/", 1)[-1] for fp in _s247_api_files})[:3]
        _s247_str = ", ".join(_s247_names)
        if len(_s247_api_files) > 3:
            _s247_str += f" +{len(_s247_api_files) - 3} more"
        lines.append(
            f"api-heavy: {len(_s247_api_files)} API/route files ({_s247_str})"
            f" — changes often need aligned updates in routes, schemas, and handlers"
        )



    return lines


def _signals_structure(
    graph: Tempo, *, _src_fps: list[str], _test_fps: set[str],
    modules: dict[str, list[str]], _s220_entry_files: list[str],
) -> list[str]:
    """Structural signals."""
    lines: list[str] = []

    # Lone files: source files with both 0 outgoing imports AND 0 incoming importers.
    # Completely structurally isolated — either dead utility modules or entry points not yet wired.
    # Only shown when 3+ exist (single lone file = normal for utilities; 3+ = structural smell).
    if len(_src_fps) >= 6:
        _LONE_SKIP = {
            "__init__.py", "__main__.py", "main.py", "app.py", "manage.py",
            "cli.py", "server.py", "conftest.py", "setup.py",
        }
        _lone_files: list[str] = []
        _src_import_set = {
            e.source_id for e in graph.edges
            if e.kind == EdgeKind.IMPORTS and e.source_id in graph.files
        }
        _imported_set = {
            e.target_id for e in graph.edges
            if e.kind == EdgeKind.IMPORTS and e.target_id in graph.files
        }
        for _fp in _src_fps:
            _bname = _fp.rsplit("/", 1)[-1]
            if _bname in _LONE_SKIP or _is_test_file(_fp):
                continue
            if _fp not in _src_import_set and _fp not in _imported_set and graph.files[_fp].symbols:
                _lone_files.append(_fp)
        if len(_lone_files) >= 3:
            _lone_names = [fp.rsplit("/", 1)[-1] for fp in _lone_files[:4]]
            _lone_str = ", ".join(_lone_names)
            if len(_lone_files) > 4:
                _lone_str += f" +{len(_lone_files) - 4} more"
            lines.append(f"lone files ({len(_lone_files)}): {_lone_str} — no imports or importers")

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
    _td_per_file: dict[str, int] = {}
    _td_file_count = 0
    for _fp in _src_fps[:200]:  # cap at 200 to keep I/O bounded
        if Path(_fp).suffix not in _SRC_EXTS:
            continue
        try:
            _content = (Path(graph.root) / _fp).read_text(errors="replace")
            _matches = _TD_PAT.findall(_content)
            if _matches:
                _td_file_count += 1
                _td_per_file[_fp] = len(_matches)
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
        # Per-file tech debt concentration: top 3 files with most markers.
        # Tells agents where to focus cleanup effort.
        if _td_total >= 5 and _td_per_file:
            _debt_hot = sorted(_td_per_file.items(), key=lambda x: -x[1])[:3]
            _debt_hot = [(fp, n) for fp, n in _debt_hot if n >= 3]
            if _debt_hot:
                _dh_parts = [f"{fp.rsplit('/', 1)[-1]} ({n})" for fp, n in _debt_hot]
                lines.append(f"debt hot: {', '.join(_dh_parts)}")


    # S134: Largest module — the top-level directory with the most source files.
    # Points agents to the heaviest architectural weight; also flags where complexity lives.
    # Only shown when 3+ top-level dirs exist (otherwise trivial single-package repos).
    if modules and len(modules) >= 3:
        _s134_file_counts: dict[str, int] = {}
        _s134_sym_counts: dict[str, int] = {}
        for _s134_mod, _s134_fps in modules.items():
            _s134_src_fps = [fp for fp in _s134_fps if not _is_test_file(fp)]
            if not _s134_src_fps:
                continue
            _s134_file_counts[_s134_mod] = len(_s134_src_fps)
            _s134_sym_counts[_s134_mod] = sum(
                len(graph.files[fp].symbols) for fp in _s134_src_fps if fp in graph.files
            )
        if _s134_file_counts:
            _s134_top = max(_s134_file_counts, key=lambda m: _s134_file_counts[m])
            _s134_fc = _s134_file_counts[_s134_top]
            _s134_sc = _s134_sym_counts.get(_s134_top, 0)
            if _s134_fc >= 3:
                lines.append(
                    f"largest module: {_s134_top}/ ({_s134_fc} files, {_s134_sc} symbols)"
                )


    # S157: Deepest module — the most deeply nested directory path in the project.
    # Deep nesting (>= 4 levels) indicates over-structured architecture.
    # Only shown when 3+ files exist and max depth >= 4.
    _s157_max_depth = 0
    _s157_max_dir = ""
    for _fp157 in graph.files:
        if _is_test_file(_fp157):
            continue
        _parts157 = _fp157.split("/")
        _depth157 = len(_parts157) - 1  # dirs only, not the file itself
        if _depth157 > _s157_max_depth:
            _s157_max_depth = _depth157
            _s157_max_dir = "/".join(_parts157[:-1])
    if len(graph.files) >= 2 and _s157_max_depth >= 4:
        lines.append(f"deepest path: {_s157_max_dir}/ ({_s157_max_depth} levels) — deeply nested structure")


    # S197: Constant explosion — high number of named constants/variables across source files.
    # Many constants may indicate magic-number parameterization or config sprawl.
    # Only shown when >= 20 non-test constant/variable symbols exist.
    _s197_consts = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path)
        and s.kind.value in ("constant", "variable")
    ]
    if len(_s197_consts) >= 20:
        _top_const_files = sorted(
            set(s.file_path for s in _s197_consts), key=lambda fp: -sum(
                1 for s in _s197_consts if s.file_path == fp
            )
        )[:2]
        _cf_str = ", ".join(fp.rsplit("/", 1)[-1] for fp in _top_const_files)
        lines.append(
            f"constant explosion: {len(_s197_consts)} named constants/variables"
            f" — consider consolidating into config module ({_cf_str} heaviest)"
        )


    # S179: Mixed-role files — non-test source files that contain test_ symbols.
    # Production code mixed with test code signals poor separation of concerns.
    # Only shown when >= 1 such mixed file exists.
    _s179_mixed: list[str] = []
    for _fp179 in graph.files:
        if _is_test_file(_fp179):
            continue
        _has_test_sym179 = any(
            s.name.startswith("test_")
            for s in graph.symbols.values()
            if s.file_path == _fp179
        )
        if _has_test_sym179:
            _s179_mixed.append(_fp179)
    if _s179_mixed:
        _s179_str = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s179_mixed[:3])
        if len(_s179_mixed) > 3:
            _s179_str += f" +{len(_s179_mixed) - 3} more"
        lines.append(
            f"mixed-role files: {len(_s179_mixed)} source files contain test_ symbols"
            f" ({_s179_str}) — move tests to dedicated test files"
        )

    # S173: Private ratio — percentage of non-test symbols that are unexported (private).
    # High private ratio (>= 80%) means the codebase hides most logic; low = over-exposed API.
    # Only shown when >= 20 source symbols exist.
    _s173_src_syms = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path)
    ]
    if len(_s173_src_syms) >= 20:
        _s173_private_count = sum(1 for s in _s173_src_syms if not s.exported)
        _s173_ratio = _s173_private_count / len(_s173_src_syms) * 100
        if _s173_ratio >= 80:
            lines.append(
                f"private ratio: {_s173_ratio:.0f}% of symbols are unexported"
                f" — heavily internalized codebase"
            )

    # S167: Orphan files — source files with 0 importers and 0 external callers to any symbol.
    # Isolated files are likely dead entry points or abandoned modules.
    # Only shown when 2+ non-entry-point source files are fully isolated.
    _s167_entry_names = {
        "main", "app", "index", "manage", "cli", "server", "run", "wsgi", "asgi",
        "setup", "conftest", "__main__",
    }
    _s167_orphans: list[str] = []
    for _fp167 in graph.files:
        fi167 = graph.files[_fp167]
        if fi167.language.value not in _CODE_LANGS:
            continue  # skip JSON, YAML, markdown, config — not meaningful orphans
        if _is_test_file(_fp167):
            continue
        _stem167 = _fp167.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if _stem167 in _s167_entry_names:
            continue
        _has_importers167 = any(
            i in graph.files and not _is_test_file(i)
            for i in graph.importers_of(_fp167)
        )
        if _has_importers167:
            continue
        _has_callers167 = any(
            len([
                c for c in graph.callers_of(s.id)
                if c.file_path != _fp167
            ]) > 0
            for s in graph.symbols.values()
            if s.file_path == _fp167
        )
        if not _has_callers167:
            _s167_orphans.append(_fp167)
    if len(_s167_orphans) >= 2:
        _s167_names = [fp.rsplit("/", 1)[-1] for fp in _s167_orphans[:3]]
        _s167_str = ", ".join(_s167_names)
        if len(_s167_orphans) > 3:
            _s167_str += f" +{len(_s167_orphans) - 3} more"
        lines.append(
            f"orphan files: {len(_s167_orphans)} isolated files ({_s167_str})"
            f" — not imported or called from anywhere"
        )


    # S220: Multi-entry app — repo has 3+ distinct application entry point files.
    # Multiple entry points can mean inconsistent startup paths or divergent CLI/server behaviors.
    # Agents should verify cross-cutting changes (config, auth, logging) apply to all entry points.
    # Only shown when 3+ entry point stems found among non-test source files.
    _s220_entry_stems = {
        "main", "app", "index", "server", "cli", "run", "manage",
        "wsgi", "asgi", "__main__", "entrypoint", "entry_point",
    }
    _s220_entry_files = [
        fp for fp in graph.files
        if not _is_test_file(fp)
        and fp.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in _s220_entry_stems
        and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s220_entry_files) >= 3:
        _s220_names = [fp.rsplit("/", 1)[-1] for fp in _s220_entry_files[:4]]
        _s220_suffix = f" (+{len(_s220_entry_files) - 3} more)" if len(_s220_entry_files) > 3 else ""
        _s220_str = ", ".join(_s220_names[:3]) + _s220_suffix
        lines.append(
            f"multi-entry app: {len(_s220_entry_files)} entry points ({_s220_str})"
            f" — cross-cutting changes (config, auth, logging) must apply to all"
        )


    # S280: Entry point overload — 5+ distinct entry point files detected.
    # Many entry points suggest a multi-mode application (CLI + server + worker);
    # each entry point has its own startup path that must be maintained independently.
    if len(_s220_entry_files) >= 5:
        _ep_names280 = [fp.rsplit("/", 1)[-1] for fp in _s220_entry_files[:5]]
        _ep_str280 = ", ".join(_ep_names280)
        if len(_s220_entry_files) > 5:
            _ep_str280 += f" +{len(_s220_entry_files) - 5} more"
        lines.append(
            f"entry point overload: {len(_s220_entry_files)} entry points ({_ep_str280})"
            f" — multi-mode app; each startup path must be maintained separately"
        )



    # S292: Polyglot codebase — 3+ programming languages detected in source files.
    # Polyglot repos require multi-language expertise; each language adds its own
    # toolchain, build system, and dependency management overhead.
    _s292_langs: set[str] = set()
    for _fp292, _fi292 in graph.files.items():
        if not _is_test_file(_fp292) and _fi292.language.value in _CODE_LANGS:
            _s292_langs.add(_fi292.language.value)
    if len(_s292_langs) >= 3:
        _lang_list292 = sorted(_s292_langs)[:4]
        _lang_str292 = ", ".join(_lang_list292)
        if len(_s292_langs) > 4:
            _lang_str292 += f" +{len(_s292_langs) - 4} more"
        lines.append(
            f"polyglot: {len(_s292_langs)} languages ({_lang_str292})"
            f" — multi-language repo; each adds toolchain and CI complexity"
        )

    # S300: Multi-package repo — 3+ independent setup.py/pyproject.toml/package.json files.
    # Monorepos with multiple packages require coordinating version bumps, shared deps,
    # and release cycles across all packages simultaneously.
    _s300_pkg_files = {
        "setup.py", "setup.cfg", "pyproject.toml", "package.json", "Cargo.toml",
        "go.mod", "pom.xml", "build.gradle",
    }
    _s300_pkg_dirs: list[str] = []
    for _fp300 in graph.files:
        _name300 = _fp300.rsplit("/", 1)[-1]
        if _name300 in _s300_pkg_files:
            _dir300 = _fp300.rsplit("/", 1)[0] if "/" in _fp300 else "."
            _s300_pkg_dirs.append(_dir300)
    _s300_unique_dirs = list(dict.fromkeys(_s300_pkg_dirs))  # preserve order, deduplicate
    if len(_s300_unique_dirs) >= 3:
        lines.append(
            f"multi-package: {len(_s300_unique_dirs)} packages detected"
            f" — monorepo; cross-package changes need coordinated versioning"
        )

    # S306: Plugin-heavy — 5+ source files in plugins/extensions/addons/modules directory.
    # Plugin-heavy codebases require understanding the plugin lifecycle and dispatch model
    # before making changes that touch the extension API.
    _s306_plugin_dirs = ("plugins", "extensions", "addons", "modules", "extras", "contrib")
    _s306_plugin_files: list[str] = []
    for _fp306 in graph.files:
        _parts306 = _fp306.lower().replace("\\", "/").split("/")
        if any(p in _s306_plugin_dirs for p in _parts306[:-1]):
            if not _is_test_file(_fp306):
                _s306_plugin_files.append(_fp306)
    if len(_s306_plugin_files) >= 5:
        lines.append(
            f"plugin-heavy: {len(_s306_plugin_files)} files in plugin/extension directories"
            f" — understand the plugin lifecycle before touching extension APIs"
        )


    # S324: Stub-heavy — 20%+ of source functions have a 1-line body (pass/raise NotImplementedError).
    # Stubs indicate planned-but-unimplemented features; high stub ratio means the codebase
    # is partly fictional — calling code assumes implementations that don't exist yet.
    _s324_src_fns = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.file_path in graph.files
        and graph.files[s.file_path].language.value in _CODE_LANGS
    ]
    _s324_stub_fns = [
        s for s in _s324_src_fns
        if s.line_count <= 1
    ]
    if len(_s324_src_fns) >= 10 and len(_s324_stub_fns) / len(_s324_src_fns) >= 0.20:
        _pct324 = int(100 * len(_s324_stub_fns) / len(_s324_src_fns))
        lines.append(
            f"stub-heavy: {_pct324}% of functions are stubs ({len(_s324_stub_fns)}/{len(_s324_src_fns)})"
            f" — many unimplemented functions; callers may depend on behavior that doesn't exist"
        )

    # S316: Async-heavy — 5+ source files use async def patterns.

    # S330: Data-pipeline codebase — 5+ files contain pipeline/processor/etl/transform patterns.
    # Pipeline architectures require understanding the data contract between stages;
    # changes to intermediate formats or schema break all downstream stages.
    _s330_pipe_words = ("pipeline", "processor", "etl", "transform", "ingestion", "enrichment")
    _s330_pipe_files: list[str] = []
    for _fp330 in graph.files:
        if _is_test_file(_fp330):
            continue
        _name330 = _fp330.lower().replace("\\", "/")
        if any(w in _name330 for w in _s330_pipe_words):
            _s330_pipe_files.append(_fp330)
    if len(_s330_pipe_files) >= 5:
        lines.append(
            f"data-pipeline: {len(_s330_pipe_files)} pipeline/ETL files detected"
            f" — understand data contract between stages before changing intermediate formats"
        )


    # S336: Class-dominant codebase — classes outnumber standalone functions by 2×.
    # Class-heavy codebases (OOP-heavy) accumulate inheritance hierarchies, global state
    # via attributes, and implicit coupling through shared base classes.
    _s336_src_syms = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path)
        and s.file_path in graph.files
        and graph.files[s.file_path].language.value in _CODE_LANGS
    ]
    _s336_classes = [s for s in _s336_src_syms if s.kind.value == "class"]
    _s336_fns = [s for s in _s336_src_syms if s.kind.value == "function"]
    if len(_s336_fns) >= 5 and len(_s336_classes) >= len(_s336_fns) * 2:
        lines.append(
            f"class-dominant: {len(_s336_classes)} classes vs {len(_s336_fns)} functions"
            f" — OOP-heavy; watch for implicit coupling via base classes and shared attributes"
        )


    # S348: Large directory — single directory has 20+ source files.
    # Directories with 20+ files have grown past cohesion; navigation is difficult and
    # changes are hard to locate without grep. Consider splitting by responsibility.
    _s348_dir_counts: dict[str, int] = {}
    for _fp348 in graph.files:
        if _is_test_file(_fp348):
            continue
        _dir348 = _fp348.rsplit("/", 1)[0] if "/" in _fp348 else "."
        _s348_dir_counts[_dir348] = _s348_dir_counts.get(_dir348, 0) + 1
    _s348_large_dirs = [(d, n) for d, n in _s348_dir_counts.items() if n >= 20]
    if _s348_large_dirs:
        _biggest348 = max(_s348_large_dirs, key=lambda x: x[1])
        _dir_name348 = _biggest348[0].rsplit("/", 1)[-1] if "/" in _biggest348[0] else _biggest348[0]
        lines.append(
            f"large directory: {_dir_name348}/ has {_biggest348[1]} source files"
            f" — past cohesion limit; consider splitting by responsibility"
        )


    # S349: Micro-module codebase — 50%+ of source files define only 1-2 symbols.
    # Micro-module repos fragment logic into tiny files; increases import graph complexity
    # and makes tracing data flow require opening many files in sequence.
    _s349_src_files = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s349_src_files) >= 10:
        _s349_tiny = [
            fp for fp in _s349_src_files
            if len([s for s in graph.symbols.values() if s.file_path == fp]) <= 2
        ]
        if len(_s349_tiny) / len(_s349_src_files) >= 0.50:
            _pct349 = int(100 * len(_s349_tiny) / len(_s349_src_files))
            lines.append(
                f"micro-module: {_pct349}% of source files ({len(_s349_tiny)}/{len(_s349_src_files)}) define ≤2 symbols"
                f" — high fragmentation; tracing flow requires navigating many small files"
            )


    # S373: Thin test suite — 50%+ of test files have only 1-2 test functions.
    # A thin test suite has poor coverage density; each test file adds test-run overhead
    # but contributes little coverage, suggesting tests are stubs or never maintained.
    _s373_test_files_all = [
        fp for fp in graph.files
        if _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s373_test_files_all) >= 5:
        _s373_thin = [
            fp for fp in _s373_test_files_all
            if len([
                s for s in graph.symbols.values()
                if s.file_path == fp and s.name.lower().startswith("test_")
            ]) <= 2
        ]
        if len(_s373_thin) / len(_s373_test_files_all) >= 0.50:
            _pct373 = int(100 * len(_s373_thin) / len(_s373_test_files_all))
            lines.append(
                f"thin test suite: {_pct373}% of test files have ≤2 test functions"
                f" — low coverage density; many stubs; consider consolidating or expanding tests"
            )

    # S367: Monorepo detection — multiple package manifests detected in different directories.
    # Monorepos require coordinated changes across packages; a single logical change may need
    # updates in multiple packages, each with its own dependency and test pipeline.
    _s367_manifest_names = (
        "package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml",
        "build.gradle", "Gemfile", "composer.json",
    )
    _s367_manifest_dirs: set[str] = set()
    for _fp367 in graph.files:
        _base367 = _fp367.rsplit("/", 1)[-1] if "/" in _fp367 else _fp367
        if _base367 in _s367_manifest_names:
            _dir367 = _fp367.rsplit("/", 1)[0] if "/" in _fp367 else "."
            _s367_manifest_dirs.add(_dir367)
    if len(_s367_manifest_dirs) >= 2:
        lines.append(
            f"monorepo: {len(_s367_manifest_dirs)} package manifests detected in separate directories"
            f" — coordinated cross-package changes required; verify all affected packages"
        )

    # S361: Framework detection — codebase uses a recognized web/app framework.
    # Knowing the framework informs code review expectations: Django has signals, Flask has
    # blueprints, FastAPI has dependency injection — each with their own change-impact patterns.
    _s361_framework_patterns: list[tuple[str, str]] = [
        ("django", "Django"),
        ("flask", "Flask"),
        ("fastapi", "FastAPI"),
        ("rails", "Rails"),
        ("spring", "Spring"),
        ("express", "Express"),
        ("nextjs", "Next.js"),
        ("nuxt", "Nuxt"),
        ("laravel", "Laravel"),
    ]
    _s361_detected: list[str] = []
    for _fp361, _fi361 in graph.files.items():
        if _is_test_file(_fp361):
            continue
        _base361 = _fp361.lower()
        for _pat361, _label361 in _s361_framework_patterns:
            if _pat361 in _base361 and _label361 not in _s361_detected:
                _s361_detected.append(_label361)
    if not _s361_detected:
        # Check import edges for framework names
        for _e361 in graph.edges:
            if _e361.kind.value == "imports":
                _tid361 = _e361.target_id.lower()
                for _pat361, _label361 in _s361_framework_patterns:
                    if _pat361 in _tid361 and _label361 not in _s361_detected:
                        _s361_detected.append(_label361)
    if _s361_detected:
        _fw_str361 = ", ".join(_s361_detected[:3])
        lines.append(
            f"framework: {_fw_str361} detected"
            f" — framework conventions shape change impact; review framework-specific patterns"
        )

    # S355: Test-only codebase — no source files found outside of test files.
    # A repo with only test files and no source is likely a test-only slice or
    # a misconfigured project; signals that the tempograph graph may be incomplete.
    _s355_all_files = [fp for fp in graph.files if graph.files[fp].language.value in _CODE_LANGS]
    _s355_test_files = [fp for fp in _s355_all_files if _is_test_file(fp)]
    if len(_s355_all_files) >= 3 and len(_s355_test_files) == len(_s355_all_files):
        lines.append(
            f"test-only: all {len(_s355_all_files)} code files are test files"
            f" — no source files detected; graph may be missing source root or pointing at test directory"
        )


    # S379: Deeply nested source — 30%+ of source files are 3+ directory levels deep.
    # Deep nesting indicates a large codebase that has grown without restructuring;
    # navigating 4+ levels to find a file slows onboarding and refactor discovery.
    _s379_src_fps = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s379_src_fps) >= 10:
        _s379_deep = [fp for fp in _s379_src_fps if fp.count("/") >= 3]
        if len(_s379_deep) / len(_s379_src_fps) >= 0.30:
            _pct379 = int(100 * len(_s379_deep) / len(_s379_src_fps))
            lines.append(
                f"deep nesting: {_pct379}% of source files ({len(_s379_deep)}/{len(_s379_src_fps)}) are 3+ levels deep"
                f" — complex directory hierarchy; consider flattening to reduce navigation friction"
            )


    # S385: High dead export ratio — 30%+ of exported symbols have 0 callers from other files.
    # A high proportion of uncalled exports indicates over-engineered public API surface;
    # each unused export creates maintenance burden and risk of unintended consumers.
    _s385_exported = [
        s for s in graph.symbols.values()
        if s.exported and not _is_test_file(s.file_path)
        and s.kind.value in ("function", "method", "class")
        and s.file_path in graph.files
        and graph.files[s.file_path].language.value in _CODE_LANGS
    ]
    if len(_s385_exported) >= 15:
        _s385_uncalled = [
            s for s in _s385_exported
            if not any(e.target_id == s.id and e.kind.value == "calls" for e in graph.edges)
            and not graph.importers_of(s.file_path)
        ]
        if len(_s385_uncalled) / len(_s385_exported) >= 0.30:
            _pct385 = int(100 * len(_s385_uncalled) / len(_s385_exported))
            lines.append(
                f"unused exports: {_pct385}% of exported symbols ({len(_s385_uncalled)}/{len(_s385_exported)}) have no callers"
                f" — over-engineered API surface; unexported unused symbols reduce blast radius"
            )


    # S397: Mixed sync/async boundary — codebase has async functions called from non-async code.
    # Mixed boundaries create subtle bugs: calling async functions without await silently
    # returns a coroutine object instead of the result, or blocks the event loop.
    _s397_async_syms = {
        s.id for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.signature.startswith("async ")
    }
    _s397_sync_syms = {
        s.id for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and not s.signature.startswith("async ")
    }
    # Find sync calling async (cross-boundary call)
    _s397_cross_calls = [
        e for e in graph.edges
        if e.kind.value == "calls"
        and e.source_id in _s397_sync_syms
        and e.target_id in _s397_async_syms
    ]
    if len(_s397_async_syms) >= 3 and len(_s397_cross_calls) >= 2:
        lines.append(
            f"mixed async/sync: {len(_s397_cross_calls)} sync→async cross-boundary call(s)"
            f" — sync functions calling async without await; may return coroutines silently"
        )

    # S391: No tests detected — 0 test files found in the codebase.
    # A codebase with no tests has no regression protection; any change can silently break
    # behavior that previously worked. This is a critical quality signal.
    _s391_all_code = [fp for fp in graph.files if graph.files[fp].language.value in _CODE_LANGS]
    _s391_tests = [fp for fp in _s391_all_code if _is_test_file(fp)]
    _s391_src = [fp for fp in _s391_all_code if not _is_test_file(fp)]
    if len(_s391_src) >= 3 and len(_s391_tests) == 0:
        lines.append(
            f"no tests: 0 test files detected across {len(_s391_src)} source files"
            f" — no regression protection; any change can silently break existing behavior"
        )

    # S403: Single-file majority — one source file accounts for 50%+ of all source lines.
    # A single file dominating the codebase indicates a god module; future maintainers
    # will underestimate the blast radius of any change to that file.
    _s403_src_files = [
        fp for fp in graph.files
        if graph.files[fp].language.value in _CODE_LANGS
        and not _is_test_file(fp)
    ]
    if len(_s403_src_files) >= 4:
        _s403_total_lines = sum(
            (graph.files[fp].line_count or 0) for fp in _s403_src_files
        )
        if _s403_total_lines > 0:
            _s403_biggest = max(_s403_src_files, key=lambda fp: graph.files[fp].line_count or 0)
            _s403_biggest_lines = graph.files[_s403_biggest].line_count or 0
            _s403_pct = _s403_biggest_lines / _s403_total_lines
            if _s403_pct >= 0.50:
                _s403_short = _s403_biggest.rsplit("/", 1)[-1]
                lines.append(
                    f"single-file majority: {_s403_short} holds {_s403_biggest_lines} of"
                    f" {_s403_total_lines} total lines ({_s403_pct:.0%})"
                    f" — god module risk; split into focused modules before growing further"
                )

    # S409: High constants ratio — 40%+ of non-test source symbols are constants/variables.
    # A high proportion of top-level variables suggests the codebase is configuration-heavy
    # or has magic values scattered across modules rather than centralized config.
    _s409_src_syms = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path)
    ]
    _s409_const_syms = [
        s for s in _s409_src_syms
        if s.kind.value in ("variable", "constant")
    ]
    if len(_s409_src_syms) >= 10 and len(_s409_const_syms) / len(_s409_src_syms) >= 0.40:
        _s409_pct = len(_s409_const_syms) / len(_s409_src_syms)
        lines.append(
            f"high constants ratio: {len(_s409_const_syms)} of {len(_s409_src_syms)} symbols"
            f" ({_s409_pct:.0%}) are constants/variables"
            f" — consider centralizing config; scattered magic values impede testability"
        )

    # S415: Multiple entry points — 3+ files each define a main() or cli() function.
    # A codebase with many independent entry points may have duplicate startup logic;
    # shared init code (logging, config) can diverge across entry points silently.
    _s415_entry_syms = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and s.name in ("main", "cli", "entrypoint", "entry_point", "run_app", "start")
        and not _is_test_file(s.file_path)
    ]
    _s415_entry_files = {s.file_path for s in _s415_entry_syms}
    if len(_s415_entry_files) >= 3:
        _entry_names415 = ", ".join(s.rsplit("/", 1)[-1] for s in list(_s415_entry_files)[:3])
        lines.append(
            f"multiple entry points: {len(_s415_entry_files)} files each define an entry function"
            f" ({_entry_names415})"
            f" — shared init logic (config, logging) may diverge; centralize startup orchestration"
        )

    # S421: Flat codebase — all source files are at the root level with no subdirectory structure.
    # A flat layout becomes unnavigable past ~10 files; adding subdirectory organization
    # forces explicit module boundaries and prevents circular import tangles.
    _s421_src_files = [
        fp for fp in graph.files
        if graph.files[fp].language.value in _CODE_LANGS
        and not _is_test_file(fp)
    ]
    _s421_subdirs = {
        fp.rsplit("/", 1)[0] for fp in _s421_src_files
        if "/" in fp
    }
    if len(_s421_src_files) >= 8 and len(_s421_subdirs) == 0:
        lines.append(
            f"flat codebase: {len(_s421_src_files)} source files all at root with no subdirectory structure"
            f" — consider grouping by domain; flat layouts become unnavigable past ~10 files"
        )

    # S427: High method-to-class ratio — classes average 10+ methods each.
    # Classes with many methods are doing too much; long method lists indicate
    # low cohesion and a class that should be split into specialized collaborators.
    _s427_classes = [
        s for s in graph.symbols.values()
        if s.kind.value == "class" and not _is_test_file(s.file_path)
    ]
    if len(_s427_classes) >= 2:
        _s427_method_counts = []
        for cls in _s427_classes:
            _method_count = sum(
                1 for s in graph.symbols.values()
                if s.parent_id == cls.id and s.kind.value == "method"
            )
            _s427_method_counts.append(_method_count)
        _s427_avg_methods = sum(_s427_method_counts) / len(_s427_method_counts)
        if _s427_avg_methods >= 10:
            lines.append(
                f"high method density: {len(_s427_classes)} classes averaging"
                f" {_s427_avg_methods:.0f} methods each"
                f" — large class surface areas; split by Single Responsibility Principle"
            )

    return lines


def _signals_async_oop(
    graph: Tempo, *, _s220_entry_files: list[str],
) -> list[str]:
    """Async/OOP signals."""
    lines: list[str] = []

    # S84: Async surface — count exported async functions to signal async-heavy codebases.
    # Helps agents understand whether the project needs coroutine/event-loop awareness.
    # Only shown when 3+ exported async functions exist (prevents false signal on tiny projects).
    _async_syms = [
        sym for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method")
        and sym.exported
        and not _is_test_file(sym.file_path)
        and sym.signature.startswith("async ")
    ]
    if len(_async_syms) >= 3:
        _async_files = len({s.file_path for s in _async_syms})
        lines.append(f"async surface: {len(_async_syms)} exported async functions in {_async_files} files")

    # S433: No async code — service-sized codebase with no async def functions.
    # A sync-only codebase cannot handle concurrent I/O efficiently; any blocking call
    # stalls the entire thread, making async adoption a potential future rewrite.
    _s433_src_files = [
        fp for fp in graph.files
        if not _is_test_file(fp)
        and any(fp.endswith(ext) for ext in (".py", ".js", ".ts", ".jsx", ".tsx"))
    ]
    if len(_s433_src_files) >= 10 and len(_async_syms) == 0:
        lines.append(
            f"sync-only: {len(_s433_src_files)} source files with no async def functions"
            f" — all I/O is blocking; async adoption requires rewriting call sites"
        )

    # S243: Framework/library detected — codebase imports a well-known web framework or library.
    # Shown to orient agents: know what routing, ORM, and middleware patterns to expect.
    # Only shown when 1+ framework import found across source files.
    _s243_frameworks: dict[str, str] = {
        "flask": "Flask", "django": "Django", "fastapi": "FastAPI",
        "starlette": "Starlette", "tornado": "Tornado", "aiohttp": "aiohttp",
        "falcon": "Falcon", "bottle": "Bottle", "sanic": "Sanic",
        "sqlalchemy": "SQLAlchemy", "alembic": "Alembic", "celery": "Celery",
        "pydantic": "Pydantic",
        "express": "Express", "koa": "Koa", "fastify": "Fastify",
        "nestjs": "NestJS", "nextjs": "Next.js",
    }
    _s243_detected: list[str] = []
    _s243_seen: set[str] = set()
    for _fi243 in graph.files.values():
        if _is_test_file(_fi243.language.value):
            continue
        for _imp243 in _fi243.imports:
            _imp243_lower = _imp243.lower()
            for _fw_key, _fw_label in _s243_frameworks.items():
                if _fw_key not in _s243_seen and _fw_key in _imp243_lower:
                    _s243_detected.append(_fw_label)
                    _s243_seen.add(_fw_key)
    if _s243_detected:
        _s243_str = ", ".join(_s243_detected[:3])
        if len(_s243_detected) > 3:
            _s243_str += f" +{len(_s243_detected) - 3} more"
        lines.append(f"frameworks: {_s243_str}")


    # S259: Global-state managers — 3+ source classes whose names end in Manager, Registry,
    # Pool, Cache, or Singleton. These often hold global state and are risky to change.
    # Only shown when 3+ such classes found.
    _s255_mgr_suffixes = ("manager", "registry", "pool", "cache", "singleton",
                          "store", "repository", "repo", "hub", "bus", "broker",
                          "container", "context", "session")
    _s255_mgr_classes = [
        sym for sym in graph.symbols.values()
        if sym.kind.value == "class"
        and not _is_test_file(sym.file_path)
        and any(sym.name.lower().endswith(sfx) for sfx in _s255_mgr_suffixes)
    ]
    if len(_s255_mgr_classes) >= 3:
        _mgr_names = [s.name for s in _s255_mgr_classes[:3]]
        _mgr_str = ", ".join(_mgr_names)
        if len(_s255_mgr_classes) > 3:
            _mgr_str += f" +{len(_s255_mgr_classes) - 3} more"
        lines.append(
            f"global-state classes: {len(_s255_mgr_classes)} managers/registries ({_mgr_str})"
            f" — likely hold global state; test initialization and teardown carefully"
        )


    # S274: OOP-heavy codebase — 20+ class definitions in non-test source files.
    # Large class counts suggest heavy object orientation; complex inheritance hierarchies
    # and class bloat are common risks. Consider checking for god classes and deep inheritance.
    _s274_classes = [
        sym for sym in graph.symbols.values()
        if sym.kind.value == "class"
        and not _is_test_file(sym.file_path)
        and graph.files.get(sym.file_path) is not None
        and graph.files[sym.file_path].language.value in _CODE_LANGS
    ]
    if len(_s274_classes) >= 20:
        lines.append(
            f"oop-heavy: {len(_s274_classes)} class definitions in source code"
            f" — complex OOP; watch for deep inheritance and god classes"
        )



    # S316: Async-heavy — 5+ source files use async def patterns.
    # Async-heavy codebases require understanding event-loop semantics, cancellation,
    # and context propagation before safely introducing blocking calls.
    _s316_async_files: list[str] = []
    for _fp316, _fi316 in graph.files.items():
        if _is_test_file(_fp316):
            continue
        _imports316 = " ".join(_fi316.imports).lower() if _fi316.imports else ""
        _has_async316 = "asyncio" in _imports316 or "aiohttp" in _imports316
        if not _has_async316:
            _has_async316 = any(
                s.signature and s.signature.startswith("async ")
                for s in graph.symbols.values()
                if s.file_path == _fp316 and s.kind.value in ("function", "method")
            )
        if _has_async316:
            _s316_async_files.append(_fp316)
    if len(_s316_async_files) >= 5:
        lines.append(
            f"async-heavy: {len(_s316_async_files)} source files use async patterns"
            f" — event-loop semantics apply; avoid introducing blocking calls"
        )


    return lines


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

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
    try:
        from ..git import file_commit_counts as _file_commit_counts
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

    # Untested hot: hot symbols (>=3 caller files) with zero test file callers.
    # These are the most dangerous to refactor — widely used but unprotected by tests.
    _untested_hot: list[tuple[int, str]] = []
    for sym in graph.symbols.values():
        if sym.kind.value not in ("function", "method") or _is_test_file(sym.file_path):
            continue
        _all_callers = graph.callers_of(sym.id)
        _src_caller_files = {c.file_path for c in _all_callers if c.file_path != sym.file_path and not _is_test_file(c.file_path)}
        _test_callers = [c for c in _all_callers if _is_test_file(c.file_path)]
        if len(_src_caller_files) >= 3 and not _test_callers:
            _untested_hot.append((len(_src_caller_files), sym.name, sym.file_path))
    if _untested_hot:
        _untested_hot.sort(key=lambda x: -x[0])
        _uh_parts = [
            f"{name} ({n}, {fp.rsplit('/', 1)[-1]})"
            for n, name, fp in _untested_hot[:3]
        ]
        lines.append(f"untested hot: {', '.join(_uh_parts)} — no test coverage")

    # Stable core: files with high import fan-in that have rarely changed (>=30d).
    # Foundational infrastructure treated as stable contracts — changes here are highest-risk.
    if graph.root:
        try:
            from ..git import file_last_modified_days as _fld_sc  # noqa: PLC0415
            _sc_candidates: list[tuple[int, int, str]] = []  # (importers, days, fp)
            for _fp, _fi in graph.files.items():
                if _is_test_file(_fp) or not _fi.symbols:
                    continue
                _src_imps = len({
                    i for i in graph.importers_of(_fp)
                    if i != _fp and i in graph.files and not _is_test_file(i)
                })
                if _src_imps >= 5:
                    _days_sc = _fld_sc(graph.root, _fp)
                    if _days_sc is not None and _days_sc >= 30:
                        _sc_candidates.append((_src_imps, _days_sc, _fp))
            if len(_sc_candidates) >= 2:
                _sc_candidates.sort(key=lambda x: -x[0])
                _sc3_fps = [fp for _, _, fp in _sc_candidates[:3]]
                _sc_parts = [f"{_display_path(fp, _sc3_fps)} ({n} importers, {d}d stable)" for n, d, fp in _sc_candidates[:3]]
                lines.append(f"stable core: {', '.join(_sc_parts)}")
        except Exception:
            pass


    # --- Compute shared variables used by multiple signal groups ---
    _src_fps = [fp for fp in graph.files if not _is_test_file(fp) and graph.files[fp].symbols]
    _test_fps = {fp for fp in graph.files if _is_test_file(fp)}

    _exported_src = [
        sym for sym in graph.symbols.values()
        if sym.exported and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method", "class", "interface", "variable", "constant")
    ]

    _src_file_cx: dict[str, int] = {}
    for _sym in graph.symbols.values():
        if not _is_test_file(_sym.file_path) and _sym.complexity >= 1:
            _src_file_cx[_sym.file_path] = _src_file_cx.get(_sym.file_path, 0) + _sym.complexity
    _total_cx = sum(_src_file_cx.values())

    _all_cx_vals = sorted(
        sym.complexity for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method") and not _is_test_file(sym.file_path) and sym.complexity >= 1
    )

    _import_adj: dict[str, list[str]] = {}
    for _edge in graph.edges:
        if _edge.kind == EdgeKind.IMPORTS:
            _src_fp = _edge.source_id
            _tgt_fp = _edge.target_id
            if (
                _src_fp in graph.files and _tgt_fp in graph.files
                and not _is_test_file(_src_fp) and not _is_test_file(_tgt_fp)
            ):
                _import_adj.setdefault(_src_fp, [])
                if _tgt_fp not in _import_adj[_src_fp]:
                    _import_adj[_src_fp].append(_tgt_fp)

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

    modules: dict[str, list[str]] = {}
    for fp in graph.files:
        parts = fp.split("/")
        mod = parts[0] if len(parts) > 1 else "."
        modules.setdefault(mod, []).append(fp)

    _s220_entry_stems = {
        "main", "app", "index", "server", "cli", "run", "manage",
        "wsgi", "asgi", "__main__", "entrypoint", "entry_point",
    }
    _s220_entry_files = [
        fp for fp in graph.files
        if not _is_test_file(fp)
        and fp.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in _s220_entry_stems
        and graph.files[fp].language.value in _CODE_LANGS
    ]

    # --- Call signal-group helpers ---
    lines.extend(_signals_complexity(
        graph, _src_fps=_src_fps, _all_cx_vals=_all_cx_vals,
        _src_file_cx=_src_file_cx, _total_cx=_total_cx,
    ))
    lines.extend(_signals_exports(
        graph, _src_fps=_src_fps, _exported_src=_exported_src,
    ))
    lines.extend(_signals_coupling(
        graph, _src_fps=_src_fps, _importer_counts=_importer_counts,
        _import_adj=_import_adj, modules=modules,
    ))
    lines.extend(_signals_coverage(
        graph, _src_fps=_src_fps, _test_fps=_test_fps,
        _commit_counts=_commit_counts,
    ))
    lines.extend(_signals_structure(
        graph, _src_fps=_src_fps, _test_fps=_test_fps,
        modules=modules, _s220_entry_files=_s220_entry_files,
    ))
    lines.extend(_signals_async_oop(
        graph, _s220_entry_files=_s220_entry_files,
    ))

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
