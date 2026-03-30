from __future__ import annotations

from ..types import Tempo, EdgeKind, Symbol
from ._utils import count_tokens, _is_test_file


def _extend_tracked(lines: list[str], new_lines: list[str], token_count: int) -> int:
    """Extend lines with new_lines and return updated token_count.

    Used in render_focused to keep a running token budget across all
    _signals_focused_* sections so each section sees the correct remaining budget
    and output does not silently overflow max_tokens."""
    if new_lines:
        lines.extend(new_lines)
        token_count += count_tokens("\n".join(new_lines))
    return token_count


# ---------------------------------------------------------------------------
# Signal group helper: test_coverage (inline test callers section)
# ---------------------------------------------------------------------------
def _signals_focused_test_coverage(
    graph: Tempo, *, ordered: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Test coverage signals (inline test callers section)."""
    lines: list[str] = []
    # Test coverage section: which test files call the primary seed symbols?
    # Only consider depth-0 (seed) symbols to avoid noise from BFS expansion.
    if token_count < max_tokens - 180:
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
            _sorted_tc = sorted(_test_callers.items())
            _cap = 15
            _shown_tc = _sorted_tc[:_cap]
            _hidden_tc = len(_sorted_tc) - len(_shown_tc)
            _rows = [f"  {_tfp} ({_tcount} caller{'s' if _tcount != 1 else ''})" for _tfp, _tcount in _shown_tc]
            if _hidden_tc:
                _rows.append(f"  ... +{_hidden_tc} more test file{'s' if _hidden_tc != 1 else ''}")
            _tcov = "\nTests:\n" + "\n".join(_rows)
            lines.append(_tcov)
            token_count += count_tokens(_tcov)
        elif _has_source_callers:
            lines.append("\nTests: none")
            token_count += 4

    # All callers: complete caller list grouped by file (for rename/refactor impact).
    return lines


# ---------------------------------------------------------------------------
# Signal group helper: test
# ---------------------------------------------------------------------------
def _signals_focused_test(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: test."""
    lines: list[str] = []
    # S116: Untested callers — callers of seed symbols in files with no test counterpart.
    # These production callers have no safety net; modifying the seed risks silent breakage.
    # Only shown when 3+ untested callers found AND test files exist in the project.
    _all_proj_test_fps = {fp for fp in graph.files if _is_test_file(fp)}
    if _all_proj_test_fps and _seed_syms:
        _uc_files: set[str] = set()
        for _us in _seed_syms:
            for _uc in graph.callers_of(_us.id):
                if _is_test_file(_uc.file_path) or _uc.file_path == _us.file_path:
                    continue
                _uc_base = _uc.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if not any(_uc_base in t for t in _all_proj_test_fps):
                    _uc_files.add(_uc.file_path)
        if len(_uc_files) >= 3:
            _uc_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_uc_files)[:3]]
            _uc_str = ", ".join(_uc_names)
            if len(_uc_files) > 3:
                _uc_str += f" +{len(_uc_files) - 3} more"
            lines.append(f"\nuntested callers: {len(_uc_files)} caller files have no tests ({_uc_str})")

    # S174: Test coverage — how many distinct test files call the focused symbol.
    # More test files = better coverage spread; 0 test callers = coverage gap.
    # Only shown when >= 2 test files call the focused symbol (positive signal).
    if _seed_syms and token_count < max_tokens - 30:
        _prim174 = _seed_syms[0]
        _s174_test_fps = {
            c.file_path for c in graph.callers_of(_prim174.id)
            if _is_test_file(c.file_path)
        }
        if len(_s174_test_fps) >= 2:
            _s174_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_s174_test_fps)[:3]]
            _s174_str = ", ".join(_s174_names)
            if len(_s174_test_fps) > 3:
                _s174_str += f" +{len(_s174_test_fps) - 3} more"
            lines.append(
                f"\ntest coverage: {len(_s174_test_fps)} test files exercise {_prim174.name}"
                f" ({_s174_str})"
            )

    # S209: Test file pointer — when there's exactly one test file with a name matching
    # the seed file's stem, surface it directly so agents know where to add tests.
    # Only shown when no other test coverage signal was shown (avoids redundancy with S174).
    if _seed_syms and token_count < max_tokens - 30:
        _prim209 = _seed_syms[0]
        _stem209 = _prim209.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        _s209_test_fps = [
            fp for fp in graph.files
            if _is_test_file(fp) and _stem209 in fp.rsplit("/", 1)[-1]
        ]
        # Only show when exactly 1 matching test file (unambiguous pointer) and
        # S174 didn't already surface >= 2 test callers
        _s174_shown = _seed_syms and len({
            c.file_path for c in graph.callers_of(_prim209.id)
            if _is_test_file(c.file_path)
        }) >= 2
        if len(_s209_test_fps) == 1 and not _s174_shown:
            _s209_name = _s209_test_fps[0].rsplit("/", 1)[-1]
            lines.append(
                f"\ntest file: {_s209_name} — add tests here for {_prim209.name}"
            )

    return lines


