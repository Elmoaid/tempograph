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


def _signals_coupling_fanin(graph: Tempo) -> list[str]:
    """Top imported files + stable core (fan-in section)."""
    lines: list[str] = []
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

    # Top imported: files most imported by other source files.
    if _importer_counts:
        _top_imported = sorted(_importer_counts.items(), key=lambda x: -x[1])[:3]
        _top_imported = [(fp, n) for fp, n in _top_imported if n >= 3]
        if _top_imported:
            _ti_fps = [fp for fp, _ in _top_imported]
            _ti_parts = [f"{_display_path(fp, _ti_fps)} ({n})" for fp, n in _top_imported]
            lines.append("")
            lines.append(f"top imported: {', '.join(_ti_parts)}")

    # Stable core: widely-imported (>= 5) + unchanged >= 30 days.
    if graph.root and _importer_counts:
        try:
            from ..git import file_last_modified_days as _fld_core  # noqa: PLC0415
            _stable_core: list[tuple[int, str, int]] = []
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

    return lines


def _signals_coupling_fanout_cochange(graph: Tempo) -> list[str]:
    """High fan-out files + co-change pairs."""
    lines: list[str] = []

    # High-coupling files: non-test source files that import >= 8 distinct source files.
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

    # Co-change pairs: file pairs that frequently change together in git.
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

    return lines


def _signals_coupling_depth(graph: Tempo) -> list[str]:
    """Import chain depth: DFS deepest chain, BFS importer depth, basic circular check."""
    lines: list[str] = []

    # Deepest import chain (DFS) — shown when depth >= 5.
    _MAX_CHAIN = 12
    _best_chain: list[str] = []
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
    _src_imp_fps = [fp for fp in _import_adj if fp in graph.files and graph.files[fp].symbols]
    for _start in _src_imp_fps[:100]:
        _stack = [(_start, [_start])]
        while _stack:
            _cur, _chain = _stack.pop()
            if len(_chain) > len(_best_chain):
                _best_chain = _chain
            if len(_chain) >= _MAX_CHAIN:
                continue
            for _nxt in _import_adj.get(_cur, []):
                if _nxt not in _chain:
                    _stack.append((_nxt, _chain + [_nxt]))
    if len(_best_chain) >= 5:
        def _short(fp: str) -> str:
            parts = fp.split("/")
            return "/".join(parts[-2:]) if len(parts) > 2 else fp
        _chain_names = [_short(fp) for fp in _best_chain]
        lines.append(
            f"dep depth: {len(_best_chain)}"
            f" ({' → '.join(_chain_names[:5])}{'...' if len(_best_chain) > 5 else ''})"
        )

    # S119: Deepest import chain (BFS from each file up the importer chain).
    _src_fps_depth = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_fps_depth) >= 5:
        _max_depth = 0
        _deepest_fp = ""
        for _dfp in _src_fps_depth:
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
            lines.append(
                f"deepest import chain: {_deepest_fp.rsplit('/', 1)[-1]}"
                f" ({_max_depth} hops from root)"
            )

    # Circular imports — quick flag; details in `--mode deps`.
    try:
        _cycles = graph.detect_circular_imports()
        if _cycles:
            _first_cycle = " → ".join(fp.rsplit("/", 1)[-1] for fp in _cycles[0])
            _more = f" +{len(_cycles) - 1} more" if len(_cycles) > 1 else ""
            lines.append(f"⚠ circular imports: {len(_cycles)} cycle(s) ({_first_cycle}{_more})")
    except Exception:
        pass

    return lines


