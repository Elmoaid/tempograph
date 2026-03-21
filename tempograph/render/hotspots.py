from __future__ import annotations

import math

from ..types import Tempo, EdgeKind, Symbol, SymbolKind
from ._utils import _is_test_file, count_tokens

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
        from ..git import file_change_velocity
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

        # Test coverage badge: [tested] = direct test callers or test file imports;
        # [no tests] = no coverage; omitted when no test files exist in project.
        _test_badge = ""
        if _any_tests_in_project and sym.kind.value in ("function", "method", "class", "module"):
            _tc_callers = [c for c in graph.callers_of(sym.id) if _is_test_file(c.file_path)]
            if _tc_callers:
                _test_badge = " [tested]"
            elif sym.file_path and any(_is_test_file(i) for i in graph.importers_of(sym.file_path)):
                _test_badge = " [tested]"
            else:
                _test_badge = " [no tests]"

        lines.append(
            f"{i:2d}. {sym.kind.value} {sym.qualified_name} "
            f"[risk={score:.0f}]{_test_badge} ({sym.file_path}:{sym.line_start})"
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

    # Complexity density: top functions by cx/lines — most logic-packed, hardest to read.
    # cx=40 in 30 lines (1.33/L) is harder to understand than cx=40 in 300 lines (0.13/L).
    _density_syms = sorted(
        [
            (sym.complexity / max(sym.line_count, 1), sym)
            for _, sym in scores
            if sym.complexity >= 3 and sym.line_count >= 5 and not _is_test_file(sym.file_path)
        ],
        key=lambda x: -x[0],
    )
    if len(_density_syms) >= 2:
        _den_parts = [
            f"{sym.name} (cx:{sym.complexity}, {sym.line_count}L, {den:.2f}/L)"
            for den, sym in _density_syms[:3]
        ]
        lines.append(f"Dense: {', '.join(_den_parts)}")

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

    # S113: Hot coverage ratio — fraction of top hotspot symbols that have test coverage.
    # Aggregates the per-symbol [tested]/[no tests] badges into a single health signal.
    # Only shown when test files exist AND at least 5 non-test hotspot symbols are scored.
    if _all_test_fps_hs and scores:
        _hs_non_test = [(sc, sym) for sc, sym in scores[:top_n] if not _is_test_file(sym.file_path)]
        if len(_hs_non_test) >= 5:
            _hs_tested_count = 0
            for _sc2, _sym2 in _hs_non_test:
                _base2 = _sym2.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if any(_base2 in t for t in _all_test_fps_hs):
                    _hs_tested_count += 1
            _hs_total = len(_hs_non_test)
            _hs_pct = int(_hs_tested_count / _hs_total * 100)
            if _hs_pct <= 70:  # only show when coverage gap is notable
                lines.append(f"hot coverage: {_hs_tested_count}/{_hs_total} top symbols have tests ({_hs_pct}%)")

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
            from ..git import cochange_pairs as _hspot_cpairs
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

    # S73: File complexity rank — top 3 source files by total cyclomatic complexity.
    # Per-symbol scores already shown above; this aggregates per file for refactor targeting.
    _file_cx: dict[str, int] = {}
    for _, sym in scores:
        if not _is_test_file(sym.file_path) and sym.complexity >= 1:
            _file_cx[sym.file_path] = _file_cx.get(sym.file_path, 0) + sym.complexity
    _top_cx_files = sorted(_file_cx.items(), key=lambda x: -x[1])
    _top_cx_files = [(fp, cx) for fp, cx in _top_cx_files if cx >= 10][:3]
    if len(_top_cx_files) >= 2:
        _fcx_parts = [f"{fp.rsplit('/', 1)[-1]} (cx:{cx})" for fp, cx in _top_cx_files]
        lines.append("")
        lines.append(f"File complexity: {', '.join(_fcx_parts)}")

    # S89: Danger zone — files in BOTH the top-churn AND top-complexity quadrant.
    # Symbol-level churn risk (above) covers individual functions; this flags files where
    # the combination of total complexity AND change rate creates the highest refactor risk.
    # Threshold: file total cx >= 15 AND churn >= 2 commits/week.
    if velocity and _file_cx:
        _dz_files: list[tuple[float, str, int, float]] = []
        for _dz_fp, _dz_cx in _file_cx.items():
            if _dz_cx < 15:
                continue
            _dz_velo = velocity.get(_dz_fp, 0.0)
            if _dz_velo < 2.0:
                continue
            _dz_score = _dz_cx * (_dz_velo ** 0.5)
            _dz_files.append((_dz_score, _dz_fp, _dz_cx, _dz_velo))
        if len(_dz_files) >= 1:
            _dz_files.sort(key=lambda x: -x[0])
            _dz_parts = [
                f"{fp.rsplit('/', 1)[-1]} (cx:{fcx}, {fv:.1f}/wk)"
                for _, fp, fcx, fv in _dz_files[:3]
            ]
            lines.append("")
            lines.append(f"Danger zone: {', '.join(_dz_parts)} — high churn + complexity")

    # S131: Hot-and-complex files — source files that are BOTH in the hotspot top half
    # AND have high average cyclomatic complexity. These are the most dangerous: actively
    # changing, and the changes are in hard-to-understand code.
    # Only shown when 2+ such files exist (avoid showing for well-maintained codebases).
    if scores:
        _hs_seen_fps_131: set[str] = set()
        _hs_file_scores_131: dict[str, float] = {}
        for _sc131, _sym131 in scores[:top_n]:
            if _sym131.file_path not in _hs_seen_fps_131 and not _is_test_file(_sym131.file_path):
                _hs_seen_fps_131.add(_sym131.file_path)
                _hs_file_scores_131[_sym131.file_path] = _sc131
        _hot_complex_files: list[tuple[float, int, str]] = []
        for _fp131, _s131 in _hs_file_scores_131.items():
            _fi131 = graph.files.get(_fp131)
            if not _fi131:
                continue
            _cx_vals131 = [
                graph.symbols[sid].complexity
                for sid in _fi131.symbols
                if sid in graph.symbols and graph.symbols[sid].complexity >= 1
                and graph.symbols[sid].kind.value in ("function", "method")
            ]
            if _cx_vals131:
                _avg_cx131 = sum(_cx_vals131) / len(_cx_vals131)
                if _avg_cx131 >= 5.0:
                    _hot_complex_files.append((_avg_cx131, int(_s131), _fp131))
        if len(_hot_complex_files) >= 2:
            _hot_complex_files.sort(key=lambda x: -x[0])
            _hc_parts = [
                f"{fp.rsplit('/', 1)[-1]} (avg cx={cx:.1f})"
                for cx, _, fp in _hot_complex_files[:3]
            ]
            lines.append("")
            lines.append(f"hot+complex: {', '.join(_hc_parts)} — active and hard to change")

    # S112: Churn spike — files whose last-7d velocity is 2× their 14-day average.
    # Sudden acceleration = something changed: new feature push, bug-fixing crunch, or refactor.
    # Agents need to know about these to prioritize review and watch for regressions.
    # Only shown when 1+ non-test file has spiked AND recent velocity >= 3 commits/week.
    if velocity and velocity_14:
        _spikes: list[tuple[float, str]] = []
        for _sp_fp, _sp_v7 in velocity.items():
            if _is_test_file(_sp_fp) or _sp_v7 < 3.0:
                continue
            _sp_v14 = velocity_14.get(_sp_fp, 0.0)
            if _sp_v14 > 0 and _sp_v7 >= 2.0 * _sp_v14:
                _spikes.append((_sp_v7, _sp_fp))
        if _spikes:
            _spikes.sort(key=lambda x: -x[0])
            _sp_parts = [f"{fp.rsplit('/', 1)[-1]} (+{v:.1f}x/wk)" for v, fp in _spikes[:2]]
            lines.append("")
            lines.append(f"Churn spike: {', '.join(_sp_parts)} — velocity doubled vs 2-week avg")

    # S97: High fan-out — functions calling 8+ distinct functions.
    # High callee count = coordination hubs: changing any callee can affect this function.
    # Shows top 3 sorted by callee count. Only source functions (no test helpers).
    _high_fanout = [
        (len(graph.callees_of(sym.id)), sym)
        for _, sym in scores[:top_n]
        if sym.kind.value in ("function", "method") and not _is_test_file(sym.file_path)
        and len(graph.callees_of(sym.id)) >= 8
    ]
    if len(_high_fanout) >= 1:
        _high_fanout.sort(key=lambda x: -x[0])
        _hf_parts = [f"{sym.name} ({n} callees)" for n, sym in _high_fanout[:3]]
        lines.append("")
        lines.append(f"High fan-out: {', '.join(_hf_parts)} — calls many functions")

    # S96: Outlier complexity — functions with cx >= 2× codebase average AND cx >= 10.
    # Average complexity anchors the signal: a cx:10 fn in a cx:2-avg codebase is notable;
    # same fn in a cx:8-avg codebase is normal. Requires >= 10 source functions for valid avg.
    _all_fn_cx = [
        sym.complexity for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method")
        and not _is_test_file(sym.file_path)
        and sym.complexity >= 1
    ]
    if len(_all_fn_cx) >= 10:
        _cx_avg = sum(_all_fn_cx) / len(_all_fn_cx)
        _outlier_threshold = max(2 * _cx_avg, 10.0)
        _outliers = [
            sym for sym in graph.symbols.values()
            if sym.kind.value in ("function", "method")
            and not _is_test_file(sym.file_path)
            and sym.complexity >= _outlier_threshold
        ]
        if len(_outliers) >= 1:
            _outliers.sort(key=lambda s: -s.complexity)
            _out_parts = [
                f"{s.name} (cx:{s.complexity}, avg:{_cx_avg:.1f})"
                for s in _outliers[:3]
            ]
            lines.append("")
            lines.append(f"Outlier complexity: {', '.join(_out_parts)}")

    # Refactor targets: unexported (private) functions with high complexity (cx >= 5) and
    # zero external callers. These are internal functions that have grown too complex
    # but have no call graph forcing the complexity — prime candidates for simplification.
    _refactor_candidates = [
        sym for sym in graph.symbols.values()
        if sym.kind.value in ("function", "method")
        and not sym.exported
        and not _is_test_file(sym.file_path)
        and sym.complexity >= 5
        and not any(c.file_path != sym.file_path for c in graph.callers_of(sym.id))
    ]
    if len(_refactor_candidates) >= 2:
        _refactor_candidates.sort(key=lambda s: -s.complexity)
        _rf_parts = [f"{s.name} (cx={s.complexity})" for s in _refactor_candidates[:4]]
        lines.append("")
        lines.append(f"Refactor targets: {', '.join(_rf_parts)} — high-cx private with no ext callers")

    # S94: Stable hotspots — top-ranked symbols in files not modified in 60+ days.
    # Mature, widely-used code that hasn't been touched: treat carefully, high breakage risk.
    # Needs git history; silently skipped otherwise. Only shown when 2+ qualify.
    if graph.root and scores:
        try:
            from ..git import file_last_modified_days as _fld_hs
            _stable_hot: list[tuple[int, str, int]] = []  # (days, sym.name, cross_callers)
            _age_cache_hs: dict[str, int | None] = {}
            for _, _sh_sym in scores[:15]:
                if _is_test_file(_sh_sym.file_path):
                    continue
                if _sh_sym.file_path not in _age_cache_hs:
                    _age_cache_hs[_sh_sym.file_path] = _fld_hs(graph.root, _sh_sym.file_path)
                _days = _age_cache_hs[_sh_sym.file_path]
                if _days is None or _days < 60:
                    continue
                _cross = len({
                    c.file_path for c in graph.callers_of(_sh_sym.id)
                    if c.file_path != _sh_sym.file_path
                })
                if _cross >= 2:
                    _stable_hot.append((_days, _sh_sym.name, _cross))
            if len(_stable_hot) >= 1:
                _sh_parts = [f"{name} ({d}d, {n} callers)" for d, name, n in _stable_hot[:3]]
                lines.append("")
                lines.append(f"Stable hot: {', '.join(_sh_parts)} — unchanged 60d+, high coupling")
        except Exception:
            pass

    # S121: Recently active files not in the hot zone — files touched recently but with low
    # hotspot scores (clean, growing code). Agents should be aware of these active files
    # even though they haven't accumulated complexity or coupling yet.
    # Only shown when 3+ recently active files have hotspot score = 0.
    try:
        from ..git import recently_modified_files as _rmf121
        _repo_root_121 = graph.root or "."
        _recent_fps = _rmf121(_repo_root_121, n_commits=10)
        # "Hot zone" = files with at least one symbol scoring >= 1.0 (real coupling/complexity)
        _hs_hot_files = {sym.file_path for sc, sym in scores if sc >= 1.0}
        _new_active = [
            fp for fp in _recent_fps
            if fp in graph.files and not _is_test_file(fp) and fp not in _hs_hot_files
            and "/templates/" not in fp and not fp.startswith("templates/")
            and "/static/" not in fp and not fp.startswith("static/")
        ]
        if len(_new_active) >= 3:
            _na_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_new_active)[:3]]
            _na_str = ", ".join(_na_names)
            if len(_new_active) > 3:
                _na_str += f" +{len(_new_active) - 3} more"
            lines.append("")
            lines.append(f"recently active (not in hotspots): {_na_str}")
    except Exception:
        pass

    # S128: Long-stale hotspot — top-ranked file untouched for 180+ days.
    # A high-risk file that's never modified is dangerous: it may have accumulated
    # tech debt silently. Different from S94 (60d stable symbols) — this is file-level age.
    # Only shown when any top-5 hotspot file has age >= 180d.
    if graph.root and scores:
        try:
            from ..git import file_last_modified_days as _fld128
            _top_hotspot_files: list[tuple[float, str]] = []
            _seen_hs_fps: set[str] = set()
            for _sc128, _sym128 in scores[:top_n]:
                if _sym128.file_path not in _seen_hs_fps and not _is_test_file(_sym128.file_path):
                    _seen_hs_fps.add(_sym128.file_path)
                    _top_hotspot_files.append((_sc128, _sym128.file_path))
            for _sc128, _fp128 in _top_hotspot_files[:5]:
                _age128 = _fld128(graph.root, _fp128)
                if _age128 is not None and _age128 >= 180:
                    lines.append("")
                    lines.append(
                        f"long-stale hotspot: {_fp128.rsplit('/', 1)[-1]}"
                        f" ({_age128}d unchanged, risk={int(_sc128)})"
                    )
                    break  # only show the most concerning one
        except Exception:
            pass

    # S107: Import bottleneck — the file most depended on by other source files.
    # A heavily-imported file that's also actively churning = maximum blast risk.
    # Shows top file with importer count + velocity if available.
    _hs_importer_counts: dict[str, int] = {}
    for fp in graph.files:
        if _is_test_file(fp):
            continue
        _n_imp = len({
            i for i in graph.importers_of(fp)
            if i in graph.files and not _is_test_file(i) and i != fp
        })
        if _n_imp >= 5:
            _hs_importer_counts[fp] = _n_imp
    if _hs_importer_counts:
        _bn_fp, _bn_n = max(_hs_importer_counts.items(), key=lambda x: x[1])
        _bn_velo = velocity.get(_bn_fp, 0.0)
        _bn_velo_str = f", {_bn_velo:.1f}/wk" if _bn_velo >= 1.0 else ""
        lines.append("")
        lines.append(f"Import bottleneck: {_bn_fp.rsplit('/', 1)[-1]} ({_bn_n} dependents{_bn_velo_str})")

    # S139: Caller concentration — when a single file accounts for >= 50% of all callers
    # to the top hotspot symbol, the dependency is "concentrated."
    # A concentrated dependency means one file is doing most of the work through a bottleneck.
    # Only shown when top hotspot symbol has >= 4 cross-file callers.
    if scores:
        _top_sym139: "Symbol" = scores[0][1]
        _callers139 = [
            c for c in graph.callers_of(_top_sym139.id)
            if c.file_path and c.file_path != _top_sym139.file_path
            and not _is_test_file(c.file_path)
        ]
        if len(_callers139) >= 4:
            _caller_file_counts139: dict[str, int] = {}
            for _c139 in _callers139:
                _caller_file_counts139[_c139.file_path] = _caller_file_counts139.get(_c139.file_path, 0) + 1
            _max_fp139 = max(_caller_file_counts139, key=lambda fp: _caller_file_counts139[fp])
            _max_count139 = _caller_file_counts139[_max_fp139]
            _pct139 = int(_max_count139 / len(_callers139) * 100)
            if _pct139 >= 50:
                lines.append("")
                lines.append(
                    f"caller concentration: {_max_fp139.rsplit('/', 1)[-1]}"
                    f" = {_pct139}% of {_top_sym139.name} callers — single file dominates usage"
                )

    # S156: Velocity + complexity — the top hotspot symbol's churn rate AND complexity combined.
    # A symbol that's both actively modified (velocity) and complex is the highest refactor risk.
    # Only shown when top hotspot has velocity >= 2.0/wk AND complexity >= 10.
    if scores and velocity:
        _top156: "Symbol" = scores[0][1]
        _v156 = velocity.get(_top156.file_path, 0.0)
        _cx156 = _top156.complexity or 0
        if _v156 >= 2.0 and _cx156 >= 10:
            lines.append(
                f"\ntop risk: {_top156.name} — cx={_cx156}, {_v156:.1f} changes/wk"
                f" — highest combined velocity+complexity"
            )

    # S200: Size hotspot — the top hotspot file is also the largest file by line count.
    # The most-changed file also being the largest = maximum cognitive load per change.
    # Only shown when the top hotspot file is in the top 3 by line count.
    if scores and graph.files:
        _top200_fp = scores[0][1].file_path
        _all_line_counts = sorted(
            [(fi.line_count, fp) for fp, fi in graph.files.items()
             if not _is_test_file(fp) and hasattr(fi, 'line_count') and fi.line_count],
            reverse=True,
        )
        _top3_large = {fp for _, fp in _all_line_counts[:3]}
        if _top200_fp in _top3_large:
            _top200_lines = graph.files[_top200_fp].line_count if _top200_fp in graph.files else 0
            _top200_base = _top200_fp.rsplit("/", 1)[-1]
            if _top200_lines and _top200_lines >= 50:
                lines.append(
                    f"\nsize hotspot: {_top200_base} is top hotspot AND largest file"
                    f" ({_top200_lines} lines) — maximum cognitive load per change"
                )

    # S194: Test file hotspot — a test file appears in the top 5 hotspot ranks.
    # Test files in the hotspot list indicate test churn, flaky tests, or spec instability.
    # Only shown when 1+ test file is among the top 5 hotspot-ranked files.
    if scores:
        _top5_test_files194 = [
            sym.file_path for _, sym in scores[:5]
            if _is_test_file(sym.file_path)
        ]
        # Deduplicate
        _seen194: set[str] = set()
        _unique_test_fps194 = [
            fp for fp in _top5_test_files194
            if not (fp in _seen194 or _seen194.add(fp))  # type: ignore[func-returns-value]
        ]
        if _unique_test_fps194:
            _t194_name = _unique_test_fps194[0].rsplit("/", 1)[-1]
            lines.append(
                f"\ntest file hotspot: {_t194_name} in top 5 hotspots — test churn"
                f" may indicate flaky tests or rapidly-changing spec"
            )

    # S188: Avg complexity of top hotspot — the top hotspot file's functions are complex on average.
    # Complex-on-average files have high maintenance cost beyond any single function.
    # Only shown when avg complexity of fns in top hotspot file >= 8.
    if scores:
        _top188_fp = scores[0][1].file_path
        _cx_vals188 = [
            s.complexity for s in graph.symbols.values()
            if s.file_path == _top188_fp
            and s.kind.value in ("function", "method")
            and s.complexity is not None
        ]
        if _cx_vals188:
            _avg_cx188 = sum(_cx_vals188) / len(_cx_vals188)
            if _avg_cx188 >= 8:
                _top188_base = _top188_fp.rsplit("/", 1)[-1]
                lines.append(
                    f"\nhigh avg complexity: {_top188_base} — avg cx {_avg_cx188:.1f}"
                    f" across {len(_cx_vals188)} fns — entire file is complex"
                )

    # S182: Hot cluster — 2+ top hotspot files share the same parent directory.
    # When multiple hot files cluster in one directory, that dir is a change concentration zone.
    # Only shown when 2+ of the top-20 hotspots are in the same directory.
    if scores and len(scores) >= 4:
        _s182_dir_counts: dict[str, list[str]] = {}
        for _sc182, _sym182 in scores[:20]:
            _dir182 = _sym182.file_path.rsplit("/", 1)[0] if "/" in _sym182.file_path else "."
            if _dir182 != ".":
                _s182_dir_counts.setdefault(_dir182, [])
                if _sym182.file_path not in _s182_dir_counts[_dir182]:
                    _s182_dir_counts[_dir182].append(_sym182.file_path)
        _s182_clusters = sorted(
            [(len(fps), d) for d, fps in _s182_dir_counts.items() if len(fps) >= 2],
            reverse=True,
        )
        if _s182_clusters:
            _s182_count, _s182_dir = _s182_clusters[0]
            _s182_dir_name = _s182_dir.rsplit("/", 1)[-1]
            lines.append(
                f"\nhot cluster: {_s182_dir_name}/ — {_s182_count} hotspot files"
                f" concentrated in one directory"
            )

    # S176: Interface hotspot — the top hotspot file contains an interface or abstract class.
    # Interfaces are contracts; changing them breaks all implementors, amplifying blast radius.
    # Only shown when the top hotspot file contains >= 1 interface/abstract symbol.
    if scores:
        _top176_fp = scores[0][1].file_path
        _s176_ifaces = [
            s for s in graph.symbols.values()
            if s.file_path == _top176_fp
            and s.kind.value in ("interface", "abstract_class")
        ]
        if _s176_ifaces:
            _s176_names = [s.name for s in _s176_ifaces[:3]]
            _s176_str = ", ".join(_s176_names)
            _top176_base = _top176_fp.rsplit("/", 1)[-1]
            lines.append(
                f"\ninterface hotspot: {_top176_base} defines {len(_s176_ifaces)}"
                f" interface(s) ({_s176_str}) — contract changes break all implementors"
            )

    # S170: Velocity spike — the top hotspot file's velocity is >= 3x the median velocity.
    # A single file being changed far more than the rest is a concentration risk.
    # Only shown when 3+ files have non-zero velocity and top >= 3x median.
    if velocity and len(velocity) >= 3:
        _vels170 = sorted(velocity.values())
        _median170_idx = len(_vels170) // 2
        _median170 = _vels170[_median170_idx]
        if _median170 > 0:
            _top170_fp = max(velocity, key=velocity.get)  # type: ignore[arg-type]
            _top170_v = velocity[_top170_fp]
            if _top170_v >= _median170 * 3.0:
                _top170_name = _top170_fp.rsplit("/", 1)[-1]
                lines.append(
                    f"\nvelocity spike: {_top170_name} — {_top170_v:.1f}/wk"
                    f" vs median {_median170:.1f}/wk ({_top170_v / _median170:.1f}×)"
                )

    # S164: Zero-test hotspot — the highest-ranked hotspot file has no corresponding test file.
    # Hotspot files are changed most often; lacking tests makes them highest refactor risk.
    # Only shown when the top hotspot file has no matching test file in the repo.
    if scores:
        _top164_fp = scores[0][1].file_path
        _top164_base = _top164_fp.rsplit("/", 1)[-1]
        _top164_stem = _top164_base.rsplit(".", 1)[0]
        _s164_test_patterns = {
            f"test_{_top164_stem}",
            f"{_top164_stem}_test",
            f"{_top164_stem}.test",
            f"{_top164_stem}.spec",
        }
        _s164_has_test = any(
            any(
                p in fp.rsplit("/", 1)[-1].lower()
                for p in _s164_test_patterns
            )
            for fp in graph.files
            if _is_test_file(fp)
        )
        if not _s164_has_test:
            lines.append(
                f"\nzero-test hotspot: {_top164_base} — top hotspot with no matching test file"
            )

    # S144: Recursive fns in hotspots — top-ranked symbols that call themselves.
    # Recursive functions are harder to modify: changing loop invariants or base cases
    # requires understanding the full recursion contract. Flag when 2+ are in top hotspots.
    if scores:
        _recursive_syms144: list[str] = []
        for _sc144, _sym144 in scores[:top_n]:
            if _sym144.kind.value not in ("function", "method"):
                continue
            if _is_test_file(_sym144.file_path):
                continue
            # Recursive if the symbol calls itself
            if any(_callee.id == _sym144.id for _callee in graph.callees_of(_sym144.id)):
                _recursive_syms144.append(_sym144.name)
        if len(_recursive_syms144) >= 2:
            _r_str = ", ".join(_recursive_syms144[:3])
            if len(_recursive_syms144) > 3:
                _r_str += f" +{len(_recursive_syms144) - 3} more"
            lines.append("")
            lines.append(f"recursive hotspots: {len(_recursive_syms144)} recursive fns in top ranks ({_r_str})")

    # S206: Fan-in spike — top-ranked hotspot symbol has significantly more callers than average.
    # A hotspot that is also a caller magnet is the highest-risk change target.
    # Only shown when the top hotspot's caller count >= 3x the average of the top 10.
    if scores:
        _caller_counts206 = []
        for _sc206, _sym206 in scores[:10]:
            _n_callers206 = len(graph.callers_of(_sym206.id))
            _caller_counts206.append((_n_callers206, _sym206))
        if len(_caller_counts206) >= 3:
            _avg206 = sum(c for c, _ in _caller_counts206) / len(_caller_counts206)
            _top_count206, _top_sym206 = max(_caller_counts206, key=lambda x: x[0])
            if _avg206 > 0 and _top_count206 >= _avg206 * 3.0:
                lines.append(
                    f"\nfan-in spike: {_top_sym206.name} — {_top_count206} callers"
                    f" vs avg {_avg206:.1f} ({_top_count206 / _avg206:.1f}×)"
                )

    # S216: Exported hotspot — the top hotspot file exports many symbols.
    # Frequently-changed exported symbols mean frequent contract changes for all callers.
    # Only shown when top hotspot file exports >= 5 fn/method/class symbols.
    if scores:
        _top216_fp = scores[0][1].file_path
        _s216_exported = [
            s for s in graph.symbols.values()
            if s.file_path == _top216_fp
            and s.exported
            and s.kind.value in ("function", "method", "class", "interface")
        ]
        if len(_s216_exported) >= 5:
            _top216_base = _top216_fp.rsplit("/", 1)[-1]
            lines.append(
                f"\nexported hotspot: {_top216_base} has {len(_s216_exported)} exported symbols"
                f" — frequent changes mean frequent contract churn for callers"
            )

    # S223: Mono-class file — the top hotspot file is dominated by a single large class.
    # A huge central class is hard to test, hard to extend, and concentrates cognitive load.
    # Only shown when the top hotspot file has 1 class that contains >= 50% of its symbols.
    if scores:
        _top223_fp = scores[0][1].file_path
        _top223_syms = [s for s in graph.symbols.values() if s.file_path == _top223_fp]
        _top223_classes = [s for s in _top223_syms if s.kind.value == "class"]
        if len(_top223_classes) == 1 and len(_top223_syms) >= 6:
            _cls223 = _top223_classes[0]
            _cls223_children = graph.children_of(_cls223.id)
            if len(_cls223_children) >= len(_top223_syms) * 0.5:
                _top223_base = _top223_fp.rsplit("/", 1)[-1]
                lines.append(
                    f"\nmono-class file: {_top223_base} dominated by {_cls223.name}"
                    f" ({len(_cls223_children)} of {len(_top223_syms)} symbols)"
                    f" — consider splitting into smaller classes"
                )

    # S230: Low-complexity hotspot — top hotspot file's functions all have very low complexity.
    # High change frequency + low complexity = likely config/data churn, not logic changes.
    # Only shown when top hotspot file has 3+ fns and avg cx <= 2.
    if scores:
        _top230_fp = scores[0][1].file_path
        _cx_vals230 = [
            s.complexity for s in graph.symbols.values()
            if s.file_path == _top230_fp
            and s.kind.value in ("function", "method")
            and s.complexity is not None
        ]
        if len(_cx_vals230) >= 3:
            _avg_cx230 = sum(_cx_vals230) / len(_cx_vals230)
            if _avg_cx230 <= 2:
                _top230_base = _top230_fp.rsplit("/", 1)[-1]
                lines.append(
                    f"\nlow-complexity hotspot: {_top230_base} avg cx {_avg_cx230:.1f}"
                    f" — frequently changed but simple; likely config/data churn"
                )

    # S236: Ghost hotspot — top hotspot symbol has 0 direct test callers.
    # Frequently-changed code with no test callers is high-risk; no safety net.
    # Only shown when the top-ranked hotspot fn/method has 0 test callers.
    if scores:
        for _sc236, _sym236 in scores[:5]:
            if _sym236.kind.value not in ("function", "method"):
                continue
            _test_callers236 = [
                c for c in graph.callers_of(_sym236.id)
                if _is_test_file(c.file_path)
            ]
            if not _test_callers236 and not _is_test_file(_sym236.file_path):
                lines.append(
                    f"\nghost hotspot: {_sym236.name} ({_sym236.file_path.rsplit('/', 1)[-1]})"
                    f" — top hotspot with 0 test callers, no safety net"
                )
                break

    # S250: Cluster hotspot — 3+ hotspot files from the same directory.
    # A whole module being unstable (not just one file) suggests coordination risk.
    # Only shown when top-10 hotspot files include 3+ from the same directory.
    if scores:
        _s250_top_files = []
        _s250_seen_files: set[str] = set()
        for _, _sym250 in scores[:20]:
            _fp250 = _sym250.file_path
            if _fp250 not in _s250_seen_files and not _is_test_file(_fp250):
                _s250_top_files.append(_fp250)
                _s250_seen_files.add(_fp250)
            if len(_s250_top_files) >= 10:
                break
        _s250_dirs: dict[str, list[str]] = {}
        for _fp250 in _s250_top_files:
            _dir250 = _fp250.rsplit("/", 1)[0] if "/" in _fp250 else "."
            _s250_dirs.setdefault(_dir250, []).append(_fp250.rsplit("/", 1)[-1])
        _s250_clusters = [(d, fs) for d, fs in _s250_dirs.items() if len(fs) >= 3]
        if _s250_clusters:
            _top_dir250, _top_files250 = max(_s250_clusters, key=lambda x: len(x[1]))
            _f250_str = ", ".join(_top_files250[:3])
            if len(_top_files250) > 3:
                _f250_str += f" +{len(_top_files250) - 3} more"
            _dir_label250 = _top_dir250.rsplit("/", 1)[-1] if "/" in _top_dir250 else _top_dir250
            lines.append(
                f"\ncluster hotspot: {len(_top_files250)} files in {_dir_label250}/ ({_f250_str})"
                f" — whole module is unstable; coordinate changes carefully"
            )

    # S260: Undocumented hotspot — top hotspot fn/method has no docstring.
    # Frequently-changed undocumented functions accumulate hidden complexity.
    # Only shown when the top non-test hotspot function has no docstring (empty signature body).
    if scores:
        _top253 = next(
            (sym for _, sym in scores[:5]
             if sym.kind.value in ("function", "method") and not _is_test_file(sym.file_path)),
            None
        )
        if _top253:
            _sig253 = _top253.signature or ""
            # A docstring would typically appear in the signature or be tracked as metadata.
            # Heuristic: if name doesn't end in _ (dunder) and has no docstring indicator.
            _has_doc = '"""' in _sig253 or "'''" in _sig253 or "# doc" in _sig253
            if not _has_doc and not _top253.name.startswith("__"):
                lines.append(
                    f"\nundocumented hotspot: {_top253.name}"
                    f" — top hotspot has no docstring; add docs when modifying"
                )

    # S242: Test file hotspot — top hotspot symbol lives in a test file.
    # Frequently-changed test code suggests flaky tests, brittle fixtures, or rapidly-evolving specs.
    # Only shown when the top-ranked hotspot symbol is itself in a test file.
    if scores:
        _top242_sym = next(
            (sym for _, sym in scores[:3] if sym.kind.value in ("function", "method")),
            None
        )
        if _top242_sym and _is_test_file(_top242_sym.file_path):
            lines.append(
                f"\ntest file hotspot: {_top242_sym.name} ({_top242_sym.file_path.rsplit('/', 1)[-1]})"
                f" — most-changed symbol is in a test; consider stabilizing test infrastructure"
            )


    # S255: Utility hotspot — the top-ranked hotspot lives in a generic utility module.
    # Utility modules are shared across many callers; hotspot status here risks coupling
    # unrelated features through shared helpers.
    # Only shown when the top hotspot file is named utils/helpers/common/shared/base.
    _s255_util_stems = {"utils", "util", "helpers", "helper", "common", "shared", "base",
                        "mixins", "mixin", "tools", "lib", "misc", "core"}
    if scores:
        _s255_seen: set[str] = set()
        for _, _sym255 in scores[:5]:
            _fp255 = _sym255.file_path
            if _fp255 in _s255_seen or _is_test_file(_fp255):
                continue
            _s255_seen.add(_fp255)
            _stem255 = _fp255.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if _stem255 in _s255_util_stems:
                _importers255 = len(graph.importers_of(_fp255))
                lines.append(
                    f"\nutility hotspot: {_fp255.rsplit('/', 1)[-1]} is a shared utility"
                    f" ({_importers255} importer(s)) — changes here have wide blast radius"
                )
                break


    # S262: Stable hotspot — top-ranked hotspot symbol has 3+ test callers.
    # High churn with high test coverage is lower risk; changes here are unlikely
    # to go undetected. Positive signal for teams considering refactors.
    if scores:
        for _sc262, _sym262 in scores[:5]:
            if _sym262.kind.value not in ("function", "method"):
                continue
            if _is_test_file(_sym262.file_path):
                continue
            _test_callers262 = [c for c in graph.callers_of(_sym262.id) if _is_test_file(c.file_path)]
            if len(_test_callers262) >= 3:
                lines.append(
                    f"\nstable hotspot: {_sym262.name} has {len(_test_callers262)} test callers"
                    f" — well-tested high-churn symbol; refactoring here has a safety net"
                )
                break


    # S268: Churn concentration — top 3+ hotspot symbols all live in the same file.
    # When high-churn symbols concentrate in one file, that file is an instability
    # hotspot: changes there are frequent, contested, and risk merge conflicts.
    if scores:
        _s268_top_files: list[str] = []
        _s268_seen_files268: set[str] = set()
        for _, _sym268 in scores[:10]:
            _fp268 = _sym268.file_path
            if _fp268 not in _s268_seen_files268 and not _is_test_file(_fp268):
                _s268_top_files.append(_fp268)
                _s268_seen_files268.add(_fp268)
            if len(_s268_top_files) >= 5:
                break
        if _s268_top_files:
            _s268_file_counts: dict[str, int] = {}
            for _, _sym268 in scores[:10]:
                _fp268 = _sym268.file_path
                if not _is_test_file(_fp268):
                    _s268_file_counts[_fp268] = _s268_file_counts.get(_fp268, 0) + 1
            _s268_max_file = max(_s268_file_counts, key=_s268_file_counts.get)
            if _s268_file_counts[_s268_max_file] >= 3:
                _s268_label = _s268_max_file.rsplit("/", 1)[-1]
                lines.append(
                    f"\nchurn concentration: {_s268_file_counts[_s268_max_file]} of top hotspots"
                    f" in {_s268_label} — single file is instability center, high merge conflict risk"
                )


    # S277: Single-caller hotspot — top hotspot symbol is called from only 1 other symbol.
    # A high-complexity function with only one caller may be an inlined helper that
    # could be folded back, or a function named for the wrong abstraction level.
    if scores:
        for _sc277, _sym277 in scores[:5]:
            if _sym277.kind.value not in ("function", "method"):
                continue
            if _is_test_file(_sym277.file_path):
                continue
            _ext_callers277 = [
                c for c in graph.callers_of(_sym277.id)
                if c.file_path != _sym277.file_path
            ]
            if len(_ext_callers277) == 1:
                _caller_name277 = _ext_callers277[0].name
                lines.append(
                    f"\nsingle-caller hotspot: {_sym277.name} called only from {_caller_name277}"
                    f" — high-churn fn with one user; consider inlining or renaming"
                )
                break


    # S283: Untested hotspot — top hotspot file has no test coverage AND repo has no tests at all.
    # The riskiest combination: high-churn code with zero test infrastructure.
    _s283_any_tests = any(_is_test_file(fp) for fp in graph.files)
    if not _s283_any_tests and scores:
        _s283_top = next((sym for _, sym in scores[:3] if not _is_test_file(sym.file_path)), None)
        if _s283_top:
            lines.append(
                f"\nuntested repo: no test files found — all hotspot churn is completely unprotected"
            )


    # S289: Interface module hotspot — top hotspot symbol lives in an __init__.py file.
    # Package init files act as public interfaces; changes there affect ALL importers
    # of the package, not just files that use that symbol directly.
    if scores:
        _s289_top = next(
            (sym for _, sym in scores[:5]
             if not _is_test_file(sym.file_path)
             and sym.file_path.rsplit("/", 1)[-1] == "__init__.py"),
            None
        )
        if _s289_top:
            _importers289 = len([f for f in graph.importers_of(_s289_top.file_path) if f in graph.files])
            lines.append(
                f"\ninterface hotspot: {_s289_top.name} is in __init__.py"
                f" ({_importers289} package importer(s)) — changes here affect all package consumers"
            )


    # S295: Re-exported hotspot — top hotspot symbol has the same name exported from another file.
    # Re-exported symbols create multiple blast radii: changes must propagate
    # through both the definition and all the re-export facades.
    if scores:
        for _sc295, _sym295 in scores[:5]:
            if _sym295.kind.value not in ("function", "method", "class"):
                continue
            if _is_test_file(_sym295.file_path):
                continue
            # Find other files that export a symbol with the same name
            _same_name295 = [
                s for s in graph.symbols.values()
                if s.name == _sym295.name
                and s.file_path != _sym295.file_path
                and s.exported
                and not _is_test_file(s.file_path)
            ]
            if _same_name295:
                _facade295 = _same_name295[0].file_path.rsplit("/", 1)[-1]
                lines.append(
                    f"\nre-exported hotspot: {_sym295.name} also exported from {_facade295}"
                    f" — multi-path symbol; changes propagate through all export facades"
                )
                break

    # S299: Mono-file hotspot — all top-5 hotspot symbols come from the same file.
    # When a single file monopolises the hotspot list, it's structurally overloaded;
    # the module has grown past cohesion and needs splitting.
    if len(scores) >= 3:
        _top_files299 = [sym.file_path for _, sym in scores[:5]]
        if len(set(_top_files299)) == 1 and not _is_test_file(_top_files299[0]):
            _mf_name299 = _top_files299[0].rsplit("/", 1)[-1]
            lines.append(
                f"\nmono-file hotspot: all top {len(_top_files299)} hotspots in {_mf_name299}"
                f" — file monopolises churn; strong split candidate"
            )

    # S305: Hotspot bottleneck — top hotspot file is imported by 5+ other source files.
    # A file that is simultaneously high-churn AND imported widely is a systemic risk:
    # any change to it forces re-evaluation across all its dependents.
    if scores:
        _top305_sym = scores[0][1]
        _top305_fp = _top305_sym.file_path
        if not _is_test_file(_top305_fp):
            _importer_files305 = {
                fp for fp in graph.importers_of(_top305_fp)
                if not _is_test_file(fp) and fp != _top305_fp
            }
            if len(_importer_files305) >= 5:
                lines.append(
                    f"\nhotspot bottleneck: {_top305_fp.rsplit('/', 1)[-1]} — top hotspot"
                    f" imported by {len(_importer_files305)} files; churn ripples widely"
                )

    # S312: Score-dominant hotspot — top hotspot file accounts for 40%+ of total hotspot score.
    # One file dominating the score means all change energy is concentrated there;
    # it's the single biggest risk point in the codebase right now.
    if scores and len(scores) >= 3:
        _total_score312 = sum(sc for sc, _ in scores)
        _top_score312 = scores[0][0]
        if _total_score312 > 0 and (_top_score312 / _total_score312) >= 0.40:
            _top_sym312 = scores[0][1]
            _pct312 = int(100 * _top_score312 / _total_score312)
            if not _is_test_file(_top_sym312.file_path):
                lines.append(
                    f"\nscore-dominant hotspot: {_top_sym312.file_path.rsplit('/', 1)[-1]}"
                    f" — {_pct312}% of total hotspot risk; highest-priority stabilization target"
                )

    # S318: Non-primary-language hotspot — top hotspot symbol lives in a non-Python/JS/TS file.
    # Hotspots in secondary languages (Go, Rust, C) often involve cross-language FFI
    # or specialized subsystems that require domain expertise to safely modify.
    _PRIMARY_LANGS318 = {"python", "javascript", "typescript"}
    if scores:
        _top318 = scores[0][1]
        _lang318 = _top318.language.value.lower() if _top318.language else ""
        if _lang318 and _lang318 not in _PRIMARY_LANGS318 and not _is_test_file(_top318.file_path):
            lines.append(
                f"\nnon-primary-language hotspot: {_top318.file_path.rsplit('/', 1)[-1]}"
                f" ({_lang318}) — hotspot in secondary language; domain expertise required"
            )

    # S326: Hotspot in multi-commit file — top hotspot file appears in the most recent git history.
    # The top hotspot is already the most changed file; if it's also the most recently touched,
    # it signals an active instability zone that deserves isolation or review before merging.
    # (Implementation: check that file_path appears in file_commit_counts, approximated via callers)
    if scores:
        _top326 = scores[0][1]
        _file326 = _top326.file_path
        if not _is_test_file(_file326):
            # Proxy: file is "multi-commit" if it has symbols with many cross-file callers AND
            # is NOT a test file. Already captured by the main hotspot score; add extra context
            # when the top file's symbol count also suggests high activity.
            _file_syms326 = [s for s in graph.symbols.values() if s.file_path == _file326]
            _callee_count326 = sum(
                1 for e in graph.edges
                if e.kind.value == "calls"
                and any(s.id == e.source_id for s in _file_syms326)
            )
            if len(_file_syms326) >= 10 and _callee_count326 >= 20:
                lines.append(
                    f"\nhigh-activity hotspot: {_file326.rsplit('/', 1)[-1]} has"
                    f" {len(_file_syms326)} symbols and {_callee_count326} outgoing calls"
                    f" — dense file; isolate changes with thorough code review"
                )

    # S332: Cross-module hotspot — top hotspot is called from 3+ distinct top-level directories.
    # A hotspot that spans multiple top-level modules is a cross-cutting concern;
    # changes to it require coordinating reviews across multiple team boundaries.
    if scores:
        _top332 = scores[0][1]
        if not _is_test_file(_top332.file_path):
            _callers332 = graph.callers_of(_top332.id)
            _top_dirs332: set[str] = set()
            for _c332 in _callers332:
                if _c332.file_path != _top332.file_path:
                    _parts332 = _c332.file_path.replace("\\", "/").split("/")
                    if len(_parts332) >= 2:
                        _top_dirs332.add(_parts332[0])
            if len(_top_dirs332) >= 3:
                lines.append(
                    f"\ncross-module hotspot: {_top332.name} called from"
                    f" {len(_top_dirs332)} top-level dirs ({', '.join(sorted(_top_dirs332)[:3])})"
                    f" — cross-cutting concern; multi-team coordination required"
                )

    # S338: Risk concentration — top 3 hotspot symbols hold 70%+ of total hotspot score.
    # When a small cluster dominates the risk distribution, the codebase has a tight
    # instability core; stabilizing just those 3 files would significantly improve overall health.
    if len(scores) >= 5:
        _total_s338 = sum(sc for sc, _ in scores)
        _top3_s338 = sum(sc for sc, _ in scores[:3])
        if _total_s338 > 0 and (_top3_s338 / _total_s338) >= 0.70:
            _pct338 = int(100 * _top3_s338 / _total_s338)
            _names338 = [sym.file_path.rsplit("/", 1)[-1] for _, sym in scores[:3]]
            lines.append(
                f"\nrisk concentration: top 3 hotspots hold {_pct338}% of total risk"
                f" ({', '.join(_names338)})"
                f" — stabilising these 3 files improves overall codebase health most"
            )

    # S344: __init__ module hotspot — top hotspot lives in an __init__.py or index file.
    # __init__.py hotspots indicate that the package interface itself is unstable;
    # any import of the package is affected, making the blast radius the entire dependency tree.
    if scores:
        _top344 = scores[0][1]
        _fname344 = _top344.file_path.rsplit("/", 1)[-1].lower()
        if _fname344 in ("__init__.py", "index.py", "index.ts", "index.js") and not _is_test_file(_top344.file_path):
            lines.append(
                f"\ninit module hotspot: {_top344.file_path.rsplit('/', 1)[-1]}"
                f" — package interface is unstable; every importer of the package is affected"
            )

    return "\n".join(lines)  # ALWAYS return here — never inside a conditional block