# ---------------------------------------------------------------------------
# Signal group helper: complexity
# ---------------------------------------------------------------------------
def _signals_focused_complexity_call_graph(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S120 call depth + S103 cross-file callees."""
    lines: list[str] = []
    # S120: Call depth — longest call chain from seed down to a leaf (no callees).
    # Measures how deep the seed reaches before hitting dead-end functions.
    # Deep chains (>= 5) mean a change at the top propagates through many layers.
    if _seed_syms and token_count < max_tokens - 60:
        _s120_max_depth = 0
        for _s120_seed in _seed_syms[:2]:  # limit BFS to top 2 seeds
            _s120_visited: set[str] = set()
            _s120_queue = [(_s120_seed.id, 0)]
            while _s120_queue:
                _s120_sid, _s120_d = _s120_queue.pop(0)
                if _s120_sid in _s120_visited:
                    continue
                _s120_visited.add(_s120_sid)
                _s120_max_depth = max(_s120_max_depth, _s120_d)
                for _s120_callee in graph.callees_of(_s120_sid):
                    if _s120_callee.id not in _s120_visited:
                        _s120_queue.append((_s120_callee.id, _s120_d + 1))
        if _s120_max_depth >= 5:
            lines.append(f"\ncall depth: {_s120_max_depth} hops to leaf")

    # S103: Cross-file callees — distinct files the seed's direct callees live in.
    # A function pulling from many files = wide dependency scope = broad change risk.
    # Shown when seed calls into 3+ distinct external files.
    if _seed_syms and token_count < max_tokens - 80:
        _cf_callee_files: set[str] = set()
        _cf_callee_count = 0
        for _cfs in _seed_syms:
            for _cfe in graph.callees_of(_cfs.id):
                if _cfe.file_path != _cfs.file_path:
                    _cf_callee_files.add(_cfe.file_path)
                    _cf_callee_count += 1
        if len(_cf_callee_files) >= 3:
            lines.append(f"\ncross-file callees: {_cf_callee_count} fns in {len(_cf_callee_files)} files")

    return lines


def _signals_focused_complexity_callee_quality(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S192 callee complexity + S180 complex hub."""
    lines: list[str] = []
    # S192: Callee complexity — the focused symbol's external callees have high average complexity.
    # Calling into complex functions means cognitive load is high even for simple-looking fns.
    # Only shown when avg complexity of external callees >= 5 and 3+ external callees with cx data.
    if _seed_syms and token_count < max_tokens - 30:
        _prim192 = _seed_syms[0]
        if _prim192.kind.value in ("function", "method"):
            _callee_cx192 = [
                c.complexity for c in graph.callees_of(_prim192.id)
                if c.complexity is not None and c.complexity > 0
                and c.file_path != _prim192.file_path
            ]
            if len(_callee_cx192) >= 3:
                _avg_cx192 = sum(_callee_cx192) / len(_callee_cx192)
                if _avg_cx192 >= 5.0:
                    lines.append(
                        f"\ncallee complexity: avg cx {_avg_cx192:.1f}"
                        f" across {len(_callee_cx192)} callees"
                        f" — calls into complex functions, high cognitive load"
                    )

    # S180: Complex hub — focused symbol has high cyclomatic complexity AND many callers.
    # High cx + many callers = cognitive load at a widely-used junction; refactor priority.
    # Only shown when seed is a fn/method, cx >= 8, and callers >= 5.
    if _seed_syms and token_count < max_tokens - 30:
        _prim180 = _seed_syms[0]
        if _prim180.kind.value in ("function", "method"):
            _cx180 = _prim180.complexity or 0
            _caller_count180 = len(graph.callers_of(_prim180.id))
            if _cx180 >= 8 and _caller_count180 >= 5:
                lines.append(
                    f"\ncomplex hub: {_prim180.name} — cx={_cx180}, {_caller_count180} callers"
                    f" — high-complexity function used everywhere, refactor candidate"
                )

    return lines


def _signals_focused_complexity_size(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S168 large fn in file + S303 long function + S416 large function body."""
    lines: list[str] = []
    # S168: Large fn — the primary symbol is among the largest in its file (>= 50 lines).
    # Large functions are hard to reason about and test; they often hide multiple responsibilities.
    # Only shown when seed is a fn/method, line_count >= 50, and it's the largest in its file.
    if _seed_syms and token_count < max_tokens - 30:
        _prim168 = _seed_syms[0]
        if _prim168.kind.value in ("function", "method") and _prim168.line_count >= 50:
            _file_fn_sizes168 = [
                s.line_count for s in graph.symbols.values()
                if s.file_path == _prim168.file_path
                and s.kind.value in ("function", "method")
                and s.line_count is not None
            ]
            if _file_fn_sizes168 and _prim168.line_count >= max(_file_fn_sizes168) * 0.8:
                lines.append(
                    f"\nlarge fn: {_prim168.name} ({_prim168.line_count} lines)"
                    f" — largest in {_prim168.file_path.rsplit('/', 1)[-1]}, consider splitting"
                )

    # S303: Long function — focused function is 30+ lines (high cyclomatic complexity proxy).
    # Long functions tend to have more paths, harder to test, and harder to understand.
    # Reading the full body before editing reduces the chance of missing a branch.
    if _seed_syms and token_count < max_tokens - 30:
        _prim303 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim303 and _prim303.line_count >= 30:
            lines.append(
                f"\nlong function: {_prim303.name} is {_prim303.line_count} lines"
                f" — read full body before editing; high branch count"
            )

    # S416: Large function body — focused function spans 50+ lines.
    # Very long functions have multiple responsibilities and low cohesion; they are harder to
    # test in isolation and the mental model required to understand them grows with size.
    if _seed_syms and token_count < max_tokens - 30:
        _prim416 = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
        if _prim416 and _prim416.line_start and _prim416.line_end:
            _body_len416 = _prim416.line_end - _prim416.line_start
            if _body_len416 >= 50:
                lines.append(
                    f"\nlarge function: {_prim416.name} spans {_body_len416} lines"
                    f" — long functions often have multiple responsibilities; extract sub-functions"
                )

    return lines


def _signals_focused_complexity(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: complexity."""
    return (
        _signals_focused_complexity_call_graph(
            graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
        )
        + _signals_focused_complexity_callee_quality(
            graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
        )
        + _signals_focused_complexity_size(
            graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
        )
    )


# ---------------------------------------------------------------------------
# Signal group helper: structure
# ---------------------------------------------------------------------------
def _signals_focused_structure_file(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """File-level structure signals: siblings, sibling count, export ratio."""
    lines: list[str] = []
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

    # S132: Sibling count — total fn/method symbols in the primary seed's file.
    # Dense files (>= 8 fns) are harder to navigate without unintended side effects.
    # Gives agents orientation: they're working in a large module, not a focused one.
    if _seed_syms and token_count < max_tokens - 40:
        _prim132 = _seed_syms[0]
        _fi132 = graph.files.get(_prim132.file_path)
        if _fi132:
            _fn_count132 = sum(
                1 for sid in _fi132.symbols
                if sid in graph.symbols
                and graph.symbols[sid].kind.value in ("function", "method")
                and graph.symbols[sid].id != _prim132.id
            )
            if _fn_count132 >= 8:
                lines.append(f"\nsibling count: {_fn_count132} fns in {_prim132.file_path.rsplit('/', 1)[-1]}")

    # S136: Export ratio — fraction of fn/method symbols in the primary seed's file that are exported.
    # Low ratio (< 30%) = mostly internal module. High ratio (> 80%) = public API file.
    # Helps agents know whether changes leak into the public interface or stay internal.
    # Only shown when the file has 4+ fn/method symbols (too noisy for tiny files).
    if _seed_syms and token_count < max_tokens - 40:
        _prim136 = _seed_syms[0]
        _fi136 = graph.files.get(_prim136.file_path)
        if _fi136:
            _fns136 = [
                graph.symbols[sid] for sid in _fi136.symbols
                if sid in graph.symbols
                and graph.symbols[sid].kind.value in ("function", "method")
            ]
            if len(_fns136) >= 4:
                _exp136 = sum(1 for s in _fns136 if s.exported)
                _pct136 = int(_exp136 / len(_fns136) * 100)
                _fname136 = _prim136.file_path.rsplit("/", 1)[-1]
                if _pct136 >= 80:
                    lines.append(f"\nexport ratio: {_exp136}/{len(_fns136)} fns public in {_fname136} — all-public API file")
                elif _pct136 <= 25:
                    lines.append(f"\nexport ratio: {_exp136}/{len(_fns136)} fns public in {_fname136} — mostly internal module")

    return lines


def _signals_focused_structure_params(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Parameter-shape structure signals: S141 (param count), S234 (long param list), S362 (overloaded)."""
    lines: list[str] = []
    # S141: Param count — seed function has many parameters (>= 6), which is a design smell.
    # Too many parameters = violation of single responsibility or missing abstraction.
    # Extracts count from the signature string via comma-counting heuristic.
    # Only shown for functions/methods with a non-empty signature.
    if _seed_syms and token_count < max_tokens - 30:
        _prim141 = _seed_syms[0]
        if _prim141.kind.value in ("function", "method") and _prim141.signature:
            _sig141 = _prim141.signature
            # Extract param list: find content between first ( and last )
            _p141_open = _sig141.find("(")
            _p141_close = _sig141.rfind(")")
            if _p141_open >= 0 and _p141_close > _p141_open:
                _params141 = _sig141[_p141_open + 1:_p141_close].strip()
                if _params141 and _params141 not in ("self", "cls"):
                    # Count commas at depth 0 (skip nested brackets/generics)
                    _depth141 = 0
                    _comma_count141 = 0
                    for _ch141 in _params141:
                        if _ch141 in "([{<":
                            _depth141 += 1
                        elif _ch141 in ")]}>" :
                            _depth141 -= 1
                        elif _ch141 == "," and _depth141 == 0:
                            _comma_count141 += 1
                    _n_params141 = _comma_count141 + 1
                    # Adjust for self/cls as first param
                    if _params141.lstrip().startswith(("self,", "cls,")):
                        _n_params141 -= 1
                    if _n_params141 >= 6:
                        lines.append(f"\nparam count: {_n_params141} — consider a config object or split the function")

    # S234: Long parameter list — focused fn/method has >= 5 parameters.
    # Many parameters = hard to call correctly, often signals missing data objects.
    # Only shown when seed has >= 5 params in its signature.
    if _seed_syms and token_count < max_tokens - 30:
        _prim234 = _seed_syms[0]
        if _prim234.kind.value in ("function", "method"):
            _sig234 = _prim234.signature or ""
            # Count commas in the parameter section as a proxy for param count
            _paren_start = _sig234.find("(")
            _paren_end = _sig234.rfind(")")
            if _paren_start != -1 and _paren_end != -1:
                _params_str = _sig234[_paren_start + 1:_paren_end].strip()
                # Remove self/cls
                _params_str = _params_str.replace("self, ", "").replace("cls, ", "")
                _params_str = _params_str.replace("self,", "").replace("cls,", "")
                _params_str = _params_str.replace("self", "").replace("cls", "").strip()
                if _params_str:
                    _param_count234 = len([p for p in _params_str.split(",") if p.strip()])
                    if _param_count234 >= 5:
                        lines.append(
                            f"\nlong parameter list: {_prim234.name} has {_param_count234} params"
                            f" — consider grouping into a config/data object"
                        )

    # S362: Overloaded parameters — focused function/method has 8+ parameters.
    # Functions with 8+ parameters are hard to call correctly and indicate
    # missing abstractions; callers must remember argument order and often use positional args.
    if _seed_syms and token_count < max_tokens - 30:
        _prim362 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim362 and _prim362.signature:
            # Count comma-separated params (rough heuristic — exclude self/cls)
            _sig362 = _prim362.signature
            _paren362_start = _sig362.find("(")
            _paren362_end = _sig362.rfind(")")
            if _paren362_start != -1 and _paren362_end != -1:
                _params362 = _sig362[_paren362_start + 1:_paren362_end].strip()
                if _params362:
                    _param_parts362 = [
                        p for p in _params362.split(",")
                        if p.strip() and p.strip() not in ("self", "cls", "*", "**kwargs", "*args")
                    ]
                    if len(_param_parts362) >= 8:
                        lines.append(
                            f"\nparam overload: {_prim362.name} has {len(_param_parts362)} parameters"
                            f" — difficult to call correctly; consider a config object or builder pattern"
                        )

    return lines


def _signals_focused_structure(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: structure."""
    lines: list[str] = []
    lines.extend(_signals_focused_structure_file(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_structure_params(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    return lines


# ---------------------------------------------------------------------------
# Signal group helper: class_hierarchy
# ---------------------------------------------------------------------------
def _signals_focused_class_hierarchy_size(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Class hierarchy signals: class method count (S150)."""
    lines: list[str] = []
    # S150: Class method count — when seed is a class, show total method count.
    # Large classes (>= 8 methods) often violate single responsibility.
    # Helps agents gauge refactor scope before touching a class.
    if _seed_syms and token_count < max_tokens - 30:
        _prim150 = _seed_syms[0]
        if _prim150.kind.value == "class":
            _methods150 = [
                s for s in graph.children_of(_prim150.id)
                if s.kind.value == "method"
            ]
            if len(_methods150) >= 8:
                lines.append(f"\nclass size: {len(_methods150)} methods — large class, consider decomposition")
    return lines


def _signals_focused_class_hierarchy_depth(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Class hierarchy signals: inheritance depth (S155, S293)."""
    lines: list[str] = []
    # S155: Inheritance depth — BFS up INHERITS edges from seed class to count chain depth.
    # Deep chains (>= 3 levels) indicate high coupling to base class behavior.
    # Only shown when seed is a class and inheritance depth >= 3.
    if _seed_syms and token_count < max_tokens - 30:
        _prim155 = _seed_syms[0]
        if _prim155.kind.value in ("class", "interface"):
            _inh_depth = 0
            _inh_visited: set[str] = {_prim155.id}
            _inh_current = _prim155.id
            while True:
                # Find parent class via INHERITS edge (source=child, target=parent)
                from ..types import EdgeKind as _EK155
                _parent155 = next(
                    (graph.symbols[e.target_id] for e in graph.edges
                     if e.kind == _EK155.INHERITS and e.source_id == _inh_current
                     and e.target_id in graph.symbols and e.target_id not in _inh_visited),
                    None
                )
                if _parent155 is None:
                    break
                _inh_depth += 1
                _inh_visited.add(_parent155.id)
                _inh_current = _parent155.id
                if _inh_depth >= 10:  # safety cap
                    break
            if _inh_depth >= 3:
                lines.append(f"\ninheritance depth: {_inh_depth} levels — deep hierarchy, high base-class coupling")

    # S293: Deep inheritance — focused class inherits from a chain 3+ levels deep.
    # Deep hierarchies are hard to reason about; changes at the top cascade silently
    # through all descendants. Prefer composition over deep inheritance.
    if _seed_syms and token_count < max_tokens - 30:
        _prim293 = next((s for s in _seed_syms if s.kind.value == "class"), None)
        if _prim293:
            # Walk inheritance chain upward
            _depth293 = 0
            _current293_ids = {_prim293.id}
            _chain293: list[str] = [_prim293.name]
            while _depth293 < 10:
                _parent_ids293 = [
                    e.target_id for e in graph.edges
                    if e.kind.value == "inherits" and e.source_id in _current293_ids
                    and e.target_id not in _current293_ids
                ]
                if not _parent_ids293:
                    break
                _depth293 += 1
                _current293_ids = set(_parent_ids293)
                _first_parent293 = graph.symbols.get(_parent_ids293[0])
                if _first_parent293:
                    _chain293.append(_first_parent293.name)
            if _depth293 >= 3:
                _chain_str293 = " → ".join(reversed(_chain293[:4]))
                lines.append(
                    f"\ndeep inheritance: {_prim293.name} is {_depth293} levels deep"
                    f" ({_chain_str293}) — deep hierarchy; prefer composition"
                )
    return lines


def _signals_focused_class_hierarchy_bases(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Class hierarchy signals: subclass count and multiple inheritance (S228, S320)."""
    lines: list[str] = []
    # S228: Class symbol focused — the focused symbol is a class; show subclass count.
    # Classes with subclasses have contracts that affect all inheritors; changes propagate down.
    # Only shown when seed is a class with >= 1 subclass in the graph.
    if _seed_syms and token_count < max_tokens - 30:
        _prim228 = _seed_syms[0]
        if _prim228.kind.value == "class":
            from ..types import EdgeKind as _EK228
            _subclasses228 = [
                graph.symbols[e.source_id]
                for e in graph.edges
                if e.kind.value == "inherits" and e.target_id == _prim228.id
                and e.source_id in graph.symbols
            ]
            if _subclasses228:
                _sub_names228 = [s.name for s in _subclasses228[:3]]
                _sub_str228 = ", ".join(_sub_names228)
                if len(_subclasses228) > 3:
                    _sub_str228 += f" +{len(_subclasses228) - 3} more"
                lines.append(
                    f"\nclass with subclasses: {len(_subclasses228)} subclass(es) ({_sub_str228})"
                    f" — interface changes break all inheritors"
                )

    # S320: Multiple inheritance — focused class inherits from 2+ distinct base classes.
    # Multiple inheritance (mixin-heavy or diamond) creates fragile MRO dependencies;
    # adding or reordering base classes changes behavior in non-obvious ways.
    if _seed_syms and token_count < max_tokens - 30:
        _prim320 = next((s for s in _seed_syms if s.kind.value == "class"), None)
        if _prim320:
            _bases320 = [
                e for e in graph.edges
                if e.kind.value == "inherits" and e.source_id == _prim320.id
            ]
            if len(_bases320) >= 2:
                _base_names320 = [
                    graph.symbols[e.target_id].name
                    for e in _bases320[:3]
                    if e.target_id in graph.symbols
                ]
                lines.append(
                    f"\nmultiple inheritance: {_prim320.name} inherits from"
                    f" {len(_bases320)} bases ({', '.join(_base_names320)})"
                    f" — MRO-sensitive; reordering bases changes behavior"
                )
    return lines


def _signals_focused_class_hierarchy(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: class_hierarchy."""
    lines: list[str] = []
    lines.extend(_signals_focused_class_hierarchy_size(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_class_hierarchy_depth(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_class_hierarchy_bases(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    return lines


# ---------------------------------------------------------------------------
# Signal group helper: class_patterns — sub-helpers
# ---------------------------------------------------------------------------
def _signals_focused_class_patterns_inheritance(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: class_patterns — inheritance signals (S287, S334)."""
    lines: list[str] = []
    # S287: Method override — focused method has the same name as a method in a parent class.
    # Overriding methods must maintain the parent's contract (Liskov Substitution Principle).
    # Changes to signature or return type may break polymorphic callers.
    if _seed_syms and token_count < max_tokens - 30:
        _prim287 = _seed_syms[0]
        if _prim287.kind.value == "method" and _prim287.parent_id:
            _parent287 = graph.symbols.get(_prim287.parent_id)
            if _parent287 and _parent287.kind.value == "class":
                # Find parent classes (via inherits edges)
                _super_class_ids287 = [
                    e.target_id for e in graph.edges
                    if e.kind.value == "inherits" and e.source_id == _parent287.id
                ]
                for _super_id287 in _super_class_ids287:
                    _super_children287 = graph.children_of(_super_id287)
                    _matching287 = [c for c in _super_children287 if c.name == _prim287.name]
                    if _matching287:
                        _super_sym287 = graph.symbols.get(_super_id287)
                        _super_name287 = _super_sym287.name if _super_sym287 else "parent"
                        lines.append(
                            f"\nmethod override: {_prim287.name} overrides {_super_name287}.{_prim287.name}"
                            f" — must preserve parent's contract; signature changes break polymorphism"
                        )
                        break

    # S334: Interface method — focused method is declared in a class with 3+ abstract methods.
    # Interface methods define contracts; any change to parameters or return type is a
    # breaking change for all implementors, not just direct callers.
    if _seed_syms and token_count < max_tokens - 30:
        _prim334 = next((s for s in _seed_syms if s.kind.value == "method"), None)
        if _prim334 and _prim334.parent_id:
            _parent334 = graph.symbols.get(_prim334.parent_id)
            if _parent334 and _parent334.kind.value == "class":
                # Count abstract methods in parent
                _sibling_methods334 = [
                    s for s in graph.symbols.values()
                    if s.parent_id == _parent334.id and s.kind.value == "method"
                    and s.line_count <= 1  # stub/abstract: body is just pass or raise
                    and s.name not in ("__init__", "__new__", "__repr__", "__str__")
                ]
                if len(_sibling_methods334) >= 3:
                    lines.append(
                        f"\ninterface method: {_prim334.name} is in abstract class {_parent334.name}"
                        f" ({len(_sibling_methods334)} abstract methods)"
                        f" — contract change; all implementations must be updated"
                    )

    return lines


def _signals_focused_class_patterns_size(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: class_patterns — size and isolation signals (S253, S275, S356)."""
    lines: list[str] = []
    # S253: Fat class — focused symbol is a class with 10+ methods/properties.
    # Large classes often violate SRP; consider splitting into smaller components.
    # Only shown when focused symbol is a class and has 10+ child methods.
    if _seed_syms and token_count < max_tokens - 30:
        _prim253 = next((s for s in _seed_syms if s.kind.value == "class"), None)
        if _prim253 and _prim253.kind.value == "class":
            _children253 = graph.children_of(_prim253.id)
            _methods253 = [c for c in _children253 if c.kind.value in ("method", "function")]
            if len(_methods253) >= 10:
                lines.append(
                    f"\nfat class: {_prim253.name} has {len(_methods253)} methods"
                    f" — large class; consider splitting into focused components"
                )

    # S275: Orphaned class — focused class has 0 callers from outside its own file.
    # An exported class that nobody uses externally is either dead code or
    # intentionally kept for extension (interface/base); clarify which.
    if _seed_syms and token_count < max_tokens - 30:
        _prim275 = next((s for s in _seed_syms if s.kind.value == "class"), None)
        if _prim275:
            _ext_callers275 = [
                c for c in graph.callers_of(_prim275.id)
                if c.file_path != _prim275.file_path
            ]
            _method_has_ext_callers275 = any(
                any(c.file_path != _prim275.file_path for c in graph.callers_of(child.id))
                for child in graph.children_of(_prim275.id)
            )
            if not _ext_callers275 and not _method_has_ext_callers275 and _prim275.exported:
                lines.append(
                    f"\norphaned class: {_prim275.name} is exported but has no external callers"
                    f" — may be dead code or an unused base class"
                )

    # S356: God method — focused method lives in a class with 20+ total methods.
    # God classes accumulate responsibilities until no single developer can hold them in their head;
    # methods in these classes are hard to test in isolation and often share hidden state.
    if _seed_syms and token_count < max_tokens - 30:
        _prim356 = next(
            (s for s in _seed_syms if s.kind.value == "method" and s.parent_id), None
        )
        if _prim356 and _prim356.parent_id:
            _siblings356 = [
                s for s in graph.symbols.values()
                if s.parent_id == _prim356.parent_id and s.kind.value == "method"
            ]
            if len(_siblings356) >= 20:
                _parent_name356 = (
                    graph.symbols[_prim356.parent_id].name
                    if _prim356.parent_id in graph.symbols else "unknown"
                )
                lines.append(
                    f"\ngod class: {_parent_name356} has {len(_siblings356)} methods"
                    f" — god class; {_prim356.name} shares state with many siblings; hard to test in isolation"
                )

    return lines


# ---------------------------------------------------------------------------
# Signal group helper: class_patterns
# ---------------------------------------------------------------------------
def _signals_focused_class_patterns(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: class_patterns."""
    lines: list[str] = []
    lines.extend(_signals_focused_class_patterns_inheritance(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_class_patterns_size(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    return lines


# ---------------------------------------------------------------------------
# Signal group helper: coupling
# ---------------------------------------------------------------------------
def _signals_focused_coupling_fanout(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Coupling signals: outgoing call fan-out (S186, S272)."""
    lines: list[str] = []
    if not (_seed_syms and token_count < max_tokens - 30):
        return lines
    _prim = _seed_syms[0]
    if _prim.kind.value not in ("function", "method"):
        return lines

    # S186: Cross-file callee — calls functions in 3+ distinct external files.
    _callee_files = {
        c.file_path for c in graph.callees_of(_prim.id)
        if c.file_path != _prim.file_path
    }
    if len(_callee_files) >= 3:
        _cf_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_callee_files)[:3]]
        _cf_str = ", ".join(_cf_names)
        if len(_callee_files) > 3:
            _cf_str += f" +{len(_callee_files) - 3} more"
        lines.append(
            f"\ncross-file callee: {_prim.name} calls into {len(_callee_files)} files"
            f" ({_cf_str}) — coordination fn, changes ripple to many modules"
        )

    # S272: High callee fan-out — calls 5+ distinct external functions.
    _callees = [c for c in graph.callees_of(_prim.id) if c.file_path != _prim.file_path]
    _unique = {c.name for c in _callees}
    if len(_unique) >= 5:
        lines.append(
            f"\nhigh fan-out: {_prim.name} calls {len(_unique)} distinct external fns"
            f" — many dependencies; consider dependency injection for testability"
        )

    return lines


def _signals_focused_coupling_callers(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Coupling signals: incoming callers and re-export visibility (S314, S309)."""
    lines: list[str] = []
    if not (_seed_syms and token_count < max_tokens - 30):
        return lines
    _prim = _seed_syms[0]

    # S314: High caller count — called from 10+ distinct files.
    _callers = graph.callers_of(_prim.id)
    _caller_files = {c.file_path for c in _callers if c.file_path != _prim.file_path}
    if len(_caller_files) >= 10:
        lines.append(
            f"\nhigh caller count: {_prim.name} called from {len(_caller_files)} files"
            f" — de-facto stable API; behavior changes break callers even without signature change"
        )

    # S309: Re-exported symbol — also exported from an __init__ or index file.
    if _prim.exported:
        _reexport = [
            s for s in graph.symbols.values()
            if s.name == _prim.name
            and s.file_path != _prim.file_path
            and s.exported
            and (
                s.file_path.endswith("__init__.py")
                or s.file_path.rsplit("/", 1)[-1].startswith("index.")
            )
        ]
        if _reexport:
            _facade_name = _reexport[0].file_path.rsplit("/", 1)[-1]
            lines.append(
                f"\nre-exported: {_prim.name} also exported from {_facade_name}"
                f" — dual blast radius; importers of the facade are also affected"
            )

    return lines


def _signals_focused_coupling_hidden(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Coupling signals: non-obvious/hidden coupling (S210 cochange, S266 circular)."""
    lines: list[str] = []
    if not (_seed_syms and token_count < max_tokens - 30):
        return lines
    _prim = _seed_syms[0]

    # S210: Cochange partners outside static graph — hidden coupling via git history.
    if graph.root:
        try:
            from ..git import cochange_pairs as _cp, is_git_repo as _igr
            if _igr(graph.root):
                _seed_fp = _prim.file_path
                _static_neighbors: set[str] = set()
                # CALLS: use indexed per-symbol lookups (O(k) vs O(30K edge scan))
                for _s in graph.symbols_in_file(_seed_fp):
                    for _c in graph.callees_of(_s.id):
                        _static_neighbors.add(_c.file_path)
                    for _c in graph.callers_of(_s.id):
                        _static_neighbors.add(_c.file_path)
                # IMPORTS: use reverse-indexed file-level lookup
                _static_neighbors.update(graph.importers_of(_seed_fp))
                _static_neighbors.update(
                    _fp for _fp, _imp_list in graph._importers.items()
                    if _seed_fp in _imp_list
                )
                _pairs = _cp(graph.root, _seed_fp, n=10)
                _hidden = [
                    p for p in _pairs
                    if p["path"] not in _static_neighbors
                    and p["path"] != _seed_fp
                    and not _is_test_file(p["path"])
                    and p["count"] >= 3
                ]
                if len(_hidden) >= 2:
                    _h_names = [p["path"].rsplit("/", 1)[-1] for p in _hidden[:3]]
                    _h_str = ", ".join(_h_names)
                    if len(_hidden) > 3:
                        _h_str += f" +{len(_hidden) - 3} more"
                    lines.append(
                        f"\ncochange partners (not in call graph): {_h_str}"
                        f" — co-edit history suggests hidden coupling"
                    )
        except Exception:
            pass

    # S266: Circular call — focused symbol and one of its callees also call back to it.
    if _prim.kind.value in ("function", "method"):
        _callers_ids = {c.id for c in graph.callers_of(_prim.id)}
        _callees_ids = {c.id for c in graph.callees_of(_prim.id)}
        _mutual = _callers_ids & _callees_ids
        if _mutual:
            _mutual_name = next(
                (graph.symbols[sid].name for sid in _mutual if sid in graph.symbols),
                None
            )
            if _mutual_name:
                lines.append(
                    f"\ncircular call: {_prim.name} ↔ {_mutual_name} call each other"
                    f" — mutual dependency; changes must maintain protocol on both sides"
                )

    return lines


def _signals_focused_coupling(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: coupling."""
    _kw = dict(_seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens)
    return (
        _signals_focused_coupling_fanout(graph, **_kw)
        + _signals_focused_coupling_callers(graph, **_kw)
        + _signals_focused_coupling_hidden(graph, **_kw)
    )


# ---------------------------------------------------------------------------
# Signal group helper: naming
# ---------------------------------------------------------------------------
def _signals_focused_naming(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: naming."""
    lines: list[str] = []
    # S162: Overloaded name — the primary symbol's name appears in 3+ different files.
    # Same name in many files = collision risk; reader context shifts when jumping between files.
    # Only shown when the seed symbol name occurs in 3+ distinct source files.
    if _seed_syms and token_count < max_tokens - 30:
        _prim162 = _seed_syms[0]
        _s162_files = {
            s.file_path for s in graph.symbols.values()
            if s.name == _prim162.name and not _is_test_file(s.file_path)
        }
        if len(_s162_files) >= 3:
            lines.append(
                f"\noverloaded name: '{_prim162.name}' appears in {len(_s162_files)} files"
                f" — name collision risk when navigating"
            )

    # S328: Verbose function name — focused function has a snake_case name with 5+ segments.
    # Functions with very long names often evolved through over-specialisation;
    # they tend to be hard to discover, test, and refactor without cascading renames.
    if _seed_syms and token_count < max_tokens - 30:
        _prim328 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim328:
            _parts328 = _prim328.name.split("_")
            if len(_parts328) >= 5 and all(len(p) > 0 for p in _parts328):
                lines.append(
                    f"\nverbose name: {_prim328.name} has {len(_parts328)}-segment name"
                    f" — over-specific; consider splitting the function to reflect the name"
                )

    # S368: Generic symbol name — focused symbol has a very generic, collision-prone name.
    # Generic names like "run", "process", "execute" increase search noise and make
    # symbol lookup ambiguous; many unrelated symbols share these names across the codebase.
    # S368: Generic name — PRUNED: name quality — agent can see the name
    # S374: Deprecated symbol — PRUNED: name quality — agent can see legacy/old in name
    if False:  # PRUNED: name quality
        if _seed_syms and token_count < max_tokens - 30:
            _prim368 = _seed_syms[0] if _seed_syms else None
            if _prim368:
                _generic_names368 = {
                    "run", "execute", "process", "handle", "call", "invoke",
                    "start", "stop", "init", "setup", "load", "save", "get", "set",
                    "update", "delete", "create", "parse", "format", "validate",
                }
                if _prim368.name.lower() in _generic_names368:
                    _same_name368 = [
                        s for s in graph.symbols.values()
                        if s.name.lower() == _prim368.name.lower() and s.id != _prim368.id
                    ]
                    if _same_name368:
                        lines.append(
                            f"\ngeneric name: '{_prim368.name}' shared by {len(_same_name368) + 1} symbols"
                            f" — highly generic; refine to intent-revealing name to reduce search ambiguity"
                        )
        if _seed_syms and token_count < max_tokens - 30:
            _prim374 = _seed_syms[0] if _seed_syms else None
            if _prim374:
                _dep_markers374 = (
                    "old_", "legacy_", "deprecated_", "_old", "_legacy", "_deprecated",
                    "v1_", "_v1", "v2_", "_v2", "obsolete_", "_obsolete",
                )
                _is_dep374 = any(_prim374.name.lower().startswith(m) or _prim374.name.lower().endswith(m) for m in _dep_markers374)
                if _is_dep374:
                    lines.append(
                        f"\ndeprecated: {_prim374.name} has a deprecated/legacy naming marker"
                        f" — callers may not know newer alternative exists; document replacement or remove"
                    )

    return lines


# ---------------------------------------------------------------------------
# Signal group helper: fn_traits (decomposed sub-helpers)
# ---------------------------------------------------------------------------

def _fn_trait_leaf(graph: Tempo, prim: "Symbol") -> "str | None":
    """S198: Leaf function — 0 external callees, ≥5 callers."""
    if prim.kind.value not in ("function", "method"):
        return None
    ext_callees = [c for c in graph.callees_of(prim.id) if c.file_path != prim.file_path]
    caller_count = len(graph.callers_of(prim.id))
    if len(ext_callees) == 0 and caller_count >= 5:
        return (
            f"\nleaf function: {prim.name} has {caller_count} callers"
            f" and 0 external callees — stable leaf, safe to refactor internals"
        )
    return None


def _fn_trait_async(prim: "Symbol") -> "str | None":
    """S204: Async function — 'async' in signature."""
    if prim.kind.value not in ("function", "method"):
        return None
    if "async" in (prim.signature or ""):
        return (
            f"\nasync fn: {prim.name} — callers must await,"
            f" changes affect async context propagation"
        )
    return None


def _fn_trait_private_callers(graph: Tempo, prim: "Symbol") -> "str | None":
    """S214: Private symbol with external non-test callers."""
    if not prim.name.startswith("_") or prim.name.startswith("__"):
        return None
    ext_callers = [
        c for c in graph.callers_of(prim.id)
        if c.file_path != prim.file_path and not _is_test_file(c.file_path)
    ]
    if ext_callers:
        return (
            f"\nprivate symbol with external callers: {prim.name}"
            f" called from {len(ext_callers)} external file(s)"
            f" — underscore naming convention violated"
        )
    return None


def _fn_trait_recursive(graph: Tempo, prim: "Symbol") -> "str | None":
    """S221: Recursive function — calls itself directly."""
    if prim.kind.value not in ("function", "method"):
        return None
    if any(c.id == prim.id for c in graph.callees_of(prim.id)):
        return (
            f"\nrecursive fn: {prim.name} calls itself"
            f" — changes must preserve loop invariants and base cases"
        )
    return None


def _fn_trait_property(prim: "Symbol") -> "str | None":
    """S244: Property accessor — @property or getter with no extra params."""
    return None  # PRUNED: taxonomic label — agent sees @property in signature
    if prim.kind.value != "method":
        return None
    sig = prim.signature or ""
    name = prim.name
    is_property = (
        "@property" in sig
        or sig.strip().startswith("@property")
        or (
            name.startswith("get_") and "(" in sig
            and "self" in sig
            and sig.count(",") == 0
        )
    )
    if is_property:
        return (
            f"\nproperty accessor: {name} is accessed as an attribute"
            f" — type or name changes break all usages silently"
        )
    return None


def _fn_trait_abstract(graph: Tempo, prim: "Symbol") -> "str | None":
    """S249: Abstract method — cascades signature changes to all implementations."""
    return None  # PRUNED: taxonomic label — agent sees @abstractmethod in signature
    if prim.kind.value not in ("function", "method"):
        return None
    sig = prim.signature or ""
    if "abstractmethod" not in sig and "@abc.abstractmethod" not in sig:
        return None
    impl_count = sum(
        1 for s in graph.symbols.values()
        if s.name == prim.name
        and s.file_path != prim.file_path
        and s.kind.value in ("function", "method")
    )
    return (
        f"\nabstract method: {prim.name} must be implemented by all subclasses"
        + (f" — {impl_count} implementation(s) found" if impl_count else "")
        + " — signature changes cascade to all concrete classes"
    )


# ---------------------------------------------------------------------------
# Signal group helper: fn_traits
# ---------------------------------------------------------------------------
def _signals_focused_fn_traits(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: fn_traits."""
    if not _seed_syms or token_count >= max_tokens - 30:
        return []
    prim = _seed_syms[0]
    checks = [
        _fn_trait_leaf(graph, prim),
        _fn_trait_async(prim),
        _fn_trait_private_callers(graph, prim),
        _fn_trait_recursive(graph, prim),
        _fn_trait_property(prim),
        _fn_trait_abstract(graph, prim),
    ]
    return [line for line in checks if line is not None]


# ---------------------------------------------------------------------------
# Signal group helper: fn_patterns
# ---------------------------------------------------------------------------
def _signals_focused_fn_patterns(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: fn_patterns."""
    lines: list[str] = []
    # S346: Side-effect function — name implies global state mutation.
    # Functions that modify global state are hard to test and prone to order-dependent bugs;
    # callers may assume they're pure (no side effects) based on the return type.
    if _seed_syms and token_count < max_tokens - 30:
        _prim346 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim346:
            _se_patterns346 = (
                "set_global_", "update_state_", "reset_", "clear_cache_",
                "flush_", "invalidate_", "global_", "modify_config_",
            )
            _is_se346 = any(_prim346.name.lower().startswith(p) for p in _se_patterns346)
            if _is_se346:
                lines.append(
                    f"\nside-effect: {_prim346.name} mutates global/shared state"
                    f" — callers may assume pure function; order-dependent bugs possible"
                )

    # S380: Entry point function — focused function IS the application entry point.
    # Entry point functions are often tested via integration tests, not unit tests;
    # small changes to startup order or argument parsing can have wide-ranging effects.
    if _seed_syms and token_count < max_tokens - 30:
        _prim380 = _seed_syms[0] if _seed_syms else None
        if _prim380 and _prim380.kind.value in ("function", "method"):
            _entry_names380 = {
                "main", "run", "start", "serve", "launch", "entrypoint",
                "cli", "app", "create_app", "application",
            }
            _fname380 = _prim380.file_path.rsplit("/", 1)[-1].lower() if _prim380.file_path else ""
            _is_entry380 = (
                _prim380.name.lower() in _entry_names380
                and _fname380 in ("__main__.py", "main.py", "app.py", "server.py", "cli.py", "run.py")
            )
            if _is_entry380:
                lines.append(
                    f"\nentry point: {_prim380.name} is the application entry point"
                    f" — startup sequence changes are hard to unit-test; cover with integration tests"
                )

    # S398: Error-swallowing function — focused function name implies it suppresses exceptions.
    # Functions that suppress errors silently mask bugs; callers cannot distinguish success
    # from failure and issues become invisible until production symptoms appear.
    if _seed_syms and token_count < max_tokens - 30:
        _prim398 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim398:
            _swallow_patterns398 = (
                "swallow", "ignore_error", "silent_", "suppress_error",
                "no_raise", "_safe", "safe_",
            )
            _is_swallow398 = any(p in _prim398.name.lower() for p in _swallow_patterns398)
            if _is_swallow398:
                lines.append(
                    f"\nerror-swallowing: {_prim398.name} implies silent error suppression"
                    f" — callers cannot detect failures; log or re-raise to preserve observability"
                )

    # S392: Pure utility function — focused function calls 0 other symbols.
    # Pure functions with no outbound calls are easy to test in isolation and safe to refactor;
    # this is a positive signal worth noting as it indicates well-bounded scope.
    if _seed_syms and token_count < max_tokens - 30:
        _prim392 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim392:
            _callees392 = graph.callees_of(_prim392.id)
            if not _callees392 and _prim392.line_count >= 3:
                lines.append(
                    f"\npure utility: {_prim392.name} has no outbound calls"
                    f" — self-contained; easiest to test in isolation and safe to refactor independently"
                )

    # S386: Callback-style function — focused function takes a parameter named fn/callback/handler.
    # Callback-style APIs are harder to type-check and test; the callable contract is implicit
    # and callers must know the expected signature without IDE autocompletion.
    if _seed_syms and token_count < max_tokens - 30:
        _prim386 = next(
            (s for s in _seed_syms if s.kind.value in ("function", "method")), None
        )
        if _prim386 and _prim386.signature:
            _cb_param_names = {"fn", "func", "callback", "cb", "handler", "on_success",
                               "on_error", "on_complete", "hook", "callable_"}
            _sig386 = _prim386.signature
            _params386 = _sig386[_sig386.find("(") + 1: _sig386.rfind(")")].lower() if "(" in _sig386 else ""
            _has_cb386 = any(
                p.strip().split(":")[0].strip().split("=")[0].strip() in _cb_param_names
                for p in _params386.split(",")
            )
            if _has_cb386:
                lines.append(
                    f"\ncallback-style: {_prim386.name} accepts a callable argument"
                    f" — implicit callable contract; document expected signature and error behavior"
                )

    return lines


# ---------------------------------------------------------------------------
# Signal group helpers: fn_advanced (decomposed sub-helpers)
# ---------------------------------------------------------------------------
# Signal group helpers: fn_advanced (decomposed sub-helpers)
# ---------------------------------------------------------------------------
_BUILTINS593: frozenset = frozenset((
    "list", "dict", "set", "tuple", "type", "id", "input", "format",
    "filter", "map", "zip", "sum", "max", "min", "len", "range",
    "open", "print", "str", "int", "float", "bool", "bytes", "object",
))
_GENERIC_NAMES654: frozenset = frozenset({
    "run", "main", "execute", "start", "stop", "process", "handle", "handler",
    "get", "set", "init", "setup", "teardown", "update", "delete", "create",
    "load", "save", "read", "write", "parse", "format", "render",
})


def _signals_fn_recursion(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S340/S404/S500/S570/S684: recursion patterns."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        return lines
    # S340/S404/S500: self-loop edge (all three check the same condition; emit once)
    _self_calls = any(c.id == _prim.id for c in graph.callees_of(_prim.id))
    if _self_calls:
        lines.append(
            f"\nrecursive: {_prim.name} calls itself"
            f" — verify base case and maximum depth; consider iterative refactor for large inputs"
        )
    # S570: callees-based check (covers cases where graph.callees_of ≠ graph.edges)
    if not _is_test_file(_prim.file_path):
        _callees570 = graph.callees_of(_prim.id)
        _is_recursive570 = any(c.id == _prim.id or c.name == _prim.name for c in _callees570)
        if _is_recursive570:
            lines.append(
                f"\nrecursive function: {_prim.name} calls itself"
                f" — ensure a base case is reachable; missing base case causes RuntimeError: maximum recursion depth"
            )
    # S684: strict id equality via callees
    if not _is_test_file(_prim.file_path):
        _callees684 = graph.callees_of(_prim.id)
        if any(c.id == _prim.id for c in _callees684):
            lines.append(
                f"\nrecursive function: {_prim.name} calls itself directly"
                f" — verify base case and maximum recursion depth before modifying"
            )
    return lines


def _signals_fn_oop_class_prop(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S576/S630/S690: empty class, property accessor, method-heavy class signals."""
    lines: list[str] = []
    _prim_cls = next((s for s in _seed_syms if s.kind.value == "class"), None)
    if _prim_cls and not _is_test_file(_prim_cls.file_path):
        _children = graph.children_of(_prim_cls.id)
        # S576: Empty class — PRUNED: taxonomic label — agent sees empty class body
        if False:  # PRUNED: taxonomic label
            if not [c for c in _children if c.kind.value in ("method", "class", "function")]:
                lines.append(
                    f"\nempty class: {_prim_cls.name} has no methods"
                    f" — pure stub or data container; consider @dataclass, TypedDict, or NamedTuple"
                )
        # S690: Method-heavy class
        _methods = [c for c in _children if c.kind.value in ("method", "function")]
        if len(_methods) >= 10:
            lines.append(
                f"\nmethod-heavy class: {_prim_cls.name} has {len(_methods)} methods"
                f" — god class; split by responsibility before adding more methods"
            )
    # S630: Property accessor
    _prim_prop = next((s for s in _seed_syms if s.kind.value == "property"), None)
    if _prim_prop and not _is_test_file(_prim_prop.file_path):
        _callers630 = graph.callers_of(_prim_prop.id)
        lines.append(
            f"\nproperty callers: {_prim_prop.name} is a @property accessed by {len(_callers630)} caller(s)"
            f" — looks like an attribute read but executes code; relevant if lazy/cached/expensive"
        )
    return lines


def _signals_fn_oop_abstract_proto(
    graph: "Tempo", _seed_syms: list,
) -> list[str]:
    """S428/S451: abstract method and protocol/interface method signals."""
    lines: list[str] = []
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        return lines
    # S428: Abstract method — PRUNED: taxonomic label — agent sees Base/Abstract in class name
    if False:  # PRUNED: taxonomic label
        if _prim.parent_id:
            _parent = graph.symbols.get(_prim.parent_id)
            _base_kws = ("base", "abstract", "interface", "protocol", "mixin")
            if _parent and any(kw in _parent.name.lower() for kw in _base_kws):
                _subclass_impls = [
                    s for s in graph.symbols.values()
                    if s.name == _prim.name and s.id != _prim.id
                    and s.kind.value in ("function", "method") and s.file_path != _prim.file_path
                ]
                if _subclass_impls:
                    lines.append(
                        f"\nabstract method: {_prim.name} is from {_parent.name}"
                        f" with {len(_subclass_impls)} concrete implementation(s)"
                        f" — changes will cascade to all concrete classes; review each subclass"
                    )
    # S451: Protocol/interface method
    _prim_m = next((s for s in _seed_syms if s.kind.value == "method"), None)
    if _prim_m and _prim_m.parent_id:
        _parent_m = graph.symbols.get(_prim_m.parent_id)
        _proto_kws = ("protocol", "interface", "abc", "abstract", "mixin", "base")
        if _parent_m and any(kw in _parent_m.name.lower() for kw in _proto_kws):
            _impls = [s for s in graph.symbols.values()
                      if s.name == _prim_m.name and s.kind.value == "method"
                      and s.id != _prim_m.id and s.parent_id != _prim_m.parent_id]
            if _impls:
                lines.append(
                    f"\nprotocol method: {_prim_m.name} is defined in {_parent_m.name}"
                    f" with {len(_impls)} known implementation(s)"
                    f" — signature changes break all conforming types; update every implementation"
                )
    return lines


def _signals_fn_oop_behavioral(
    graph: "Tempo", _prim: object,
) -> list[str]:
    """S434/S440/S446/S475: factory, callback, global-state, generator signals."""
    lines: list[str] = []
    # S434: Factory function pattern
    _factory_prefixes = ("create_", "make_", "build_", "factory_", "new_", "get_instance_")
    if any(_prim.name.lower().startswith(p) for p in _factory_prefixes):
        _callees = [c.name for c in graph.callees_of(_prim.id)]
        _class_callees = [n for n in _callees if n and n[0].isupper()]
        if _class_callees:
            lines.append(
                f"\nfactory function: {_prim.name} instantiates {', '.join(dict.fromkeys(_class_callees[:3]))}"
                f" — callers depend on the returned shape; changing what is built silently breaks all callsites"
            )
    # S440: Callback-heavy function
    if _prim.signature:
        _cb_kws = ("callback", "handler", "on_", "fn_", "func_", "hook_", "listener_")
        _params = [p.strip().split(":")[0].strip().split("=")[0].strip()
                   for p in _prim.signature.split("(", 1)[-1].rstrip("):").split(",")]
        _cb_params = [p for p in _params if any(kw in p.lower() for kw in _cb_kws)]
        if len(_cb_params) >= 2:
            lines.append(
                f"\ncallback-heavy: {_prim.name} receives {len(_cb_params)} callback param(s)"
                f" ({', '.join(_cb_params[:3])})"
                f" — behavior is caller-determined; each callsite is an independent contract"
            )
    # S446: Global state mutation
    _global_kws = ("global_", "state_", "cache_", "config_", "registry_", "singleton_", "shared_")
    _is_mutator = any(kw in _prim.name.lower() for kw in ("set_", "update_", "reset_", "clear_", "flush_", "init_", "register_"))
    if _is_mutator and any(kw in _prim.name.lower() for kw in _global_kws):
        lines.append(
            f"\nglobal state mutation: {_prim.name} modifies shared state"
            f" — concurrent callers see each other's side effects; isolate state before refactoring"
        )
    # S475: Generator/iterator function
    _gen_prefixes = ("iter_", "generate_", "stream_", "yield_", "produce_", "enumerate_")
    if any(_prim.name.lower().startswith(p) for p in _gen_prefixes):
        _callers_gen = graph.callers_of(_prim.id)
        if _callers_gen:
            lines.append(
                f"\ngenerator function: {_prim.name} is a lazy iterator"
                f" — callers must iterate or convert to list; replacing with list changes memory semantics"
            )
    return lines


def _signals_fn_oop_fn_patterns(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S428/S434/S440/S446/S451/S475: abstract, factory, callback, global-state, protocol, generator signals."""
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        return []
    return _signals_fn_oop_abstract_proto(graph, _seed_syms) + _signals_fn_oop_behavioral(graph, _prim)


def _signals_fn_oop(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S428/S434/S440/S446/S451/S475/S576/S630/S690: OOP and design-pattern signals (dispatcher)."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        lines += _signals_fn_oop_class_prop(graph, _seed_syms, token_count, max_tokens)
        return lines
    lines += _signals_fn_oop_fn_patterns(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_oop_class_prop(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_signature_return(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S422/S513/S546/S552: return-type signals (union, generator, optional, async)."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        return lines
    sig = _prim.signature or ""
    # S422: Multiple return type hints
    if any(p in sig for p in ("Union[", "Optional[", " | None", "None | ")):
        lines.append(
            f"\nunion return type: {_prim.name} returns Optional/Union type"
            f" — callers must handle None/variant; document when None is returned and why"
        )
    # S513: Generator function (return hint)
    _gen_hints = ("-> iterator", "-> generator", "-> iterable", "-> asynciterator", "-> asyncgenerator")
    if any(h in sig.lower() for h in _gen_hints):
        lines.append(
            f"\ngenerator function: {_prim.name} returns a lazy iterator"
            f" — callers must iterate or explicitly close it; converting to list changes memory + latency profile"
        )
    # S546: Optional return type
    _has_optional546 = (
        "Optional[" in sig
        or ("-> None" not in sig and "| None" in sig and "->" in sig)
    )
    if _has_optional546:
        lines.append(
            f"\noptional return: {_prim.name} returns Optional/None-typed result"
            f" — every call site must handle the None case; missing checks cause AttributeError at runtime"
        )
    # S552: Async function
    _pre_paren552 = sig.split("(", 1)[0] if "(" in sig else sig
    if "async" in _pre_paren552.split():
        lines.append(
            f"\nasync function: {_prim.name} is async — every caller must await it"
            f" or run via asyncio.run(); forgetting await silently returns a coroutine object"
        )
    return lines


def _signals_fn_signature_params(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S457/S531/S564/S581/S702: parameter-shape signals (high arity, mutable default, variadic)."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        return lines
    sig = _prim.signature or ""
    # S457: High parameter count
    _raw = sig.split("(", 1)[-1].rstrip("):")
    _params = [p.strip() for p in _raw.split(",") if p.strip() and p.strip() not in ("self", "cls", "*", "**kwargs", "*args")]
    if len(_params) >= 6:
        lines.append(
            f"\nhigh parameter count: {_prim.name} takes {len(_params)} parameters"
            f" — hard to call and test; consider a parameter object or splitting the function"
        )
    # S531: Mutable default argument
    if not _is_test_file(_prim.file_path) and sig:
        _param531 = sig.split("(", 1)[1].rsplit(")", 1)[0] if "(" in sig else ""
        _mutable_markers531 = ("=[]", "={}", "=set()", "=list()", "=dict()")
        if any(m in _param531.replace(" ", "") for m in _mutable_markers531):
            lines.append(
                f"\nmutable default: {_prim.name} uses a mutable default argument"
                f" — shared across all calls; mutations in one call silently affect future calls"
            )
    # S564: Variadic function (*args/**kwargs)
    if not _is_test_file(_prim.file_path) and sig:
        _param564 = sig.split("(", 1)[1].rsplit(")", 1)[0] if "(" in sig else ""
        if "*args" in _param564 or "**kwargs" in _param564:
            lines.append(
                f"\nvariadic function: {_prim.name} accepts {'*args' if '*args' in _param564 else ''}"
                f"{'/**kwargs' if '**kwargs' in _param564 else ''} — callers bypass type checking;"
                f" add specific overloads or narrower signatures when possible"
            )
    # S581: Many parameters (6+)
    if not _is_test_file(_prim.file_path) and sig:
        _paren581 = sig.find("(")
        _rparen581 = sig.rfind(")")
        if _paren581 != -1 and _rparen581 != -1:
            _params_str581 = sig[_paren581 + 1:_rparen581].strip()
            if _params_str581:
                _param_count581 = len([
                    p for p in _params_str581.split(",")
                    if p.strip() and p.strip() not in ("self", "cls")
                ])
                if _param_count581 >= 6:
                    lines.append(
                        f"\nmany parameters: {_prim.name} has {_param_count581} parameters"
                        f" — wide signatures reduce readability; consider a config object or named tuple"
                    )
    # S702: High arity (5+)
    if not _is_test_file(_prim.file_path) and sig:
        _param_str702 = sig.split("(", 1)[-1].split(")", 1)[0] if "(" in sig else ""
        _no_self702 = _param_str702.replace("self,", "").replace("self", "").strip()
        _arity702 = (
            len([p for p in _no_self702.split(",") if p.strip()])
            if _no_self702 else 0
        )
        if _arity702 >= 5:
            lines.append(
                f"\nhigh arity: {_prim.name} has {_arity702} parameters"
                f" — consider grouping parameters into a config/options object"
            )
    return lines


def _signals_fn_signature_kind(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S464/S508/S519: function-kind signals (property method, untyped export, callback/handler)."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if not _prim:
        return lines
    sig = _prim.signature or ""
    # S464: Property method
    _is_prop = (
        "@property" in sig.lower() or ".setter" in sig.lower() or ".deleter" in sig.lower()
        or (_prim.name.startswith("get_") and _prim.parent_id and _prim.parent_id in graph.symbols)
    )
    if not _is_prop:
        _callers464 = graph.callers_of(_prim.id)
        _is_prop = _prim.name.startswith(("get_", "set_", "is_", "has_")) and len(_callers464) >= 3
    if _is_prop:
        lines.append(
            f"\nproperty method: {_prim.name} is a getter/setter"
            f" — attribute-style callers are invisible to call-edge analysis; grep for usages before renaming"
        )
    # S508: Untyped exported function
    if _prim.exported and not _is_test_file(_prim.file_path) and "->" not in sig:
        _callers508 = list(graph.callers_of(_prim.id))
        _raw_callers = getattr(graph, "_callers", {}).get(_prim.id, [])
        if len(_callers508) + len(_raw_callers) >= 3:
            lines.append(
                f"\nuntyped export: {_prim.name} is exported with {len(_callers508) + len(_raw_callers)} caller(s)"
                f" but has no return type annotation — callers rely on implicit return type"
            )
    # S519: Callback/handler function (name-based)
    if not _is_test_file(_prim.file_path):
        _name519 = _prim.name.lower()
        _is_cb519 = (
            any(_name519.startswith(p) for p in ("on_", "handle_"))
            or any(_name519.endswith(s) for s in ("_handler", "_callback", "_cb", "_listener"))
        )
        if _is_cb519:
            lines.append(
                f"\ncallback/handler: {_prim.name} is named as an event handler"
                f" — called indirectly via event dispatch; static call graph may miss callers"
            )
    return lines


def _signals_fn_signature(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S422/S457/S464/S508/S513/S519/S531/S546/S552/S564/S581/S702: signature and type signals."""
    lines: list[str] = []
    lines += _signals_fn_signature_return(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_signature_params(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_signature_kind(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_conventions_naming(
    graph: "Tempo", _seed_syms: list, _prim: object,
) -> list[str]:
    """S470/S558/S593/S654: name-quality signals (deprecated, builtin shadow, generic name)."""
    return []  # PRUNED: name quality — agent can see the name in the signature
    lines: list[str] = []
    # S470: Deprecated function
    _dep_markers = ("deprecated", "legacy", "old_", "_old", "_deprecated", "_legacy", "compat_")
    if any(m in _prim.name.lower() for m in _dep_markers):
        _callers = graph.callers_of(_prim.id)
        lines.append(
            f"\ndeprecated function: {_prim.name} is marked deprecated/legacy"
            f" with {len(_callers)} active caller(s)"
            f" — verify migration to replacement is complete before removing"
        )
    # S558: Deprecated name
    _dep_markers558 = ("deprecated", "old_", "_old", "legacy", "_v1", "v1_", "obsolete")
    _lname558 = _seed_syms[0].name.lower()
    if any(m in _lname558 for m in _dep_markers558):
        lines.append(
            f"\ndeprecated name: {_seed_syms[0].name} contains a deprecation marker"
            f" — callers are accruing technical debt; migrate to the replacement before removal"
        )
    # S593: Builtin shadow
    if (
        _seed_syms[0].name in _BUILTINS593
        and _seed_syms[0].kind.value in ("function", "method", "class")
        and not _is_test_file(_seed_syms[0].file_path)
    ):
        lines.append(
            f"\nbuiltin shadow: {_seed_syms[0].name} shadows a Python builtin"
            f" — callers that expect the builtin will silently use this instead; rename to avoid confusion"
        )
    # S654: Generic name
    if (
        not _is_test_file(_seed_syms[0].file_path)
        and _seed_syms[0].kind.value in ("function", "method", "class")
        and _seed_syms[0].name.lower() in _GENERIC_NAMES654
    ):
        lines.append(
            f"\ngeneric name: '{_seed_syms[0].name}' is a common, non-specific symbol name"
            f" — hard to grep and refactor; consider a domain-specific name that signals intent"
        )
    return lines


def _signals_fn_conventions_behavior(
    graph: "Tempo", _seed_syms: list,
) -> list[str]:
    """S476/S482/S488/S494: thread-safety, mixin, operator overload, and factory signals."""
    lines: list[str] = []
    # S476/S482: Thread-safe and mixin (fn/method only)
    _prim_fn = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if _prim_fn:
        _lock_markers = ("_locked", "_synchronized", "_atomic", "_thread_safe", "_safe", "with_lock_")
        _lock_callee_names = {"acquire", "release", "lock", "unlock", "synchronized"}
        _is_thread_safe = any(m in _prim_fn.name.lower() for m in _lock_markers)
        if not _is_thread_safe:
            _callees_lock = [
                c.name for c in graph.callees_of(_prim_fn.id)
                if c.name.lower() in _lock_callee_names
            ]
            _is_thread_safe = bool(_callees_lock)
        if _is_thread_safe:
            lines.append(
                f"\nthread-safe: {_prim_fn.name} uses locking or synchronization"
                f" — changes must preserve the lock invariants; test under concurrency before merging"
            )
        # S482: Mixin class method
        _mixin_class = next(
            (s for s in graph.symbols.values()
             if s.kind.value == "class" and s.file_path == _prim_fn.file_path and "mixin" in s.name.lower()),
            None,
        )
        if _mixin_class:
            _users = graph.importers_of(_prim_fn.file_path)
            lines.append(
                f"\nmixin method: {_prim_fn.name} lives in {_mixin_class.name}"
                f" — changes propagate to all {len(_users)} consumer(s) that include this mixin;"
                f" verify super() chains are preserved"
            )
    # S488: Operator overload (class seeds)
    _prim_cls = next((s for s in _seed_syms if s.kind.value == "class"), None)
    if _prim_cls:
        _op_names = {"__eq__", "__hash__", "__lt__", "__le__", "__gt__", "__ge__",
                     "__add__", "__sub__", "__mul__", "__truediv__", "__mod__",
                     "__radd__", "__rsub__", "__rmul__"}
        _ops = [s for s in graph.symbols.values()
                if s.file_path == _prim_cls.file_path and s.kind.value == "method" and s.name in _op_names]
        if _ops:
            lines.append(
                f"\noperator overloads: {_prim_cls.name} defines {', '.join(s.name for s in _ops[:4])}"
                f" — changing operator semantics affects dicts, sets, and sorted() behavior;"
                f" verify all collection usage is compatible"
            )
    # S494: Class factory function
    _factory_pfx = ("make_", "create_", "build_", "new_", "from_", "get_or_create_")
    _prim_fac = next((s for s in _seed_syms if s.kind.value in ("function", "method")), None)
    if _prim_fac and any(_prim_fac.name.lower().startswith(p) for p in _factory_pfx):
        _callers_fac = graph.callers_of(_prim_fac.id)
        if _callers_fac:
            lines.append(
                f"\nfactory function: {_prim_fac.name} is a factory with {len(_callers_fac)} caller(s)"
                f" — changing return type or validation silently breaks all construction sites"
            )
    return lines


def _signals_fn_conventions_scope(
    graph: "Tempo", _seed_syms: list,
) -> list[str]:
    """S537/S636: module scope and visibility signals (private module export, init-file symbol)."""
    lines: list[str] = []
    # S537: Private module export
    _prim_any = _seed_syms[0]
    if _prim_any.exported and not _is_test_file(_prim_any.file_path):
        _fp537 = _prim_any.file_path.replace("\\", "/")
        _basename537 = _fp537.rsplit("/", 1)[-1]
        _is_private537 = (
            _basename537.startswith("_") and _basename537 != "__init__.py"
        ) or "/_" in _fp537
        if _is_private537:
            lines.append(
                f"\nprivate module: {_prim_any.name} is exported from a private file"
                f" ({_basename537}) — public symbol in private module is confusing; move or re-export via __init__.py"
            )
    # S636: Init-file symbol
    _prim636 = _seed_syms[0]
    if (
        not _is_test_file(_prim636.file_path)
        and (_prim636.file_path.endswith("/__init__.py") or _prim636.file_path == "__init__.py")
    ):
        _importers636 = graph.importers_of(_prim636.file_path)
        lines.append(
            f"\ninit-file symbol: {_prim636.name} is in __init__.py ({len(_importers636)} package importer(s))"
            f" — part of the package public API; changes affect all package consumers"
        )
    return lines


def _signals_fn_conventions(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S470/S476/S482/S488/S494/S537/S558/S593/S636/S654: naming and convention signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = next((s for s in _seed_syms if s.kind.value in ("function", "method", "class")), None)
    if not _prim:
        return lines
    lines += _signals_fn_conventions_naming(graph, _seed_syms, _prim)
    lines += _signals_fn_conventions_behavior(graph, _seed_syms)
    lines += _signals_fn_conventions_scope(graph, _seed_syms)
    return lines


def _signals_fn_quality_a(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S281/S350/S501/S525/S587/S599: caller/usage/purity quality signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    # S350: Orphaned symbol
    if _prim.kind.value in ("function", "method", "class"):
        _callers = graph.callers_of(_prim.id)
        _importers = list(graph.importers_of(_prim.file_path))
        if not _callers and not _importers and not _prim.name.startswith("_"):
            lines.append(
                f"\norphaned: {_prim.name} has 0 callers and the file is not imported"
                f" — may be unreachable dead code; verify before modifying"
            )
    # S281: Undocumented public function
    if (_prim.kind.value in ("function", "method") and _prim.exported and not _is_test_file(_prim.file_path)):
        _sig = _prim.signature or ""
        if '"""' not in _sig and "'''" not in _sig:
            _ext_callers = [c for c in graph.callers_of(_prim.id) if c.file_path != _prim.file_path]
            if len(_ext_callers) >= 3:
                lines.append(
                    f"\nundocumented: {_prim.name} is public with {len(_ext_callers)} callers"
                    f" but has no docstring — callers must infer behavior from code"
                )
    # S501: Pure function
    _prim_fn501 = next((s for s in _seed_syms if s.kind.value == "function"), None)
    if _prim_fn501 and not _prim_fn501.parent_id:
        _callees501 = graph.callees_of(_prim_fn501.id)
        _has_callers501 = bool(getattr(graph, "_callers", {}).get(_prim_fn501.id))
        if not _callees501 and _has_callers501:
            lines.append(
                f"\npure function: {_prim_fn501.name} makes no outbound calls"
                f" — treat as a pure transformation; any side-effect introduced is a silent contract break"
            )
    # S525: Name collision (defined in 3+ non-test files)
    if not _is_test_file(_prim.file_path):
        _all525 = [s for s in graph.find_symbol(_prim.name) if not _is_test_file(s.file_path)]
        if len(_all525) >= 3:
            _coll_files525 = [s.file_path.rsplit("/", 1)[-1] for s in _all525[:3]]
            lines.append(
                f"\nname collision: {_prim.name} is defined in {len(_all525)} source files"
                f" ({', '.join(_coll_files525)})"
                f" — wildcard imports or same-name references may resolve to the wrong definition"
            )
    # S587: Sole caller
    if _prim.kind.value in ("function", "method") and not _is_test_file(_prim.file_path):
        _callers587 = graph.callers_of(_prim.id)
        if len(_callers587) == 1:
            _sole587 = _callers587[0]
            lines.append(
                f"\nsole caller: {_prim.name} is only called from {_sole587.name}"
                f" — consider inlining or making private; not a reusable API"
            )
    # S599: No callers
    if (
        _prim.kind.value in ("function", "method")
        and not _is_test_file(_prim.file_path)
        and not graph.callers_of(_prim.id)
    ):
        lines.append(
            f"\nno callers: {_prim.name} has zero callers in the graph"
            f" — entry point, dead code, or dynamically dispatched; verify intent before removing"
        )
    return lines


def _signals_fn_quality_b(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S600/S606/S612/S618/S624/S642: deprecation/size/scope/structure quality signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    # S600: Deprecated callers
    if _prim.kind.value in ("function", "method", "class") and not _is_test_file(_prim.file_path):
        _callers600 = graph.callers_of(_prim.id)
        _legacy_markers600 = ("legacy", "deprecated", "compat", "old_", "_old", "v1", "backport")
        if _callers600 and all(
            any(m in c.file_path.lower() for m in _legacy_markers600)
            for c in _callers600
        ):
            lines.append(
                f"\ndeprecated callers: all {len(_callers600)} caller(s) of {_prim.name}"
                f" are in legacy/compat files — symbol may be on a deprecation path; mark or schedule removal"
            )
    # S606: Large symbol (50+ lines)
    if not _is_test_file(_prim.file_path) and _prim.line_count >= 50:
        lines.append(
            f"\nlarge symbol: {_prim.name} spans {_prim.line_count} lines"
            f" — long symbols accumulate unrelated logic; consider splitting into smaller units"
        )
    # S612: Widely imported file (10+ importers)
    if not _is_test_file(_prim.file_path):
        _importers612 = graph.importers_of(_prim.file_path)
        if len(_importers612) >= 10:
            lines.append(
                f"\nwidely imported: {_prim.file_path.rsplit('/', 1)[-1]} has"
                f" {len(_importers612)} importers — treat as stable API; breakage here is wide-reaching"
            )
    # S618: Single-file consumer (exported, 1 non-test consumer file)
    if (
        _prim.kind.value in ("function", "method", "class")
        and _prim.exported
        and not _is_test_file(_prim.file_path)
    ):
        _callers618 = graph.callers_of(_prim.id)
        _caller_files618 = {c.file_path for c in _callers618 if not _is_test_file(c.file_path)}
        if len(_caller_files618) == 1:
            lines.append(
                f"\nsingle-file consumer: {_prim.name} is exported but only used in"
                f" {next(iter(_caller_files618)).rsplit('/', 1)[-1]}"
                f" — consider making private; export contract is not exercised elsewhere"
            )
    # S624: Leaf function (3+ callers, 0 callees)
    if (
        not _is_test_file(_prim.file_path)
        and _prim.kind.value in ("function", "method")
    ):
        _callers624 = graph.callers_of(_prim.id)
        _callees624 = graph.callees_of(_prim.id)
        if len(_callers624) >= 3 and not _callees624:
            lines.append(
                f"\nleaf function: {_prim.name} has {len(_callers624)} callers and no callees"
                f" — terminal node; safe to refactor in isolation; high-caller leaves suit inlining"
            )
    # S642: Bridge node (3+ callers AND 3+ callees)
    if (
        not _is_test_file(_prim.file_path)
        and _prim.kind.value in ("function", "method")
    ):
        _callers642 = graph.callers_of(_prim.id)
        _callees642 = graph.callees_of(_prim.id)
        if len(_callers642) >= 3 and len(_callees642) >= 3:
            lines.append(
                f"\nbridge node: {_prim.name} has {len(_callers642)} callers"
                f" and {len(_callees642)} callees"
                f" — cross-layer connector; changes cascade upstream AND downstream"
            )
    return lines


def _signals_fn_quality_c(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S648/S660/S666/S672/S678/S696: naming/density/fan-out/hotspot quality signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    # S648: Name collision (defined in multiple non-test files)
    if (
        not _is_test_file(_prim.file_path)
        and _prim.kind.value in ("function", "method", "class")
    ):
        _same_name648 = [
            s for s in graph.symbols.values()
            if s.name == _prim.name
            and s.file_path != _prim.file_path
            and not _is_test_file(s.file_path)
            and s.kind.value in ("function", "method", "class")
        ]
        if _same_name648:
            _collision_files648 = ", ".join(
                s.file_path.rsplit("/", 1)[-1] for s in _same_name648[:3]
            )
            lines.append(
                f"\nname collision: {_prim.name} is also defined in {_collision_files648}"
                f" — same name in multiple files; refactoring risks touching the wrong definition"
            )
    # S660: Dense file (50+ top-level symbols)
    if not _is_test_file(_prim.file_path):
        _file_sym_count660 = len([
            s for s in graph.symbols.values()
            if s.file_path == _prim.file_path and s.parent_id is None
        ])
        if _file_sym_count660 >= 50:
            lines.append(
                f"\ndense file: {_prim.file_path.rsplit('/', 1)[-1]} contains"
                f" {_file_sym_count660} top-level symbols"
                f" — monolith file; split by concern before adding more symbols"
            )
    # S666: High fan-out (5+ callees)
    if not _is_test_file(_prim.file_path):
        _callees666 = graph.callees_of(_prim.id)
        if len(_callees666) >= 5:
            lines.append(
                f"\nhigh fan-out: {_prim.name} calls {len(_callees666)} symbols"
                f" — high outgoing coupling; changes to callees will cascade here"
            )
    # S672: Duplicated name (3+ files, top-level)
    if not _is_test_file(_prim.file_path):
        _dup_count672 = sum(
            1 for s in graph.symbols.values()
            if s.name == _prim.name
            and s.parent_id is None
            and not _is_test_file(s.file_path)
        )
        if _dup_count672 >= 3:
            lines.append(
                f"\nduplicated name: '{_prim.name}' defined in {_dup_count672} files"
                f" — copy-paste drift; callers may resolve to the wrong definition"
            )
    # S678: Long function (40+ lines)
    if (
        not _is_test_file(_prim.file_path)
        and _prim.kind.value in ("function", "method")
        and _prim.line_count >= 40
    ):
        lines.append(
            f"\nlong function: {_prim.name} is {_prim.line_count} lines"
            f" — consider extracting sub-functions to reduce cognitive load"
        )
    # S696: Hotspot caller
    if not _is_test_file(_prim.file_path):
        _callers696 = [
            c for c in graph.callers_of(_prim.id)
            if c.file_path != _prim.file_path
        ]
        _hot_callers696 = [
            c for c in _callers696
            if len([
                cc for cc in graph.callers_of(c.id)
                if cc.file_path != c.file_path
            ]) >= 5
        ]
        if _hot_callers696:
            lines.append(
                f"\nhotspot caller: {_hot_callers696[0].name} (a hotspot) calls {_prim.name}"
                f" — changes propagate through a high-traffic path; extra caution needed"
            )
    return lines


def _signals_fn_quality(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S281/S350/S501/S525/S587/S599/S600/S606/S612/S618/S624/S642/S648/S660/S666/S672/S678/S696: quality signals."""
    lines: list[str] = []
    lines += _signals_fn_quality_a(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_quality_b(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_quality_c(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_props_a_class(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S708/S714/S720/S726/S732: class/test/deprecation/hierarchy property signals."""
    lines: list[str] = []
    # S708: Widely-used class — focused method's parent class is imported in 5+ files.
    # A method inside a widely-imported class has amplified blast radius;
    # even a small signature change affects every file that instantiates or inherits the class.
    if _seed_syms and token_count < max_tokens - 30:
        _prim708 = _seed_syms[0]
        if (
            not _is_test_file(_prim708.file_path)
            and _prim708.kind.value == "method"
            and _prim708.parent_id is not None
        ):
            _parent708 = graph.symbols.get(_prim708.parent_id)
            if _parent708 is not None:
                _class_importers708 = [
                    f for f in graph.importers_of(_parent708.file_path)
                    if f != _parent708.file_path
                ]
                if len(_class_importers708) >= 5:
                    lines.append(
                        f"\nwidely-used class: {_parent708.name} is imported by"
                        f" {len(_class_importers708)} files"
                        f" — method changes affect all class consumers; check all call sites"
                    )

    # S714: Query resolves to test file — the focused symbol lives in a test file, not source.
    # Agents querying test files directly may miss the real implementation; test files
    # describe expected behavior — redirect to the source counterpart for implementation details.
    if _seed_syms and token_count < max_tokens - 30:
        _prim714 = _seed_syms[0]
        if _is_test_file(_prim714.file_path):
            _src714 = _prim714.file_path.replace("\\", "/").rsplit("/", 1)[-1]
            lines.append(
                f"\nquery is a test file: {_src714} is a test file"
                f" — look at the source counterpart for implementation details"
            )

    # S720: Deprecated caller — PRUNED: name quality — guesses deprecation from name patterns
    if False:  # PRUNED: name quality
        if _seed_syms and token_count < max_tokens - 30:
            _prim720 = _seed_syms[0]
            if not _is_test_file(_prim720.file_path):
                _callers720 = graph.callers_of(_prim720.id)
                _dep_callers720 = [
                    c for c in _callers720
                    if any(kw in c.name.lower() for kw in ("old", "legacy", "deprecated"))
                ]
                if _dep_callers720:
                    _dc_names720 = ", ".join(c.name for c in _dep_callers720[:2])
                    lines.append(
                        f"\ndeprecated caller: {_prim720.name} is called by deprecated code ({_dc_names720})"
                        f" — this symbol may be on a removal path; check if it should be migrated"
                    )

    # S726: Multiple inheritance — the focused class inherits from 2 or more base classes.
    # Multiple inheritance creates complex MRO chains and is a common source of subtle bugs;
    # method resolution order surprises are hard to debug and test.
    if _seed_syms and token_count < max_tokens - 30:
        _prim726 = _seed_syms[0]
        if (
            _prim726.kind.value == "class"
            and not _is_test_file(_prim726.file_path)
            and _prim726.signature is not None
        ):
            _sig726 = _prim726.signature
            _paren_start726 = _sig726.find("(")
            _paren_end726 = _sig726.find(")")
            if _paren_start726 != -1 and _paren_end726 != -1:
                _bases726 = _sig726[_paren_start726 + 1:_paren_end726].strip()
                if _bases726.count(",") >= 1:
                    lines.append(
                        f"\nmultiple inheritance: {_prim726.name} inherits from multiple base classes"
                        f" ({_bases726}) — complex MRO; verify method resolution order is intentional"
                    )

    # S732: Large class — focused class has 10 or more methods and properties.
    # Large classes often violate single responsibility; they become maintenance burdens
    # and are hard to test in isolation — consider splitting into focused collaborators.
    if _seed_syms and token_count < max_tokens - 30:
        _prim732 = _seed_syms[0]
        if (
            _prim732.kind.value == "class"
            and not _is_test_file(_prim732.file_path)
        ):
            _children732 = graph.children_of(_prim732.id)
            _methods732 = [c for c in _children732 if c.kind.value in ("method", "property", "function")]
            if len(_methods732) >= 10:
                lines.append(
                    f"\nlarge class: {_prim732.name} has {len(_methods732)} methods/properties"
                    f" — god class candidate; consider splitting into focused collaborators"
                )

    return lines


def _signals_fn_props_a_module(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S738/S744/S750/S756/S762: module/visibility/async/classmethod property signals."""
    lines: list[str] = []
    # S738: Module-level variable — focused symbol is a global variable/constant imported widely.
    # Mutable module-level state shared across many files creates hidden coupling;
    # any change to the value ripples to every importer without a clear interface contract.
    if _seed_syms and token_count < max_tokens - 30:
        _prim738 = _seed_syms[0]
        if (
            _prim738.kind.value in ("variable", "constant")
            and not _is_test_file(_prim738.file_path)
            and _prim738.parent_id is None
        ):
            _file_importers738 = [
                f for f in graph.importers_of(_prim738.file_path)
                if f != _prim738.file_path
            ]
            if len(_file_importers738) >= 3:
                lines.append(
                    f"\nmodule-level variable: {_prim738.name} is a global variable in a file"
                    f" imported by {len(_file_importers738)} modules"
                    f" — shared mutable state can cause hidden coupling across all importers"
                )

    # S744: Test-only importer — focused symbol's file is imported exclusively by test files.
    # A source file imported only by tests is unreachable from production code; the file
    # may be dead, a test-only utility, or missing a production integration point.
    if _seed_syms and token_count < max_tokens - 30:
        _prim744 = _seed_syms[0]
        if not _is_test_file(_prim744.file_path):
            _importers744 = graph.importers_of(_prim744.file_path)
            if _importers744 and all(_is_test_file(f) for f in _importers744):
                lines.append(
                    f"\ntest-only importer: {_prim744.file_path.replace('\\\\', '/').rsplit('/', 1)[-1]}"
                    f" is imported only by test files"
                    f" — production code doesn't use this file; verify it's not dead"
                )

    # S750: No docstring and widely called — focused function/method lacks a docstring but has many callers.
    # Widely-called functions without documentation force every caller to read the source;
    # adding a docstring reduces onboarding friction and prevents misuse.
    if _seed_syms and token_count < max_tokens - 30:
        _prim750 = _seed_syms[0]
        if (
            _prim750.kind.value in ("function", "method")
            and not _is_test_file(_prim750.file_path)
            and not _prim750.doc
        ):
            _callers750 = [c for c in graph.callers_of(_prim750.id) if c.file_path != _prim750.file_path]
            if len(_callers750) >= 5:
                lines.append(
                    f"\nno docstring: {_prim750.name} is called from {len(_callers750)} files but has no docstring"
                    f" — widely-used function without docs; add a docstring to reduce caller friction"
                )

    # S756: Classmethod focus — focused method takes cls as first param (classmethod convention).
    # Classmethods share state through the class itself rather than through instances;
    # changes can affect all instances and subclass behavior simultaneously.
    if _seed_syms and token_count < max_tokens - 30:
        _prim756 = _seed_syms[0]
        if (
            _prim756.kind.value in ("function", "method", "classmethod")
            and _prim756.parent_id is not None
            and not _is_test_file(_prim756.file_path)
            and _prim756.signature is not None
            and ("(cls," in _prim756.signature or _prim756.signature.endswith("(cls)") or "(cls):" in _prim756.signature)
        ):
            lines.append(
                f"\nclassmethod focus: {_prim756.name} takes cls as first parameter — operates on class-level state;"
                f" changes affect all instances and subclasses simultaneously"
            )

    # S762: Async focus — focused function is defined with async def (async execution boundary).
    # Async functions introduce an execution boundary requiring await at every call site;
    # adding or removing async changes callers — they must add/remove await accordingly.
    if _seed_syms and token_count < max_tokens - 30:
        _prim762 = _seed_syms[0]
        if (
            _prim762.kind.value in ("function", "method")
            and not _is_test_file(_prim762.file_path)
            and _prim762.signature is not None
            and _prim762.signature.lstrip().startswith("async def")
        ):
            lines.append(
                f"\nasync focus: {_prim762.name} is an async function — callers must await it;"
                f" adding or removing async changes all call sites"
            )

    return lines


def _signals_fn_props_a_scope_isolation(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S768/S774/S780: exported-uncalled, near-isolated, multi-file symbol signals."""
    lines: list[str] = []
    # S768: Exported but uncalled — focused symbol is exported but has no cross-file callers.
    # A public symbol with no callers may be dead code, a pending API, or only consumed
    # via dynamic access (getattr, plugin loading) — worth verifying before removing.
    if _seed_syms and token_count < max_tokens - 30:
        _prim768 = _seed_syms[0]
        if (
            _prim768.exported
            and not _is_test_file(_prim768.file_path)
            and _prim768.parent_id is None
        ):
            _cross768 = [c for c in graph.callers_of(_prim768.id) if c.file_path != _prim768.file_path]
            if not _cross768:
                lines.append(
                    f"\nexported but uncalled: {_prim768.name} is public but has no cross-file callers"
                    f" — may be dead code or a pending API; verify before removing"
                )

    # S774: Near-isolated symbol — focused symbol's file has only 2 top-level symbols
    # and the sibling has no cross-file callers either.
    # Two-symbol files where both symbols are uncalled are strong deletion candidates.
    if _seed_syms and token_count < max_tokens - 30:
        _prim774 = _seed_syms[0]
        if not _is_test_file(_prim774.file_path):
            _file774 = graph.files.get(_prim774.file_path)
            if _file774:
                _top_syms774 = [
                    graph.symbols[sid] for sid in _file774.symbols
                    if sid in graph.symbols and graph.symbols[sid].parent_id is None
                ]
                if len(_top_syms774) == 2:
                    _sibling774 = next(
                        (s for s in _top_syms774 if s.id != _prim774.id), None
                    )
                    if _sibling774:
                        _sibling_cross774 = [
                            c for c in graph.callers_of(_sibling774.id)
                            if c.file_path != _prim774.file_path
                        ]
                        if not _sibling_cross774:
                            lines.append(
                                f"\nnear-isolated: {_prim774.file_path.rsplit('/', 1)[-1]} has only"
                                f" {_prim774.name} and {_sibling774.name} — sibling has no callers;"
                                f" consider removing the whole file"
                            )

    # S780: Multi-file symbol — focused symbol name appears in 3+ distinct files.
    # When a name is reused across many files, callers may invoke the wrong implementation;
    # resolving the query returns the most-relevant match but ambiguity is a refactoring risk.
    if _seed_syms and token_count < max_tokens - 30:
        _prim780 = _seed_syms[0]
        _same_name780 = set(
            s.file_path for s in graph.symbols.values()
            if s.name == _prim780.name and s.file_path != _prim780.file_path
        )
        if len(_same_name780) >= 2:
            lines.append(
                f"\nmulti-file symbol: {_prim780.name} appears in {len(_same_name780) + 1} files"
                f" — name collision risk; callers may import the wrong implementation"
            )

    return lines


def _signals_fn_props_a_scope_conventions(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S786/S792/S798: dunder method, long function, underscore-but-exported signals."""
    lines: list[str] = []
    # S786: Dunder method focus — PRUNED: taxonomic label — agent sees __dunder__ in signature
    if False:  # PRUNED: taxonomic label
        if _seed_syms and token_count < max_tokens - 30:
            _prim786 = _seed_syms[0]
            if (
                _prim786.kind.value in ("function", "method")
                and _prim786.parent_id is not None
                and _prim786.name.startswith("__") and _prim786.name.endswith("__")
                and not _is_test_file(_prim786.file_path)
            ):
                lines.append(
                    f"\ndunder method: {_prim786.name} is a Python protocol method"
                    f" — changes affect built-in operations (iteration, comparison, context managers, etc.)"
                )

    # S792: Long function — focused function spans 50+ lines.
    # Functions longer than 50 lines are hard to read in one mental pass;
    # they often mix concerns and are difficult to test or refactor safely.
    if _seed_syms and token_count < max_tokens - 30:
        _prim792 = _seed_syms[0]
        if (
            _prim792.kind.value in ("function", "method")
            and not _is_test_file(_prim792.file_path)
            and _prim792.line_start is not None
            and _prim792.line_end is not None
            and _prim792.line_end - _prim792.line_start >= 50
        ):
            _len792 = _prim792.line_end - _prim792.line_start + 1
            lines.append(
                f"\nlong function: {_prim792.name} is {_len792} lines"
                f" — difficult to read and test; consider splitting into focused sub-functions"
            )

    # S798: Underscore-prefixed but exported — focused symbol has _ prefix suggesting private
    # but is accessible from other files (callers exist outside its own file).
    # Underscore-prefixed symbols are conventionally private; external callers violate
    # the intended encapsulation boundary.
    if _seed_syms and token_count < max_tokens - 30:
        _prim798 = _seed_syms[0]
        if (
            _prim798.name.startswith("_") and not _prim798.name.startswith("__")
            and not _is_test_file(_prim798.file_path)
        ):
            _ext_callers798 = [c for c in graph.callers_of(_prim798.id) if c.file_path != _prim798.file_path]
            if _ext_callers798:
                lines.append(
                    f"\nprivate but called externally: {_prim798.name} has _ prefix (private convention)"
                    f" but is called from {len(_ext_callers798)} external file(s)"
                    f" — encapsulation violation; consider making it public or restricting callers"
                )

    return lines


def _signals_fn_props_a_scope(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S768/S774/S780/S786/S792/S798: export/isolation/naming/dunder/long property signals."""
    return (
        _signals_fn_props_a_scope_isolation(graph, _seed_syms, token_count, max_tokens)
        + _signals_fn_props_a_scope_conventions(graph, _seed_syms, token_count, max_tokens)
    )


def _signals_fn_focus_props_a(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S708–S803: focus property signals (dispatcher to sub-helpers)."""
    lines: list[str] = []
    lines += _signals_fn_props_a_class(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_a_module(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_a_scope(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_props_b_entry_fn_type(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S804–S852 fn-type subset: entry/deprecated/zero-arg/generator/operator signals."""
    lines: list[str] = []
    # S804: Entry point focus — focused symbol is a well-known entry point name.
    if _seed_syms and token_count < max_tokens - 30:
        _prim804 = _seed_syms[0]
        _ep_names804 = frozenset(("main", "run", "start", "app", "entry", "create_app", "cli", "serve"))
        if (
            _prim804.kind.value in ("function", "method")
            and _prim804.parent_id is None
            and _prim804.name in _ep_names804
            and not _is_test_file(_prim804.file_path)
        ):
            lines.append(
                f"\nentry point focus: {_prim804.name} is a well-known entry point"
                f" — changes affect startup sequencing and all initialization logic"
            )

    # S810: Deprecated symbol focus — PRUNED: name quality — agent sees docstring
    if False:  # PRUNED: name quality
        if _seed_syms and token_count < max_tokens - 30:
            _prim810 = _seed_syms[0]
            _doc810 = (_prim810.doc or "").lower()
            if "deprecated" in _doc810 and not _is_test_file(_prim810.file_path):
                lines.append(
                    f"\ndeprecated symbol: {_prim810.name} has a deprecation notice in its docstring"
                    f" — verify all callers have migrated to the replacement before modifying"
                )

    # S816: Zero-argument function focus — focused function takes no parameters at all.
    if _seed_syms and token_count < max_tokens - 30:
        _prim816 = _seed_syms[0]
        if (
            _prim816.kind.value in ("function", "method")
            and _prim816.parent_id is None
            and not _is_test_file(_prim816.file_path)
        ):
            # Use stored signature (relative file_path, so linecache would fail)
            _sig816 = (_prim816.signature or "").strip()
            if _sig816 and _sig816.startswith("def ") and ("()" in _sig816 or "( )" in _sig816):
                lines.append(
                    f"\nzero-argument function: {_prim816.name} takes no parameters"
                    f" — implicitly couples to global/module state; hard to test in isolation"
                )

    # S840: Generator function focus — focused function is named as a generator (iter_/generate_/yield_).
    if _seed_syms and token_count < max_tokens - 30:
        _prim840 = _seed_syms[0]
        _gen_prefixes840 = ("generate_", "iter_", "yield_", "gen_", "stream_")
        if (
            _prim840.kind.value in ("function", "method")
            and not _is_test_file(_prim840.file_path)
            and any(_prim840.name.lower().startswith(p) for p in _gen_prefixes840)
        ):
            lines.append(
                f"\ngenerator function: {_prim840.name} appears to be a generator (iterator-style name)"
                f" — callers must iterate or wrap in list(); cannot be called like a plain function"
            )

    # S852: Operator overload focus — focused method overloads a Python operator.
    if _seed_syms and token_count < max_tokens - 30:
        _prim852 = _seed_syms[0]
        _op_dunders852 = frozenset({
            "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__",
            "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__",
            "__mod__", "__pow__", "__and__", "__or__", "__xor__", "__lshift__",
            "__rshift__", "__iadd__", "__isub__", "__imul__", "__itruediv__",
            "__radd__", "__rsub__", "__rmul__",
        })
        if (
            _prim852.kind.value == "method"
            and not _is_test_file(_prim852.file_path)
            and _prim852.name in _op_dunders852
        ):
            lines.append(
                f"\noperator overload: {_prim852.name} overloads a Python operator"
                f" — callers using operators (+, ==, < etc.) implicitly invoke this method"
            )

    return lines


def _signals_fn_props_b_entry_structural(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S822–S846 structural subset: dense-module/long-name/deep-path/many-children signals."""
    lines: list[str] = []
    # S822: Dense module focus — focused symbol lives in a file with 10+ top-level symbols.
    if _seed_syms and token_count < max_tokens - 30:
        _prim822 = _seed_syms[0]
        if not _is_test_file(_prim822.file_path):
            _fi822 = graph.files.get(_prim822.file_path)
            if _fi822:
                _top_syms822 = [
                    sid for sid in _fi822.symbols
                    if sid in graph.symbols and graph.symbols[sid].parent_id is None
                ]
                if len(_top_syms822) >= 10:
                    lines.append(
                        f"\ndense module: {_prim822.file_path.rsplit('/', 1)[-1]} has {len(_top_syms822)} top-level symbols"
                        f" — large module accumulating responsibilities; consider splitting"
                    )

    # S828: Long name focus — focused symbol has an unusually long name (30+ chars).
    if _seed_syms and token_count < max_tokens - 30:
        _prim828 = _seed_syms[0]
        if len(_prim828.name) >= 30 and not _is_test_file(_prim828.file_path):
            lines.append(
                f"\nlong symbol name: {_prim828.name} has {len(_prim828.name)} characters"
                f" — overly specific name; callers become verbose and renaming is error-prone"
            )

    # S834: Deep path focus — focused symbol lives 4+ directories deep in the file tree.
    if _seed_syms and token_count < max_tokens - 30:
        _prim834 = _seed_syms[0]
        _depth834 = len(_prim834.file_path.replace("\\", "/").split("/")) - 1
        if _depth834 >= 4 and not _is_test_file(_prim834.file_path):
            lines.append(
                f"\ndeep path: {_prim834.name} is {_depth834} levels deep in the directory tree"
                f" — deeply nested symbol; difficult to discover and increases import path verbosity"
            )

    # S846: Many children focus — focused symbol has 10+ child symbols.
    if _seed_syms and token_count < max_tokens - 30:
        _prim846 = _seed_syms[0]
        if not _is_test_file(_prim846.file_path):
            _children846 = [
                s for s in graph.symbols.values()
                if s.parent_id == _prim846.id
            ]
            if len(_children846) >= 10:
                lines.append(
                    f"\nmany children: {_prim846.name} has {len(_children846)} child symbols"
                    f" — large namespace; callers depend on many internal symbols, increasing coupling"
                )

    return lines


def _signals_fn_props_b_entry(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S804–S852: entry/module/structural property signals (dispatcher)."""
    lines: list[str] = []
    lines += _signals_fn_props_b_entry_fn_type(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_b_entry_structural(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_props_b_oop_class(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S858, S882: abstract-method and class-focus signals."""
    lines: list[str] = []
    # S858: Abstract method focus — PRUNED: taxonomic label — agent sees Abstract/Base class name
    if False:  # PRUNED: taxonomic label
        if _seed_syms and token_count < max_tokens - 30:
            _prim858 = _seed_syms[0]
            if (
                _prim858.kind.value == "method"
                and not _is_test_file(_prim858.file_path)
                and _prim858.parent_id is not None
            ):
                _parent858 = graph.symbols.get(_prim858.parent_id)
                if _parent858 and (
                    _parent858.name.startswith("Abstract")
                    or _parent858.name.startswith("Base")
                    or "ABC" in _parent858.name
                ):
                    lines.append(
                        f"\nabstract method: {_prim858.name} is in abstract class {_parent858.name}"
                        f" — signature changes require updating all concrete subclass implementations"
                    )

    # S882: Class focus — the focused symbol is a class, not a function or method.
    # Focusing on a class shows the whole class; agents should use method-level focus
    # for targeted changes to avoid unintended modifications to sibling methods.
    if _seed_syms and token_count < max_tokens - 30:
        _prim882 = _seed_syms[0]
        if _prim882.kind.value == "class" and not _is_test_file(_prim882.file_path):
            _children882 = [
                s for s in graph.symbols.values()
                if s.parent_id == _prim882.id
                and s.kind.value in ("method", "function")
            ]
            if _children882:
                lines.append(
                    f"\nclass focus: {_prim882.name} is a class with {len(_children882)} method(s)"
                    f" — focus on individual methods for targeted changes; class-level focus shows all methods"
                )

    return lines


def _signals_fn_props_b_oop_fn_shape(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S864, S876: high-arity and long-function signals."""
    lines: list[str] = []
    # S864: High-arity function — focused function has 7+ parameters.
    # Functions with many parameters are hard to call correctly; they usually indicate
    # a missing abstraction that should be a config object, dataclass, or separate builder.
    if _seed_syms and token_count < max_tokens - 30:
        _prim864 = _seed_syms[0]
        if _prim864.kind.value in ("function", "method") and not _is_test_file(_prim864.file_path):
            _sig864 = _prim864.signature or ""
            _open864 = _sig864.find("(")
            _close864 = _sig864.rfind(")")
            if _open864 != -1 and _close864 != -1:
                _raw864 = _sig864[_open864 + 1:_close864]
                _params864 = [
                    p.strip().split(":")[0].split("=")[0].strip()
                    for p in _raw864.split(",")
                    if p.strip() and p.strip().split(":")[0].split("=")[0].strip() not in ("self", "cls", "*", "**")
                    and not p.strip().startswith("*")
                ]
                if len(_params864) >= 7:
                    lines.append(
                        f"\nhigh arity: {_prim864.name} has {len(_params864)} parameters"
                        f" — too many parameters; consider a config object or builder pattern"
                    )

    # S876: Long function focus — focused function spans 30+ lines.
    # Long functions are hard to reason about end-to-end; agents should be extra cautious
    # about side effects and implicit state changes buried deep in the function body.
    if _seed_syms and token_count < max_tokens - 30:
        _prim876 = _seed_syms[0]
        if _prim876.kind.value in ("function", "method") and not _is_test_file(_prim876.file_path):
            if _prim876.line_count >= 30:
                lines.append(
                    f"\nlong function: {_prim876.name} spans {_prim876.line_count} lines"
                    f" — long functions hide complexity; review all side effects before changing"
                )

    return lines


def _signals_fn_props_b_oop_callers(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S870, S888, S894: no-caller, multi-caller, and deprecated-file signals."""
    lines: list[str] = []
    # S870: No-caller symbol focus — focused function or method has zero recorded callers.
    # A symbol with no callers is either an entry point, a dead symbol, or called via
    # reflection/dynamic dispatch; agents should investigate before assuming it is safe to remove.
    if _seed_syms and token_count < max_tokens - 30:
        _prim870 = _seed_syms[0]
        if (
            _prim870.kind.value in ("function", "method")
            and not _is_test_file(_prim870.file_path)
            and not _prim870.name.startswith("_")
        ):
            _callers870 = graph.callers_of(_prim870.id)
            if not _callers870:
                lines.append(
                    f"\nno callers: {_prim870.name} has no recorded callers"
                    f" — may be an entry point, unused, or called via reflection/dynamic dispatch"
                )

    # S888: Multi-caller focus — focused function is called from 3+ distinct files.
    # Functions called from many files are cross-cutting concerns; any change to the
    # signature or behavior requires coordinated updates across all call sites.
    if _seed_syms and token_count < max_tokens - 30:
        _prim888 = _seed_syms[0]
        if _prim888.kind.value in ("function", "method") and not _is_test_file(_prim888.file_path):
            _callers888 = graph.callers_of(_prim888.id)
            _caller_files888 = {c.file_path for c in _callers888 if not _is_test_file(c.file_path)}
            if len(_caller_files888) >= 3:
                lines.append(
                    f"\nmulti-caller: {_prim888.name} is called from {len(_caller_files888)} distinct files"
                    f" — cross-cutting function; changes require coordinated updates across {len(_caller_files888)} files"
                )

    # S894: Deprecated file focus — PRUNED: name quality — agent sees file name
    if False:  # PRUNED: name quality
        _legacy_kws894 = ("deprecated", "legacy", "old_", "_old", "obsolete", "archive", "compat")
        if _seed_syms and token_count < max_tokens - 30:
            _prim894 = _seed_syms[0]
            _fbase894 = _prim894.file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if any(kw in _fbase894 for kw in _legacy_kws894):
                lines.append(
                    f"\ndeprecated file: {_prim894.name} is in {_prim894.file_path.rsplit('/', 1)[-1]}"
                    f" — file appears deprecated; check if functionality has been migrated before modifying"
                )

    return lines


def _signals_fn_props_b_oop(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S858–S894: OOP/access/caller property signals (dispatcher)."""
    lines: list[str] = []
    lines += _signals_fn_props_b_oop_class(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_b_oop_fn_shape(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_b_oop_callers(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_method_type(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S900/S906/S912/S918: property, constructor, dunder, private method signals."""
    return []  # PRUNED: taxonomic labels — agent sees __init__/@property/_prefix in signature
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    # S900: Property focus
    if _prim.kind.value == "property" and not _is_test_file(_prim.file_path):
        lines.append(
            f"\nproperty: {_prim.name} is a property"
            f" — transparent to callers but may hide side effects; verify getter has no mutations"
        )
    # S906: Constructor focus
    if (
        _prim.kind.value in ("function", "method")
        and _prim.name in ("__init__", "__new__", "constructor")
        and not _is_test_file(_prim.file_path)
    ):
        lines.append(
            f"\nconstructor: {_prim.name} is a constructor"
            f" — changes may break all instantiation sites; review default arguments carefully"
        )
    # S912: Dunder method focus
    if (
        _prim.kind.value in ("function", "method")
        and _prim.name.startswith("__") and _prim.name.endswith("__")
        and _prim.name not in ("__init__", "__new__")
        and not _is_test_file(_prim.file_path)
    ):
        lines.append(
            f"\ndunder method: {_prim.name} is a Python protocol method"
            f" — implements a built-in protocol; changes can break operators and third-party integrations"
        )
    # S918: Private method focus
    if (
        _prim.kind.value in ("function", "method")
        and _prim.name.startswith("_")
        and not (_prim.name.startswith("__") and _prim.name.endswith("__"))
        and not _is_test_file(_prim.file_path)
    ):
        lines.append(
            f"\nprivate method: {_prim.name} is a private implementation method"
            f" — intended for internal use only; refactoring is lower risk but may affect subclass overrides"
        )
    return lines


def _signals_fn_method_coupling(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S924/S930/S936/S942: name collision, large class member, async, many-params signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    # S924: Name collision
    if _prim.kind.value in ("function", "method") and not _is_test_file(_prim.file_path):
        _same_name = [
            s for s in graph.symbols.values()
            if s.name == _prim.name
            and s.id != _prim.id
            and s.kind.value in ("function", "method")
            and not _is_test_file(s.file_path)
        ]
        if _same_name:
            _files = {s.file_path.rsplit("/", 1)[-1] for s in _same_name}
            lines.append(
                f"\nname collision: {_prim.name} also defined in {len(_same_name)} other file(s)"
                f" ({', '.join(sorted(_files)[:3])})"
                f" — same name across modules; verify callers import the intended definition"
            )
    # S930: Large class member
    if _prim.kind.value in ("function", "method") and _prim.parent_id:
        _siblings = [
            s for s in graph.symbols.values()
            if s.parent_id == _prim.parent_id and s.kind.value in ("method", "function")
        ]
        if len(_siblings) >= 10:
            _cls = graph.symbols.get(_prim.parent_id)
            _cls_name = _cls.name if _cls else "class"
            lines.append(
                f"\nlarge class member: {_prim.name} is one of {len(_siblings)} methods in {_cls_name}"
                f" — large class; changes have higher coupling risk; consider extracting a smaller class"
            )
    # S936: Async method focus
    if (
        _prim.kind.value in ("function", "method")
        and not _is_test_file(_prim.file_path)
        and (_prim.signature or "").startswith("async ")
    ):
        lines.append(
            f"\nasync method: {_prim.name} is an async/coroutine function"
            f" — async semantics require verifying await usage, cancellation handling, and concurrency safety"
        )
    # S942: Many-parameter function
    if _prim.kind.value in ("function", "method") and not _is_test_file(_prim.file_path):
        _sig = _prim.signature or ""
        _param_str = _sig[_sig.find("(")+1:_sig.rfind(")")]
        _params = [
            p.strip().split("=")[0].strip().split(":")[0].strip()
            for p in _param_str.split(",")
            if p.strip() and p.strip() not in ("self", "cls", "*", "**")
            and not p.strip().startswith("*")
        ]
        if len(_params) >= 5:
            lines.append(
                f"\nmany parameters: {_prim.name} takes {len(_params)} parameters"
                f" — high parameter count; each caller must pass all args; signature changes break all call sites"
            )
    return lines


def _signals_fn_props_b_method(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S900–S942: method/membership/visibility property signals (dispatcher)."""
    lines: list[str] = []
    lines += _signals_fn_method_type(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_method_coupling(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_coupling_class_private(graph: "Tempo", _prim: object) -> list[str]:
    """S948: All-private class — no public method interface."""
    if _prim.kind.value != "class" or _is_test_file(_prim.file_path):
        return []
    _methods = [
        s for s in graph.symbols.values()
        if s.parent_id == _prim.id and s.kind.value in ("method", "function")
    ]
    _public_methods = [m for m in _methods if not m.name.startswith("_")]
    if _methods and not _public_methods:
        return [
            f"\nall-private class: {_prim.name} has {len(_methods)} method(s) but none are public"
            f" — no intended external interface; direct access to private methods is fragile coupling"
        ]
    return []


def _signals_fn_coupling_class_override(graph: "Tempo", _prim: object) -> list[str]:
    """S966: Override candidate — same method name in multiple classes."""
    if (
        _prim.kind.value != "method"
        or _prim.parent_id is None
        or _is_test_file(_prim.file_path)
    ):
        return []
    _siblings = [
        s for s in graph.symbols.values()
        if s.kind.value == "method"
        and s.name == _prim.name
        and s.file_path == _prim.file_path
        and s.parent_id != _prim.parent_id
        and s.parent_id is not None
    ]
    if _siblings:
        _cls_names = ", ".join(s.qualified_name.rsplit(".", 1)[0] for s in _siblings[:2])
        return [
            f"\noverride candidate: {_prim.name} also defined in {_cls_names}"
            f" — shared method name across classes; changes may need mirroring for consistent behavior"
        ]
    return []


def _signals_fn_coupling_class_interface(graph: "Tempo", _prim: object) -> list[str]:
    """S1002: Interface method — implicit interface pattern across 3+ classes."""
    if (
        _prim.kind.value != "method"
        or _prim.parent_id is None
        or _is_test_file(_prim.file_path)
    ):
        return []
    _same_name = [
        s for s in graph.symbols.values()
        if s.kind.value == "method"
        and s.name == _prim.name
        and s.parent_id is not None
        and not _is_test_file(s.file_path)
    ]
    _classes = len({s.parent_id for s in _same_name})
    if _classes >= 3:
        return [
            f"\ninterface method: {_prim.name} is defined in {_classes} classes"
            f" — implicit interface pattern; changes must maintain contract across all implementations"
        ]
    return []


def _signals_fn_coupling_class(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S948/S966/S1002: all-private class, override candidate, interface method signals."""
    if not _seed_syms or token_count >= max_tokens - 30:
        return []
    _prim = _seed_syms[0]
    lines: list[str] = []
    lines += _signals_fn_coupling_class_private(graph, _prim)
    lines += _signals_fn_coupling_class_override(graph, _prim)
    lines += _signals_fn_coupling_class_interface(graph, _prim)
    return lines


def _signals_fn_coupling_callers(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S972/S978/S984/S990: orphan, single-caller, test-only, external-only signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    if _is_test_file(_prim.file_path) or _prim.kind.value not in ("function", "method"):
        return lines
    _callers = graph.callers_of(_prim.id)
    # S972: Orphan symbol
    if not _callers:
        _callees = graph.callees_of(_prim.id)
        if not _callees:
            lines.append(
                f"\norphan symbol: {_prim.name} has no callers and no callees"
                f" — completely isolated; may be dead code or a missing registration/wire-up"
            )
    # S978: Single caller
    if len(_callers) == 1:
        lines.append(
            f"\nsingle caller: {_prim.name} is only called by {_callers[0].name}"
            f" — consider inlining; all logic changes affect only one call site"
        )
    # S984: Test-only caller
    _test_callers = [c for c in _callers if _is_test_file(c.file_path)]
    _prod_callers = [c for c in _callers if not _is_test_file(c.file_path)]
    if _test_callers and not _prod_callers:
        lines.append(
            f"\ntest-only caller: {_prim.name} is called only from test files, never from production code"
            f" — may be dead in production or needs wiring up to the application flow"
        )
    # S990: External-only
    _internal = [c for c in _callers if c.file_path == _prim.file_path]
    _external = [c for c in _callers if c.file_path != _prim.file_path]
    if _external and not _internal:
        lines.append(
            f"\nexternal-only: {_prim.name} has {len(_external)} caller(s) all from external files"
            f" — pure public API; changes always affect other modules, never just this file"
        )
    return lines


def _signals_fn_coupling_code(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S954/S960/S996/S1008/S1014: generator, utility, recursive, complexity, test-target signals."""
    lines: list[str] = []
    if not _seed_syms or token_count >= max_tokens - 30:
        return lines
    _prim = _seed_syms[0]
    # S954: Generator function focus
    if _prim.kind.value == "function" and not _is_test_file(_prim.file_path):
        _n = _prim.name.lower()
        _gen_prefixes = ("gen_", "iter_", "generate_", "yield_")
        _gen_suffixes = ("_generator", "_iterator", "_gen", "_iter")
        if any(_n.startswith(p) for p in _gen_prefixes) or any(_n.endswith(s) for s in _gen_suffixes):
            lines.append(
                f"\ngenerator focus: {_prim.name} appears to be a generator"
                f" — callers must iterate the return value; consuming as non-iterator will exhaust it silently"
            )
    # S960: Utility file focus
    _util_kws = ("utils", "util", "helpers", "helper", "common", "shared", "misc", "tools")
    _fbase = _prim.file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    if (
        _prim.kind.value in ("function", "method")
        and not _is_test_file(_prim.file_path)
        and any(_fbase == kw or _fbase.startswith(kw + "_") or _fbase.endswith("_" + kw) for kw in _util_kws)
    ):
        lines.append(
            f"\nutility file: {_prim.name} is in a utility/helpers module"
            f" — utility changes cross-cut features; test all consumer modules, not just obvious callers"
        )
    # S996: Recursive symbol
    if (
        _prim.kind.value == "function"
        and _prim.parent_id is None
        and not _is_test_file(_prim.file_path)
    ):
        _callees = graph.callees_of(_prim.id)
        if any(c.id == _prim.id for c in _callees):
            lines.append(
                f"\nrecursive: {_prim.name} calls itself"
                f" — top-level recursive function; verify base case and max depth before modifying"
            )
    # S1008: High complexity
    if (
        not _is_test_file(_prim.file_path)
        and _prim.kind.value in ("function", "method")
        and getattr(_prim, "complexity", 0) >= 10
    ):
        lines.append(
            f"\nhigh complexity: {_prim.name} has cyclomatic complexity {_prim.complexity}"
            f" — {_prim.complexity} distinct paths need test coverage; refactor before growing further"
        )
    # S1014: Test target
    if _prim.kind.value in ("function", "method", "test") and (
        _prim.name.startswith("test_") or _is_test_file(_prim.file_path)
    ):
        lines.append(
            f"\ntest target: {_prim.name} is a test function"
            f" — focusing on test code; find the implementation under test for the production logic"
        )
    return lines


def _signals_fn_props_b_coupling(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S948–S1014: isolation/coupling/pattern property signals (dispatcher)."""
    lines: list[str] = []
    lines += _signals_fn_coupling_class(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_coupling_callers(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_coupling_code(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_fn_focus_props_b(
    graph: "Tempo", _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """S804–S1014: focus property signals (dispatcher to sub-helpers)."""
    lines: list[str] = []
    lines += _signals_fn_props_b_entry(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_b_oop(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_b_method(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_props_b_coupling(graph, _seed_syms, token_count, max_tokens)
    return lines


def _signals_focused_fn_advanced(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: fn_advanced (dispatches to sub-helpers)."""
    lines: list[str] = []
    token_count = _extend_tracked(lines, _signals_fn_recursion(graph, _seed_syms, token_count, max_tokens), token_count)
    token_count = _extend_tracked(lines, _signals_fn_oop(graph, _seed_syms, token_count, max_tokens), token_count)
    token_count = _extend_tracked(lines, _signals_fn_signature(graph, _seed_syms, token_count, max_tokens), token_count)
    token_count = _extend_tracked(lines, _signals_fn_conventions(graph, _seed_syms, token_count, max_tokens), token_count)
    token_count = _extend_tracked(lines, _signals_fn_quality(graph, _seed_syms, token_count, max_tokens), token_count)
    token_count = _extend_tracked(lines, _signals_fn_focus_props_a(graph, _seed_syms, token_count, max_tokens), token_count)
    _extend_tracked(lines, _signals_fn_focus_props_b(graph, _seed_syms, token_count, max_tokens), token_count)
    return lines