def _signals_coupling_structure(
    graph: Tempo, *, modules: dict[str, list[str]],
) -> list[str]:
    """Module-level structure: orphan modules, barrel files, coupling density, circulars."""
    lines: list[str] = []

    # S129: Orphan modules — top-level dirs not imported from outside.
    if len(modules) >= 3:
        _orphan_mods: list[str] = []
        for _om, _om_files in modules.items():
            if _om == ".":
                continue
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

    # S146: Barrel file count — aggregator files that import from 5+ modules.
    _s146_import_counts: dict[str, int] = {}
    for _e146 in graph.edges:
        if _e146.kind != EdgeKind.IMPORTS:
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
    _s227_src_fps = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s227_src_fps) >= 5:
        _s227_import_counts = [
            sum(
                1 for e in graph.edges
                if e.kind.value == "imports" and e.source_id.split("::")[0] == _fp227
            )
            for _fp227 in _s227_src_fps
        ]
        _avg227 = sum(_s227_import_counts) / len(_s227_import_counts)
        if _avg227 >= 5:
            lines.append(
                f"high coupling: avg {_avg227:.1f} imports/file across {len(_s227_src_fps)}"
                f" source files — tightly interdependent modules"
            )

    # S258: High coupling density — avg imports-per-file > 5 for 10+ code files.
    _s258_code_files = [
        fp for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    ]
    if len(_s258_code_files) >= 10:
        _s258_total_imports = sum(
            1 for e in graph.edges
            if e.kind.value == "imports" and not _is_test_file(e.source_id)
        )
        _s258_avg = _s258_total_imports / len(_s258_code_files)
        if _s258_avg >= 5:
            lines.append(
                f"high coupling: avg {_s258_avg:.1f} imports/file"
                f" — dense dependency graph; refactors cascade unpredictably"
            )

    # S265: Flat structure — 8+ source files all at root level with no subdirectories.
    _s259_root_src = [
        fp for fp in graph.files
        if "/" not in fp and not _is_test_file(fp)
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

    # S286: Shallow module graph — avg < 1 import/file among 8+ code files.
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

    # S342: Circular imports detected via symbol-level edges.
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


def _signals_coupling(
    graph: Tempo, *, _src_fps: list[str],
    _importer_counts: dict[str, int], _import_adj: dict[str, list[str]],
    modules: dict[str, list[str]],
) -> list[str]:
    """Import/coupling signals."""
    lines: list[str] = []
    lines.extend(_signals_coupling_fanin(graph))
    lines.extend(_signals_coupling_fanout_cochange(graph))
    lines.extend(_signals_coupling_depth(graph))
    lines.extend(_signals_coupling_structure(graph, modules=modules))
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


def _signals_structure_a(
    graph: Tempo, *, _src_fps: list[str], _test_fps: set[str],
    modules: dict[str, list[str]], _s220_entry_files: list[str],
) -> list[str]:
    """Structural signals: file isolation, module layout, entry-point patterns."""
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



    return lines


def _signals_structure_b(graph: Tempo) -> list[str]:
    """Structural signals: repo meta — polyglot, packages, plugins, frameworks."""
    lines: list[str] = []
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

    return lines


def _signals_structure_c(graph: Tempo) -> list[str]:
    """Structural signals: quality metrics — stubs, exports, constants, methods."""
    lines: list[str] = []
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


def _signals_structure(
    graph: Tempo, *, _src_fps: list[str], _test_fps: set[str],
    modules: dict[str, list[str]], _s220_entry_files: list[str],
) -> list[str]:
    """Structural signals."""
    lines: list[str] = []
    lines.extend(_signals_structure_a(
        graph, _src_fps=_src_fps, _test_fps=_test_fps,
        modules=modules, _s220_entry_files=_s220_entry_files,
    ))
    lines.extend(_signals_structure_b(graph))
    lines.extend(_signals_structure_c(graph))
    return lines


def _signals_async_oop_a(
    graph: Tempo, *, _s220_entry_files: list[str],
) -> list[str]:
    """Async, OOP, and project-structure signals (S84–S565)."""
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

    # S439: Deep inheritance — codebase has 4+ levels of class inheritance.
    # Deep hierarchies hide behavior: the effective method set of a leaf class requires
    # tracing up 4+ classes, and each level is a potential override site.
    _s439_inherits: dict[str, list[str]] = {}
    for _e439 in graph.edges:
        if _e439.kind.value == "inherits":
            _s439_inherits.setdefault(_e439.source_id, []).append(_e439.target_id)
    _s439_max_depth = 0
    _s439_deepest: str = ""
    for _cls439_id, _parents439 in _s439_inherits.items():
        _depth439 = 0
        _cur439_ids = [_cls439_id]
        _seen439: set[str] = {_cls439_id}
        while True:
            _next439: list[str] = []
            for _cid439 in _cur439_ids:
                for _pid439 in _s439_inherits.get(_cid439, []):
                    if _pid439 not in _seen439:
                        _next439.append(_pid439)
                        _seen439.add(_pid439)
            if not _next439:
                break
            _depth439 += 1
            _cur439_ids = _next439
        if _depth439 > _s439_max_depth:
            _s439_max_depth = _depth439
            _cls439_sym = graph.symbols.get(_cls439_id)
            _s439_deepest = _cls439_sym.name if _cls439_sym else _cls439_id
    if _s439_max_depth >= 3:
        lines.append(
            f"deep inheritance: {_s439_max_depth + 1} levels deep (e.g. {_s439_deepest})"
            f" — override resolution requires tracing up {_s439_max_depth + 1} classes; prefer composition"
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

    # S445: Multi-language codebase — source files span 3+ programming languages.
    # Polyglot codebases require language-specific tooling for each component; a change
    # that looks simple in one layer may require coordinated changes in every other language.
    _s445_langs = {
        graph.files[fp].language.value
        for fp in graph.files
        if not _is_test_file(fp)
        and graph.files[fp].language.value in _CODE_LANGS
    }
    if len(_s445_langs) >= 3:
        _lang_list445 = ", ".join(sorted(_s445_langs)[:5])
        lines.append(
            f"multi-language: {len(_s445_langs)} languages in use ({_lang_list445})"
            f" — cross-language changes need coordinated builds and tooling per layer"
        )

    # S452: Test-thin codebase — test lines are under 20% of source lines.
    # Low test coverage relative to source means most changes are unverified;
    # the lower the ratio, the higher the risk of silent regressions.
    _s452_src_lines = sum(
        graph.files[fp].line_count for fp in graph.files
        if not _is_test_file(fp) and any(fp.endswith(ext) for ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb"))
        and graph.files[fp].line_count
    )
    _s452_test_lines = sum(
        graph.files[fp].line_count for fp in graph.files
        if _is_test_file(fp) and graph.files[fp].line_count
    )
    if _s452_src_lines > 500 and _s452_test_lines < _s452_src_lines * 0.2:
        _ratio452 = int(_s452_test_lines / _s452_src_lines * 100) if _s452_src_lines else 0
        lines.append(
            f"test-thin: test code is only {_ratio452}% of source ({_s452_test_lines:,} vs {_s452_src_lines:,} lines)"
            f" — most changes are unverified; add tests before refactoring"
        )

    # S458: Monorepo structure — multiple independent packages with their own package files.
    # Monorepos host multiple services in one repo; a change to a shared library
    # requires updating every consumer service and re-testing each independently.
    _s458_pkg_files = (
        "setup.py", "setup.cfg", "pyproject.toml", "package.json",
        "cargo.toml", "go.mod", "pom.xml", "build.gradle",
    )
    _s458_pkg_dirs: set[str] = set()
    for _fp458 in graph.files:
        _fname458 = _fp458.rsplit("/", 1)[-1].lower()
        if _fname458 in _s458_pkg_files:
            _dir458 = _fp458.rsplit("/", 1)[0] if "/" in _fp458 else "."
            _s458_pkg_dirs.add(_dir458)
    if len(_s458_pkg_dirs) >= 3:
        lines.append(
            f"monorepo: {len(_s458_pkg_dirs)} independent package roots detected"
            f" — shared-library changes require updating every consumer; test each service independently"
        )

    # S463: No entry points — codebase has no main()/cli()/entry() function.
    # A library with no entry points is entirely consumed by callers; there is no
    # single place to trace the full execution path end-to-end for integration testing.
    _s463_entry_names = {"main", "cli", "run", "start", "entry", "app", "serve", "launch"}
    _s463_entry_syms = [
        s for s in graph.symbols.values()
        if s.name.lower() in _s463_entry_names
        and s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
    ]
    _s463_src_files = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s463_src_files) >= 5 and not _s463_entry_syms:
        lines.append(
            f"no entry points: {len(_s463_src_files)} source files with no main/cli/run function"
            f" — library-only; no single execution path to trace for integration testing"
        )

    # S469: Shallow test suite — all test functions are trivially short (< 10 lines).
    # Tiny test functions are likely smoke tests or assertion-only stubs;
    # they prove the code runs but don't verify complex behavior or edge cases.
    _s469_test_fns = [
        s for s in graph.symbols.values()
        if _is_test_file(s.file_path)
        and s.kind.value in ("function", "method", "test")
        and s.name.startswith("test_")
        and s.line_start is not None and s.line_end is not None
    ]
    _s469_short_fns = [
        s for s in _s469_test_fns
        if (s.line_end - s.line_start) < 10
    ]
    if len(_s469_test_fns) >= 10 and len(_s469_short_fns) == len(_s469_test_fns):
        lines.append(
            f"shallow tests: all {len(_s469_test_fns)} test functions are under 10 lines"
            f" — likely smoke tests only; complex behavior and edge cases are untested"
        )

    # S481: High dead-code ratio — 30%+ of functions appear unreferenced.
    # A high percentage of unreachable code inflates maintenance surface;
    # every change must consider whether any dead branch accidentally becomes live.
    _s481_src_syms = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.kind.value in ("function", "method")
    ]
    _s481_unreferenced = [
        s for s in _s481_src_syms
        if not graph.callers_of(s.id) and not graph.importers_of(s.file_path)
    ]
    if len(_s481_src_syms) >= 20:
        _dead_ratio481 = len(_s481_unreferenced) / len(_s481_src_syms)
        if _dead_ratio481 >= 0.30:
            lines.append(
                f"high dead-code ratio: {int(_dead_ratio481 * 100)}% of functions are unreferenced"
                f" ({len(_s481_unreferenced)}/{len(_s481_src_syms)})"
                f" — clean up dead code before adding features to reduce cognitive load"
            )

    # S483: No type annotations — 5+ source files have no typed function signatures.
    # Untyped codebases make refactoring dangerous; callers rely on implicit contracts that
    # aren't machine-checkable, so type errors only surface at runtime.
    _s483_untyped: list[str] = []
    for _fp483, _fi483 in graph.files.items():
        if _is_test_file(_fp483) or _fi483.language.value != "python":
            continue
        _fns483 = [
            s for s in graph.symbols.values()
            if s.file_path == _fp483 and s.kind.value in ("function", "method")
        ]
        if not _fns483:
            continue
        _typed483 = [
            s for s in _fns483
            if s.signature and (
                "->" in s.signature
                or (
                    "(" in s.signature
                    and ":" in s.signature.split("(", 1)[1].rsplit(")", 1)[0]
                )
            )
        ]
        if len(_typed483) == 0:
            _s483_untyped.append(_fp483)
    if len(_s483_untyped) >= 5:
        lines.append(
            f"no type annotations: {len(_s483_untyped)} Python source file(s) have zero typed signatures"
            f" — add mypy/pyright before refactoring to surface implicit contract violations"
        )

    # S489: God module — a single file holds 30%+ of all source symbols.
    # Concentrating logic in one file raises merge conflict probability and
    # increases cognitive load; any change requires understanding the whole module.
    _s489_src_syms = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.kind.value in ("function", "method", "class")
    ]
    if len(_s489_src_syms) >= 20:
        from collections import Counter as _Counter489  # noqa: PLC0415
        _s489_counts = _Counter489(s.file_path for s in _s489_src_syms)
        _s489_top_fp, _s489_top_n = _s489_counts.most_common(1)[0]
        _s489_ratio = _s489_top_n / len(_s489_src_syms)
        if _s489_ratio >= 0.30:
            lines.append(
                f"god module: {_s489_top_fp.rsplit('/', 1)[-1]} holds {int(_s489_ratio * 100)}%"
                f" of source symbols ({_s489_top_n}/{len(_s489_src_syms)})"
                f" — high merge-conflict risk; consider splitting by responsibility"
            )

    # S495: Star imports — 3+ source files use `from X import *`.
    # Star imports pollute the namespace and make it impossible to trace where symbols come from;
    # a name collision silently overrides the previous binding without any error.
    _s495_star_files: list[str] = []
    for _fp495, _fi495 in graph.files.items():
        if _is_test_file(_fp495):
            continue
        if any("import *" in _imp for _imp in (_fi495.imports or [])):
            _s495_star_files.append(_fp495)
    if len(_s495_star_files) >= 3:
        lines.append(
            f"star imports: {len(_s495_star_files)} source file(s) use `import *`"
            f" — wildcard imports hide symbol origins and risk silent name collisions"
        )

    # S506: Deep nesting — source files are organized 3+ directory levels deep.
    # Deeply nested modules make imports brittle and directory structure hard to navigate;
    # any reorganization breaks all relative import paths across the affected subtree.
    _s506_max_depth = 0
    for _fp506 in graph.files:
        if _is_test_file(_fp506):
            continue
        _depth506 = _fp506.replace("\\", "/").count("/")
        if _depth506 > _s506_max_depth:
            _s506_max_depth = _depth506
    if _s506_max_depth >= 3:
        lines.append(
            f"deep nesting: source files are organized {_s506_max_depth} directories deep"
            f" — deep nesting makes refactors brittle; consider flatter module structure"
        )

    # S507: Single language dominance — 90%+ of source files are in one language.
    # Monoculture codebases gain simplicity but lose polyglot escape hatches;
    # performance-critical or platform-specific requirements force a painful split later.
    _s507_lang_counts: dict[str, int] = {}
    for _fp507, _fi507 in graph.files.items():
        if not _is_test_file(_fp507) and _fi507.language.value in _CODE_LANGS:
            _s507_lang_counts[_fi507.language.value] = _s507_lang_counts.get(_fi507.language.value, 0) + 1
    _s507_total = sum(_s507_lang_counts.values())
    if _s507_total >= 10:
        _s507_top_lang, _s507_top_n = max(_s507_lang_counts.items(), key=lambda x: x[1])
        if _s507_top_n / _s507_total >= 0.90:
            lines.append(
                f"single language: {_s507_top_lang} accounts for {int(_s507_top_n / _s507_total * 100)}%"
                f" of source files — tightly coupled to one runtime; polyglot needs require careful isolation"
            )

    # S514: Mixed async/sync — source has both async coroutines and blocking sync functions.
    # Sync code running inside an async event loop blocks all coroutines on the same thread.
    # Any refactor crossing sync/async boundaries needs a concurrency review to avoid stalls.
    _s514_async_n = 0
    _s514_sync_n = 0
    for _sym514 in graph.symbols.values():
        if _sym514.kind.value in ("function", "method") and not _is_test_file(_sym514.file_path):
            _sig514 = _sym514.signature or ""
            if _sig514.startswith("async "):
                _s514_async_n += 1
            elif _sig514.startswith("def "):
                _s514_sync_n += 1
    if _s514_async_n >= 3 and _s514_sync_n >= 3:
        lines.append(
            f"mixed async/sync: {_s514_async_n} async + {_s514_sync_n} sync source functions"
            f" — blocking calls in async context stall the event loop; audit sync→async call boundaries"
        )

    # S532: Test-heavy repo — test files exceed 50% of total indexed files.
    # More test code than source code can indicate over-specification of implementation details,
    # or that tests weren't cleaned up after source was removed. Both increase maintenance burden.
    _s532_test_n = sum(1 for fp in graph.files if _is_test_file(fp))
    _s532_total_n = len(graph.files)
    if _s532_total_n >= 10 and _s532_test_n / _s532_total_n > 0.50:
        _s532_pct = int(_s532_test_n / _s532_total_n * 100)
        lines.append(
            f"test-heavy: {_s532_pct}% of files are test files ({_s532_test_n}/{_s532_total_n})"
            f" — verify tests weren't left behind after source was deleted"
        )

    # S526: Dense codebase — average source file has 200+ lines.
    # Large average file size signals monolith tendencies; files become harder to navigate,
    # review, and test when they grow above ~200 lines. Consider splitting by responsibility.
    _s526_src_files = [(fp, fi) for fp, fi in graph.files.items() if not _is_test_file(fp)]
    if len(_s526_src_files) >= 5:
        _s526_total_lines = sum(fi.line_count for _, fi in _s526_src_files)
        _s526_avg = _s526_total_lines // len(_s526_src_files)
        if _s526_avg >= 200:
            lines.append(
                f"dense codebase: avg {_s526_avg} lines/source file ({len(_s526_src_files)} files)"
                f" — large files on average; files are hard to review and test; consider splitting by responsibility"
            )

    # S520: No standard entry points — 8+ source files but zero recognized entry points detected.
    # Projects using frameworks (pytest plugins, Django apps, library packages) have implicit entry;
    # agents must infer the execution context from framework docs rather than assuming a main() flow.
    _s520_src_files = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s520_src_files) >= 8 and not _s220_entry_files:
        lines.append(
            f"no entry points: {len(_s520_src_files)} source files but no main/server/cli/app detected"
            f" — likely uses framework conventions; infer entry context from framework docs"
        )

    # S547: No tests — 5+ source files but zero test files detected.
    # A codebase without tests offers no safety net for refactoring; any behavioral change
    # is unverifiable; treat every modification as high-risk until tests are added.
    _s547_src_count = sum(1 for fp in graph.files if not _is_test_file(fp))
    _s547_test_count = sum(1 for fp in graph.files if _is_test_file(fp))
    if _s547_src_count >= 5 and _s547_test_count == 0:
        lines.append(
            f"no tests: {_s547_src_count} source files, 0 test files detected"
            f" — no safety net for refactoring; treat every change as high-risk"
        )

    # S553: Mixed languages — source files span 3+ different programming languages.
    # Multi-language repos require multiple toolchains, runtimes, and mental models;
    # cross-language calls add marshalling overhead and reduce static analysis coverage.
    _s553_langs = {
        graph.files[fp].language.value
        for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language.value in _CODE_LANGS
    }
    if len(_s553_langs) >= 3:
        _lang_list553 = ", ".join(sorted(_s553_langs)[:5])
        lines.append(
            f"mixed languages: {len(_s553_langs)} source languages detected ({_lang_list553})"
            f" — multiple runtimes increase cognitive overhead and reduce unified static analysis coverage"
        )

    # S559: Single entry point — exactly 1 recognized entry point file in the repo.
    # A single-entry-point codebase funnels all traffic through one file; it is the highest-value
    # target for both breakage and optimization; changes to it affect every execution path.
    _s559_entry_names = frozenset(("main.py", "app.py", "server.py", "cli.py", "run.py", "index.js", "index.ts"))
    _s559_entry_files = [
        fp for fp in graph.files
        if fp.replace("\\", "/").rsplit("/", 1)[-1].lower() in _s559_entry_names
        and not _is_test_file(fp)
    ]
    if len(_s559_entry_files) == 1:
        _ep_name559 = _s559_entry_files[0].rsplit("/", 1)[-1]
        lines.append(
            f"single entry point: {_ep_name559} is the only entry point"
            f" — all execution flows through this file; changes here affect every code path"
        )

    # S565: Large test ratio — test file line count exceeds 2× source file line count.
    # Over-tested codebases (by line count) often have brittle implementation-coupled tests;
    # high test volume relative to source signals tests that constrain refactoring more than they enable it.
    _s565_src = [(fp, fi) for fp, fi in graph.files.items() if not _is_test_file(fp)]
    _s565_tst = [(fp, fi) for fp, fi in graph.files.items() if _is_test_file(fp)]
    if len(_s565_src) >= 3 and len(_s565_tst) >= 3:
        _src_lines565 = sum(fi.line_count for _, fi in _s565_src)
        _tst_lines565 = sum(fi.line_count for _, fi in _s565_tst)
        if _src_lines565 > 0 and _tst_lines565 >= _src_lines565 * 2:
            _ratio565 = round(_tst_lines565 / _src_lines565, 1)
            lines.append(
                f"large test ratio: test code is {_ratio565}× source code ({_tst_lines565} vs {_src_lines565} lines)"
                f" — high test volume may indicate brittle implementation-coupled tests; prefer behavior tests"
            )

    return lines

