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

<<<<<<< HEAD
=======
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

>>>>>>> agent/creative/diff-untested-symbols
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
