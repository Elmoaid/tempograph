from __future__ import annotations

from ..types import Tempo, Symbol, SymbolKind
from ._utils import _is_test_file, count_tokens

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
            from ..git import file_change_velocity as _fcv, file_commit_counts as _fcc, is_git_repo as _igr
            _vel = _fcv(graph.root)
            if _igr(graph.root):
                _churn_counts = _fcc(graph.root)
        except Exception:
            pass

    # S72: Symbols touched summary — total symbols + exported count across all changed files.
    # Gives agents immediate scope: "23 symbols touched (8 exported)" vs. just file count.
    _all_changed_syms = [
        graph.symbols[sid]
        for fp in normalized
        for sid in graph.files[fp].symbols
        if sid in graph.symbols and not _is_test_file(fp)
    ]
    if _all_changed_syms:
        _exp_count = sum(1 for s in _all_changed_syms if s.exported)
        _sym_summary = f"{len(_all_changed_syms)} symbols touched"
        if _exp_count:
            _sym_summary += f" ({_exp_count} exported)"
        lines.append(_sym_summary)
        # S77: List exported symbols from changed non-test files — direct API surface view.
        # Shows agents WHICH exported symbols are in the diff (not just how many).
        # Only shown when 2-8 exported symbols (fewer = obvious, more = too noisy).
        _exported_syms = [s for s in _all_changed_syms if s.exported and s.kind.value in ("function", "method", "class", "interface")]
        if 2 <= len(_exported_syms) <= 8:
            _exp_names = [s.name for s in _exported_syms]
            lines.append(f"Exported: {', '.join(_exp_names)}")
        # S80: Global change risk verdict — top-level signal before file details.
        # Combines blast radius and exported-with-callers count.
        # Agents use this as a quick go/no-go before reviewing change details.
        _total_blast_files = len({
            i for fp in normalized
            for i in graph.importers_of(fp)
            if i != fp and i in graph.files
        })
        _exported_with_callers = sum(
            1 for s in _all_changed_syms
            if s.exported and any(c.file_path not in normalized for c in graph.callers_of(s.id))
        )
        _risk_score = _total_blast_files + _exported_with_callers * 3
        if _risk_score >= 16:
            _risk_label: str | None = "HIGH"
        elif _risk_score >= 6:
            _risk_label = "MEDIUM"
        else:
            _risk_label = None  # low risk: don't emit — absence of warning is the signal
        if _risk_label is not None:
            _risk_detail_parts = []
            if _exported_with_callers:
                _risk_detail_parts.append(f"{_exported_with_callers} exported with callers")
            if _total_blast_files:
                _risk_detail_parts.append(f"blast: {_total_blast_files} files")
            _risk_detail = f" — {', '.join(_risk_detail_parts)}" if _risk_detail_parts else ""
            lines.append(f"change risk: {_risk_label}{_risk_detail}")
            lines.append("")

    # S104: Scope spread — count of distinct top-level directories in the diff.
    # Cross-module diffs (touching 3+ separate directories) need broader review.
    # Only shown when 3+ distinct module directories are touched.
    _diff_dirs = {
        fp.split("/")[0] if "/" in fp else "."
        for fp in normalized
        if not _is_test_file(fp)
    }
    if len(_diff_dirs) >= 3:
        _dir_list = sorted(_diff_dirs)[:5]
        _dir_str = ", ".join(_dir_list)
        if len(_diff_dirs) > 5:
            _dir_str += f" +{len(_diff_dirs) - 5} more"
        lines.append(f"scope: {len(_diff_dirs)} modules ({_dir_str})")

    # Risk summary: top changed files by blast radius, so agents can prioritize review.
    # Only shown when 2+ changed files with blast >= 2; single-file diffs skip this.
    _risk_blast = sorted(
        [
            (len({i for i in graph.importers_of(fp) if i != fp and i in graph.files}), fp)
            for fp in normalized
        ],
        key=lambda x: -x[0],
    )
    _risk_blast_gt1 = [(n, fp) for n, fp in _risk_blast if n >= 2]
    if len(_risk_blast_gt1) >= 2:
        _risk_parts = [f"{fp.rsplit('/', 1)[-1]} (blast:{n})" for n, fp in _risk_blast_gt1[:3]]
        lines.append(f"Risk: {', '.join(_risk_parts)}")
        lines.append("")

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
            from ..git import cochange_pairs as _cpairs
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

    # Unchanged tests: source files in the diff whose matching test file was NOT changed.
    # Signals to agents that test updates may be needed alongside the code change.
    _unchanged_tests: list[str] = []
    for _fp in normalized:
        if _is_test_file(_fp):
            continue
        _base = _fp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        _matching_test = next(
            (fp for fp in graph.files if _is_test_file(fp) and _base in fp.rsplit("/", 1)[-1]),
            None,
        )
        if _matching_test and _matching_test not in normalized:
            _unchanged_tests.append(
                f"{_matching_test.rsplit('/', 1)[-1]} (tests {_fp.rsplit('/', 1)[-1]})"
            )
    if _unchanged_tests:
        lines.append("")
        _ut_str = ", ".join(_unchanged_tests[:3])
        if len(_unchanged_tests) > 3:
            _ut_str += f" +{len(_unchanged_tests) - 3} more"
        lines.append(f"Unchanged tests: {_ut_str} — consider updating")

    # S78: Tests in diff — when test files ARE included in the diff, confirm them.
    # The "good news" companion to S65: agents get a clear ✓ when tests are present.
    # Only shown when 1-4 test files are in the diff (otherwise obvious from file list).
    _tests_in_diff = [fp.rsplit("/", 1)[-1] for fp in normalized if _is_test_file(fp)]
    if 1 <= len(_tests_in_diff) <= 4:
        _tdf_str = ", ".join(_tests_in_diff)
        lines.append(f"Tests in diff: {_tdf_str} ✓")

    # S114: Test ratio for changed source files — how many changed files have test coverage.
    # Complements "Unchanged tests" (lists specifics) with a summary ratio.
    # Only shown for diffs with 3+ changed source files (smaller diffs too noisy).
    _src_changed = [fp for fp in normalized if not _is_test_file(fp)]
    if len(_src_changed) >= 3:
        _all_proj_tests = {fp for fp in graph.files if _is_test_file(fp)}
        if _all_proj_tests:
            _src_with_tests = sum(
                1 for fp in _src_changed
                if any(fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] in t for t in _all_proj_tests)
            )
            _src_total = len(_src_changed)
            _src_pct = int(_src_with_tests / _src_total * 100)
            # Only show when partial coverage (neither 0% nor 100%)
            if 0 < _src_with_tests < _src_total:
                lines.append(f"test coverage: {_src_with_tests}/{_src_total} changed files have tests ({_src_pct}%)")

    # S102: Private callers — count of non-exported callers of changed exported symbols.
    # Agents often focus on external API consumers, but internal private callers also
    # need updating after a signature change. This surfaces the hidden internal blast.
    # Only shown when >= 3 private callers exist across all changed exported symbols.
    if _all_changed_syms:
        _priv_caller_set: set[str] = set()
        for _exp_s in _all_changed_syms:
            if not _exp_s.exported:
                continue
            for _caller in graph.callers_of(_exp_s.id):
                if not _caller.exported and _caller.file_path not in {fp for fp in normalized}:
                    _priv_caller_set.add(f"{_caller.file_path}::{_caller.name}")
        if len(_priv_caller_set) >= 3:
            lines.append(f"Private callers: {len(_priv_caller_set)} — internal non-exported callers of changed exports")

    # S127: Max complexity in diff — the highest cyclomatic complexity of any function touched.
    # Agents need to know if they're modifying a complex function (high test risk).
    # Only shown when the max complexity in the diff is >= 10 (below that = routine code).
    _cx_max_sym: "Symbol | None" = None
    _cx_max_val = 0
    for _s127 in _all_changed_syms:
        if _s127.kind.value in ("function", "method") and (_s127.complexity or 0) > _cx_max_val:
            _cx_max_val = _s127.complexity
            _cx_max_sym = _s127
    if _cx_max_sym and _cx_max_val >= 10:
        lines.append(
            f"max complexity in diff: cx={_cx_max_val} ({_cx_max_sym.name}"
            f" in {_cx_max_sym.file_path.rsplit('/', 1)[-1]})"
        )

    # S118: Entry points changed — highlight when the diff touches known entry point files.
    # Entry points (main.py, server.py, __main__.py, app.py, cli.py, index.ts) are highest
    # blast-radius files — changes here ripple to all consumers. Flag them explicitly.
    _ENTRY_BASENAMES = frozenset({
        "main.py", "main.ts", "main.tsx", "main.rs", "main.go",
        "index.ts", "index.tsx", "index.js",
        "app.py", "app.ts", "app.tsx",
        "__main__.py", "server.py", "cli.py", "lib.rs",
    })
    _ep_changed = [fp for fp in normalized if fp.rsplit("/", 1)[-1] in _ENTRY_BASENAMES]
    if _ep_changed:
        _ep_names = [fp.rsplit("/", 1)[-1] for fp in _ep_changed]
        _ep_str = ", ".join(_ep_names)
        lines.append(f"entry points changed: {_ep_str} — top-level API/runner modified")

    # S133: Touched test count — test files that exist in the graph for changed source files.
    # Tells agents how many tests they should run after this diff; zero = untested change.
    # Only shown when 1+ source file in the diff and test files exist in the project.
    _s133_src_changed = [fp for fp in normalized if not _is_test_file(fp)]
    if _s133_src_changed:
        _all_test_fps_133 = {fp for fp in graph.files if _is_test_file(fp)}
        if _all_test_fps_133:
            _touched_tests: set[str] = set()
            for _fp133 in _s133_src_changed:
                _stem133 = _fp133.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                for _tfp in _all_test_fps_133:
                    if _stem133 in _tfp:
                        _touched_tests.add(_tfp)
            if _touched_tests:
                lines.append(f"touched test count: {len(_touched_tests)} test files cover the diff")
            else:
                lines.append("touched test count: 0 — no test files found for changed files")

    return "\n".join(lines)