def _signals_async_oop_b(graph: Tempo) -> list[str]:
    """Symbol and export metric signals (S571–S649)."""
    lines: list[str] = []
    # S571: No exports — 5+ source files but zero exported (public) symbols detected.
    # A codebase with no exports is likely a script collection or has accidentally made
    # everything private; agents can't reliably identify the public API surface.
    _s571_exported = [
        sym for sym in graph.symbols.values()
        if not _is_test_file(sym.file_path) and sym.exported
    ]
    _s571_src_count = sum(1 for fp in graph.files if not _is_test_file(fp))
    if _s571_src_count >= 5 and not _s571_exported:
        lines.append(
            f"no exports: {_s571_src_count} source files but 0 exported symbols detected"
            f" — all symbols are private; agents cannot identify the public API surface"
        )

    # S577: Orphan test — test file with no corresponding source file in the graph.
    # Test files without a matching source file often result from source renames or
    # deletions; tests are wired to nothing and silently provide false coverage confidence.
    _s577_test_fps = [fp for fp in graph.files if _is_test_file(fp)]
    _s577_src_stems = {
        fp.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "")
        for fp in graph.files
        if not _is_test_file(fp)
    }
    _s577_orphans = [
        fp for fp in _s577_test_fps
        if fp.replace("\\", "/").rsplit("/", 1)[-1].replace("test_", "").replace("_test.py", ".py").replace(".py", "") not in _s577_src_stems
    ]
    if _s577_orphans:
        _orph_names577 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s577_orphans[:3])
        lines.append(
            f"orphan tests: {len(_s577_orphans)} test file(s) with no matching source ({_orph_names577})"
            f" — source was renamed or deleted; tests cover nothing and create false confidence"
        )

    # S582: No cross-file imports — 5+ source files but zero import edges between them.
    # A fully disconnected module graph means every file is an island; either the project
    # uses dynamic imports not detected by the parser, or the files share no dependencies at all.
    _s582_src_files = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s582_src_files) >= 5:
        _s582_import_edges = [
            e for e in graph.edges
            if e.kind.value == "imports"
            and not _is_test_file(e.source_id)
            and not _is_test_file(e.target_id)
        ]
        if not _s582_import_edges:
            lines.append(
                f"no cross-file imports: {len(_s582_src_files)} source files with zero import edges detected"
                f" — may use dynamic imports, path manipulation, or files are truly unrelated"
            )

    # S588: Single-language repo — all indexed source files share one language.
    # Homogeneous repos are simpler but less portable; agents can apply language-specific
    # best practices uniformly. Worth noting when the entire codebase is one language.
    _s588_langs = {
        graph.files[fp].language
        for fp in graph.files
        if not _is_test_file(fp) and graph.files[fp].language
    }
    if len(_s588_langs) == 1:
        _only_lang588 = next(iter(_s588_langs))
        _src_count588 = len([fp for fp in graph.files if not _is_test_file(fp)])
        if _src_count588 >= 3:
            lines.append(
                f"single-language repo: all {_src_count588} source files are {_only_lang588}"
                f" — uniform stack; language-specific linting and type tools apply globally"
            )

    # S594: No public classes — 5+ source files but zero exported class symbols detected.
    # A repo with only functions and no public classes may be intentionally procedural,
    # or may indicate that domain models are missing or unexported.
    _s594_src_fps = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s594_src_fps) >= 5:
        _s594_exported_classes = [
            sym for sym in graph.symbols.values()
            if not _is_test_file(sym.file_path)
            and sym.exported
            and sym.kind.value == "class"
        ]
        if not _s594_exported_classes:
            lines.append(
                f"no public classes: {len(_s594_src_fps)} source files but zero exported class symbols"
                f" — procedural style or domain models are unexported/missing"
            )

    # S601: Flat repo — 10+ source files all in the root directory with no subdirectories.
    # A flat layout is fine for scripts but signals that the codebase has outgrown its structure;
    # consider grouping related files into packages.
    _s601_root_files = [
        fp for fp in graph.files
        if not _is_test_file(fp)
        and "/" not in fp.replace("\\", "/")
        and fp.endswith(".py")
    ]
    if len(_s601_root_files) >= 10:
        lines.append(
            f"flat repo: {len(_s601_root_files)} source files all in the root directory"
            f" — consider organizing into packages as the codebase grows"
        )

    # S607: High dead symbol ratio — more than 30% of all indexed symbols have no callers.
    # A high fraction of unreferenced symbols indicates code accumulation without pruning;
    # the codebase grows but cleanup is neglected.
    _s607_all_syms = [
        sym for sym in graph.symbols.values()
        if not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method", "class")
    ]
    if len(_s607_all_syms) >= 10:
        _s607_dead = [s for s in _s607_all_syms if not graph.callers_of(s.id)]
        _s607_ratio = len(_s607_dead) / len(_s607_all_syms)
        if _s607_ratio > 0.30:
            lines.append(
                f"high dead ratio: {len(_s607_dead)}/{len(_s607_all_syms)} symbols"
                f" ({_s607_ratio:.0%}) have no callers — accumulation without pruning; schedule a dead-code pass"
            )

    # S613: Circular import pairs — two source files import each other (mutual dependency).
    # Circular imports in Python are runtime errors in many patterns; they also indicate
    # that module responsibilities are poorly separated.
    _import_edges613 = {
        (e.source_id, e.target_id)
        for e in graph.edges
        if e.kind.value == "imports"
        and not _is_test_file(e.source_id)
        and not _is_test_file(e.target_id)
    }
    _circular613 = [
        (a, b) for (a, b) in _import_edges613
        if (b, a) in _import_edges613 and a < b
    ]
    if _circular613:
        _circ_names613 = f"{_circular613[0][0].rsplit('/', 1)[-1]} ↔ {_circular613[0][1].rsplit('/', 1)[-1]}"
        lines.append(
            f"circular imports: {len(_circular613)} mutual import pair(s) ({_circ_names613})"
            f" — bidirectional dependencies indicate poor module separation; refactor to break cycle"
        )

    # S619: Large average file — average source file line count exceeds 200 lines.
    # When the typical file is long, the codebase favors large monoliths over small modules;
    # navigation and comprehension cost are both elevated.
    _s619_src_files = {
        fp: fi for fp, fi in graph.files.items()
        if not _is_test_file(fp) and fi.line_count > 0
    }
    if len(_s619_src_files) >= 5:
        _avg_lines619 = sum(fi.line_count for fi in _s619_src_files.values()) // len(_s619_src_files)
        if _avg_lines619 > 200:
            lines.append(
                f"large average file: average source file is {_avg_lines619} lines"
                f" — codebase favors monolithic files; navigation cost is elevated"
            )

    # S625: High export ratio — 5+ source files where >70% of symbols are exported but no imports.
    # A repo with many exported symbols but no cross-file imports may be a collection of
    # independent modules with no shared entry point — harder to trace end-to-end.
    _s625_src_files = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s625_src_files) >= 5:
        _s625_export_count = sum(
            1 for s in graph.symbols.values()
            if s.exported and not _is_test_file(s.file_path)
        )
        _s625_sym_count = sum(
            1 for s in graph.symbols.values()
            if not _is_test_file(s.file_path)
        )
        _s625_import_edges = [
            e for e in graph.edges
            if e.kind.value == "imports"
            and not _is_test_file(e.source_id)
            and not _is_test_file(e.target_id)
        ]
        if _s625_sym_count > 0 and not _s625_import_edges:
            _export_pct625 = int(100 * _s625_export_count / _s625_sym_count)
            if _export_pct625 >= 70:
                lines.append(
                    f"high export ratio: {_export_pct625}% of symbols exported but zero cross-file imports"
                    f" — independent module collection; no shared entry point to trace end-to-end"
                )

    # S631: Procedural style — 10+ exported functions but zero exported classes in source files.
    # No exported classes suggests the codebase uses a procedural rather than OOP style;
    # this is fine intentionally but unexpected in frameworks that rely on class-based dispatch.
    _s631_src = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_s631_src) >= 5:
        _exported_fns631 = sum(
            1 for s in graph.symbols.values()
            if s.exported and s.kind.value in ("function",) and not _is_test_file(s.file_path)
        )
        _exported_cls631 = sum(
            1 for s in graph.symbols.values()
            if s.exported and s.kind.value == "class" and not _is_test_file(s.file_path)
        )
        if _exported_fns631 >= 10 and _exported_cls631 == 0:
            lines.append(
                f"procedural style: {_exported_fns631} exported functions, 0 exported classes"
                f" — no OOP surface; verify this is intentional for the framework in use"
            )

    # S637: Test-heavy symbols — test code contains 3x more symbols than source code.
    # An inverted symbol ratio suggests test infrastructure has grown disproportionately;
    # test helpers and fixtures may themselves need refactoring.
    _s637_src_syms = sum(
        1 for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.parent_id is None
    )
    _s637_test_syms = sum(
        1 for s in graph.symbols.values()
        if _is_test_file(s.file_path) and s.parent_id is None
    )
    if _s637_src_syms >= 5 and _s637_test_syms >= _s637_src_syms * 3:
        lines.append(
            f"test-heavy symbols: {_s637_test_syms} top-level test symbols vs {_s637_src_syms} source symbols"
            f" — test infrastructure may need its own refactoring pass"
        )

    # S643: Deep inheritance chain — at least one class inherits from a class that itself inherits.
    # Inheritance depth >= 3 signals a fragile base class problem; changes to base classes
    # propagate through all descendants, and deep chains are hard to reason about.
    _inherits643 = {
        e.source_id: e.target_id
        for e in graph.edges
        if e.kind.value == "inherits"
        and not _is_test_file(e.source_id)
    }
    # Check for chains: A→B→C (depth 3)
    _deep_chains643 = []
    for child, parent in _inherits643.items():
        grandparent = _inherits643.get(parent)
        if grandparent:
            _deep_chains643.append((child.split("::")[-1], parent.split("::")[-1], grandparent.split("::")[-1]))
    if _deep_chains643:
        _chain643 = _deep_chains643[0]
        lines.append(
            f"deep inheritance: {len(_deep_chains643)} class(es) have inheritance depth >= 3"
            f" (e.g. {_chain643[0]} → {_chain643[1]} → {_chain643[2]})"
            f" — fragile base class risk; prefer composition over deep inheritance"
        )

    # S649: Exception class density — more than 20% of exported classes are Error/Exception types.
    # A codebase with many custom exception classes is building an exception hierarchy;
    # this is fine but signals that callers must handle a wide surface of failure modes.
    _exported_classes649 = [
        s for s in graph.symbols.values()
        if s.exported and s.kind.value == "class" and not _is_test_file(s.file_path)
    ]
    if len(_exported_classes649) >= 5:
        _exc_classes649 = [
            s for s in _exported_classes649
            if s.name.endswith(("Error", "Exception", "Warning", "Failure", "Fault"))
        ]
        _exc_pct649 = int(100 * len(_exc_classes649) / len(_exported_classes649))
        if _exc_pct649 >= 20:
            lines.append(
                f"exception class density: {len(_exc_classes649)}/{len(_exported_classes649)}"
                f" exported classes ({_exc_pct649}%) are error/exception types"
                f" — wide failure surface; callers must handle many distinct exception types"
            )

    return lines

