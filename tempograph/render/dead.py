from __future__ import annotations

from ..types import Tempo, Symbol, SymbolKind
from ._utils import count_tokens, _is_test_file, _dead_code_confidence, _DISPATCH_PATTERNS

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

    # S98: Total removable lines — sum of line counts for high+medium confidence dead symbols.
    # Gives agents immediate ROI signal: "is this worth cleaning up?"
    # Only shown when total >= 50 lines (smaller amounts aren't worth flagging).
    _removable_lines = sum(sym.line_count for sym, conf in scored if conf >= 40)
    _removable_header = ""
    if _removable_lines >= 50:
        _removable_header = f" (~{_removable_lines} lines removable)"

    # S109: Dead ratio — fraction of total (non-test) symbols that are dead.
    # Quick health signal: "10% dead = manageable, 40% dead = major cleanup needed."
    # Only shown when there are 10+ total non-test symbols to avoid tiny-project noise.
    _total_non_test_syms = sum(
        1 for sym in graph.symbols.values() if not _is_test_file(sym.file_path)
    )
    _dead_ratio_str = ""
    if _total_non_test_syms >= 10 and dead:
        _high_conf_dead = sum(1 for sym, conf in scored if conf >= 40)
        _ratio_pct = int(_high_conf_dead / _total_non_test_syms * 100)
        if _ratio_pct >= 5:
            _dead_ratio_str = f" [{_ratio_pct}% of {_total_non_test_syms} source symbols]"

    # S126: Exported dead ratio — fraction of exported (public API) symbols that are dead.
    # High ratio = bloated, stale API surface. Even more alarming than overall dead ratio.
    # Only shown when 5+ total exported non-test symbols exist and ratio >= 20%.
    _total_exported_src = sum(
        1 for sym in graph.symbols.values()
        if sym.exported and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method", "class", "interface")
    )
    if _total_exported_src >= 5:
        _dead_exported = sum(
            1 for sym, conf in scored
            if conf >= 40 and sym.exported and sym.kind.value in ("function", "method", "class", "interface")
        )
        _exp_dead_pct = int(_dead_exported / _total_exported_src * 100)
        if _exp_dead_pct >= 20:
            _dead_ratio_str += f" [exported: {_dead_exported}/{_total_exported_src} public symbols dead ({_exp_dead_pct}%)]"

    lines = [f"Potential dead code ({len(dead)} symbols){_removable_header}{_dead_ratio_str}:"]

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

    # Largest dead: top 3 dead symbols by line count (high+medium confidence only).
    # These are the highest ROI individual deletions — big functions that nobody calls.
    _ld_candidates = sorted(
        [(sym, conf) for sym, conf in scored if conf >= 40],
        key=lambda x: -x[0].line_count,
    )
    if len(_ld_candidates) >= 2 and _ld_candidates[0][0].line_count >= 20:
        _ld_parts = [
            f"{sym.name} ({sym.line_count}L, conf:{conf})"
            for sym, conf in _ld_candidates[:3]
        ]
        lines.append(f"Largest dead: {', '.join(_ld_parts)}")

    # S92: Complex dead — top dead symbols by cyclomatic complexity (cx >= 5).
    # Complements "Largest dead" (line count): a short but complex dead function
    # has high cognitive overhead; deleting it reduces maintainability burden.
    _cx_dead = sorted(
        [(sym, conf) for sym, conf in scored if conf >= 40 and sym.complexity >= 5],
        key=lambda x: -x[0].complexity,
    )
    if len(_cx_dead) >= 2:
        _cd_parts = [
            f"{sym.name} (cx:{sym.complexity}, conf:{conf})"
            for sym, conf in _cx_dead[:3]
        ]
        lines.append(f"Complex dead: {', '.join(_cd_parts)}")

    # S95: Dead API — exported symbols with 0 cross-file callers in the dead code list.
    # Distinct from private dead: exported symbols may be called from external code
    # outside the indexed codebase. Deprecation-then-delete vs. immediate removal.
    _dead_api = [
        (sym, conf) for sym, conf in scored
        if sym.exported and conf >= 40
        and not any(c.file_path != sym.file_path for c in graph.callers_of(sym.id))
    ]
    if len(_dead_api) >= 2:
        _da_parts = [f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]}, conf:{conf})" for sym, conf in _dead_api[:4]]
        _da_str = ", ".join(_da_parts)
        if len(_dead_api) > 4:
            _da_str += f" +{len(_dead_api) - 4} more"
        lines.append(f"Dead API ({len(_dead_api)}): {_da_str} — exported, no callers (verify before deleting)")

    # S101: Clustered dead — files with 3+ dead symbols are batch cleanup targets.
    # More actionable than a scattered list: "clean up this file" vs. "hunt everywhere."
    # Shows top 2 worst offenders with symbol count and file name.
    _dead_by_file: dict[str, int] = {}
    for sym, conf in scored:
        if conf >= 40:
            _dead_by_file[sym.file_path] = _dead_by_file.get(sym.file_path, 0) + 1
    _clustered = sorted(
        [(fp, cnt) for fp, cnt in _dead_by_file.items() if cnt >= 3],
        key=lambda x: -x[1],
    )
    if len(_clustered) >= 1:
        _cl_parts = [f"{cnt} in {fp.rsplit('/', 1)[-1]}" for fp, cnt in _clustered[:2]]
        lines.append(f"Clustered dead: {', '.join(_cl_parts)} — batch cleanup targets")

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
    from ..git import file_last_modified_days as _file_last_modified_days  # noqa: PLC0415
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

    # S106: Stale dead — dead symbols in files untouched for 90+ days.
    # These are the safest to delete: nobody's been near them in months.
    # Different from "Recently dead" which flags accidentally-wired new code.
    # Only shown when git history is available and 2+ stale symbols qualify.
    _stale_dead = [
        (sym, conf, _file_age(sym.file_path))
        for sym, conf in scored
        if conf >= 40
        and (_file_age(sym.file_path) or 0) >= 90
    ]
    if len(_stale_dead) >= 2:
        _ages = [age for _, _, age in _stale_dead if age]
        _avg_age = int(sum(_ages) / len(_ages)) if _ages else 0
        _sd_names = [f"{sym.name} ({age}d)" for sym, _, age in _stale_dead[:4] if age]
        _sd_str = ", ".join(_sd_names)
        if len(_stale_dead) > 4:
            _sd_str += f" +{len(_stale_dead) - 4} more"
        lines.append(f"Stale dead ({len(_stale_dead)}, avg {_avg_age}d): {_sd_str} — safe to delete")

    # Transitively dead: non-dead symbols whose ALL callers are already dead.
    # find_dead_code() only marks symbols with 0 external callers or unimported files.
    # This catches functions only called by dead functions — "second-order" dead code.
    _transitively_dead: list[Symbol] = []
    for _td_sym in graph.symbols.values():
        if _td_sym.id in _dead_sym_ids:
            continue
        if _is_test_file(_td_sym.file_path):
            continue
        _td_callers = graph.callers_of(_td_sym.id)
        if not _td_callers:
            continue  # Already in find_dead_code() results or 0-caller symbol
        if all(c.id in _dead_sym_ids for c in _td_callers):
            _transitively_dead.append(_td_sym)
    if len(_transitively_dead) >= 1:
        _trd_names = [
            f"{s.name} ({s.file_path.rsplit('/', 1)[-1]})"
            for s in _transitively_dead[:4]
        ]
        _trd_str = ", ".join(_trd_names)
        if len(_transitively_dead) > 4:
            _trd_str += f" +{len(_transitively_dead) - 4} more"
        lines.append(f"Transitively dead ({len(_transitively_dead)}): {_trd_str} — only called by dead code")

    # S69: Safe-to-delete tier — conf >= 75 symbols.
    # Requires: no callers (30) + no file importers (25) + no renderers (10) + large (15) = 80 max.
    # Threshold 75 = slam-dunk deletions: file is isolated AND symbol is large. Subset of HIGH tier.
    _safe_delete = [(sym, conf) for sym, conf in scored if conf >= 75]
    if len(_safe_delete) >= 2:
        _sd_parts = [f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]}, conf:{conf})" for sym, conf in _safe_delete[:4]]
        _sd_str = ", ".join(_sd_parts)
        if len(_safe_delete) > 4:
            _sd_str += f" +{len(_safe_delete) - 4} more"
        lines.append(f"Safe to delete ({len(_safe_delete)}): {_sd_str}")

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

    # S76: Private dead hint — non-exported functions/methods with 0 callers.
    # find_dead_code() only reports exported symbols; private dead code is invisible without this.
    # Shows count only (not full list) to keep output concise.
    _private_dead_count = 0
    for _pd_sym in graph.symbols.values():
        if _pd_sym.exported or _is_test_file(_pd_sym.file_path):
            continue
        if _pd_sym.kind.value not in ("function", "method"):
            continue
        if not graph.callers_of(_pd_sym.id) and _pd_sym.line_count >= 2:
            _private_dead_count += 1
    if _private_dead_count >= 3:
        lines.append(f"Private dead: {_private_dead_count} non-exported symbols with 0 callers (not shown here)")

    # S123: Dead-by-module breakdown — which top-level directories carry the most dead code.
    # Helps agents prioritize cleanup by module: "render/ has 8 dead symbols, utils/ has 5".
    # Only shown when 2+ distinct modules have dead code AND total dead >= 8.
    if len(dead) >= 8:
        _dead_by_module: dict[str, int] = {}
        for _dm_sym, _dm_conf in scored:
            if _dm_conf < 40:
                continue
            _parts = _dm_sym.file_path.split("/")
            _mod = _parts[0] if len(_parts) > 1 else "."
            _dead_by_module[_mod] = _dead_by_module.get(_mod, 0) + 1
        _module_items = sorted(_dead_by_module.items(), key=lambda x: -x[1])
        if len(_module_items) >= 2:
            _mb_parts = [f"{mod}/ ({cnt})" for mod, cnt in _module_items[:4]]
            lines.append(f"dead by module: {', '.join(_mb_parts)}")

    # S159: Dead constants — unused constant/variable declarations.
    # Dead constants are often magic numbers or config values from abandoned features.
    # Only shown when 3+ dead constants/variables found.
    _dead_consts = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("constant", "variable")
    ]
    if len(_dead_consts) >= 3:
        _dc_names = [s.name for s in _dead_consts[:3]]
        _dc_str = ", ".join(_dc_names)
        if len(_dead_consts) > 3:
            _dc_str += f" +{len(_dead_consts) - 3} more"
        lines.append(f"dead constants: {len(_dead_consts)} unused constants/variables ({_dc_str})")

    # S202: Dead error handlers — error-handling functions that are dead.
    # Dead error handlers leave users without proper error recovery paths.
    # Only shown when 1+ dead error handler function found (single is alarming enough).
    _s202_error_patterns = ("handle_", "on_error", "catch_", "except_", "error_handler")
    _s202_dead_handlers = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) or sym.name.endswith("_error") or "error_handler" in sym.name
                for p in _s202_error_patterns)
    ]
    if len(_s202_dead_handlers) >= 1:
        _eh_names = [s.name for s in _s202_dead_handlers[:3]]
        _eh_str = ", ".join(_eh_names)
        if len(_s202_dead_handlers) > 3:
            _eh_str += f" +{len(_s202_dead_handlers) - 3} more"
        lines.append(
            f"dead error handlers: {len(_s202_dead_handlers)} unused error handler(s) ({_eh_str})"
            f" — missing error recovery"
        )

    # S208: Dead callbacks — callback/handler/listener/hook functions that are dead.
    # Unregistered callbacks suggest event wiring was removed but the handler wasn't cleaned up.
    # Only shown when 1+ dead callback function found (conf >= 40, standalone fns only).
    _s208_cb_patterns = ("_callback", "_handler", "_listener", "_hook")
    _s208_dead_cbs = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.endswith(p) for p in _s208_cb_patterns)
    ]
    if len(_s208_dead_cbs) >= 1:
        _cb_names = [s.name for s in _s208_dead_cbs[:3]]
        _cb_str = ", ".join(_cb_names)
        if len(_s208_dead_cbs) > 3:
            _cb_str += f" +{len(_s208_dead_cbs) - 3} more"
        lines.append(
            f"dead callbacks: {len(_s208_dead_cbs)} unused callback/handler fn(s) ({_cb_str})"
            f" — event wiring may have been removed"
        )

    # S218: Dead initializers — init/setup/configure functions with 0 callers (conf >= 40).
    # Dead setup functions suggest abandoned initialization paths; risky if they contain side effects.
    # Only shown when 1+ dead initializer found.
    _s218_init_patterns = ("init_", "initialize_", "setup_app", "configure_", "bootstrap_", "startup_")
    _s218_dead_inits = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s218_init_patterns)
    ]
    if len(_s218_dead_inits) >= 1:
        _init_names = [s.name for s in _s218_dead_inits[:3]]
        _init_str = ", ".join(_init_names)
        if len(_s218_dead_inits) > 3:
            _init_str += f" +{len(_s218_dead_inits) - 3} more"
        lines.append(
            f"dead initializers: {len(_s218_dead_inits)} unused init/setup fn(s) ({_init_str})"
            f" — abandoned initialization paths"
        )

        # S232: Dead serializers — serialize/to_dict/from_dict/to_json fns with 0 callers.
    # Dead serializers often indicate abandoned API shapes or migration leftovers.
    # Only shown when 1+ dead serializer function found (conf >= 40).
    _s232_ser_patterns = ("serialize_", "deserialize_", "to_dict", "from_dict",
                          "to_json", "from_json", "to_xml", "from_xml", "marshal_", "unmarshal_")
    _s232_dead_sers = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) or sym.name == p for p in _s232_ser_patterns)
    ]
    if len(_s232_dead_sers) >= 1:
        _ser_names = [s.name for s in _s232_dead_sers[:3]]
        _ser_str = ", ".join(_ser_names)
        if len(_s232_dead_sers) > 3:
            _ser_str += f" +{len(_s232_dead_sers) - 3} more"
        lines.append(
            f"dead serializers: {len(_s232_dead_sers)} unused serialize/marshal fn(s) ({_ser_str})"
            f" — abandoned API shapes or migration leftovers"
        )

        # S238: Dead middleware — middleware/before_*/after_* functions that are dead.
    # Dead middleware suggests request pipeline wiring was removed but the fn wasn't cleaned up.
    # Only shown when 1+ dead middleware function found (conf >= 40).
    _s238_mw_patterns = ("middleware_", "before_", "after_", "pre_", "post_",
                          "intercept_", "filter_", "on_request", "on_response")
    _s238_dead_mw = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s238_mw_patterns)
    ]
    if len(_s238_dead_mw) >= 1:
        _mw_names = [s.name for s in _s238_dead_mw[:3]]
        _mw_str = ", ".join(_mw_names)
        if len(_s238_dead_mw) > 3:
            _mw_str += f" +{len(_s238_dead_mw) - 3} more"
        lines.append(
            f"dead middleware: {len(_s238_dead_mw)} unused middleware fn(s) ({_mw_str})"
            f" — request pipeline wiring may have been removed"
        )

        # S225: Dead validators — validate_*/check_* functions with 0 callers (conf >= 40).
    # Dead validators suggest removed feature gates or abandoned data integrity checks.
    # Only shown when 2+ such dead validator functions found.
    _s225_val_patterns = ("validate_", "check_", "verify_", "assert_", "ensure_")
    _s225_dead_vals = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s225_val_patterns)
    ]
    if len(_s225_dead_vals) >= 2:
        _val_names = [s.name for s in _s225_dead_vals[:3]]
        _val_str = ", ".join(_val_names)
        if len(_s225_dead_vals) > 3:
            _val_str += f" +{len(_s225_dead_vals) - 3} more"
        lines.append(
            f"dead validators: {len(_s225_dead_vals)} unused validate/check fn(s) ({_val_str})"
            f" — removed feature gates or abandoned integrity checks"
        )

        # S196: Dead fixtures — setup_*/teardown_* functions that are dead.
    # Test fixture functions with 0 callers are often orphaned test infrastructure.
    # Only shown when 2+ such dead fixture functions found.
    _s196_dead_fixtures = [
        sym for sym, conf in scored
        if conf >= 40
        and sym.kind.value in ("function", "method")
        and (
            sym.name.startswith("setup_") or sym.name.startswith("teardown_")
            or sym.name.startswith("fixture_") or sym.name.startswith("create_")
        )
    ]
    if len(_s196_dead_fixtures) >= 2:
        _fix_names = [s.name for s in _s196_dead_fixtures[:3]]
        _fix_str = ", ".join(_fix_names)
        if len(_s196_dead_fixtures) > 3:
            _fix_str += f" +{len(_s196_dead_fixtures) - 3} more"
        lines.append(
            f"dead fixtures: {len(_s196_dead_fixtures)} unused setup/teardown/fixture fns ({_fix_str})"
        )

    # S190: Dead overrides — methods in a live class that override a parent method but have 0 callers.
    # A live class with an unused override = the child behavior is never triggered.
    # Only shown when >= 1 such method found with live class (has callers) but 0-caller override.
    _s190_dead_overrides: list[str] = []
    for _cls190 in graph.symbols.values():
        if _cls190.kind.value != "class" or _is_test_file(_cls190.file_path):
            continue
        # Class must be live: at least one method has cross-file callers
        # (instantiation like Child() creates edges to methods, not the class itself)
        _cls190_children = graph.children_of(_cls190.id)
        if not any(
            any(c.file_path != _cls190.file_path for c in graph.callers_of(m.id))
            for m in _cls190_children
            if m.kind.value == "method"
        ):
            continue
        # Find parent class via INHERITS edge (source=child, target=parent)
        _parent190 = next(
            (
                graph.symbols[e.target_id]
                for e in graph.edges
                if e.kind.value == "inherits" and e.source_id == _cls190.id
                and e.target_id in graph.symbols
            ),
            None,
        )
        if _parent190 is None:
            continue
        _parent_method_names190 = {
            s.name for s in graph.children_of(_parent190.id)
            if s.kind.value == "method"
        }
        for _child190 in graph.children_of(_cls190.id):
            if (
                _child190.kind.value == "method"
                and _child190.name in _parent_method_names190
                and len(graph.callers_of(_child190.id)) == 0
            ):
                _s190_dead_overrides.append(_child190.name)
    if len(_s190_dead_overrides) >= 1:
        _ov_str = ", ".join(list(dict.fromkeys(_s190_dead_overrides))[:3])
        if len(_s190_dead_overrides) > 3:
            _ov_str += f" +{len(_s190_dead_overrides) - 3} more"
        lines.append(
            f"dead overrides: {len(_s190_dead_overrides)} override method(s) unused ({_ov_str})"
        )

    # S184: Dead getters/setters — accessor methods (get_*/set_*) that are dead.
    # Methods in classes score lower confidence than standalone fns; threshold reflects this.
    # Only shown when 2+ such dead accessor methods found.
    _s184_dead_accessors = [
        sym for sym, conf in scored
        if conf >= 15
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and (sym.name.startswith("get_") or sym.name.startswith("set_"))
    ]
    if len(_s184_dead_accessors) >= 2:
        _acc_names = [s.name for s in _s184_dead_accessors[:3]]
        _acc_str = ", ".join(_acc_names)
        if len(_s184_dead_accessors) > 3:
            _acc_str += f" +{len(_s184_dead_accessors) - 3} more"
        lines.append(
            f"dead accessors: {len(_s184_dead_accessors)} dead getter/setter methods ({_acc_str})"
        )

    # S178: Dead exports — exported functions that have 0 callers and confidence >= 40.
    # These are public API symbols that were never used — over-exposed surface or abandoned stubs.
    # Only shown when 3+ such dead exported functions found.
    _s178_dead_exports = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.exported
        and sym.kind.value in ("function", "method")
        and len(graph.callers_of(sym.id)) == 0
    ]
    if len(_s178_dead_exports) >= 3:
        _de_names = [s.name for s in _s178_dead_exports[:3]]
        _de_str = ", ".join(_de_names)
        if len(_s178_dead_exports) > 3:
            _de_str += f" +{len(_s178_dead_exports) - 3} more"
        lines.append(f"dead exports: {len(_s178_dead_exports)} exported fns with 0 callers ({_de_str})")

    # S172: Dead class — a class with conf >= 40 that contains at least 1 method.
    # Dead classes = entire feature removal candidates; deleting one removes many symbols.
    # Only shown when >= 1 non-test class qualifies.
    _s172_dead_classes: list[str] = []
    for _cls172, _conf172 in scored:
        if _conf172 < 40:
            continue
        if _is_test_file(_cls172.file_path):
            continue
        if _cls172.kind.value != "class":
            continue
        # Must have at least one method (non-trivial class)
        _methods172 = [
            ch for ch in graph.children_of(_cls172.id)
            if ch.kind.value == "method"
        ]
        if _methods172:
            _s172_dead_classes.append(_cls172.name)
    if len(_s172_dead_classes) >= 1:
        _dclass_str = ", ".join(_s172_dead_classes[:3])
        if len(_s172_dead_classes) > 3:
            _dclass_str += f" +{len(_s172_dead_classes) - 3} more"
        lines.append(f"dead classes: {len(_s172_dead_classes)} fully-dead class(es) ({_dclass_str})")

    # S166: Zombie methods — dead methods that belong to classes with active (live) callers.
    # These are particularly surprising: the class is used but the method is unreachable.
    # Only shown when 2+ such zombie methods found.
    _s166_zombies: list[str] = []
    for _sym166, _conf166 in scored:
        if _conf166 < 40:
            continue
        if _is_test_file(_sym166.file_path):
            continue
        if _sym166.kind.value != "method":
            continue
        # Find the parent class via CONTAINS edges (parent contains this method)
        _parent_cls166 = next(
            (
                graph.symbols[e.source_id]
                for e in graph.edges
                if e.kind.value == "contains" and e.target_id == _sym166.id
                and e.source_id in graph.symbols
                and graph.symbols[e.source_id].kind.value == "class"
            ),
            None,
        )
        if _parent_cls166 is not None and len(graph.callers_of(_parent_cls166.id)) > 0:
            _s166_zombies.append(_sym166.name)
    if len(_s166_zombies) >= 2:
        _z_str = ", ".join(_s166_zombies[:3])
        if len(_s166_zombies) > 3:
            _z_str += f" +{len(_s166_zombies) - 3} more"
        lines.append(f"zombie methods: {len(_s166_zombies)} dead methods in live classes ({_z_str})")

    # S148: Largest dead fn — the single biggest dead symbol by line count.
    # Large dead code (>= 20 lines) = likely an abandoned feature, not a trivial stub.
    # Provides a high-value cleanup target: one deletion removes significant code mass.
    _src_dead_fns = [
        (sym, conf) for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and sym.line_count >= 20
    ]
    if _src_dead_fns:
        _biggest_sym = max(_src_dead_fns, key=lambda x: x[0].line_count)[0]
        lines.append(
            f"largest dead fn: {_biggest_sym.line_count}L {_biggest_sym.name}"
            f" in {_biggest_sym.file_path.rsplit('/', 1)[-1]} — consider removing"
        )

    # S140: Dead test helpers — unused functions defined in test files (not fixtures/conftest).
    # Test helper fns that nobody calls are stale utilities from abandoned test strategies.
    # Safe to delete; flag when >= 3 are found to prompt cleanup.
    _dead_test_helpers = [
        sym for sym, conf in scored
        if conf >= 10
        and _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and not sym.name.startswith("test_")
        and not sym.name.startswith("Test")
        and sym.name not in ("setUp", "tearDown", "setUpClass", "tearDownClass")
    ]
    if len(_dead_test_helpers) >= 3:
        _dth_names = [s.name for s in _dead_test_helpers[:3]]
        _dth_str = ", ".join(_dth_names)
        if len(_dead_test_helpers) > 3:
            _dth_str += f" +{len(_dead_test_helpers) - 3} more"
        lines.append(f"dead test helpers: {len(_dead_test_helpers)} unused helper fns in test files ({_dth_str})")

    # S153: Whole-file dead — source files where every symbol is a dead code candidate.
    # These are likely entirely abandoned files; deleting them is safer than symbol-by-symbol.
    # Only shown when 2+ such files found (1 might be a config/init file).
    _dead_sym_files: dict[str, int] = {}
    for sym, conf in scored:
        if conf >= 40 and not _is_test_file(sym.file_path):
            _dead_sym_files[sym.file_path] = _dead_sym_files.get(sym.file_path, 0) + 1
    _whole_file_dead: list[str] = []
    for _wf_fp, _wf_dead_count in _dead_sym_files.items():
        _wf_fi = graph.files.get(_wf_fp)
        if not _wf_fi:
            continue
        # Count total src symbols (not just dead ones) in this file
        _wf_total = sum(
            1 for sid in _wf_fi.symbols
            if sid in graph.symbols
            and graph.symbols[sid].kind.value in ("function", "method", "class")
        )
        if _wf_total >= 2 and _wf_dead_count >= _wf_total:
            _whole_file_dead.append(_wf_fp)
    if len(_whole_file_dead) >= 2:
        _wfd_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_whole_file_dead)[:3]]
        _wfd_str = ", ".join(_wfd_names)
        if len(_whole_file_dead) > 3:
            _wfd_str += f" +{len(_whole_file_dead) - 3} more"
        lines.append(f"whole-file dead: {len(_whole_file_dead)} files fully dead ({_wfd_str}) — candidates for deletion")

    # S241: Dead config/settings — config_*/settings_*/get_config/load_config functions with 0 callers.
    # Dead config accessors often signal removed features whose configuration was never cleaned up.
    # Only shown when 2+ dead config-accessor functions found (conf >= 40).
    _s241_cfg_patterns = ("config_", "settings_", "get_config", "load_config", "get_setting",
                          "load_settings", "parse_config", "read_config")
    _s241_dead_cfg = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.lower().startswith(p) for p in _s241_cfg_patterns)
    ]
    if len(_s241_dead_cfg) >= 2:
        _cfg_names = [s.name for s in _s241_dead_cfg[:3]]
        _cfg_str = ", ".join(_cfg_names)
        if len(_s241_dead_cfg) > 3:
            _cfg_str += f" +{len(_s241_dead_cfg) - 3} more"
        lines.append(
            f"dead config: {len(_s241_dead_cfg)} unused config fn(s) ({_cfg_str})"
            f" — removed feature configurations not yet cleaned up"
        )

    # S248: Dead exception classes — custom exception classes with 0 raise/except sites.
    # Dead exception classes bloat the exception hierarchy; unused errors may signal
    # removed features whose error paths were never cleaned up.
    # Only shown when 2+ dead exception classes found (conf >= 40).
    _s248_exc_indicators = ("error", "exception", "err", "exc", "fault", "failure")
    _s248_dead_exc = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value == "class"
        and any(sym.name.lower().endswith(ind) for ind in _s248_exc_indicators)
    ]
    if len(_s248_dead_exc) >= 2:
        _exc_names = [s.name for s in _s248_dead_exc[:3]]
        _exc_str = ", ".join(_exc_names)
        if len(_s248_dead_exc) > 3:
            _exc_str += f" +{len(_s248_dead_exc) - 3} more"
        lines.append(
            f"dead exceptions: {len(_s248_dead_exc)} unused exception class(es) ({_exc_str})"
            f" — removed error paths not yet cleaned up"
        )


    # S257: Dead type definitions — Schema/DTO/Request/Response/Config classes with 0 callers.
    # Dead type definitions suggest removed features or migrated data contracts that
    # were never cleaned up; they bloat the type system and mislead readers.
    # Only shown when 2+ such classes found (conf >= 40).
    _s257_type_suffixes = ("schema", "dto", "request", "response", "config", "settings",
                           "payload", "params", "options", "data", "model", "spec")
    _s257_dead_types = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value == "class"
        and any(sym.name.lower().endswith(ind) for ind in _s257_type_suffixes)
    ]
    if len(_s257_dead_types) >= 2:
        _type_names = [s.name for s in _s257_dead_types[:3]]
        _type_str = ", ".join(_type_names)
        if len(_s257_dead_types) > 3:
            _type_str += f" +{len(_s257_dead_types) - 3} more"
        lines.append(
            f"dead type defs: {len(_s257_dead_types)} unused type class(es) ({_type_str})"
            f" — removed data contracts not yet cleaned up"
        )


    # S264: Dead CLI commands — cmd_*/command_*/do_* functions with 0 callers.
    # Dead CLI handlers suggest removed subcommands whose dispatch wiring was cleaned up
    # but the handler itself was left behind.
    # Only shown when 2+ such functions found (conf >= 40).
    _s264_cmd_prefixes = ("cmd_", "command_", "do_", "run_cmd", "execute_", "action_", "subcommand_")
    _s264_dead_cmds = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s264_cmd_prefixes)
    ]
    if len(_s264_dead_cmds) >= 2:
        _cmd_names = [s.name for s in _s264_dead_cmds[:3]]
        _cmd_str = ", ".join(_cmd_names)
        if len(_s264_dead_cmds) > 3:
            _cmd_str += f" +{len(_s264_dead_cmds) - 3} more"
        lines.append(
            f"dead CLI commands: {len(_s264_dead_cmds)} unused command handler(s) ({_cmd_str})"
            f" — subcommand removed but handler not cleaned up"
        )


    # S270: Dead event handlers — on_*/handle_*/listener_* functions with 0 callers.
    # Dead event handlers suggest removed event subscriptions whose handler was not
    # cleaned up; they may also be mistakenly detached (silent bugs).
    # Only shown when 2+ such functions found (conf >= 40).
    _s270_evt_prefixes = ("on_", "handle_", "listener_", "observer_", "subscriber_",
                          "on_message", "on_event", "on_change", "on_error", "on_connect")
    _s270_dead_evt = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s270_evt_prefixes)
    ]
    if len(_s270_dead_evt) >= 2:
        _evt_names = [s.name for s in _s270_dead_evt[:3]]
        _evt_str = ", ".join(_evt_names)
        if len(_s270_dead_evt) > 3:
            _evt_str += f" +{len(_s270_dead_evt) - 3} more"
        lines.append(
            f"dead event handlers: {len(_s270_dead_evt)} unused event handler(s) ({_evt_str})"
            f" — event subscription may have been removed or silently detached"
        )


    # S279: Dead async functions — async def functions with 0 callers (conf >= 40).
    # Unused async functions are particularly risky because their deletion is not
    # always obvious from sync callers; they may be event loop callbacks or coroutines.
    # Only shown when 2+ dead async functions found.
    _s279_dead_async = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and sym.signature and "async" in sym.signature.lower()
    ]
    if len(_s279_dead_async) >= 2:
        _async_names = [s.name for s in _s279_dead_async[:3]]
        _async_str = ", ".join(_async_names)
        if len(_s279_dead_async) > 3:
            _async_str += f" +{len(_s279_dead_async) - 3} more"
        lines.append(
            f"dead async fns: {len(_s279_dead_async)} unused async function(s) ({_async_str})"
            f" — may be detached coroutines or removed event loop callbacks"
        )


    # S285: Dead factory functions — create_*/make_*/build_* functions with 0 callers.
    # Dead factory functions suggest removed object creation paths; they may indicate
    # refactored construction logic where old factories were abandoned.
    # Only shown when 2+ such functions found (conf >= 40).
    _s285_factory_prefixes = ("create_", "make_", "build_", "construct_", "instantiate_",
                              "new_", "factory_", "get_or_create_")
    _s285_dead_factories = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s285_factory_prefixes)
    ]
    if len(_s285_dead_factories) >= 2:
        _fac_names = [s.name for s in _s285_dead_factories[:3]]
        _fac_str = ", ".join(_fac_names)
        if len(_s285_dead_factories) > 3:
            _fac_str += f" +{len(_s285_dead_factories) - 3} more"
        lines.append(
            f"dead factories: {len(_s285_dead_factories)} unused factory fn(s) ({_fac_str})"
            f" — object creation paths removed or replaced; safe to clean up"
        )


    # S291: Dead property getters — get_*/fetch_*/retrieve_* methods with 0 callers.
    # Unused getters suggest removed data access paths; they bloat the API surface
    # and mislead developers about what data is actually consumed.
    # Only shown when 3+ such methods found (conf >= 40).
    _s291_getter_prefixes = ("get_", "fetch_", "retrieve_", "load_", "read_", "query_")
    _s291_dead_getters = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s291_getter_prefixes)
    ]
    if len(_s291_dead_getters) >= 3:
        _getter_names = [s.name for s in _s291_dead_getters[:3]]
        _getter_str = ", ".join(_getter_names)
        if len(_s291_dead_getters) > 3:
            _getter_str += f" +{len(_s291_dead_getters) - 3} more"
        lines.append(
            f"dead getters: {len(_s291_dead_getters)} unused getter fn(s) ({_getter_str})"
            f" — data access paths removed; safe to clean up API surface"
        )


    # S297: Dead validators — validate_*/check_*/verify_*/ensure_* functions with 0 callers.
    # Validation/guard functions are often added alongside a feature and forgotten when
    # the feature is removed; leftover validators are misleading — they imply invariants
    # that nothing actually enforces anymore.
    # Only shown when 3+ such functions found (conf >= 30).
    _s297_val_prefixes = ("validate_", "check_", "verify_", "ensure_", "assert_", "is_valid_")
    _s297_dead_vals = [
        sym for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.lower().startswith(p) for p in _s297_val_prefixes)
    ]
    if len(_s297_dead_vals) >= 3:
        _val_names = [s.name for s in _s297_dead_vals[:3]]
        _val_str = ", ".join(_val_names)
        if len(_s297_dead_vals) > 3:
            _val_str += f" +{len(_s297_dead_vals) - 3} more"
        lines.append(
            f"dead validators: {len(_s297_dead_vals)} unused validation fn(s) ({_val_str})"
            f" — removed features leave orphaned guards; misleading if left in codebase"
        )

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
