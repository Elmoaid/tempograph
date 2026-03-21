from __future__ import annotations

from pathlib import Path

from ..types import Tempo, EdgeKind, Symbol
from ._utils import _is_test_file
from .focused import _cochange_orbit

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
        # Early-exit for type stub files not indexed by graph (see S419 in main signals).
        if file_path.lower().endswith(".pyi"):
            return (
                f"type stub blast: {file_path.rsplit('/', 1)[-1]} is a type stub"
                f" — stub changes break type checks without runtime errors;"
                f" update callers together\n"
                f"(stub files are not graph-indexed; run mypy/pyright to check downstream impact)"
            )
        return f"File '{file_path}' not found."

    lines = [f"Blast radius for {file_path}:", ""]

    # S108: File age in blast header — how recently was this file last committed?
    # Freshly touched = actively being developed = changes need extra care.
    # Old = potentially ossified — touching it after long stability carries surprise risk.
    if graph.root:
        try:
            from ..git import file_last_modified_days as _fld_blast  # noqa: PLC0415
            _blast_age = _fld_blast(graph.root, file_path)
            if _blast_age is not None:
                if _blast_age <= 3:
                    _age_label = f"last touched: {_blast_age}d ago (active)"
                elif _blast_age >= 90:
                    _age_label = f"last touched: {_blast_age}d ago (stable)"
                else:
                    _age_label = f"last touched: {_blast_age}d ago"
                lines.append(_age_label)
        except Exception:
            pass

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
        # S51: recent callers — importers modified in last 14 days signal blast radius growing
        if _src_importers and graph.root:
            try:
                from ..git import file_last_modified_days as _fld  # noqa: PLC0415
                _recent = [(imp, _fld(graph.root, imp)) for imp in _src_importers]
                _recent = [(imp, d) for imp, d in _recent if d is not None and d <= 14]
                if len(_recent) >= 2:
                    _recent.sort(key=lambda x: x[1])
                    _rec_parts = [f"{imp.rsplit('/', 1)[-1]} ({d}d ago)" for imp, d in _recent[:3]]
                    lines.append(f"  Recent callers (14d): {', '.join(_rec_parts)}  ← blast radius growing")
            except Exception:
                pass
        lines.append("")

    # Symbols in this file that are called externally
    symbols = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols]

    # S111: Export surface — fraction of symbols in blast file that are exported.
    # High export ratio = public API file = callers everywhere = max review caution.
    # Only shown when 3+ total symbols and ratio >= 50%.
    _all_file_syms = [s for s in symbols if s.kind.value in ("function", "method", "class", "interface")]
    _exported_file_syms = [s for s in _all_file_syms if s.exported]
    if len(_all_file_syms) >= 3 and len(_exported_file_syms) >= 2:
        _exp_pct = int(len(_exported_file_syms) / len(_all_file_syms) * 100)
        if _exp_pct >= 50:
            lines.append(f"export surface: {len(_exported_file_syms)}/{len(_all_file_syms)} symbols exported ({_exp_pct}%)")

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

    # S91: Untested exports — exported functions/methods with no test callers.
    # Agents need to know which symbols lack a safety net before making changes.
    # Only shown when 2+ qualify (single untested export is too common to be signal).
    _untested_exports = [
        sym for sym in symbols
        if sym.exported and sym.kind.value in ("function", "method")
        and not any(_is_test_file(c.file_path) for c in graph.callers_of(sym.id))
    ]
    if len(_untested_exports) >= 2:
        _ue_names = [s.name for s in _untested_exports[:5]]
        _ue_str = ", ".join(_ue_names)
        if len(_untested_exports) > 5:
            _ue_str += f" +{len(_untested_exports) - 5} more"
        lines.append(f"Untested exports ({len(_untested_exports)}): {_ue_str} — no test coverage")
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
            from ..git import file_last_modified_days as _fld  # noqa: PLC0415
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

    # S74: "Imports from" — direct source dependencies of the blast target file.
    # Rounds out the picture: shows what THIS file depends on, not just who depends on it.
    _deps_of_target = sorted({
        e.target_id for e in graph.edges
        if e.kind == EdgeKind.IMPORTS
        and e.source_id == file_path
        and e.target_id in graph.files
        and not _is_test_file(e.target_id)
        and e.target_id != file_path
    })
    if len(_deps_of_target) >= 3:
        _dep_names = [fp.rsplit("/", 1)[-1] for fp in _deps_of_target[:5]]
        _dep_str = ", ".join(_dep_names)
        if len(_deps_of_target) > 5:
            _dep_str += f" +{len(_deps_of_target) - 5} more"
        lines.append(f"Imports from ({len(_deps_of_target)}): {_dep_str}")
        lines.append("")

    # S99: Peak exposure — the exported symbol with the most cross-file callers.
    # When a file has many exported symbols, agents need to know WHICH one carries the most risk.
    # Only shown when 2+ exported symbols exist and the peak has >= 3 cross-file callers.
    _peak_sym: "Symbol | None" = None
    _peak_count = 0
    for _exp_sym in symbols:
        if not _exp_sym.exported or _exp_sym.kind.value not in ("function", "method"):
            continue
        _cf = len({c.file_path for c in graph.callers_of(_exp_sym.id) if c.file_path != file_path and not _is_test_file(c.file_path)})
        if _cf > _peak_count:
            _peak_count = _cf
            _peak_sym = _exp_sym
    _exp_sym_count = sum(1 for s in symbols if s.exported and s.kind.value in ("function", "method"))
    if _peak_sym and _peak_count >= 3 and _exp_sym_count >= 2:
        lines.append(f"Peak exposure: {_peak_sym.name} ({_peak_count} caller files) — most-called export")
        lines.append("")

    # S70: Singleton caller hint — file only imported by 1 non-test file.
    # This tight coupling suggests the two files may be candidates for merging.
    _src_imps = [imp for imp in importers if not _is_test_file(imp)]
    if len(_src_imps) == 1:
        lines.append(f"Singleton caller: only used by {_src_imps[0].rsplit('/', 1)[-1]} — consider merging")
        lines.append("")

    # S88: Transitive blast — BFS through import graph to count all transitively affected files.
    # Direct blast shows 1 hop; transitive blast shows full ripple effect across the codebase.
    # Only shown when transitive count > direct count AND > 5 (otherwise direct already covers it).
    if importers:
        _trans_seen: set[str] = {file_path}
        _trans_queue = list(importers)
        _trans_depth = 0
        _trans_max_depth = 0
        _level_queue = list(importers)
        while _level_queue and _trans_depth < 8 and len(_trans_seen) < 200:
            _trans_depth += 1
            _next_level: list[str] = []
            for _tf in _level_queue:
                if _tf in _trans_seen:
                    continue
                _trans_seen.add(_tf)
                for _nxt in graph.importers_of(_tf):
                    if _nxt not in _trans_seen and _nxt in graph.files and not _is_test_file(_nxt):
                        _next_level.append(_nxt)
            if _next_level:
                _trans_max_depth = _trans_depth
            _level_queue = _next_level
        _trans_total = len(_trans_seen) - 1  # exclude the file itself
        if _trans_total > len(importers) and _trans_total >= 5:
            _non_test_direct = len([i for i in importers if not _is_test_file(i)])
            lines.append(
                f"Transitive blast: {_trans_total} total files affected "
                f"({_non_test_direct} direct, {_trans_max_depth} levels deep)"
            )
            lines.append("")

    # S90: Call chain preview — top 2-3 entry paths reaching exported symbols in this file.
    # Shows 2-hop chains: "top_caller::fn → mid_caller::fn → target_sym".
    # Only shown when 2+ distinct two-hop chains exist — adds depth the 1-hop callers list misses.
    _cc_exported = sorted(
        (sym for sym in symbols if sym.exported and sym.kind.value in ("function", "method")),
        key=lambda s: -len([
            c for c in graph.callers_of(s.id)
            if c.file_path != file_path and not _is_test_file(c.file_path)
        ])
    )
    if _cc_exported:
        _cc_paths: list[str] = []
        _cc_seen: set[str] = set()
        for _cc_sym in _cc_exported[:4]:
            _cc_ext = [
                c for c in graph.callers_of(_cc_sym.id)
                if c.file_path != file_path and not _is_test_file(c.file_path)
            ]
            for _cc_mid in _cc_ext[:3]:
                _cc_top = [
                    c for c in graph.callers_of(_cc_mid.id)
                    if c.file_path != _cc_mid.file_path
                    and not _is_test_file(c.file_path)
                    and c.file_path != file_path
                ]
                if _cc_top:
                    _top = _cc_top[0]
                    _chain = (
                        f"{_top.file_path.rsplit('/', 1)[-1]}::{_top.name}"
                        f" → {_cc_mid.name} → {_cc_sym.name}"
                    )
                    if _chain not in _cc_seen:
                        _cc_seen.add(_chain)
                        _cc_paths.append(_chain)
        if len(_cc_paths) >= 2:
            lines.append("Call chains (entry paths):")
            for _path in _cc_paths[:3]:
                lines.append(f"  {_path}")
            lines.append("")

    # S124: Untested blast callers — how many source files importing this file have no test coverage.
    # Complements S122 (ratio) by listing concrete count for agents to action.
    # Only shown when 3+ non-test importers AND test files exist in the project.
    _s124_downstream_src = [f for f in importers if not _is_test_file(f) and f in graph.files]
    if len(_s124_downstream_src) >= 3:
        _s124_all_tests = {fp for fp in graph.files if _is_test_file(fp)}
        if _s124_all_tests:
            _s124_untested = [
                fp for fp in _s124_downstream_src
                if not any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _s124_all_tests)
            ]
            if len(_s124_untested) >= 2:
                lines.append(f"untested callers: {len(_s124_untested)}/{len(_s124_downstream_src)} importing files have no tests")

    # S122: Downstream test coverage — fraction of files in blast radius that have tests.
    # Low downstream coverage = high blast risk: breakage can go undetected.
    # Only shown when 4+ non-test importers exist (otherwise blast radius too small to matter).
    _s122_downstream = [f for f in importers if not _is_test_file(f) and f in graph.files]
    if len(_s122_downstream) >= 4:
        _all_proj_test_fps_bl = {fp for fp in graph.files if _is_test_file(fp)}
        if _all_proj_test_fps_bl:
            _s122_tested = sum(
                1 for fp in _s122_downstream
                if any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _all_proj_test_fps_bl)
            )
            _s122_total = len(_s122_downstream)
            _s122_pct = int(_s122_tested / _s122_total * 100)
            if _s122_pct < 80:
                lines.append(f"downstream coverage: {_s122_tested}/{_s122_total} importers have tests ({_s122_pct}%)")

    # S145: Subclass count — when the blast target defines a class with INHERITS subclasses.
    # Changing a parent class ripples to all subclasses: method overrides, super() calls,
    # type annotations. Flag when 2+ subclasses extend a class defined in this file.
    _s145_classes = [s for s in symbols if s.kind.value in ("class", "interface")]
    if _s145_classes:
        _total_subclasses145: int = 0
        _s145_class_names: list[str] = []
        for _cls145 in _s145_classes:
            _sub_count = sum(
                1 for e in graph.edges
                if e.kind == EdgeKind.INHERITS and e.target_id == _cls145.id
            )
            if _sub_count >= 1:
                _total_subclasses145 += _sub_count
                _s145_class_names.append(f"{_cls145.name} ({_sub_count})")
        if _total_subclasses145 >= 2:
            _s145_str = ", ".join(_s145_class_names[:3])
            lines.append(f"subclass count: {_total_subclasses145} subclasses extend {_s145_str}")

    # S138: Aggregator file — blast target imports from many other modules.
    # Files that pull from 5+ distinct source modules are barrel/aggregator files:
    # changes to any upstream propagate here, AND any change here propagates to all importers.
    # Highest blast-amplification pattern. Only shown when 5+ distinct imports found.
    _s138_imports_from = sorted({
        e.target_id for e in graph.edges
        if e.kind == EdgeKind.IMPORTS
        and e.source_id == file_path
        and e.target_id in graph.files
        and not _is_test_file(e.target_id)
        and e.target_id != file_path
    })
    if len(_s138_imports_from) >= 5:
        lines.append(
            f"aggregator file: imports from {len(_s138_imports_from)} modules"
            f" — barrel/hub; upstream changes flow through here"
        )

    # S152: Cross-language blast — blast radius crosses language boundaries.
    # When a file in one language is imported/used by files in different languages,
    # changes require understanding both ecosystems. Flag when 2+ distinct languages found.
    _s152_target_lang = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    _s152_lang_ext: dict[str, int] = {}
    for _fp152 in importers:
        _ext152 = _fp152.rsplit(".", 1)[-1] if "." in _fp152 else ""
        if _ext152 and _ext152 != _s152_target_lang:
            _s152_lang_ext[_ext152] = _s152_lang_ext.get(_ext152, 0) + 1
    if _s152_lang_ext:
        _s152_langs_str = ", ".join(f".{ext}({n})" for ext, n in sorted(_s152_lang_ext.items())[:3])
        lines.append(f"cross-language blast: importers span {len(_s152_lang_ext)+1} languages ({_s152_langs_str})")

    # S158: Init-heavy — blast target is __init__.py with many exported symbols.
    # __init__ files are import entry points; high symbol count = many dependents at risk.
    # Only shown when file is __init__.py and has >= 8 exported symbols.
    _s158_basename = file_path.rsplit("/", 1)[-1]
    if _s158_basename in ("__init__.py", "index.ts", "index.js", "mod.rs"):
        _s158_exported = [
            s for s in symbols
            if s.exported and s.kind.value in ("function", "method", "class", "interface")
        ]
        if len(_s158_exported) >= 8:
            lines.append(
                f"init-heavy: {len(_s158_exported)} exported symbols in {_s158_basename}"
                f" — package entry point, changes affect all importers"
            )

    # S201: Isolated file — blast target has 0 external importers and 0 external callers.
    # A file with no incoming dependencies is safe to modify or delete without coordination.
    # Only shown when file has neither external importers nor symbols with external callers.
    _s201_importers = [
        i for i in graph.importers_of(file_path)
        if i in graph.files and i != file_path
    ]
    _s201_has_ext_callers = any(
        any(c.file_path != file_path for c in graph.callers_of(s.id))
        for s in symbols
    )
    if not _s201_importers and not _s201_has_ext_callers:
        lines.append(
            f"isolated file: no external importers or callers"
            f" — safe to modify or delete without coordination"
        )

    # S195: Blast fan-out — blast target's symbols call into 5+ distinct external files.
    # High outgoing call fan-out = the file reaches widely; changes ripple in multiple directions.
    # Only shown when symbols in blast target call into 5+ distinct non-target files.
    _s195_callee_files: set[str] = set()
    for _sym195 in symbols:
        for _callee195 in graph.callees_of(_sym195.id):
            if _callee195.file_path != file_path and not _is_test_file(_callee195.file_path):
                _s195_callee_files.add(_callee195.file_path)
    if len(_s195_callee_files) >= 5:
        _fan_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_s195_callee_files)[:3]]
        _fan_str = ", ".join(_fan_names)
        if len(_s195_callee_files) > 3:
            _fan_str += f" +{len(_s195_callee_files) - 3} more"
        lines.append(
            f"blast fan-out: calls into {len(_s195_callee_files)} external files"
            f" ({_fan_str}) — wide outgoing dependency, changes may cascade"
        )

    # S189: Sibling files — other source files sharing the same directory as the blast target.
    # Co-located files are often tightly coupled and may need coordinated updates.
    # Only shown when 2+ sibling source files exist in the same directory.
    _s189_target_dir = file_path.rsplit("/", 1)[0] if "/" in file_path else "."
    _s189_siblings = [
        fp for fp in graph.files
        if fp != file_path and not _is_test_file(fp)
        and (fp.rsplit("/", 1)[0] if "/" in fp else ".") == _s189_target_dir
    ]
    if len(_s189_siblings) >= 2:
        _sib_names = [fp.rsplit("/", 1)[-1] for fp in _s189_siblings[:3]]
        _sib_str = ", ".join(_sib_names)
        if len(_s189_siblings) > 3:
            _sib_str += f" +{len(_s189_siblings) - 3} more"
        lines.append(
            f"sibling files: {len(_s189_siblings)} co-located files ({_sib_str})"
            f" — may require coordinated updates"
        )

    # S183: Large export count — blast target exports >= 10 symbols.
    # High export count = large public API surface; any change is a potential breaking change.
    # Only shown when the blast file exports >= 10 fn/method/class/interface symbols.
    _s183_exported = [
        s for s in symbols
        if s.exported and s.kind.value in ("function", "method", "class", "interface")
    ]
    if len(_s183_exported) >= 10:
        lines.append(
            f"large export count: {len(_s183_exported)} exported symbols"
            f" — broad public API surface, changes risk breaking callers"
        )

    # S177: Cross-module callers — callers of blast-target symbols span 4+ distinct top-level dirs.
    # When 4+ modules call this file, a change requires coordination across many subsystems.
    # Only shown when callers come from 4+ distinct top-level directories.
    _s177_caller_modules: set[str] = set()
    for _sym177 in symbols:
        for _c177 in graph.callers_of(_sym177.id):
            _parts177 = _c177.file_path.split("/")
            _mod177 = _parts177[0] if len(_parts177) > 1 else "."
            if _mod177 != file_path.split("/")[0]:  # exclude own module
                _s177_caller_modules.add(_mod177)
    if len(_s177_caller_modules) >= 4:
        _s177_mods_str = ", ".join(sorted(_s177_caller_modules)[:4])
        if len(_s177_caller_modules) > 4:
            _s177_mods_str += f" +{len(_s177_caller_modules) - 4} more"
        lines.append(
            f"cross-module callers: {len(_s177_caller_modules)} modules depend on this file"
            f" ({_s177_mods_str})"
        )

    # S171: Indirect blast — files that are 2 hops away via importers (importers of importers).
    # Changing this file ripples to direct importers AND to their importers.
    # Only shown when 5+ distinct 2nd-hop importer files exist.
    _s171_direct = {i for i in graph.importers_of(file_path) if i in graph.files and i != file_path}
    _s171_indirect: set[str] = set()
    for _dir171 in _s171_direct:
        for _ind171 in graph.importers_of(_dir171):
            if _ind171 in graph.files and _ind171 != file_path and _ind171 not in _s171_direct:
                _s171_indirect.add(_ind171)
    if len(_s171_indirect) >= 5:
        lines.append(
            f"indirect blast: {len(_s171_indirect)} files 2 hops away via importers"
            f" — change propagates further than direct importers"
        )

    # S165: Call depth — BFS through CALLS edges from blast target, longest chain depth.
    # Deep call chains (>= 4 hops) amplify risk: a change propagates through many stack frames.
    # Only shown when BFS from any symbol in the target file reaches depth >= 4.
    _s165_seed_ids = {s.id for s in symbols}
    _s165_max_depth = 0
    _s165_deepest_name = ""
    for _seed_id165 in list(_s165_seed_ids)[:10]:  # cap BFS seeds for performance
        _s165_visited: set[str] = {_seed_id165}
        _s165_frontier = [(_seed_id165, 0)]
        while _s165_frontier:
            _cur_id165, _depth165 = _s165_frontier.pop(0)
            if _depth165 > _s165_max_depth:
                _s165_max_depth = _depth165
                _s165_deepest_name = graph.symbols[_seed_id165].name if _seed_id165 in graph.symbols else ""
            if _depth165 >= 6:  # safety cap
                continue
            for _callee165 in graph.callees_of(_cur_id165):
                if _callee165.id not in _s165_visited:
                    _s165_visited.add(_callee165.id)
                    _s165_frontier.append((_callee165.id, _depth165 + 1))
    if _s165_max_depth >= 4:
        lines.append(
            f"call depth: {_s165_max_depth} hops from {_s165_deepest_name}"
            f" — deep call chain increases change blast"
        )

    # S207: Single importer — exactly 1 file imports the blast target.
    # Narrow dependency means blast is contained; useful to know before refactoring.
    # Positive signal: suppressed when importers != 1 (too many = already shown in blast header).
    _s207_importers = [
        i for i in graph.importers_of(file_path)
        if i in graph.files and i != file_path
    ]
    if len(_s207_importers) == 1:
        _s207_name = _s207_importers[0].rsplit("/", 1)[-1]
        lines.append(
            f"single importer: only {_s207_name} imports this file"
            f" — narrow dependency, check that consumer before modifying"
        )

    # S217: Entry point blast — the blast target is an application entry point.
    # Entry points (main.py, app.py, index.js, cli.py) are widely invoked; changing them
    # affects startup, CLI behavior, or the entire request path.
    # Only shown when blast file stem matches known entry point names.
    _s217_entry_stems = {
        "main", "app", "index", "server", "cli", "run", "manage", "wsgi", "asgi", "__main__"
    }
    _s217_stem = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    if _s217_stem in _s217_entry_stems:
        lines.append(
            f"entry point blast: {file_path.rsplit('/', 1)[-1]} is an application entry point"
            f" — changes affect startup / CLI / full request path"
        )

    # S219: Call concentration — ≥75% of external CALLS callers reference the same exported symbol.
    # When callers overwhelmingly use one specific symbol, the real blast bottleneck is narrower
    # than the file-level view suggests. Helps agents focus review on that one symbol.
    # Only shown when 4+ distinct non-test external caller files exist and concentration >= 75%.
    _s219_exp_ids = {s.id for s in symbols if s.exported and s.kind.value in ("function", "method")}
    _s219_unique_callers = {
        loc.split(":")[0]
        for locs in external_callers.values()
        for loc in locs
        if not _is_test_file(loc.split(":")[0])
    }
    if len(_s219_unique_callers) >= 4 and _s219_exp_ids:
        _s219_counts: dict[str, int] = {}
        for _e219 in graph.edges:
            if _e219.kind is EdgeKind.CALLS and _e219.target_id in _s219_exp_ids:
                _fp219 = _e219.source_id.split("::")[0]
                if _fp219 != file_path and not _is_test_file(_fp219):
                    _s219_counts[_e219.target_id] = _s219_counts.get(_e219.target_id, 0) + 1
        if _s219_counts:
            _s219_total = sum(_s219_counts.values())
            _s219_top_id, _s219_top_n = max(_s219_counts.items(), key=lambda kv: kv[1])
            if _s219_total > 0 and _s219_top_n / _s219_total >= 0.75:
                _s219_name = next(
                    (s.name for s in symbols if s.id == _s219_top_id), _s219_top_id.split("::")[-1]
                )
                _s219_pct = int(_s219_top_n / _s219_total * 100)
                lines.append(
                    f"call concentration: {_s219_pct}% of callers use {_s219_name}"
                    f" — that symbol is the real blast bottleneck"
                )

    # S224: Test file blast — the blast target is a test file.
    # Test files typically have few or no production importers; changes are lower risk.
    # Positive signal: flagged as safe to refactor/delete without coordinating production code.
    if _is_test_file(file_path):
        lines.append(
            f"test file blast: {file_path.rsplit('/', 1)[-1]} is a test file"
            f" — no production code depends on this; safe to modify independently"
        )

    # S231: Package init blast — blast target is an __init__.py file.
    # __init__.py re-exports symbols for the entire package; all package importers are affected.
    # Only shown when blast file is an __init__.py.
    if file_path.rsplit("/", 1)[-1] in ("__init__.py", "__init__.ts", "__init__.js"):
        lines.append(
            f"package init blast: {file_path} is a package init"
            f" — all importers of the package are affected by changes here"
        )

    # S237: Internal-only file — blast target exports no symbols.
    # Files with no exports are internal implementation details; changes stay within the module.
    # Positive signal: safe to refactor without breaking callers' public interface expectations.
    if symbols and not any(s.exported for s in symbols):
        lines.append(
            f"internal-only file: {file_path.rsplit('/', 1)[-1]} has no exported symbols"
            f" — changes don't affect public interface"
        )

    # S246: Mixin/base class blast — blast target defines a class that other classes inherit from.
    # Mixin or base class changes cascade silently through ALL inheriting classes.
    # Only shown when 1+ class in the file is a base/mixin (other classes inherit from it).
    _s246_mixin_indicators = ("mixin", "base", "abstract", "interface")
    _s246_class_syms = [s for s in symbols if s.kind.value == "class"]
    _s246_mixin_classes = [
        s for s in _s246_class_syms
        if any(ind in s.name.lower() for ind in _s246_mixin_indicators)
    ]
    if not _s246_mixin_classes:
        # Also detect via INHERITS edges: if any other class inherits from classes in this file
        _s246_this_class_ids = {s.id for s in _s246_class_syms}
        _s246_subclasses: list[str] = []
        for _e246 in graph.edges:
            if (
                _e246.kind.value == "inherits"
                and _e246.target_id in _s246_this_class_ids
                and _e246.source_id.split("::")[0] != file_path
            ):
                _s246_subclasses.append(_e246.source_id.split("::")[-1])
        if len(_s246_subclasses) >= 2:
            _s246_mixin_classes = _s246_class_syms  # trigger signal
    if _s246_mixin_classes:
        _s246_names = [s.name for s in _s246_mixin_classes[:2]]
        _s246_str = ", ".join(_s246_names)
        # Count subclasses across the graph
        _s246_all_ids = {s.id for s in _s246_mixin_classes}
        _s246_n_subs = sum(
            1 for _e in graph.edges
            if _e.kind.value == "inherits" and _e.target_id in _s246_all_ids
            and _e.source_id.split("::")[0] != file_path
        )
        if _s246_n_subs >= 1:
            lines.append(
                f"mixin/base blast: {_s246_str} has {_s246_n_subs} subclass(es)"
                f" — changes cascade silently through all inheritors"
            )

    # S239: Async function blast — blast target contains async functions.
    # Callers must properly await async functions; any refactoring to sync breaks all call sites.
    # Only shown when 1+ async function exists in the blast target (Python/JS/TS).
    _s239_async_syms = [
        s for s in symbols
        if s.kind.value in ("function", "method")
        and s.signature
        and (s.signature.startswith("async def ") or s.signature.startswith("async "))
    ]
    if _s239_async_syms:
        _s239_names = [s.name for s in _s239_async_syms[:3]]
        _s239_str = ", ".join(_s239_names)
        if len(_s239_async_syms) > 3:
            _s239_str += f" +{len(_s239_async_syms) - 3} more"
        lines.append(
            f"async blast: {len(_s239_async_syms)} async fn(s) ({_s239_str})"
            f" — callers must await; sync conversion breaks all call sites"
        )

    # S261: Platform-specific blast — blast target file name indicates it's platform-specific.
    # Platform-specific code needs testing on that exact platform (CI may not cover it).
    # Only shown when file name contains a platform indicator.
    _s254_platform_markers = (
        "_windows", "_win32", "_win64", "_linux", "_darwin", "_macos", "_osx",
        "_posix", "_unix", "_freebsd", "_android", "_ios", "_arm", "_x86",
    )
    _s254_base254 = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    _s254_platform = next(
        (m.lstrip("_") for m in _s254_platform_markers if _s254_base254.endswith(m)),
        None
    )
    if _s254_platform:
        lines.append(
            f"platform-specific: {file_path.rsplit('/', 1)[-1]} targets {_s254_platform}"
            f" — test on that platform; CI may not cover it"
        )

    # S251: Well-tested blast — blast target's exported symbols have many test callers.
    # Positive signal: high test coverage means refactoring here has a safety net.
    # Only shown when 5+ distinct test files call into this blast target.
    _s251_test_caller_files: set[str] = set()
    for _sym251 in symbols:
        for _c251 in graph.callers_of(_sym251.id):
            if _is_test_file(_c251.file_path):
                _s251_test_caller_files.add(_c251.file_path)
    if len(_s251_test_caller_files) >= 5:
        lines.append(
            f"well-tested: {len(_s251_test_caller_files)} test file(s) cover this module"
            f" — high safety net; refactoring here is lower risk"
        )


    # S256: High fan-out blast — blast target imports from many other modules.
    # A file with wide dependencies is harder to refactor in isolation; changes
    # often require updates across all its upstream dependencies.
    _s256_imports = [
        e for e in graph.edges
        if e.kind.value == "imports" and e.source_id == file_path
    ]
    _s256_import_count = len({e.target_id for e in _s256_imports})
    if _s256_import_count >= 6:
        lines.append(
            f"high fan-out: imports from {_s256_import_count} modules"
            f" — wide dependency surface; refactoring this file requires many upstream checks"
        )


    # S263: Leaf module — blast target has no outgoing imports (fully self-contained).
    # Leaf modules are the easiest to extract, test, or replace in isolation.
    # Positive signal: shown when blast target has 3+ symbols but zero import edges out.
    if symbols:
        _s263_outgoing = [
            e for e in graph.edges
            if e.kind.value == "imports" and e.source_id == file_path
        ]
        if not _s263_outgoing and len(symbols) >= 3:
            lines.append(
                f"leaf module: no outgoing imports"
                f" — self-contained; safe to extract, mock, or replace in isolation"
            )


    # S269: Deep importer chain — a direct importer of this file is itself imported by many files.
    # This means changes here amplify: they affect the direct importer AND everything that
    # depends on that importer, creating a second-order blast radius.
    _s269_deep_importers = []
    for _imp269 in graph.importers_of(file_path):
        if _imp269 not in graph.files or _imp269 == file_path:
            continue
        _s269_second_order = len([
            f for f in graph.importers_of(_imp269)
            if f in graph.files and f != file_path and f != _imp269
        ])
        if _s269_second_order >= 5:
            _s269_deep_importers.append((_s269_second_order, _imp269.rsplit("/", 1)[-1]))
    if _s269_deep_importers:
        _s269_deep_importers.sort(reverse=True)
        _n269, _name269 = _s269_deep_importers[0]
        lines.append(
            f"deep importer chain: {_name269} imports this and is itself imported by {_n269} files"
            f" — second-order blast; changes propagate further than direct importers suggest"
        )


    # S278: Test infrastructure blast — blast target is a conftest or shared test fixture.
    # Changes to test infrastructure (conftest.py, fixtures.py, test_utils.py) affect
    # ALL tests that rely on those fixtures; blast is test-suite-wide.
    _s278_infra_names = {"conftest", "fixtures", "test_utils", "test_helpers", "test_base",
                         "testutils", "testhelpers", "factory", "factories", "fakes", "mocks"}
    _s278_stem = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    if _s278_stem in _s278_infra_names or _is_test_file(file_path):
        _s278_test_importers = [
            f for f in graph.importers_of(file_path)
            if f in graph.files and _is_test_file(f)
        ]
        if _s278_test_importers:
            lines.append(
                f"test infra blast: {len(_s278_test_importers)} test file(s) depend on this fixture"
                f" — changes here affect the entire test suite"
            )


    # S284: Cross-package blast — blast target is imported by files in 3+ top-level directories.
    # When a file's importers span many packages, a change creates a coordinated multi-team
    # blast; each package team must review and validate the impact.
    _s284_importer_dirs: set[str] = set()
    for _imp284 in graph.importers_of(file_path):
        if _imp284 in graph.files and _imp284 != file_path:
            _dir284 = _imp284.split("/")[0] if "/" in _imp284 else "."
            if not _dir284.startswith("."):
                _s284_importer_dirs.add(_dir284)
    if len(_s284_importer_dirs) >= 3:
        _dirs284 = sorted(_s284_importer_dirs)[:3]
        _dir_str284 = ", ".join(_dirs284)
        if len(_s284_importer_dirs) > 3:
            _dir_str284 += f" +{len(_s284_importer_dirs) - 3} more"
        lines.append(
            f"cross-package blast: imported by {len(_s284_importer_dirs)} packages ({_dir_str284})"
            f" — multi-team impact; coordinate changes across all consuming packages"
        )


    # S290: No importers — blast target has exported symbols but nothing imports it.
    # A non-test file with exported symbols and zero importers may be dead code,
    # a new module not yet wired in, or an external entry point (tested separately).
    if symbols:
        _s290_importers = [
            f for f in graph.importers_of(file_path)
            if f in graph.files and f != file_path
        ]
        _s290_exported = [s for s in symbols if s.exported]
        if not _s290_importers and _s290_exported and not _is_test_file(file_path):
            lines.append(
                f"no importers: {len(_s290_exported)} exported symbol(s) but nothing imports this file"
                f" — may be dead code, an unwired module, or a standalone entry point"
            )


    # S296: Generated code blast — blast target's path suggests it is auto-generated.
    # Generated files should not be edited directly; changes must be made in the
    # generator or template, then re-generated.
    _s296_gen_indicators = (
        "_generated", ".generated.", "_pb2", "_pb2_grpc", ".pb.", "_gen.",
        "/generated/", "/gen/", "/__generated__/", "/_generated/",
    )
    _s296_gen_stems = {"generated", "gen", "auto_generated", "autogenerated"}
    _fp296_lower = file_path.lower().replace("\\", "/")
    _stem296 = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    _is_generated296 = (
        any(ind in _fp296_lower for ind in _s296_gen_indicators)
        or _stem296 in _s296_gen_stems
    )
    if _is_generated296:
        lines.append(
            f"generated file: {file_path.rsplit('/', 1)[-1]} appears to be auto-generated"
            f" — edit the generator/template, not this file directly"
        )

    # S301: Large API surface — target file exports 15+ distinct symbols.
    # Files with large symbol counts have wide blast radii even without explicit importers;
    # any rename, signature change, or removal can break many unknown consumers.
    _s301_exported = [
        s for s in graph.symbols.values()
        if s.file_path == file_path and s.exported
        and s.kind.value in ("function", "method", "class", "variable", "constant")
    ]
    if len(_s301_exported) >= 15:
        lines.append(
            f"large API surface: {len(_s301_exported)} exported symbols"
            f" — wide blast radius; renaming or removing any symbol breaks unknown callers"
        )

    # S307: Routing-layer blast — target is imported by files in routes/controllers/views dirs.
    # Route files are the entry-surface of the application; changes to imported utilities
    # may affect request handling, authentication, or serialization globally.
    _s307_route_dirs = ("routes", "controllers", "views", "handlers", "endpoints", "api")
    _s307_route_importers: list[str] = []
    for _imp307 in importers:
        _parts307 = _imp307.lower().replace("\\", "/").split("/")
        if any(p in _s307_route_dirs for p in _parts307):
            _s307_route_importers.append(_imp307)
    if len(_s307_route_importers) >= 2:
        _ri_names307 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s307_route_importers[:2])
        lines.append(
            f"routing-layer blast: imported by {len(_s307_route_importers)} route/controller file(s)"
            f" ({_ri_names307}) — changes here affect request handling directly"
        )

    # S311: Deprecated API blast — target file name or path contains deprecated/legacy/compat.
    # Deprecated code is often kept alive by undocumented callers; changes break them silently
    # because consumers assume the API is stable despite the "deprecated" label.
    _s311_dep_words = ("deprecated", "legacy", "compat", "obsolete", "old_", "_old", "v1_", "_v1")
    _fp311_lower = file_path.lower().replace("\\", "/")
    _is_deprecated311 = any(w in _fp311_lower for w in _s311_dep_words)
    if _is_deprecated311 and importers:
        lines.append(
            f"deprecated API: {file_path.rsplit('/', 1)[-1]} is marked deprecated"
            f" but still imported by {len(importers)} file(s)"
            f" — callers may not know it's deprecated; add migration notes"
        )

    # S317: Core utility blast — target is in utils/helpers/common AND imported by 5+ files.
    # Utility modules accumulate dependencies over time; changes often have wider impact
    # than expected because every caller silently depends on subtle behavior.
    _s317_util_dirs = ("utils", "helpers", "common", "shared", "lib", "core", "base")
    _s317_is_util = any(
        part in _s317_util_dirs
        for part in file_path.lower().replace("\\", "/").split("/")[:-1]
    ) or file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in _s317_util_dirs
    if _s317_is_util and len(importers) >= 5:
        lines.append(
            f"core utility blast: {file_path.rsplit('/', 1)[-1]} is a shared utility"
            f" imported by {len(importers)} files — utility changes ripple everywhere; test thoroughly"
        )

    # S323: Shared config blast — target is a settings/config file imported by 5+ files.
    # Config files hold global state (feature flags, limits, keys); changing a default
    # value silently affects all paths that use that config.
    _s323_cfg_names = {"settings", "config", "configuration", "constants", "env",
                       "defaults", "params", "options", "app_config"}
    _s323_stem = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    _is_config323 = (
        _s323_stem in _s323_cfg_names
        or _s323_stem.startswith("settings_")
        or _s323_stem.endswith("_config")
        or _s323_stem.endswith("_settings")
    )
    if _is_config323 and len(importers) >= 5:
        lines.append(
            f"config blast: {file_path.rsplit('/', 1)[-1]} is a config/settings file"
            f" imported by {len(importers)} files — default value changes affect all consumers silently"
        )

    # S325: Dual-purpose module — blast target contains both library symbols AND an entry point.
    # Files that mix library code with `if __name__ == '__main__'` patterns have implicit
    # coupling between the script and the library; changes to either half affect both uses.
    _s325_all_syms = list(graph.symbols.values())
    _s325_file_syms = [s for s in _s325_all_syms if s.file_path == file_path]
    _s325_has_lib = any(
        s.kind.value in ("function", "class") and s.exported for s in _s325_file_syms
    )
    _s325_has_main = any(
        s.name in ("main", "__main__") or "if __name__" in (s.signature or "")
        for s in _s325_file_syms
    )
    if _s325_has_lib and _s325_has_main and importers:
        lines.append(
            f"dual-purpose module: {file_path.rsplit('/', 1)[-1]} mixes library and script code"
            f" — changes to either side affect both library callers and script behavior"
        )

    # S331: Schema blast — target defines ORM/schema models imported widely.
    # Schema changes affect all serialization, validation, migration, and API layers
    # simultaneously; a single field rename can break dozens of consumers.
    _s331_schema_words = ("schema", "model", "entity", "orm", "record", "table", "migration")
    _fp331 = file_path.lower().replace("\\", "/")
    _is_schema331 = any(w in _fp331 for w in _s331_schema_words)
    if not _is_schema331:
        _stem331 = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        _is_schema331 = any(w in _stem331 for w in _s331_schema_words)
    if _is_schema331 and len(importers) >= 4:
        lines.append(
            f"schema blast: {file_path.rsplit('/', 1)[-1]} appears to be a schema/model file"
            f" imported by {len(importers)} files"
            f" — field/type changes propagate to serializers, validators, and API layers"
        )

    # S337: Version file blast — target defines __version__ and is imported by multiple files.
    # Version strings are often used for runtime compatibility checks; changing the value
    # may silently break callers that compare versions programmatically.
    _s337_file_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    _s337_has_version = any(
        s.name in ("__version__", "VERSION", "version", "APP_VERSION", "__version_info__")
        for s in _s337_file_syms
    )
    if _s337_has_version and len(importers) >= 3:
        lines.append(
            f"version file blast: {file_path.rsplit('/', 1)[-1]} exports version info"
            f" imported by {len(importers)} files"
            f" — version string change may break runtime compatibility checks"
        )

    # S343: Conftest blast — target is a conftest.py or shared test fixture file.
    # conftest.py defines fixtures shared by all tests in the directory subtree;
    # changes to it can break test isolation and cause cascading test failures.
    _fp343 = file_path.rsplit("/", 1)[-1].lower()
    _is_conftest343 = _fp343 in ("conftest.py", "fixtures.py", "test_helpers.py", "test_utils.py")
    if _is_conftest343 and importers:
        lines.append(
            f"conftest blast: {file_path.rsplit('/', 1)[-1]} is a shared test fixture"
            f" — changes here cascade through all dependent tests; run full test suite"
        )

    # S377: Protocol/interface blast — blast target defines abstract interfaces or Protocol classes.
    # Protocol/ABC files define contracts that many concrete classes implement;
    # changes to the interface propagate to every implementor, not just direct importers.
    _s377_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    _s377_abstract_names = (
        "protocol", "interface", "abstract", "abc", "base_class", "base_model",
        "iservice", "irepository", "ihandler",
    )
    _s377_fp_lower = file_path.lower()
    _s377_is_protocol = (
        any(p in _s377_fp_lower for p in _s377_abstract_names)
        or any(
            s.name.lower().startswith(p)
            for s in _s377_syms
            for p in ("Base", "Abstract", "Protocol", "Interface", "I")
            if len(s.name) > 2
        )
    )
    if _s377_is_protocol and importers:
        lines.append(
            f"protocol blast: {file_path.rsplit('/', 1)[-1]} defines abstract interface(s)"
            f" — every implementor is affected; check all concrete subclasses for breaking changes"
        )

    # S371: Utility belt blast — blast target has 15+ exported symbols each imported by different files.
    # A utility belt file is essentially an undifferentiated bag of helpers; it's hard to know
    # which helpers are truly safe to modify without understanding all consumer patterns.
    _s371_file_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    if len(_s371_file_syms) >= 15 and len(importers) >= 5:
        # Check that multiple importers each use different symbols (breadth of usage)
        _s371_sym_ids = {s.id for s in _s371_file_syms}
        _s371_importer_syms: dict[str, set[str]] = {}
        for _e371 in graph.edges:
            if (_e371.kind.value == "calls"
                    and _e371.source_id in {
                        s.id for s in graph.symbols.values()
                        if not _is_test_file(s.file_path)
                    }
                    and _e371.target_id in _s371_sym_ids):
                _src_file371 = next(
                    (s.file_path for s in graph.symbols.values() if s.id == _e371.source_id),
                    None
                )
                if _src_file371 and _src_file371 != file_path:
                    _s371_importer_syms.setdefault(_src_file371, set()).add(_e371.target_id)
        if len(_s371_importer_syms) >= 4:
            lines.append(
                f"utility belt: {len(_s371_file_syms)} symbols in {file_path.rsplit('/', 1)[-1]}"
                f" used across {len(_s371_importer_syms)} files"
                f" — undifferentiated helper bag; extract domain-specific modules to reduce blast radius"
            )

    # S365: Middleware blast — blast target is a middleware/interceptor/decorator file.
    # Middleware wraps every request or operation in the stack; changes to it affect all
    # code paths simultaneously, including ones that have no tests exercising that path.
    _fp365 = file_path.lower()
    _mw_patterns365 = (
        "middleware", "interceptor", "decorator", "wrapper", "filter",
        "hook", "plugin", "mixin",
    )
    _is_middleware365 = any(p in _fp365 for p in _mw_patterns365) and not _is_test_file(file_path)
    if _is_middleware365 and importers:
        lines.append(
            f"middleware blast: {file_path.rsplit('/', 1)[-1]} is a middleware/interceptor"
            f" used by {len(importers)} files"
            f" — wraps all code paths; changes affect every caller simultaneously"
        )

    # S359: Entrypoint blast — blast target is the application's main entry point.
    # Entrypoints wire together all subsystems; changes to them affect startup behavior,
    # argument parsing, and initialization order — subtle bugs that are hard to catch in unit tests.
    _fp359 = file_path.rsplit("/", 1)[-1].lower()
    _is_entry359 = _fp359 in (
        "__main__.py", "main.py", "index.js", "index.ts", "app.py", "server.py",
        "manage.py", "wsgi.py", "asgi.py", "entrypoint.py",
    )
    if _is_entry359 and importers:
        lines.append(
            f"entrypoint blast: {file_path.rsplit('/', 1)[-1]} is the application entrypoint"
            f" — startup/init order changes are not unit-testable; test in integration or staging"
        )

    # S353: Constants blast — blast target is a purely constants/enums file.
    # Pure constants files are deceptively "safe" to edit; downstream consumers may depend
    # on specific values via hardcoded literals, making renames or reorders silently breaking.
    _s353_file_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    _s353_const_kinds = {"variable", "constant"}
    _s353_fn_kinds = {"function", "method", "class"}
    _s353_all_const = _s353_file_syms and all(
        s.kind.value in _s353_const_kinds or s.name.isupper()
        for s in _s353_file_syms
    )
    _s353_no_fns = not any(s.kind.value in _s353_fn_kinds for s in _s353_file_syms)
    if _s353_all_const and _s353_no_fns and len(importers) >= 3:
        lines.append(
            f"constants blast: {file_path.rsplit('/', 1)[-1]} is a constants/enums file"
            f" imported by {len(importers)} files"
            f" — value/rename changes may silently break consumers that hardcode expected values"
        )

    # S395: Type definition blast — blast target defines TS interfaces/types/enums.
    # TypeScript type files are imported by many consumers; changes to types cascade
    # through compilation and may cause type errors across many downstream files.
    _fp395 = file_path.lower()
    _is_type_file395 = (
        (_fp395.endswith(".d.ts") or "types" in _fp395 or "interfaces" in _fp395
         or "enums" in _fp395 or "typings" in _fp395)
        and not _is_test_file(file_path)
    )
    if _is_type_file395 and len(importers) >= 3:
        lines.append(
            f"type definition blast: {file_path.rsplit('/', 1)[-1]} defines shared TypeScript types"
            f" imported by {len(importers)} files"
            f" — type changes cascade through compilation; check all consumers for type errors"
        )

    # S389: Database model blast — blast target is a DB model/schema file.
    # Database model files define the data contract between application and database;
    # schema changes require migrations and may break queries throughout the codebase.
    _fp389 = file_path.rsplit("/", 1)[-1].lower()
    _dir389 = file_path.lower()
    _db_patterns389 = (
        "model", "schema", "entity", "table", "migration", "orm",
        "dao", "repository", "activerecord",
    )
    _is_db389 = (
        any(p in _fp389 for p in _db_patterns389)
        and not _is_test_file(file_path)
        and not any(
            skip in _fp389 for skip in ("view", "controller", "route", "api")
        )
    )
    if _is_db389 and importers:
        lines.append(
            f"DB model blast: {file_path.rsplit('/', 1)[-1]} defines a data model"
            f" imported by {len(importers)} files"
            f" — schema changes require database migrations; review all ORM queries in consumers"
        )

    # S383: Test fixture blast — blast target is a shared test fixture/factory file.
    # Test fixture files are imported by many tests; changes break test isolation and
    # can cause cascading failures unrelated to the code being tested.
    _fp383 = file_path.rsplit("/", 1)[-1].lower()
    _is_fixture383 = _fp383 in ("conftest.py", "fixtures.py", "factories.py", "test_helpers.py", "test_utils.py")
    _test_importers383 = [fp for fp in graph.importers_of(file_path) if _is_test_file(fp)]
    if _is_fixture383 and len(_test_importers383) >= 3:
        lines.append(
            f"test fixture blast: {file_path.rsplit('/', 1)[-1]} is a shared test fixture"
            f" imported by {len(_test_importers383)} test files"
            f" — fixture changes break test isolation; run full test suite after any modification"
        )

    # S401: Shared secrets blast — blast target file name or symbols suggest it holds credentials.
    # Files containing API keys, secrets, or tokens that are imported widely are high-risk;
    # a single accidental log statement or serialization call can leak credentials.
    _secret_words401 = (
        "secret", "credential", "api_key", "apikey", "token", "password",
        "passwd", "auth_key", "private_key", "access_key",
    )
    _fp401_lower = file_path.lower().replace("-", "_")
    _file_is_secret401 = any(w in _fp401_lower for w in _secret_words401)
    _sym_has_secret401 = any(
        any(w in s.name.lower() for w in _secret_words401)
        for s in graph.symbols.values()
        if s.file_path == file_path
    )
    _total_importers401 = len(importers)
    if (_file_is_secret401 or _sym_has_secret401) and _total_importers401 >= 3:
        lines.append(
            f"secrets blast: {file_path.rsplit('/', 1)[-1]} contains credentials/tokens"
            f" and is imported by {_total_importers401} file(s)"
            f" — wide credential sharing increases leak surface; scope access via DI or env-only"
        )

    # S407: Init-file blast — blast target is an __init__.py that re-exports many symbols.
    # Changing an __init__.py affects every importer of the package; even renaming a re-export
    # can silently break downstream importers that rely on the package-level name.
    _fp407 = file_path.rsplit("/", 1)[-1].lower()
    _is_init407 = _fp407 in ("__init__.py", "index.js", "index.ts", "index.tsx")
    _init_import_edges407 = [
        e for e in graph.edges
        if e.kind.value == "imports" and e.source_id == file_path
    ]
    _init_dir407 = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
    _init_package_size407 = sum(
        1 for fp in graph.files
        if (fp.rsplit("/", 1)[0] if "/" in fp else "") == _init_dir407
        and fp != file_path
    )
    if _is_init407 and len(_init_import_edges407) >= 5 and _init_package_size407 >= 3:
        lines.append(
            f"init-file blast: {_fp407} re-exports from {len(_init_import_edges407)} module(s)"
            f" in a package of {_init_package_size407} file(s)"
            f" — renaming any re-export silently breaks all downstream importers"
        )

    # S413: Symbol-dense blast — blast target file has 30+ symbols (classes, functions, constants).
    # A file with many symbols is a de facto utility hub; each symbol is a potential blast
    # propagation point, and the full impact of any change is proportional to total symbol count.
    _s413_all_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    if len(_s413_all_syms) >= 30:
        _kinds413 = {}
        for s in _s413_all_syms:
            _kinds413[s.kind.value] = _kinds413.get(s.kind.value, 0) + 1
        _top_kind413 = max(_kinds413, key=lambda k: _kinds413[k])
        lines.append(
            f"symbol-dense: {file_path.rsplit('/', 1)[-1]} defines {len(_s413_all_syms)} symbols"
            f" ({_kinds413[_top_kind413]} {_top_kind413}s)"
            f" — each symbol is a blast propagation point; refactor to split by concern"
        )

    # S419: Type stub blast — blast target is a type stub file (.pyi).
    # Type stub files define the public API contract; changing a stub silently breaks
    # type checks in all importers even when the runtime code hasn't changed.
    if file_path.lower().endswith(".pyi"):
        _stub_importers419 = len(importers)
        lines.append(
            f"type stub blast: {file_path.rsplit('/', 1)[-1]} is a type stub"
            f" imported/used by {_stub_importers419} file(s)"
            f" — stub changes break type checks without runtime errors; update callers together"
        )

    # S425: Constants-file blast — blast target is a file containing only constants.
    # Constants files are deceptively high-impact; they're imported everywhere but rarely
    # changed, so developers underestimate how many files will recompile or reload.
    _s425_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    _s425_is_const_file = (
        len(_s425_syms) >= 3
        and all(s.kind.value in ("variable", "constant") for s in _s425_syms)
    )
    if _s425_is_const_file and len(importers) >= 3:
        lines.append(
            f"constants-file blast: {file_path.rsplit('/', 1)[-1]} defines"
            f" {len(_s425_syms)} constant(s) imported by {len(importers)} file(s)"
            f" — constants files are silently high-impact; all importers pick up changes immediately"
        )

    # S431: Event emitter blast — blast target file contains event emit/dispatch functions.
    # Event emitters have hidden blast radius — subscribers don't show up as direct importers,
    # so all the subscribers are affected but invisible in a normal dependency trace.
    _s431_event_patterns = (
        "emit_", "dispatch_", "publish_", "fire_event_",
        "trigger_", "broadcast_", "send_event_",
    )
    _s431_event_syms = [
        s for s in graph.symbols.values()
        if s.file_path == file_path
        and s.kind.value in ("function", "method")
        and any(s.name.lower().startswith(p) for p in _s431_event_patterns)
    ]
    if len(_s431_event_syms) >= 2:
        _ev_names431 = ", ".join(s.name for s in _s431_event_syms[:3])
        lines.append(
            f"event emitter blast: {file_path.rsplit('/', 1)[-1]} has {len(_s431_event_syms)}"
            f" event dispatch fn(s) ({_ev_names431})"
            f" — subscribers are invisible in dependency trace; grep for event names to find all consumers"
        )

    # S437: Circular import risk — blast target has a mutual import relationship.
    # File A imports from B while B imports from A creates a circular dependency;
    # any change can trigger import errors at runtime (especially in Python) and
    # makes refactoring extremely fragile since both files must change together.
    _s437_outbound_files = {
        e.target_id for e in graph.edges
        if e.kind.value == "imports" and e.source_id == file_path
        and e.target_id != file_path
    }
    _s437_circular = [fp for fp in _s437_outbound_files if file_path in graph.importers_of(fp)]
    if _s437_circular:
        _circ_name437 = _s437_circular[0].rsplit("/", 1)[-1]
        lines.append(
            f"circular import risk: {file_path.rsplit('/', 1)[-1]} ↔ {_circ_name437}"
            f" have mutual imports"
            f" — any change in either file can trigger import errors; refactor to break the cycle"
        )

    if not importers and not external_callers and not render_targets:
        lines.append("No external dependencies found — safe to modify in isolation.")

    # S443: Public API file — blast target exports functions/classes used across 5+ files.
    # Files that form the public API of a module have implicit contracts with all consumers;
    # removing or renaming any export is a breaking change even if internal callers look fine.
    _s443_exported = [s for s in symbols if s.exported]
    _s443_consumer_files: set[str] = set()
    for _sym443 in _s443_exported:
        for _e443 in graph.edges:
            if _e443.kind.value == "calls" and _e443.target_id == _sym443.id:
                _caller443 = graph.symbols.get(_e443.source_id)
                if _caller443 and _caller443.file_path != file_path:
                    _s443_consumer_files.add(_caller443.file_path)
    if len(_s443_consumer_files) >= 5:
        lines.append(
            f"public API file: {len(_s443_exported)} exported symbol(s)"
            f" consumed by {len(_s443_consumer_files)} files"
            f" — any signature change is a breaking change; version or deprecate before removing"
        )

    # S449: Multi-package blast — blast target is imported across 3+ distinct package directories.
    # When a file is used across many packages, it is an implicit shared library; any
    # incompatible change requires synchronized updates in every depending package.
    _s449_importer_dirs: set[str] = set()
    for _imp449 in importers:
        _dir449 = _imp449.rsplit("/", 1)[0] if "/" in _imp449 else ""
        _s449_importer_dirs.add(_dir449)
    if len(_s449_importer_dirs) >= 3:
        lines.append(
            f"multi-package blast: imported from {len(_s449_importer_dirs)} distinct directories"
            f" — treat as shared library; incompatible changes require coordinated updates across all packages"
        )

    # S453: Middleware blast — blast target is a middleware or interceptor component.
    # Middleware sits in the request/response pipeline and processes every call;
    # any bug or performance regression has a global impact, not just one feature.
    _s453_mw_keywords = ("middleware", "interceptor", "filter", "pipeline", "decorator", "hook")
    _s453_mw_name = file_path.lower().replace("\\", "/")
    _s453_is_mw = any(kw in _s453_mw_name for kw in _s453_mw_keywords)
    if _s453_is_mw and importers:
        lines.append(
            f"middleware blast: {file_path.rsplit('/', 1)[-1]} is a middleware/interceptor"
            f" used by {len(importers)} file(s)"
            f" — bugs or performance regressions affect every request through this pipeline"
        )

    # S459: Core utility blast — blast target has 20+ exported functions used across 5+ files.
    # A file acting as a utility hub is a de-facto shared library; any breaking change
    # (renaming a function, changing a return type) ripples through every consumer.
    _s459_exported_fns = [
        s for s in graph.symbols.values()
        if s.file_path == file_path
        and s.kind.value in ("function", "method")
        and s.exported
    ]
    if len(_s459_exported_fns) >= 20 and len(importers) >= 5:
        lines.append(
            f"utility hub blast: {file_path.rsplit('/', 1)[-1]} exports {len(_s459_exported_fns)}"
            f" functions used by {len(importers)} files"
            f" — treat as a shared library; any breaking change needs broad coordination"
        )

    # S467: Test-only importer — blast target is only imported from test files.
    # If a file is only imported from tests, it may be a test helper or dead production code;
    # deleting it only breaks tests, but its presence suggests it was intended for production use.
    _s467_all_importers = graph.importers_of(file_path)
    _s467_non_test = [fp for fp in _s467_all_importers if not _is_test_file(fp)]
    _s467_test = [fp for fp in _s467_all_importers if _is_test_file(fp)]
    if _s467_test and not _s467_non_test and not _is_test_file(file_path):
        lines.append(
            f"test-only importer: {file_path.rsplit('/', 1)[-1]} is only imported from test files"
            f" ({len(_s467_test)} test file(s))"
            f" — may be an unused helper; verify before keeping or deleting"
        )

    # S473: Constants-only blast — blast target contains only constant/variable definitions.
    # Pure constants files are the broadest implicit dependency: every consumer
    # is silently coupled to every constant's value, name, and type simultaneously.
    if symbols:
        _s473_non_const = [
            s for s in symbols
            if s.kind.value not in ("variable", "constant", "property")
        ]
        if not _s473_non_const and len(symbols) >= 5 and importers:
            lines.append(
                f"constants-only blast: {file_path.rsplit('/', 1)[-1]} contains only {len(symbols)} constant/variable definitions"
                f" imported by {len(importers)} file(s)"
                f" — renaming or removing any constant silently breaks all consumers"
            )

    # S479: Bridge file blast — blast target imports from one component and is imported by another.
    # Bridge files couple two otherwise-independent modules; removing or splitting them
    # breaks both sides simultaneously, requiring coordinated changes across the boundary.
    _s479_outbound_dirs: set[str] = set()
    for _e479 in graph.edges:
        if _e479.kind.value == "imports" and _e479.source_id == file_path:
            _dir479 = _e479.target_id.rsplit("/", 1)[0] if "/" in _e479.target_id else ""
            if _dir479 and _dir479 != (file_path.rsplit("/", 1)[0] if "/" in file_path else ""):
                _s479_outbound_dirs.add(_dir479)
    _s479_inbound_dirs: set[str] = set()
    for _imp479 in importers:
        _dir479 = _imp479.rsplit("/", 1)[0] if "/" in _imp479 else ""
        if _dir479 and _dir479 != (file_path.rsplit("/", 1)[0] if "/" in file_path else ""):
            _s479_inbound_dirs.add(_dir479)
    if _s479_outbound_dirs and _s479_inbound_dirs and not _s479_outbound_dirs & _s479_inbound_dirs:
        lines.append(
            f"bridge file: {file_path.rsplit('/', 1)[-1]} connects"
            f" {len(_s479_outbound_dirs)} outbound module(s) ↔ {len(_s479_inbound_dirs)} inbound module(s)"
            f" — removing it requires coordinated changes on both sides of the boundary"
        )

    # S484: Data model blast — blast target defines dataclasses, NamedTuples, or TypedDicts.
    # Data model files are imported everywhere for type annotations and destructuring;
    # renaming a field or changing its type breaks all downstream consumers silently.
    _s484_model_markers = ("dataclass", "NamedTuple", "TypedDict", "dataclasses")
    _s484_file_imports = next(
        (fi.imports for fp, fi in graph.files.items() if fp == file_path), []
    ) or []
    _s484_is_model = any(
        any(m in imp for m in _s484_model_markers) for imp in _s484_file_imports
    )
    if _s484_is_model and importers:
        lines.append(
            f"data model blast: {file_path.rsplit('/', 1)[-1]} defines typed data models"
            f" imported by {len(importers)} file(s)"
            f" — field renames or type changes silently break all consumers; update together"
        )

    # S490: High-complexity blast target — the blast file contains a function with cx ≥ 15.
    # Highly complex functions are harder to modify safely; changes carry higher regression risk
    # than in simple files, and ripple effects are harder to reason about.
    _s490_syms = [s for s in graph.symbols.values() if s.file_path == file_path]
    _s490_complex = [
        s for s in _s490_syms
        if s.kind.value in ("function", "method")
        and s.complexity is not None
        and s.complexity >= 15
    ]
    if _s490_complex:
        _top490 = max(_s490_complex, key=lambda s: s.complexity or 0)
        lines.append(
            f"high complexity: {_top490.name} has cyclomatic complexity {_top490.complexity}"
            f" — complex functions carry higher regression risk; add tests before modifying"
        )

    # S496: Package init blast — blast target is a package __init__.py.
    # Init files define the public interface of a package; any change to imports or re-exports
    # silently breaks every consumer that relies on the package's public API.
    _basename496 = file_path.rsplit("/", 1)[-1]
    if _basename496 == "__init__.py" and importers:
        lines.append(
            f"package init blast: {file_path} is a package interface"
            f" used by {len(importers)} file(s)"
            f" — changes to __init__.py re-exports propagate to all consumers; review every import"
        )

    # S504: Leaf file blast — blast target imports from others but nothing imports it.
    # Leaf files are the farthest point in the dependency chain; their changes cannot
    # propagate via imports, but any change still requires local testing of all symbols.
    _s504_outbound = {
        e.target_id for e in graph.edges
        if e.kind.value == "imports" and e.source_id == file_path and e.target_id != file_path
    }
    if _s504_outbound and not importers:
        lines.append(
            f"leaf file blast: {file_path.rsplit('/', 1)[-1]} imports {len(_s504_outbound)} file(s) but has no importers"
            f" — blast radius is fully contained; changes are safe from propagation but test locally"
        )

    # S511: Single-consumer blast — blast target is only imported by exactly one other file.
    # Files with a single consumer are easier to refactor (only one caller to update),
    # but they often encode a tight coupling that prevents reuse across the codebase.
    if len(importers) == 1:
        _single511 = next(iter(importers))
        lines.append(
            f"single consumer: {file_path.rsplit('/', 1)[-1]} is only imported by {_single511.rsplit('/', 1)[-1]}"
            f" — tightly coupled to one consumer; safe to change in sync, but consider if the coupling is intentional"
        )

    # S533: Type stub paired — blast target has a corresponding .pyi stub file.
    # .pyi stubs are the static type contract for the module; changing signatures without
    # updating the stub causes type checker failures even when tests pass.
    if graph.root and file_path.endswith(".py"):
        from pathlib import Path as _Path533  # noqa: PLC0415
        _stub533 = _Path533(graph.root) / file_path.replace(".py", ".pyi")
        if _stub533.exists():
            lines.append(
                f"type stub paired: {file_path.rsplit('/', 1)[-1]} has a .pyi stub"
                f" — sync the stub after any signature change or type checking will fail"
            )

    # S527: Wide export surface — blast target exports 20+ symbols (large public API).
    # Files with many exports have a proportionally large blast radius for signature changes;
    # any one of the exports may be breaking, so each change needs a broader callers audit.
    if fi:
        _s527_exported = [
            sid for sid in fi.symbols
            if sid in graph.symbols and graph.symbols[sid].exported
        ]
        if len(_s527_exported) >= 20:
            lines.append(
                f"wide export surface: {file_path.rsplit('/', 1)[-1]} exports {len(_s527_exported)} symbols"
                f" — large public API; use blast --query <symbol> to target specific symbol blast radius"
            )

    # S521: Cross-package blast — blast target's importers span 3+ distinct top-level packages.
    # When a shared file is imported from many packages, a breaking change forces coordinated
    # updates across team/ownership boundaries — the coordination cost scales with package count.
    if importers:
        _s521_src_importers = [imp for imp in importers if not _is_test_file(imp)]
        _s521_top_dirs = {imp.replace("\\", "/").split("/")[0] for imp in _s521_src_importers}
        if len(_s521_top_dirs) >= 3:
            lines.append(
                f"cross-package blast: {file_path.rsplit('/', 1)[-1]} is imported from"
                f" {len(_s521_top_dirs)} distinct packages ({', '.join(sorted(_s521_top_dirs)[:3])})"
                f" — changes require coordinated updates across ownership boundaries"
            )

    # S515: Config file blast — blast target is a configuration or settings file.
    # Config files propagate to all consumers at runtime even when not statically imported;
    # constants, feature flags, and env-driven values silently affect all code paths on reload.
    _s515_config_markers = ("config", "settings", "constants", "conf", "env")
    _s515_is_config = any(m in file_path.lower() for m in _s515_config_markers)
    if _s515_is_config:
        lines.append(
            f"config file blast: {file_path.rsplit('/', 1)[-1]} is a config/settings file"
            f" — runtime values propagate everywhere; verify all consumers handle changed defaults"
        )

    # S539: Circular import blast — blast target is part of a circular import cycle.
    # Modifying a file involved in a circular import can break initialization order at runtime;
    # Python partially evaluates circular imports so attribute access order during init matters.
    _s539_fp_norm = file_path.replace("\\", "/")
    _s539_outbound = {
        e.target_id.replace("\\", "/") for e in graph.edges
        if e.kind.value == "imports"
        and e.source_id.replace("\\", "/") == _s539_fp_norm
        and e.target_id.replace("\\", "/") != _s539_fp_norm
    }
    _s539_cycle_partners = [
        dep for dep in _s539_outbound
        if _s539_fp_norm in {
            e.target_id.replace("\\", "/") for e in graph.edges
            if e.kind.value == "imports" and e.source_id.replace("\\", "/") == dep
        }
    ]
    if _s539_cycle_partners:
        _cycle_name539 = _s539_cycle_partners[0].rsplit("/", 1)[-1]
        lines.append(
            f"circular import blast: {file_path.rsplit('/', 1)[-1]} is in a circular import with {_cycle_name539}"
            f" — changes affect init order; test module-level attribute access after any modification"
        )

    # S548: Module init blast — blast target is a package __init__.py file.
    # __init__.py defines the public API of the entire package; changes cascade to every
    # consumer of the package, not just direct importers of this file.
    _fp548 = file_path.replace("\\", "/")
    if _fp548.endswith("__init__.py") or _fp548 == "__init__.py":
        lines.append(
            f"module init: {file_path} is a package __init__ — changes cascade to all consumers of this package"
            f"; re-exports in __init__ silently affect downstream importers"
        )

    # S554: Entry point blast — blast target is a recognized application entry point.
    # Entry point files (main.py, app.py, server.py, cli.py) are called first at startup;
    # bugs introduced here abort the entire process before any other code runs.
    _basename554 = file_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    _entry_names554 = frozenset(("main.py", "app.py", "server.py", "cli.py", "run.py", "wsgi.py", "asgi.py"))
    if _basename554 in _entry_names554:
        lines.append(
            f"entry point: {file_path} is an application entry point"
            f" — bugs here abort startup; validate initialization order carefully"
        )

    # S560: Test-only blast — all direct importers of the blast file are test files.
    # Production code never imports this file; it exists solely to support tests
    # or was production code that regressed to test-only usage — consider deleting.
    _importer_files560 = graph.importers_of(file_path)
    if _importer_files560 and all(_is_test_file(fp) for fp in _importer_files560):
        lines.append(
            f"test-only blast: {file_path.rsplit('/', 1)[-1]} is only imported by test files"
            f" — production code never uses this; verify it's not dead production code"
        )

    # S566: Utility module blast — blast target is a utils/helpers file with 5+ importers.
    # Utility modules with high import counts are hidden load-bearing files; any change
    # ripples to all consumers even though utilities appear to be "low-level" code.
    _s566_fp_lower = file_path.lower().replace("\\", "/")
    _s566_util_markers = ("utils", "helpers", "util", "helper", "common", "shared", "misc")
    _s566_is_util = any(m in _s566_fp_lower for m in _s566_util_markers)
    if _s566_is_util and len(importers) >= 5:
        lines.append(
            f"utility module blast: {file_path.rsplit('/', 1)[-1]} is a utility file with {len(importers)} importers"
            f" — seemingly low-level but high impact; utility changes break many consumers at once"
        )

    # S572: Large file blast — blast target has 300+ lines (high-density change surface).
    # Large files concentrate many symbol definitions; any change competes with many
    # co-located symbols for test coverage and reviewer attention.
    if file_path in graph.files:
        _fi572 = graph.files[file_path]
        if _fi572.line_count >= 300:
            lines.append(
                f"large file blast: {file_path.rsplit('/', 1)[-1]} has {_fi572.line_count} lines"
                f" — high-density change surface; changes compete for reviewer attention and test coverage"
            )

    # S578: Shared module blast — blast file lives in a shared/common/core/lib directory.
    # Modules in shared infrastructure directories are used by many packages by convention;
    # changes can break consumers that don't appear in the local import graph.
    _fp578 = file_path.replace("\\", "/")
    _shared_markers578 = ("/shared/", "/common/", "/core/", "/lib/", "/base/", "/foundation/")
    if any(m in f"/{_fp578}/" for m in _shared_markers578):
        lines.append(
            f"shared module blast: {file_path.rsplit('/', 1)[-1]} is in a shared infrastructure directory"
            f" — changes here may break consumers not visible in the local import graph"
        )

    # S583: Many callers per symbol — blast target has 10+ cross-file callers.
    # Symbols with very high caller counts are de-facto stable APIs; any signature
    # or behavioral change requires updating many call sites simultaneously.
    _s583_syms = [
        sym for sym in graph.symbols.values()
        if sym.file_path == file_path or sym.file_path.replace("\\", "/") == _fp578
    ]
    if _s583_syms:
        _max_callers583 = max(len(graph.callers_of(s.id)) for s in _s583_syms)
        if _max_callers583 >= 10:
            _hot_sym583 = max(_s583_syms, key=lambda s: len(graph.callers_of(s.id)))
            lines.append(
                f"high-caller symbol: {_hot_sym583.name} has {_max_callers583} callers"
                f" — de-facto stable API; signature changes require updating many call sites"
            )

    # S589: Init-file blast — blast target is an __init__.py file.
    # Changes to package init files affect every consumer of the package;
    # even additive changes can break star-imports and re-export contracts.
    _fp589 = file_path.replace("\\", "/")
    if _fp589.endswith("__init__.py") or _fp589 == "__init__.py":
        lines.append(
            f"init file blast: {file_path.rsplit('/', 1)[-1]} is a package __init__"
            f" — changes affect all consumers of this package; star-import contracts may break"
        )

    # S595: Low importer count — blast target has 0 or 1 importers outside tests.
    # A file with no non-test importers is effectively internal to its module;
    # changes have limited blast radius but the file may be a dead-end or orphan.
    _non_test_importers595 = [fp for fp in importers if not _is_test_file(fp)]
    if len(_non_test_importers595) <= 1 and not (
        _fp589.endswith("__init__.py") or _fp589 == "__init__.py"
    ):
        _msg595 = "no non-test importers" if not _non_test_importers595 else f"1 non-test importer ({_non_test_importers595[0].rsplit('/', 1)[-1]})"
        lines.append(
            f"low blast radius: {file_path.rsplit('/', 1)[-1]} has {_msg595}"
            f" — changes are locally contained; verify this file isn't an unreferenced orphan"
        )

    # S602: No test coverage — blast target has no corresponding test file in the graph.
    # A file with no test coverage means regressions from blast-radius changes go undetected;
    # any modification here requires manual verification or new tests.
    _stem602 = _fp589.rsplit("/", 1)[-1].replace(".py", "").replace(".js", "").replace(".ts", "")
    _test_fps602 = [
        fp for fp in graph.files
        if _is_test_file(fp) and _stem602 in fp.replace("\\", "/").rsplit("/", 1)[-1]
    ]
    if not _test_fps602 and not _fp589.endswith("__init__.py"):
        lines.append(
            f"no test coverage: no test file found for {file_path.rsplit('/', 1)[-1]}"
            f" — changes here cannot be regression-tested; add tests before modifying"
        )

    # S608: High-churn name pattern — blast target name suggests frequently-modified infrastructure.
    # Files named with routing, handler, controller, or middleware suffixes/prefixes change often
    # as they sit at integration boundaries; blast radius changes here are high-risk.
    _stem608 = _fp589.rsplit("/", 1)[-1].lower()
    _churn_markers608 = ("handler", "router", "route", "controller", "middleware", "dispatch",
                         "gateway", "proxy", "adapter", "bridge")
    if any(m in _stem608 for m in _churn_markers608):
        lines.append(
            f"high-churn pattern: {file_path.rsplit('/', 1)[-1]} matches a high-churn naming pattern"
            f" — integration-boundary files change frequently; expect blast-radius changes often"
        )

    # S614: Deep path blast — blast target is nested 4+ directories deep.
    # Files buried deep in a package hierarchy are harder to find and navigate;
    # they may be under-tested because developers overlook them during coverage reviews.
    _depth614 = len(_fp589.split("/"))
    if _depth614 >= 4:
        lines.append(
            f"deep path blast: {file_path} is {_depth614} directories deep"
            f" — deeply nested files are often under-tested and overlooked in reviews"
        )

    # S620: Cross-package blast — blast file has importers from 3+ distinct top-level packages.
    # When consumers span multiple top-level packages, a breaking change here requires
    # coordinated updates across independent parts of the codebase.
    _importer_roots620 = {
        fp.replace("\\", "/").split("/")[0]
        for fp in importers
        if "/" in fp.replace("\\", "/")
        and not _is_test_file(fp)
    }
    if len(_importer_roots620) >= 3:
        _root_list620 = ", ".join(sorted(_importer_roots620)[:4])
        lines.append(
            f"cross-package blast: {file_path.rsplit('/', 1)[-1]} is used across"
            f" {len(_importer_roots620)} top-level packages ({_root_list620})"
            f" — breaking change here requires coordinated updates in multiple packages"
        )

    # S626: Utility module blast — blast target filename is utils.py, helpers.py, or similar.
    # Utility modules are often shared grab-bags; changes cascade widely and utility debt
    # accumulates because boundaries are unclear.
    _util_names626 = ("utils.py", "helpers.py", "util.py", "helper.py", "common.py", "misc.py", "shared.py")
    _fp_base626 = _fp589.rsplit("/", 1)[-1].lower()
    if _fp_base626 in _util_names626:
        _importer_count626 = len([fp for fp in importers if not _is_test_file(fp)])
        lines.append(
            f"utility file blast: {_fp589.rsplit('/', 1)[-1]} is a shared utility file"
            f" with {_importer_count626} non-test importer(s)"
            f" — utility modules accumulate mixed concerns; consider splitting by domain"
        )

    # S632: Import hub — blast target has 5+ importers and more fan-in than fan-out.
    # A module with many more importers than dependencies is a pure hub — it aggregates
    # nothing but is heavily consumed; any change ripples widely with no upstream buffer.
    _fanin632 = len([fp for fp in importers if not _is_test_file(fp)])
    _fanout632 = len({
        e.target_id for e in graph.edges
        if e.kind.value == "imports"
        and e.source_id == _fp589
    })
    if _fanin632 >= 5 and _fanin632 >= _fanout632 * 3:
        lines.append(
            f"import hub: {_fp589.rsplit('/', 1)[-1]} has {_fanin632} importers but only {_fanout632} dependencies"
            f" — pure consumer hub; changes here ripple widely with no upstream buffer"
        )

    # S638: Thin wrapper module — blast target has exactly 1 non-test symbol and 3+ importers.
    # A module that exposes a single symbol but is imported by many callers is a thin wrapper;
    # it may exist only for historical reasons and could be collapsed into a caller.
    _syms638 = [
        s for s in graph.symbols.values()
        if s.file_path == _fp589 and not _is_test_file(s.file_path) and s.parent_id is None
    ]
    _non_test_importers638 = [fp for fp in importers if not _is_test_file(fp)]
    if len(_syms638) == 1 and len(_non_test_importers638) >= 3:
        lines.append(
            f"thin wrapper: {_fp589.rsplit('/', 1)[-1]} has 1 symbol and {len(_non_test_importers638)} importers"
            f" — single-purpose wrapper; consider merging into the most frequent caller"
        )

    # S644: Pure class module — all exported symbols in blast target are class definitions.
    # A file with only classes (no module-level functions) is an object factory;
    # constructor signature changes break all callers without any function-level hint.
    _exported_syms644 = [
        s for s in graph.symbols.values()
        if s.file_path == _fp589 and s.exported and s.parent_id is None
        and not _is_test_file(s.file_path)
    ]
    if len(_exported_syms644) >= 2:
        _all_classes644 = all(s.kind.value == "class" for s in _exported_syms644)
        if _all_classes644:
            lines.append(
                f"pure class module: all {len(_exported_syms644)} exported symbols in"
                f" {_fp589.rsplit('/', 1)[-1]} are classes"
                f" — constructor changes break all callers; consider making init params keyword-only"
            )

    # S650: Mutual import — blast target and at least one of its importers import each other.
    # Bidirectional file-level imports create a circular dependency; this prevents
    # clean module separation and can cause import errors in some Python patterns.
    _import_targets650 = {
        e.target_id for e in graph.edges
        if e.kind.value == "imports" and e.source_id == _fp589
    }
    _mutual650 = [
        fp for fp in importers
        if not _is_test_file(fp)
        and fp in _import_targets650
    ]
    if _mutual650:
        _mutual_name650 = _mutual650[0].rsplit("/", 1)[-1]
        lines.append(
            f"mutual import: {_fp589.rsplit('/', 1)[-1]} and {_mutual_name650} import each other"
            f" — circular file dependency; can cause ImportError in Python; break the cycle"
        )

    # S656: Constants-only module — blast target exports only constants, no functions or classes.
    # A file that only exports constants is a config/settings module; changes to it affect
    # every consumer's behavior silently (no API signature to grep for).
    _all_syms656 = [
        s for s in graph.symbols.values()
        if s.file_path == _fp589 and s.exported and s.parent_id is None
        and not _is_test_file(s.file_path)
    ]
    if len(_all_syms656) >= 2:
        _all_consts656 = all(
            s.kind.value in ("constant", "variable")
            for s in _all_syms656
        )
        if _all_consts656:
            _importer_count656 = len([fp for fp in importers if not _is_test_file(fp)])
            lines.append(
                f"constants-only module: {_fp589.rsplit('/', 1)[-1]} exports only constants"
                f" ({len(_all_syms656)} values, {_importer_count656} importer(s))"
                f" — config changes affect all importers silently; no API signature to grep"
            )

    # S662: Large blast target — blast target file exceeds 300 lines.
    # Large files have higher coupling density; more symbols means more potential callers
    # and changes may interact with code you didn't intend to modify.
    _fi662 = graph.files.get(_fp589)
    if _fi662 and _fi662.line_count > 300:
        lines.append(
            f"large blast target: {_fp589.rsplit('/', 1)[-1]} is {_fi662.line_count} lines"
            f" — large file has high coupling density; careful scoping of your change is needed"
        )

    # S668: Single importer — blast target has exactly 1 external importer.
    # A module with only one consumer is lightly coupled; it may be removable
    # or could be inlined into its only caller to reduce indirection.
    _importers668 = graph.importers_of(_fp589)
    _ext_importers668 = [f for f in _importers668 if f != _fp589]
    if len(_ext_importers668) == 1:
        lines.append(
            f"single importer: {_fp589.rsplit('/', 1)[-1]} is only imported by"
            f" {_ext_importers668[0].rsplit('/', 1)[-1]}"
            f" — consider inlining or merging to reduce file count"
        )

    # S674: Entry point blast — blast target matches a well-known entry point filename.
    # Entry point files (main.py, app.py, server.py, cli.py, __main__.py) wire together
    # the whole system; changes here can silently break startup and shutdown paths.
    _entry_names674 = {
        "main.py", "app.py", "server.py", "cli.py", "__main__.py",
        "entrypoint.py", "entry.py", "wsgi.py", "asgi.py", "run.py",
    }
    _blast_basename674 = _fp589.rsplit("/", 1)[-1]
    if _blast_basename674 in _entry_names674:
        lines.append(
            f"entry point blast: {_blast_basename674} is a known entry point"
            f" — changes here affect system startup; verify initialization order and side effects"
        )

    # S680: Test file blast — blast target is a test file.
    # Running blast on a test file usually indicates the agent is looking at the wrong target;
    # test files rarely need blast analysis and blasting them yields misleading results.
    if _is_test_file(_fp589):
        lines.append(
            f"test file blast: {_fp589.rsplit('/', 1)[-1]} is a test file"
            f" — blast radius of test files is rarely meaningful; consider targeting the source file"
        )

    # S686: Zero-impact blast — blast target has no importers and no cross-file callers.
    # A file that nothing imports is an island; changes to it have no blast radius
    # and the file itself may be dead code or an unused entry point.
    _importers686 = graph.importers_of(_fp589)
    _ext686 = [f for f in _importers686 if f != _fp589]
    _fi686 = graph.files.get(_fp589)
    _all_syms686 = [
        s for s in graph.symbols.values()
        if s.file_path == _fp589
    ] if _fi686 else []
    _has_ext_callers686 = any(
        graph.callers_of(s.id)
        for s in _all_syms686
    )
    if not _ext686 and not _has_ext_callers686 and not _is_test_file(_fp589):
        lines.append(
            f"zero-impact blast: {_fp589.rsplit('/', 1)[-1]} has no importers or callers"
            f" — island file; changes are risk-free but file itself may be dead code"
        )

    # S692: Heavily imported — blast target is imported by 10+ files.
    # Files with 10+ importers are deeply coupled into the codebase;
    # even a small interface change can require updates across many consuming files.
    _all_importers692 = graph.importers_of(_fp589)
    _ext_importers692 = [f for f in _all_importers692 if f != _fp589]
    if len(_ext_importers692) >= 10:
        lines.append(
            f"heavily imported: {_fp589.rsplit('/', 1)[-1]} is imported by {len(_ext_importers692)} files"
            f" — wide coupling; interface changes require coordinated updates across the codebase"
        )

    # S698: Single export — blast target file exports exactly 1 public top-level symbol.
    # A file with one exported symbol is a candidate for consolidation;
    # callers could import the symbol from a parent module to reduce file proliferation.
    _fi698 = graph.files.get(_fp589)
    if _fi698 and not _is_test_file(_fp589):
        _pub_syms698 = [
            s for s in graph.symbols.values()
            if s.file_path == _fp589
            and s.parent_id is None
            and s.kind.value not in ("unknown", "module")
        ]
        if len(_pub_syms698) == 1:
            lines.append(
                f"single export: {_fp589.rsplit('/', 1)[-1]} exports only '{_pub_syms698[0].name}'"
                f" — single-symbol file; consider consolidating into a parent module"
            )

    # S704: No external dependencies — blast target makes no cross-file calls.
    # A file that calls nothing outside itself is self-contained; it has no dependency-induced
    # blast propagation upward in the call chain, making changes lower risk.
    _target_syms704 = [
        s for s in graph.symbols.values()
        if s.file_path == _fp589
    ]
    _has_ext_callees704 = any(
        any(c.file_path != _fp589 for c in graph.callees_of(s.id))
        for s in _target_syms704
    )
    if _target_syms704 and not _has_ext_callees704 and not _is_test_file(_fp589):
        _importers704 = [f for f in graph.importers_of(_fp589) if f != _fp589]
        if _importers704:  # only signal if there ARE importers (otherwise it's just dead code)
            lines.append(
                f"no external dependencies: {_fp589.rsplit('/', 1)[-1]} calls nothing outside itself"
                f" — leaf module; changes are contained; only caller-side integration matters"
            )

    # S710: Deeply nested blast — blast target is 3+ directory levels below the repo root.
    # Files buried deep in the directory tree are harder to discover, require long import paths,
    # and are more likely to be missed when searching for related code.
    _depth710 = len(_fp589.replace("\\", "/").split("/"))
    if _depth710 >= 4:  # root/a/b/c/file.py = 5 parts but relative path starts at level 1
        lines.append(
            f"deeply nested: {_fp589.rsplit('/', 1)[-1]} is at depth {_depth710 - 1}"
            f" in the directory tree — hard-to-find file; consider flattening"
        )

    # S716: Config file blast — the blast target is a config/settings/constants/exceptions file.
    # Configuration files often have many importers because constants and settings are used
    # globally; editing them can have unexpected wide impact across the entire codebase.
    _blast_basename716 = _fp589.replace("\\", "/").rsplit("/", 1)[-1]
    if _blast_basename716 in {"config.py", "settings.py", "constants.py", "exceptions.py", "errors.py"}:
        lines.append(
            f"config file blast: {_blast_basename716} is a shared config/constants file"
            f" — changes here propagate to all importers; review blast radius carefully"
        )

    # S722: Utility file blast — the blast target has a utility-style name (util/helper/common etc).
    # Utility files are conventionally imported everywhere; even small changes can have a large
    # blast radius that may not be obvious from the file's apparent simplicity.
    _util_kws722 = ("util", "helper", "common", "shared", "base", "mixin")
    _blast_stem722 = _fp589.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "").lower()
    if any(kw in _blast_stem722 for kw in _util_kws722):
        lines.append(
            f"utility file blast: {_fp589.rsplit('/', 1)[-1]} has a utility-style name"
            f" — widely imported by convention; expect broad blast radius"
        )

    # S728: Package init blast — the blast target is a __init__.py file.
    # Package init files aggregate and re-export symbols; every consumer of the package
    # depends on __init__, so changes here have package-wide blast radius.
    if _fp589.replace("\\", "/").rsplit("/", 1)[-1] == "__init__.py":
        lines.append(
            f"package init blast: {_fp589} is a package __init__.py"
            f" — all package consumers depend on this; changes affect the entire public API"
        )

    # S734: Data model blast — the blast target is a data model/schema/entity file.
    # Domain model changes cascade to all serializers, validators, consumers, and DB mappings;
    # even small attribute renames can trigger widespread migration needs.
    _model_kws734 = ("model", "schema", "entity", "dto", "domain")
    _blast_stem734 = _fp589.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "").lower()
    if any(kw in _blast_stem734 for kw in _model_kws734):
        lines.append(
            f"data model blast: {_fp589.rsplit('/', 1)[-1]} is a data model/schema file"
            f" — domain changes cascade to serializers, validators, and all consumers"
        )

    # S740: Middleware/decorator blast — blast target has a middleware or decorator filename.
    # Middleware and decorator files intercept all code paths that pass through them;
    # changes affect every route or call wrapped by this middleware/decorator.
    _mw_kws740 = ("middleware", "decorator", "wrapper", "interceptor", "hook")
    _blast_stem740 = _fp589.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "").lower()
    if any(kw in _blast_stem740 for kw in _mw_kws740):
        lines.append(
            f"middleware blast: {_fp589.rsplit('/', 1)[-1]} is a middleware/decorator file"
            f" — cross-cutting concern; changes affect every code path using this wrapper"
        )

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
