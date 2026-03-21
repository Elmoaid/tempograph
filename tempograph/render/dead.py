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