def _signals_async_oop_c(graph: Tempo) -> list[str]:
    """File quality and hub pattern signals (S655–S787)."""
    lines: list[str] = []
    # S655: High average complexity — average symbol complexity exceeds 5.
    # Complex functions are harder to understand, test, and maintain;
    # a high average signals that the codebase may need targeted refactoring passes.
    _cx_syms655 = [
        s for s in graph.symbols.values()
        if s.complexity and s.complexity > 1
        and not _is_test_file(s.file_path)
        and s.kind.value in ("function", "method")
    ]
    if len(_cx_syms655) >= 10:
        _avg_cx655 = sum(s.complexity for s in _cx_syms655) // len(_cx_syms655)
        if _avg_cx655 > 5:
            lines.append(
                f"high average complexity: avg cyclomatic complexity is {_avg_cx655}"
                f" across {len(_cx_syms655)} functions"
                f" — complex codebase; target functions above cx=10 for refactoring first"
            )

    # S661: Deep directory nesting — source files span 5+ directory levels.
    # Files buried deep require long import paths and are easy to miss during code review;
    # deep nesting also signals overly granular package decomposition.
    _max_depth661 = max(
        (
            len(fp.replace("\\", "/").split("/"))
            for fp in graph.files
            if not _is_test_file(fp)
        ),
        default=0,
    )
    if _max_depth661 >= 5:
        lines.append(
            f"deep nesting: source files reach {_max_depth661} directory levels deep"
            f" — long import paths and review-invisible files; consider flattening package structure"
        )

    # S667: No tests detected — repo has no test files at all (high risk for any change).
    # Repos without tests have no safety net; any modification is an untested change
    # and regressions will only surface at runtime.
    _has_tests667 = any(_is_test_file(fp) for fp in graph.files)
    if not _has_tests667 and len(graph.files) >= 3:
        lines.append(
            "no tests detected: no test files found in this repo"
            " — all changes are untested; add a test suite before refactoring"
        )

    # S673: Dominant file — one non-test file holds >30% of all repo symbols.
    # A file that dominates the symbol count is a gravity well that accumulates
    # responsibilities; it should be the first target for decomposition.
    _total_syms673 = sum(
        1 for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.parent_id is None
    )
    if _total_syms673 > 0:
        _file_counts673: dict[str, int] = {}
        for s in graph.symbols.values():
            if not _is_test_file(s.file_path) and s.parent_id is None:
                _file_counts673[s.file_path] = _file_counts673.get(s.file_path, 0) + 1
        _top_fp673 = max(_file_counts673, key=lambda f: _file_counts673[f], default=None)
        if _top_fp673 and _file_counts673[_top_fp673] / _total_syms673 > 0.30:
            _pct673 = int(_file_counts673[_top_fp673] / _total_syms673 * 100)
            lines.append(
                f"dominant file: {_top_fp673.rsplit('/', 1)[-1]} holds {_pct673}% of repo symbols"
                f" — gravity-well file; prioritize decomposing this file"
            )

    # S679: High orphan ratio — more than 30% of source files have no importers.
    # Files that nothing imports may be dead modules, scripts, or tools that
    # have drifted from the main codebase; high orphan ratios signal poor cohesion.
    _src_files679 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_files679) >= 4:
        _orphans679 = [
            fp for fp in _src_files679
            if not graph.importers_of(fp)
        ]
        _orphan_pct679 = len(_orphans679) / len(_src_files679)
        if _orphan_pct679 > 0.30:
            _opct679 = int(_orphan_pct679 * 100)
            lines.append(
                f"high orphan ratio: {_opct679}% of source files have no importers"
                f" — many disconnected files; verify none are abandoned dead modules"
            )

    # S685: Class-heavy repo — more than 60% of non-test exported symbols are classes.
    # A repo where classes greatly outnumber functions often has anemic domain models
    # with shallow behaviour; heavy OOP can obscure data flow and reduce testability.
    _all_src685 = [
        s for s in graph.symbols.values()
        if s.parent_id is None and not _is_test_file(s.file_path)
        and s.kind.value not in ("unknown", "module")
    ]
    if len(_all_src685) >= 5:
        _class_count685 = sum(1 for s in _all_src685 if s.kind.value == "class")
        if _class_count685 / len(_all_src685) > 0.60:
            _cpct685 = int(_class_count685 / len(_all_src685) * 100)
            lines.append(
                f"class-heavy repo: {_cpct685}% of symbols are classes ({_class_count685}/{len(_all_src685)})"
                f" — OOP-heavy; verify classes have behaviour, not just data"
            )

    # S691: Global variable density — top-level variables/constants are >20% of all symbols.
    # High global state density increases coupling between modules and makes testing harder;
    # constants are acceptable but mutable globals are a refactoring risk.
    _all_top691 = [
        s for s in graph.symbols.values()
        if s.parent_id is None and not _is_test_file(s.file_path)
        and s.kind.value not in ("unknown", "module")
    ]
    if len(_all_top691) >= 5:
        _var_count691 = sum(
            1 for s in _all_top691
            if s.kind.value in ("variable", "constant")
        )
        if _var_count691 / len(_all_top691) > 0.20:
            _vpct691 = int(_var_count691 / len(_all_top691) * 100)
            lines.append(
                f"high global state: {_vpct691}% of top-level symbols are variables/constants"
                f" — check for mutable globals; prefer dependency injection or module-level constants"
            )

    # S697: Low test proxy — source has 5x+ more symbols than test symbols.
    # When the production codebase greatly outnumbers test code, changes are under-tested;
    # this is a structural proxy for coverage gaps.
    _src_syms697 = sum(
        1 for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.parent_id is None
        and s.kind.value not in ("unknown", "module")
    )
    _test_syms697 = sum(
        1 for s in graph.symbols.values()
        if _is_test_file(s.file_path) and s.parent_id is None
        and s.kind.value not in ("unknown", "module")
    )
    if _src_syms697 >= 5 and _test_syms697 > 0 and _src_syms697 >= _test_syms697 * 5:
        lines.append(
            f"low test proxy: {_src_syms697} source symbols vs {_test_syms697} test symbols"
            f" — source outpaces tests by {_src_syms697 // max(_test_syms697, 1)}x; likely under-tested"
        )
    elif _src_syms697 >= 5 and _test_syms697 == 0:
        pass  # covered by S667 (no tests detected)

    # S703: Empty source files — 2+ non-test source files with no exported symbols.
    # Files that define no symbols are placeholder stubs, empty modules, or leftover scaffolding;
    # they add noise to import paths and confuse agents trying to understand the codebase.
    _empty_src703 = [
        fp for fp in graph.files
        if not _is_test_file(fp)
        and not any(s.file_path == fp for s in graph.symbols.values())
    ]
    if len(_empty_src703) >= 2:
        lines.append(
            f"empty source files: {len(_empty_src703)} source file(s) define no symbols"
            f" — stub files or scaffolding; remove or populate before continuing"
        )

    # S709: High file-per-symbol ratio — avg >3 source files per exported symbol.
    # Too many micro-files with few symbols each increases cognitive overhead for navigation;
    # consolidating logically related symbols reduces import path complexity.
    _src_files709 = [fp for fp in graph.files if not _is_test_file(fp)]
    _src_syms709 = sum(
        1 for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.parent_id is None
        and s.kind.value not in ("unknown", "module")
    )
    if len(_src_files709) >= 4 and _src_syms709 > 0:
        _ratio709 = len(_src_files709) / _src_syms709
        if _ratio709 > 3.0:
            lines.append(
                f"micro-files: {len(_src_files709)} source files with only {_src_syms709} symbols"
                f" ({_ratio709:.1f} files/symbol) — over-fragmented; consider consolidating thin files"
            )

    # S715: Hub file — a single source file is imported by >40% of all other source files.
    # Hub files are architectural bottlenecks; changes to them propagate widely and can break
    # many consumers simultaneously. They often need careful versioning and change control.
    _src_files715 = [
        fp for fp in graph.files
        if not _is_test_file(fp) and fp.endswith(".py")
    ]
    if len(_src_files715) >= 4:
        for _fp715 in _src_files715:
            _importers715 = [
                f for f in graph.importers_of(_fp715)
                if f != _fp715 and not _is_test_file(f)
            ]
            _ratio715 = len(_importers715) / max(len(_src_files715) - 1, 1)
            if _ratio715 > 0.40:
                lines.append(
                    f"hub file: {_fp715.rsplit('/', 1)[-1]} is imported by"
                    f" {len(_importers715)}/{len(_src_files715) - 1} source files ({_ratio715:.0%})"
                    f" — architectural bottleneck; changes here cascade widely"
                )
                break

    # S721: No entry points — no files with standard entry-point names found in the repo.
    # A repo without main.py / app.py / server.py etc. is likely a library or has non-standard
    # naming; agents should not assume a runnable entrypoint exists.
    _entry_names721 = {
        "main.py", "app.py", "server.py", "cli.py", "__main__.py",
        "run.py", "index.py", "wsgi.py", "asgi.py",
    }
    _all_basenames721 = {fp.rsplit("/", 1)[-1] for fp in graph.files}
    if not (_all_basenames721 & _entry_names721) and len(graph.files) >= 3:
        lines.append(
            f"no entry points: none of {len(graph.files)} files have standard entry-point names"
            f" — library-style repo or entry point uses non-standard naming"
        )

    # S727: Single-file repo — the entire codebase is in one non-test source file.
    # All logic in one file makes every change touch the same place, prevents parallel
    # development, and makes testing harder; time to split into modules.
    _src_files727 = [
        fp for fp in graph.files
        if not _is_test_file(fp) and fp.endswith(".py")
    ]
    if len(_src_files727) == 1:
        _sole727 = _src_files727[0].rsplit("/", 1)[-1]
        lines.append(
            f"single-file repo: entire codebase is in {_sole727}"
            f" — all logic in one file; consider splitting into modules as complexity grows"
        )

    # S733: Flat repo — all source files are in the root directory with no subdirectories.
    # Flat repos work at small scale but become hard to navigate as file count grows;
    # organize into subdirectories before cognitive overhead becomes a problem.
    _src_files733 = [
        fp for fp in graph.files
        if not _is_test_file(fp) and fp.endswith(".py")
    ]
    if len(_src_files733) >= 5:
        _has_subdir733 = any("/" in fp.replace("\\", "/") for fp in _src_files733)
        if not _has_subdir733:
            lines.append(
                f"flat repo: all {len(_src_files733)} source files are in the root directory"
                f" — consider organizing into subdirectories as the codebase grows"
            )

    # S739: High constant density — the repo has more than twice as many variables/constants
    # as functions. Config-heavy or data-heavy repos may lack abstraction; constants scattered
    # across files make configuration management harder.
    _all_syms739 = list(graph.symbols.values())
    _fn_count739 = sum(
        1 for s in _all_syms739
        if s.kind.value in ("function", "method") and not _is_test_file(s.file_path)
    )
    _var_count739 = sum(
        1 for s in _all_syms739
        if s.kind.value in ("variable", "constant") and not _is_test_file(s.file_path)
    )
    if _fn_count739 > 0 and _var_count739 > _fn_count739 * 2:
        lines.append(
            f"high constant density: {_var_count739} variables/constants vs {_fn_count739} functions"
            f" — config-heavy repo; verify constants are not scattered across multiple files"
        )

    # S745: Test concentration — more than 80% of test symbols are in one test file.
    # When tests are heavily concentrated in one file, the test suite becomes monolithic;
    # it's hard to navigate, slow to run selectively, and prone to test coupling.
    _test_files745 = [fp for fp in graph.files if _is_test_file(fp)]
    if len(_test_files745) >= 2:
        _total_test_syms745 = sum(
            len(graph.files[fp].symbols) for fp in _test_files745 if fp in graph.files
        )
        if _total_test_syms745 > 0:
            for _tfp745 in _test_files745:
                _file_syms745 = len(graph.files[_tfp745].symbols) if _tfp745 in graph.files else 0
                if _file_syms745 / _total_test_syms745 > 0.80:
                    lines.append(
                        f"test concentration: {_tfp745.rsplit('/', 1)[-1]} has"
                        f" {_file_syms745}/{_total_test_syms745}"
                        f" ({_file_syms745 / _total_test_syms745:.0%}) of all test symbols"
                        f" — monolithic test file; split into feature-specific test modules"
                    )
                    break

    # S751: Single test file — the repo has source symbols but only one test file.
    # A single test file often means tests were added as an afterthought and coverage
    # is shallow; as the codebase grows, one test file becomes a maintenance bottleneck.
    _all_test_fps751 = [fp for fp in graph.files if _is_test_file(fp)]
    _src_syms751 = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.parent_id is None
        and s.kind.value not in ("unknown", "module")
    ]
    if len(_all_test_fps751) == 1 and len(_src_syms751) >= 5:
        lines.append(
            f"single test file: all tests in {_all_test_fps751[0].rsplit('/', 1)[-1]}"
            f" — one test file for {len(_src_syms751)} source symbols; split by module as the project grows"
        )

    # S757: Constant-heavy repo — ratio of constants/variables to functions exceeds 2:1.
    # When a codebase has far more constants than functions, it may have config sprawl,
    # magic numbers distributed across files, or a fragmented configuration architecture.
    _const_syms757 = sum(
        1 for s in graph.symbols.values()
        if s.kind.value in ("constant", "variable")
        and not _is_test_file(s.file_path)
        and s.parent_id is None
    )
    _fn_syms757 = sum(
        1 for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.parent_id is None
    )
    if _fn_syms757 >= 3 and _const_syms757 >= _fn_syms757 * 2:
        lines.append(
            f"constant-heavy repo: {_const_syms757} constants vs {_fn_syms757} functions"
            f" — config sprawl likely; consolidate constants into dedicated config modules"
        )

    # S763: Classless repo — the repo has no classes at all (purely functional style).
    # A classless codebase is deliberately functional; adding classes changes the design
    # paradigm and may conflict with existing patterns — consider modules/functions instead.
    _class_syms763 = sum(
        1 for s in graph.symbols.values()
        if s.kind.value == "class" and not _is_test_file(s.file_path)
    )
    _src_fn763 = sum(
        1 for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.parent_id is None
    )
    if _class_syms763 == 0 and _src_fn763 >= 5:
        lines.append(
            f"classless repo: no classes found — purely functional style with {_src_fn763} top-level functions;"
            f" new classes would break the established paradigm"
        )

    # S769: High method-to-class ratio — average methods per class exceeds 10.
    # Classes with many methods are often "god objects" or service classes that do too much;
    # high method counts signal that a class has accumulated too many responsibilities.
    _classes769 = [
        s for s in graph.symbols.values()
        if s.kind.value == "class" and not _is_test_file(s.file_path)
    ]
    if len(_classes769) >= 2:
        _total_methods769 = sum(
            sum(1 for c in graph.symbols.values() if c.parent_id == cls.id and c.kind.value in ("function", "method"))
            for cls in _classes769
        )
        _avg769 = _total_methods769 / len(_classes769)
        if _avg769 >= 10:
            lines.append(
                f"high method-to-class ratio: avg {_avg769:.1f} methods per class"
                f" — classes are large; consider splitting responsibilities"
            )

    # S775: Large average file size — avg source file exceeds 200 lines.
    # When source files average more than 200 lines, the codebase has large files
    # that are hard to navigate and review — consider splitting into smaller modules.
    _src_files775 = [fi for fp, fi in graph.files.items() if not _is_test_file(fp)]
    if len(_src_files775) >= 3:
        _total_lines775 = sum(fi.line_count if fi.line_count else 0 for fi in _src_files775)
        _avg_lines775 = _total_lines775 / len(_src_files775)
        if _avg_lines775 >= 200:
            lines.append(
                f"large avg file size: {_avg_lines775:.0f} avg lines per source file"
                f" — files are large; consider splitting into smaller, focused modules"
            )

    # S787: High import coupling — the most-imported file is imported by > 50% of source files.
    # When a single file is imported by more than half the codebase, it becomes a structural
    # singleton; any breaking change requires touching every importer simultaneously.
    _src_fps787 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_fps787) >= 4:
        _max_importers787 = 0
        _max_fp787 = None
        for _fp787 in _src_fps787:
            _cnt787 = len([f for f in graph.importers_of(_fp787) if not _is_test_file(f)])
            if _cnt787 > _max_importers787:
                _max_importers787 = _cnt787
                _max_fp787 = _fp787
        _threshold787 = len(_src_fps787) * 0.5
        if _max_fp787 and _max_importers787 >= _threshold787:
            lines.append(
                f"high import coupling: {_max_fp787.rsplit('/', 1)[-1]} imported by"
                f" {_max_importers787}/{len(_src_fps787)} source files (>{_threshold787:.0f})"
                f" — structural singleton; breaking changes require touching all importers"
            )

    return lines

def _signals_async_oop_d(graph: Tempo) -> list[str]:
    """Language, docstring, and module cohesion signals (S781–S1009)."""
    lines: list[str] = []
    # S781: Many small files — average source file is under 10 lines with 5+ source files.
    # Over-fragmented codebases split logic into many tiny files, increasing navigation
    # cost and import overhead; consider consolidating into fewer coherent modules.
    _src_files781 = [fi for fp, fi in graph.files.items() if not _is_test_file(fp)]
    if len(_src_files781) >= 5:
        _avg_lines781 = sum(fi.line_count or 0 for fi in _src_files781) / len(_src_files781)
        if _avg_lines781 < 10:
            lines.append(
                f"many small files: {len(_src_files781)} source files averaging {_avg_lines781:.1f} lines"
                f" — over-fragmented; consider consolidating related modules"
            )

    # S793: Deep nesting — majority of source files are 3+ directory levels deep.
    # Codebases where most files are deeply nested are over-organized; navigation
    # requires traversing many directories and imports become verbose.
    _all_src793 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_all_src793) >= 5:
        _deep793 = [fp for fp in _all_src793 if fp.replace("\\", "/").count("/") >= 3]
        if len(_deep793) / len(_all_src793) > 0.5:
            lines.append(
                f"deep nesting: {len(_deep793)}/{len(_all_src793)} source files"
                f" at 3+ directory levels — over-organized structure increases navigation cost"
            )

    # S799: No entry point diversity — repo has only one entry point (low resilience).
    # Repos with a single entry point have a single failure point for the entire startup
    # path; adding CLI/worker entry points improves operational flexibility.
    _entry799 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.name in ("main", "run", "start", "app", "application", "create_app", "entry")
        and s.parent_id is None
        and not graph.callers_of(s.id)
    ]
    _total_src799 = sum(1 for fp in graph.files if not _is_test_file(fp))
    if len(_entry799) == 1 and _total_src799 >= 5:
        lines.append(
            f"no entry diversity: only one entry function ({_entry799[0].name} in"
            f" {_entry799[0].file_path.rsplit('/', 1)[-1]}) — one startup path;"
            f" consider adding CLI or worker entry points for resilience"
        )

    # S805: Multi-language repo — codebase uses 3+ distinct programming languages.
    # Repos with 3+ languages require contributors to context-switch across ecosystems;
    # each language adds tooling, linting, and dependency management overhead.
    _langs805 = set(
        lang for fp, fi in graph.files.items()
        if not _is_test_file(fp)
        for lang in [fi.language.value]
        if lang not in ("unknown", "text", "markdown", "json", "yaml", "toml", "html", "css")
    )
    if len(_langs805) >= 3:
        lines.append(
            f"multi-language repo: {len(_langs805)} programming languages detected ({', '.join(sorted(_langs805)[:4])})"
            f" — cross-language codebase; ensure tooling covers all languages"
        )

    # S817: No docstring coverage — 10+ public functions but none have docstrings.
    # Undocumented codebases rely entirely on code readability; agents and reviewers
    # cannot infer intent from names alone, increasing onboarding and review cost.
    _pub_fns817 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function",)
        and s.parent_id is None
        and not s.name.startswith("_")
        and not _is_test_file(s.file_path)
    ]
    if len(_pub_fns817) >= 10:
        _with_doc817 = [s for s in _pub_fns817 if s.doc]
        if not _with_doc817:
            lines.append(
                f"no docstring coverage: {len(_pub_fns817)} public functions with zero docstrings"
                f" — undocumented API; intent cannot be inferred from names alone"
            )

    # S811: Large average file size — average source file line count exceeds 300.
    # Oversized files accumulate multiple responsibilities; they increase cognitive load
    # and are a leading indicator of future hotspots and refactoring pressure.
    _src_files811 = [fi for fp, fi in graph.files.items() if not _is_test_file(fp)]
    if _src_files811:
        _avg_lines811 = sum(fi.line_count for fi in _src_files811) / len(_src_files811)
        if _avg_lines811 > 300:
            lines.append(
                f"large avg file size: {_avg_lines811:.0f} lines per source file on average"
                f" — files may benefit from splitting to improve navigability"
            )

    # S835: Deeply nested codebase — average file path depth is 3+ directory levels.
    # Deeply nested file trees increase cognitive load when navigating the codebase;
    # imports become verbose and finding files by name alone becomes error-prone.
    _all_fps835 = [fp for fp in graph.files if not _is_test_file(fp)]
    if _all_fps835:
        _avg_depth835 = sum(len(fp.replace("\\", "/").split("/")) - 1 for fp in _all_fps835) / len(_all_fps835)
        if _avg_depth835 >= 3:
            lines.append(
                f"deep nesting: avg path depth {_avg_depth835:.1f} levels across {len(_all_fps835)} source files"
                f" — deeply nested; imports are verbose and files are hard to locate by name"
            )

    # S829: No module-level constants — codebase has functions but no named constants.
    # Repos with no constants use magic values directly; reviewers cannot tell if
    # numeric literals are intentional limits or accidental values.
    _fns829 = [s for s in graph.symbols.values() if s.kind.value == "function" and not _is_test_file(s.file_path)]
    _consts829 = [s for s in graph.symbols.values() if s.kind.value in ("constant", "variable") and s.parent_id is None and not _is_test_file(s.file_path)]
    if len(_fns829) >= 5 and not _consts829:
        lines.append(
            f"no module constants: {len(_fns829)} functions but zero named constants"
            f" — magic values in source; consider extracting thresholds and limits to named constants"
        )

    # S823: Test-heavy repo — test files outnumber source files 2:1 or more.
    # Over-investment in tests relative to source code may indicate over-engineering,
    # duplicated test scenarios, or abandoned source modules with surviving tests.
    _src_files823 = [fp for fp in graph.files if not _is_test_file(fp)]
    _tst_files823 = [fp for fp in graph.files if _is_test_file(fp)]
    if len(_src_files823) >= 3 and len(_tst_files823) >= len(_src_files823) * 2:
        lines.append(
            f"test-heavy repo: {len(_tst_files823)} test files vs {len(_src_files823)} source files"
            f" — test suite is 2×+ the source; check for duplicated scenarios or orphaned tests"
        )

    # S841: No async functions — codebase has many functions but none are async.
    # A repo with zero async functions may be using blocking I/O; async-naive
    # patterns can become bottlenecks when services are later integrated with async frameworks.
    _all_fns841 = [s for s in graph.symbols.values() if s.kind.value == "function" and not _is_test_file(s.file_path)]
    _async_fns841 = [s for s in _all_fns841 if (s.signature or "").lstrip().startswith("async ")]
    if len(_all_fns841) >= 10 and not _async_fns841:
        lines.append(
            f"no async functions: {len(_all_fns841)} functions but none are async"
            f" — all synchronous; blocking I/O may become a bottleneck in async frameworks"
        )

    # S847: Many small modules — repo has 10+ files all under 20 lines.
    # A repo with many tiny modules has over-fragmented its logic; each function
    # is isolated in its own file, making cross-cutting concerns hard to see.
    _src_files847 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_files847) >= 10:
        _small_files847 = [fp for fp in _src_files847 if graph.files[fp].line_count < 20]
        if len(_small_files847) == len(_src_files847):
            lines.append(
                f"many small modules: all {len(_src_files847)} source files are under 20 lines"
                f" — over-fragmented; consider consolidating related small modules"
            )

    # S859: Low module cohesion — many source files each have 5+ top-level functions.
    # When many modules each expose many unrelated functions, the codebase lacks
    # cohesion; functions should be grouped by shared data or purpose, not by accident.
    _src_fps859 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_fps859) >= 5:
        _high_fn_files859 = []
        for _fp859 in _src_fps859:
            _fi859 = graph.files[_fp859]
            _top_fns859 = [
                graph.symbols[sid] for sid in _fi859.symbols
                if sid in graph.symbols
                and graph.symbols[sid].kind.value == "function"
                and graph.symbols[sid].parent_id is None
            ]
            if len(_top_fns859) >= 5:
                _high_fn_files859.append(_fp859)
        if len(_high_fn_files859) >= len(_src_fps859) * 0.5:
            lines.append(
                f"low cohesion: {len(_high_fn_files859)}/{len(_src_fps859)} files each expose 5+ top-level functions"
                f" — functions may not be grouped by shared purpose; consider grouping by domain"
            )

    # S853: High dead ratio — over 40% of exported source symbols are unused.
    # A repo where most of its public API is dead is accumulating significant cleanup debt;
    # maintaining dead symbols wastes review time and creates misleading documentation.
    _all_exported853 = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path)
        and s.parent_id is None
        and not s.name.startswith("_")
    ]
    if len(_all_exported853) >= 10:
        _dead853 = graph.find_dead_code()
        _dead_ids853 = {s.id for s in _dead853}
        _dead_exported853 = [s for s in _all_exported853 if s.id in _dead_ids853]
        _ratio853 = len(_dead_exported853) / len(_all_exported853)
        if _ratio853 >= 0.4:
            lines.append(
                f"high dead ratio: {len(_dead_exported853)}/{len(_all_exported853)} exported symbols ({_ratio853:.0%}) appear unused"
                f" — significant cleanup debt; review dead code before adding more public API"
            )

    # S865: Abstract-heavy codebase — 3+ classes with Abstract/Base prefix or ABC suffix.
    # Many abstract base classes indicate a deep class hierarchy; agents must understand
    # which concrete implementations exist and whether all contracts are satisfied.
    _abstract_classes865 = [
        s for s in graph.symbols.values()
        if s.kind.value == "class"
        and not _is_test_file(s.file_path)
        and (s.name.startswith("Abstract") or s.name.startswith("Base") or s.name.endswith("ABC"))
    ]
    if len(_abstract_classes865) >= 3:
        _abc_names865 = ", ".join(s.name for s in _abstract_classes865[:3])
        lines.append(
            f"abstract-heavy: {len(_abstract_classes865)} abstract/base classes ({_abc_names865})"
            f" — deep class hierarchy; verify all contracts are implemented by concrete subclasses"
        )

    # S883: Monolith file — one file contains 50%+ of all source symbols.
    # A single file dominating the symbol count indicates a concentration of logic;
    # changes to it carry higher blast radius than changes to smaller, focused files.
    _src_syms883 = [
        s for s in graph.symbols.values()
        if not _is_test_file(s.file_path) and s.kind.value in ("function", "method", "class")
    ]
    if len(_src_syms883) >= 6:
        _file_counts883: dict[str, int] = {}
        for s in _src_syms883:
            _file_counts883[s.file_path] = _file_counts883.get(s.file_path, 0) + 1
        _top_file883, _top_count883 = max(_file_counts883.items(), key=lambda x: x[1])
        _pct883 = int(_top_count883 / len(_src_syms883) * 100)
        if _pct883 >= 50:
            lines.append(
                f"monolith file: {_top_file883.rsplit('/', 1)[-1]} contains {_pct883}% of source symbols"
                f" — concentrated logic; changes have wide blast radius"
            )

    # S877: Low docstring coverage — 70%+ of exported non-test functions lack docstrings.
    # Undocumented functions require reading the full body to understand intent; agents
    # generating or modifying code in this codebase should add docstrings proactively.
    _src_fns877 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and s.exported
        and not _is_test_file(s.file_path)
    ]
    if len(_src_fns877) >= 5:
        _undoc877 = [s for s in _src_fns877 if not s.doc]
        _undoc_pct877 = int(len(_undoc877) / len(_src_fns877) * 100)
        if _undoc_pct877 >= 70:
            lines.append(
                f"low doc coverage: {_undoc_pct877}% of exported functions lack docstrings"
                f" — undocumented codebase; read function bodies carefully to infer intent"
            )

    # S871: No test files — repo has 5+ source files but no test files.
    # An untested codebase means all changes carry undetected regression risk;
    # agents should flag any behavior changes as potentially breaking.
    _test_files871 = [fp for fp in graph.files if _is_test_file(fp)]
    if not _test_files871 and len(graph.files) >= 5:
        lines.append(
            f"no test files: {len(graph.files)} source files but no test files detected"
            f" — untested codebase; changes carry higher risk of undetected regressions"
        )

    # S889: High fan-in file — one file is imported by 5+ other source files.
    # Files with high fan-in are critical infrastructure; any change triggers a
    # large blast radius across the dependency tree of importing modules.
    _fan_in889: dict[str, int] = {}
    for fp889 in graph.files:
        if not _is_test_file(fp889):
            importers889 = graph.importers_of(fp889)
            count889 = sum(1 for i in importers889 if not _is_test_file(i))
            if count889 > 0:
                _fan_in889[fp889] = count889
    if _fan_in889:
        _top_fp889, _top_in889 = max(_fan_in889.items(), key=lambda x: x[1])
        if _top_in889 >= 5:
            lines.append(
                f"high fan-in: {_top_fp889.rsplit('/', 1)[-1]} imported by {_top_in889} files"
                f" — critical infrastructure; changes here have wide blast radius"
            )

    # S895: Circular import pair — two source files mutually import each other.
    # Circular imports create tight coupling and make isolated testing impossible;
    # they are a common source of import-order errors and refactoring difficulty.
    _seen_pairs895: set[tuple[str, str]] = set()
    for fp895 in graph.files:
        if _is_test_file(fp895):
            continue
        importers895 = {i for i in graph.importers_of(fp895) if not _is_test_file(i)}
        for imp895 in importers895:
            if fp895 in {i for i in graph.importers_of(imp895) if not _is_test_file(i)}:
                pair895 = tuple(sorted([fp895.rsplit("/", 1)[-1], imp895.rsplit("/", 1)[-1]]))
                _seen_pairs895.add(pair895)  # type: ignore[arg-type]
    if _seen_pairs895:
        _ex895 = sorted(_seen_pairs895)[0]
        lines.append(
            f"circular imports: {len(_seen_pairs895)} mutual import pair(s)"
            f" — e.g. {_ex895[0]} ↔ {_ex895[1]}; circular imports create tight coupling"
        )

    # S901: Flat structure — all source files are in a single root directory.
    # A flat codebase with 5+ files and no subdirectories becomes hard to navigate
    # as it grows; consider grouping by module or domain to improve discoverability.
    if len(graph.files) >= 5:
        _src_files901 = [fp for fp in graph.files if not _is_test_file(fp)]
        _dirs901 = {
            fp.replace("\\", "/").rsplit("/", 1)[0] if "/" in fp.replace("\\", "/") else "."
            for fp in _src_files901
        }
        if len(_dirs901) == 1 and "." in _dirs901 and len(_src_files901) >= 5:
            lines.append(
                f"flat structure: all {len(_src_files901)} source files are in the root directory"
                f" — no subdirectory organization; consider grouping by module as codebase grows"
            )

    # S907: High constant ratio — repo has more constants than functions (config-heavy codebase).
    # A constant-heavy repo often has scattered configuration values mixed with business logic;
    # centralizing into dedicated config files improves discoverability and reduces change risk.
    _all_fns907 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method") and not _is_test_file(s.file_path)
    ]
    _all_consts907 = [
        s for s in graph.symbols.values()
        if s.kind.value == "constant" and not _is_test_file(s.file_path)
    ]
    if len(_all_fns907) >= 5 and len(_all_consts907) > len(_all_fns907):
        lines.append(
            f"high constant ratio: {len(_all_consts907)} constants vs {len(_all_fns907)} functions"
            f" — constant-heavy codebase; consider centralizing configuration into dedicated files"
        )

    # S913: Test-heavy codebase — test files outnumber source files 2:1 or more.
    # An unusually high test-to-code ratio may indicate orphaned tests, large integration
    # test suites, or test files that outlasted the features they cover.
    _test_fps913 = [fp for fp in graph.files if _is_test_file(fp)]
    _src_fps913 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_src_fps913) >= 3 and len(_test_fps913) >= len(_src_fps913) * 2:
        lines.append(
            f"test-heavy: {len(_test_fps913)} test files vs {len(_src_fps913)} source files"
            f" — unusually high test/source ratio; check for orphaned or over-duplicated tests"
        )

    # S919: No entry points — repo has no recognizable main/run/start/execute function.
    # A codebase without entry points is hard to understand at a glance; agents should
    # check for hidden entry points in __main__ blocks or framework-driven invocations.
    _entry_names919 = {"main", "run", "start", "execute", "launch", "serve", "app"}
    _src_syms919 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method")
        and s.parent_id is None
        and not _is_test_file(s.file_path)
    ]
    if len(_src_syms919) >= 5 and not any(s.name in _entry_names919 for s in _src_syms919):
        lines.append(
            f"no entry point: no main/run/start/execute function found in {len(_src_syms919)} source functions"
            f" — unclear invocation path; check for __main__ blocks or framework-driven entry points"
        )

    # S925: Mixed language repo — both Python and JavaScript/TypeScript files coexist.
    # Multi-language repos require agents to understand cross-language contracts; changes
    # to shared interfaces (APIs, schemas, events) must be reflected in both languages.
    _py_files925 = [fp for fp in graph.files if fp.endswith(".py") and not _is_test_file(fp)]
    _js_files925 = [fp for fp in graph.files if fp.endswith((".js", ".ts", ".jsx", ".tsx"))]
    if len(_py_files925) >= 2 and len(_js_files925) >= 2:
        lines.append(
            f"mixed languages: {len(_py_files925)} Python file(s) and {len(_js_files925)} JS/TS file(s)"
            f" — cross-language repo; ensure shared API contracts are updated consistently"
        )

    # S931: Large public API — repo exports 20+ top-level functions or classes.
    # A very large public surface is harder to maintain; agents should be conservative
    # about adding new exports and check that any removed exports have no consumers.
    _public_syms931 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "class")
        and s.parent_id is None
        and not s.name.startswith("_")
        and not _is_test_file(s.file_path)
    ]
    if len(_public_syms931) >= 20:
        lines.append(
            f"large public API: {len(_public_syms931)} exported top-level symbols"
            f" — wide public surface; be conservative adding exports; check consumers before removing"
        )

    # S937: No constants — repo has no module-level constants (potential magic values in code).
    # Codebases without defined constants often embed magic numbers and strings inline;
    # this makes thresholds, limits, and configuration values hard to find and change safely.
    _all_consts937 = [
        s for s in graph.symbols.values()
        if s.kind.value == "constant" and not _is_test_file(s.file_path)
    ]
    _src_syms937 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "method") and not _is_test_file(s.file_path)
    ]
    if len(_src_syms937) >= 5 and not _all_consts937:
        lines.append(
            f"no constants: no module-level constants found across {len(_src_syms937)} source functions"
            f" — may indicate magic values in code; consider extracting thresholds and config into constants"
        )

    # S943: Function-only codebase — all source symbols are top-level functions; no class methods.
    # A codebase with no class methods is fully procedural; agents should avoid suggesting
    # OOP refactors unless there's clear evidence of state that needs encapsulation.
    _all_methods943 = [
        s for s in graph.symbols.values()
        if s.kind.value == "method"
        and not _is_test_file(s.file_path)
    ]
    _all_fns943 = [
        s for s in graph.symbols.values()
        if s.kind.value == "function"
        and s.parent_id is None
        and not _is_test_file(s.file_path)
    ]
    if len(_all_fns943) >= 5 and not _all_methods943:
        lines.append(
            f"function-only: {len(_all_fns943)} top-level functions, 0 class methods"
            f" — fully procedural codebase; avoid OOP refactor suggestions without clear encapsulation need"
        )

    # S949: All-private codebase — every source function/class is prefixed with _.
    # A codebase with no public API may be designed as an internal library;
    # adding public symbols here should be intentional — undocumented exports create accidental APIs.
    _src_syms949 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "class")
        and s.parent_id is None
        and not _is_test_file(s.file_path)
    ]
    if len(_src_syms949) >= 5:
        _public949 = [s for s in _src_syms949 if not s.name.startswith("_")]
        if not _public949:
            lines.append(
                f"all-private: {len(_src_syms949)} source symbols found, none are public"
                f" — internal-only codebase; adding exports should be intentional to avoid accidental APIs"
            )

    # S955: Mega function — any source function exceeds 200 lines.
    # A function this large almost always mixes concerns; adding features risks subtle breakage
    # in unrelated logic buried in the same body.
    _mega955 = None
    for _s955 in graph.symbols.values():
        if (
            _s955.kind.value == "function"
            and _s955.parent_id is None
            and not _is_test_file(_s955.file_path)
            and _s955.line_count >= 200
        ):
            if _mega955 is None or _s955.line_count > _mega955.line_count:
                _mega955 = _s955
    if _mega955 is not None:
        lines.append(
            f"mega function: {_mega955.name} spans {_mega955.line_count} lines"
            f" — exceeds 200-line threshold; candidate for mandatory decomposition before any new additions"
        )

    # S961: Flat architecture — all source files are at the root level with no subdirectory structure.
    # Flat repos with many files have no module boundaries; a growing flat codebase accumulates
    # coupling across everything and becomes harder to reason about incrementally.
    _src_files961 = [
        fp for fp in graph.files
        if not _is_test_file(fp) and "/" not in fp.replace("\\", "/").lstrip("./")
    ]
    _all_src_files961 = [fp for fp in graph.files if not _is_test_file(fp)]
    if len(_all_src_files961) >= 8 and len(_src_files961) == len(_all_src_files961):
        lines.append(
            f"flat architecture: all {len(_all_src_files961)} source files are at the root level"
            f" — no module boundaries; consider introducing package subdirectories as the codebase grows"
        )

    # S967: No tests at all — the repo has zero test files.
    # Without any test files, changes cannot be verified against regression;
    # agents should treat all changes as high risk regardless of apparent simplicity.
    _test_files967 = [fp for fp in graph.files if _is_test_file(fp)]
    _src_files967 = [fp for fp in graph.files if not _is_test_file(fp)]
    if not _test_files967 and len(_src_files967) >= 3:
        lines.append(
            f"no tests: 0 test files detected in {len(_src_files967)} source file(s)"
            f" — no regression safety net; all changes carry high risk regardless of apparent scope"
        )

    # S973: Lone class — exactly one class exists alongside many functions.
    # A single class in an otherwise function-oriented codebase often acts as a namespace;
    # this may indicate an incomplete OOP migration or a namespace anti-pattern.
    _src_classes973 = [
        s for s in graph.symbols.values()
        if s.kind.value == "class" and s.parent_id is None and not _is_test_file(s.file_path)
    ]
    _src_fns973 = [
        s for s in graph.symbols.values()
        if s.kind.value == "function" and s.parent_id is None and not _is_test_file(s.file_path)
    ]
    if len(_src_classes973) == 1 and len(_src_fns973) >= 10:
        lines.append(
            f"lone class: only 1 class ({_src_classes973[0].name}) among {len(_src_fns973)} functions"
            f" — may be a namespace class; verify it adds value over module-level functions"
        )

    # S979: No classes — codebase has only top-level functions, no classes defined.
    # A purely functional codebase means OOP patterns (polymorphism, encapsulation)
    # are handled via closures or modules; agents should avoid class-based refactors.
    _src_classes979 = [
        s for s in graph.symbols.values()
        if s.kind.value == "class" and not _is_test_file(s.file_path)
    ]
    _src_fns979 = [
        s for s in graph.symbols.values()
        if s.kind.value == "function" and s.parent_id is None and not _is_test_file(s.file_path)
    ]
    if not _src_classes979 and len(_src_fns979) >= 5:
        _nfiles979 = len({s.file_path for s in _src_fns979})
        lines.append(
            f"no classes: {len(_src_fns979)} source functions across {_nfiles979} file(s) with 0 class definitions"
            f" — purely functional codebase; OOP abstractions replaced by modules and closures"
        )

    # S985: No entrypoint — codebase has no obvious entry point function.
    # Without a clear entry point, execution flow is ambiguous; agents may misidentify
    # the primary code path when tracing bugs or reasoning about change impact.
    _entry_names985 = {"main", "run", "__main__", "start", "app", "entry", "entrypoint"}
    _has_entry985 = any(
        s.name.lower() in _entry_names985
        and s.kind.value == "function"
        and s.parent_id is None
        and not _is_test_file(s.file_path)
        for s in graph.symbols.values()
    )
    _src_fns985 = [
        s for s in graph.symbols.values()
        if s.kind.value == "function" and not _is_test_file(s.file_path)
    ]
    if not _has_entry985 and len(_src_fns985) >= 5:
        lines.append(
            f"no entrypoint: no main/run/start function found among {len(_src_fns985)} source function(s)"
            f" — entry point is unclear; execution flow harder to trace for agents and reviewers"
        )

    # S991: God class — a single class has 8 or more methods.
    # A class with many methods accumulates multiple responsibilities; changes to one
    # responsibility risk unintended coupling to others, making the class hard to test safely.
    _class_method_counts991: dict[str, tuple[str, int]] = {}
    for s in graph.symbols.values():
        if s.kind.value == "method" and s.parent_id is not None and not _is_test_file(s.file_path):
            _cname991 = s.parent_id.rsplit("::", 1)[-1] if "::" in s.parent_id else s.parent_id
            _class_method_counts991[s.parent_id] = (_cname991, _class_method_counts991.get(s.parent_id, (_cname991, 0))[1] + 1)
    _god_classes991 = [(name, cnt) for _, (name, cnt) in _class_method_counts991.items() if cnt >= 8]
    if _god_classes991:
        _top_god991 = max(_god_classes991, key=lambda x: x[1])
        lines.append(
            f"god class candidate: {_top_god991[0]} has {_top_god991[1]} methods"
            f" — single class accumulating many responsibilities; changes may have unintended coupling"
        )

    # S997: Test heavy — test suite defines far more functions than source.
    # When test count significantly exceeds source function count, CI slows and
    # tests become brittle; agents may need to update many tests per code change.
    _src_fns997 = [
        s for s in graph.symbols.values()
        if s.kind.value == "function" and s.parent_id is None and not _is_test_file(s.file_path)
    ]
    _test_fns997 = [
        s for s in graph.symbols.values()
        if s.kind.value in ("function", "test") and _is_test_file(s.file_path) and s.name.startswith("test_")
    ]
    if len(_src_fns997) >= 2 and len(_test_fns997) >= len(_src_fns997) * 3:
        lines.append(
            f"test heavy: {len(_test_fns997)} test functions for {len(_src_fns997)} source functions"
            f" — high test burden; CI may be slow and expect many test updates per code change"
        )

    # S1003: Deep nesting — codebase contains files nested 3 or more directory levels deep.
    # Deeply nested source files indicate complex package hierarchies; agents must track
    # long import paths and may miss files hidden in rarely explored subdirectories.
    _root1003 = graph.root.replace("\\", "/").rstrip("/")
    _deep_files1003 = [
        fp for fp in graph.files
        if fp.replace("\\", "/").replace(_root1003 + "/", "").count("/") >= 3
        and not _is_test_file(fp)
    ]
    if _deep_files1003:
        _deepest1003 = max(_deep_files1003, key=lambda fp: fp.replace("\\", "/").replace(_root1003 + "/", "").count("/"))
        _depth1003 = _deepest1003.replace("\\", "/").replace(_root1003 + "/", "").count("/")
        lines.append(
            f"deep nesting: {len(_deep_files1003)} source file(s) nested {_depth1003}+ levels deep"
            f" — complex package hierarchy; agents may miss deeply nested modules"
        )

    # S1009: Mixed languages — codebase spans 3 or more distinct programming languages.
    # Multi-language repos require agents to switch language context frequently;
    # cross-language call boundaries are harder to trace and may hide type or contract mismatches.
    _lang_counts1009 = {
        lang: count
        for lang, count in graph.stats.get("languages", {}).items()
        if count > 0
    }
    if len(_lang_counts1009) >= 3:
        _lang_list1009 = ", ".join(k for k, _ in sorted(_lang_counts1009.items(), key=lambda x: -x[1])[:5])
        lines.append(
            f"mixed languages: {len(_lang_counts1009)} languages detected ({_lang_list1009})"
            f" — multi-language repo; cross-language call boundaries are harder to trace for agents"
        )

    # S1015: Dominant file — a single source file holds more than half of all source symbols.
    # Extreme symbol concentration signals a monolithic module; all agent queries are likely
    # to converge on that file, which becomes a change bottleneck for unrelated work.
    _src_syms1015 = [s for s in graph.symbols.values() if not _is_test_file(s.file_path)]
    if _src_syms1015:
        _file_counts1015: dict[str, int] = {}
        for _s1015 in _src_syms1015:
            _file_counts1015[_s1015.file_path] = _file_counts1015.get(_s1015.file_path, 0) + 1
        _top_file1015, _top_count1015 = max(_file_counts1015.items(), key=lambda x: x[1])
        if _top_count1015 > len(_src_syms1015) // 2 and len(_file_counts1015) >= 2:
            _pct1015 = int(100 * _top_count1015 / len(_src_syms1015))
            lines.append(
                f"dominant file: {_top_file1015.rsplit('/', 1)[-1]} holds {_pct1015}% of all source symbols"
                f" — monolithic module; all changes converge here, blocking parallel work"
            )

    return lines

def _signals_async_oop(
    graph: Tempo, *, _s220_entry_files: list[str],
) -> list[str]:
    """Async/OOP signals."""
    lines: list[str] = []
    lines.extend(_signals_async_oop_a(graph, _s220_entry_files=_s220_entry_files))
    lines.extend(_signals_async_oop_b(graph))
    lines.extend(_signals_async_oop_c(graph))
    lines.extend(_signals_async_oop_d(graph))
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
