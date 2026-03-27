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


def _calm_zones_lines(graph: Tempo, velocity: dict[str, float]) -> list[str]:
    """Identify stable, heavily-imported files — the load-bearing walls of the codebase.

    These are the INVERSE of hotspots: not currently churning, but critical because
    many files depend on them. Breaking one silently cascades across the codebase.

    Fires when: velocity data available AND ≥1 non-test file has low churn (<2 commits/week)
    AND ≥5 non-test importers. Sorted by importer count: most load-bearing first.
    """
    if not velocity:
        return []

    candidates: list[tuple[int, str, float]] = []
    for fp in graph.files:
        if _is_test_file(fp):
            continue
        vel = velocity.get(fp, 0.0)
        if vel >= 2.0:
            continue
        src_importers = [i for i in graph.importers_of(fp) if not _is_test_file(i)]
        if len(src_importers) < 5:
            continue
        candidates.append((len(src_importers), fp, vel))

    if not candidates:
        return []

    candidates.sort(key=lambda x: -x[0])
    lines = ["", "calm zones (stable, load-bearing):"]
    for imp_count, fp, vel in candidates[:4]:
        base = fp.rsplit("/", 1)[-1]
        vel_note = f", {vel:.1f} commits/wk" if vel >= 0.1 else ""
        lines.append(f"  {base} — {imp_count} importers{vel_note}")
    return lines


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

    # S40: pre-prime file age cache — one batch git call instead of N per-file calls.
    if graph.root:
        try:
            from ..git import prime_file_age_cache as _prime_hs  # noqa: PLC0415
            _prime_hs(graph.root)
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

    lines.extend(_calm_zones_lines(graph, velocity))

    lines.extend(_collect_hotspots_signals(
        graph, scores, velocity, velocity_14, _all_test_fps_hs, top_n
    ))
    return "\n".join(lines)  # ALWAYS return here — never inside a conditional block

def _signals_hotspots_core_a_churn(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S113: Hot coverage ratio — fraction of top hotspot symbols that have test coverage.
    # Aggregates the per-symbol [tested]/[no tests] badges into a single health signal.
    # Only shown when test files exist AND at least 5 non-test hotspot symbols are scored.
    if all_test_fps and scores:
        _hs_non_test = [(sc, sym) for sc, sym in scores[:top_n] if not _is_test_file(sym.file_path)]
        if len(_hs_non_test) >= 5:
            _hs_tested_count = 0
            for _sc2, _sym2 in _hs_non_test:
                _base2 = _sym2.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if any(_base2 in t for t in all_test_fps):
                    _hs_tested_count += 1
            _hs_total = len(_hs_non_test)
            _hs_pct = int(_hs_tested_count / _hs_total * 100)
            if _hs_pct <= 70:  # only show when coverage gap is notable
                out.append(f"hot coverage: {_hs_tested_count}/{_hs_total} top symbols have tests ({_hs_pct}%)")

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
            out.append("")
            out.append(f"Churn risk: {', '.join(_cr_parts)}")

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
            out.append("")
            out.append(f"Hotspot concentration: {', '.join(_conc_parts)}")

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
                out.append("")
                out.append(f"Coupled pairs: {', '.join(_cp_parts)}")
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
        out.append("")
        out.append(f"File complexity: {', '.join(_fcx_parts)}")

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
            out.append("")
            out.append(f"Danger zone: {', '.join(_dz_parts)} — high churn + complexity")

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
            out.append("")
            out.append(f"hot+complex: {', '.join(_hc_parts)} — active and hard to change")

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
            out.append("")
            out.append(f"Churn spike: {', '.join(_sp_parts)} — velocity doubled vs 2-week avg")


def _signals_hotspots_core_a_quality(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
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
        out.append("")
        out.append(f"High fan-out: {', '.join(_hf_parts)} — calls many functions")

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
            out.append("")
            out.append(f"Outlier complexity: {', '.join(_out_parts)}")

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
        out.append("")
        out.append(f"Refactor targets: {', '.join(_rf_parts)} — high-cx private with no ext callers")

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
                out.append("")
                out.append(f"Stable hot: {', '.join(_sh_parts)} — unchanged 60d+, high coupling")
        except Exception:
            pass


def _signals_hotspots_core_a_activity(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
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
            out.append("")
            out.append(f"recently active (not in hotspots): {_na_str}")
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
                    out.append("")
                    out.append(
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
        out.append("")
        out.append(f"Import bottleneck: {_bn_fp.rsplit('/', 1)[-1]} ({_bn_n} dependents{_bn_velo_str})")

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
                out.append("")
                out.append(
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
            out.append(
                f"\ntop risk: {_top156.name} — cx={_cx156}, {_v156:.1f} changes/wk"
                f" — highest combined velocity+complexity"
            )


def _signals_hotspots_core_a_structure(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
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
                out.append(
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
            out.append(
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
                out.append(
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
            out.append(
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
            out.append(
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
                out.append(
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
            out.append(
                f"\nzero-test hotspot: {_top164_base} — top hotspot with no matching test file"
            )


def _signals_hotspots_core_a_risks(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
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
            out.append("")
            out.append(f"recursive hotspots: {len(_recursive_syms144)} recursive fns in top ranks ({_r_str})")

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
                out.append(
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
            out.append(
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
                out.append(
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
                out.append(
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
                out.append(
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
            out.append(
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
                out.append(
                    f"\nundocumented hotspot: {_top253.name}"
                    f" — top hotspot has no docstring; add docs when modifying"
                )


def _signals_hotspots_core_a(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    _signals_hotspots_core_a_churn(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_a_quality(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_a_activity(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_a_structure(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_a_risks(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)



def _signals_hotspots_core_b_type(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S242: Test file hotspot — top hotspot symbol lives in a test file.
    # Frequently-changed test code suggests flaky tests, brittle fixtures, or rapidly-evolving specs.
    # Only shown when the top-ranked hotspot symbol is itself in a test file.
    if scores:
        _top242_sym = next(
            (sym for _, sym in scores[:3] if sym.kind.value in ("function", "method")),
            None
        )
        if _top242_sym and _is_test_file(_top242_sym.file_path):
            out.append(
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
                out.append(
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
                out.append(
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
                out.append(
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
                out.append(
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
            out.append(
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
            out.append(
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
                out.append(
                    f"\nre-exported hotspot: {_sym295.name} also exported from {_facade295}"
                    f" — multi-path symbol; changes propagate through all export facades"
                )
                break


def _signals_hotspots_core_b_concentration(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S299: Mono-file hotspot — all top-5 hotspot symbols come from the same file.
    # When a single file monopolises the hotspot list, it's structurally overloaded;
    # the module has grown past cohesion and needs splitting.
    if len(scores) >= 3:
        _top_files299 = [sym.file_path for _, sym in scores[:5]]
        if len(set(_top_files299)) == 1 and not _is_test_file(_top_files299[0]):
            _mf_name299 = _top_files299[0].rsplit("/", 1)[-1]
            out.append(
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
                out.append(
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
                out.append(
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
            out.append(
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
            _file_syms326 = graph.symbols_in_file(_file326)
            _sym_ids326 = {s.id for s in _file_syms326}
            _CALLS326 = EdgeKind.CALLS
            _callee_count326 = sum(
                1 for e in graph.edges
                if e.kind is _CALLS326 and e.source_id in _sym_ids326
            )
            if len(_file_syms326) >= 10 and _callee_count326 >= 20:
                out.append(
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
                out.append(
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
            out.append(
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
            out.append(
                f"\ninit module hotspot: {_top344.file_path.rsplit('/', 1)[-1]}"
                f" — package interface is unstable; every importer of the package is affected"
            )


def _signals_hotspots_core_b_structure(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S376: Same-file hotspot cluster — top 3 hotspot symbols all live in the same file.
    # When the top 3 hotspots are all in one file, that file has very concentrated risk;
    # it is likely a core module that warrants extra scrutiny before any change.
    if len(scores) >= 3:
        _files376 = [scores[i][1].file_path for i in range(3)]
        if len(set(_files376)) == 1 and not _is_test_file(_files376[0]):
            out.append(
                f"\nhotspot cluster: top 3 hotspots all in {_files376[0].rsplit('/', 1)[-1]}"
                f" — extreme risk concentration; this file is the single most critical stabilization target"
            )

    # S370: Divergent hotspot — top hotspot symbol is in a different file than the 2nd hotspot.
    # When the top 2 hotspots live in different files/modules, risk is distributed rather than
    # concentrated; both files need attention but in separate change operations.
    if len(scores) >= 2:
        _top370 = scores[0][1]
        _sec370 = scores[1][1]
        if (not _is_test_file(_top370.file_path) and not _is_test_file(_sec370.file_path)
                and _top370.file_path != _sec370.file_path):
            _dir_top370 = _top370.file_path.rsplit("/", 1)[0] if "/" in _top370.file_path else "."
            _dir_sec370 = _sec370.file_path.rsplit("/", 1)[0] if "/" in _sec370.file_path else "."
            if _dir_top370 != _dir_sec370:
                out.append(
                    f"\ndivergent hotspots: top risks in different modules"
                    f" ({_top370.file_path.rsplit('/', 1)[-1]} vs {_sec370.file_path.rsplit('/', 1)[-1]})"
                    f" — risk is distributed; plan changes in both areas separately"
                )

    # S364: Test support hotspot — top hotspot is a shared test helper/fixture/factory file.
    # High-churn test support files are themselves a testing risk; if conftest/factories
    # change frequently, dependent tests may break for reasons unrelated to the code under test.
    if scores:
        _top364 = scores[0][1]
        _fp364 = _top364.file_path.lower()
        _test_support364 = (
            "conftest", "fixtures", "factories", "test_helpers", "test_utils",
            "testing", "test_support", "mock_",
        )
        _is_support364 = any(p in _fp364 for p in _test_support364) or _is_test_file(_top364.file_path)
        if _is_support364:
            out.append(
                f"\ntest support hotspot: {_top364.file_path.rsplit('/', 1)[-1]}"
                f" — high-churn test support; frequent changes break tests for unrelated reasons"
            )

    # S358: Generated-file hotspot — top hotspot lives in a generated/auto-generated file.
    # Generated files should not be edited directly; if they are a hotspot, the generator
    # or its configuration is the actual source of churn, not the generated file.
    if scores:
        _top358 = scores[0][1]
        _fp358 = _top358.file_path.lower()
        _gen_patterns358 = (
            "_pb2.py", "_pb2_grpc.py", "_gen.py", "_generated.py",
            "schema_gen", "auto_gen", "autogenerated", ".g.ts", ".g.dart",
        )
        _is_gen358 = any(p in _fp358 for p in _gen_patterns358)
        if _is_gen358:
            out.append(
                f"\ngenerated-file hotspot: {_top358.file_path.rsplit('/', 1)[-1]} is auto-generated"
                f" — do not edit directly; churn originates in the generator or .proto/.schema source"
            )

    # S352: Megafile hotspot — top hotspot file has 500+ lines of code.
    # Megafiles concentrate change history; a large file with many symbols is harder to reason
    # about and typically accumulates more accidental complexity over time.
    if scores:
        _top352 = scores[0][1]
        if not _is_test_file(_top352.file_path):
            _fi352 = graph.files.get(_top352.file_path)
            if _fi352 and _fi352.line_count >= 500:
                out.append(
                    f"\nmegafile hotspot: {_top352.file_path.rsplit('/', 1)[-1]} has {_fi352.line_count} lines"
                    f" — large files accumulate accidental complexity; consider splitting by responsibility"
                )

    # S394: Cross-language hotspot — top hotspot file is not in the primary codebase language.
    # When the top hotspot is in a non-primary language (e.g., a Go file in a Python repo),
    # it may lack the same testing and review culture as the main language.
    if scores:
        _top394 = scores[0][1]
        _fp394 = _top394.file_path
        if _fp394 in graph.files and not _is_test_file(_fp394):
            _lang394 = graph.files[_fp394].language.value
            # Find the dominant language among all source files
            _lang_counts394: dict[str, int] = {}
            for _fp_c394, _fi_c394 in graph.files.items():
                if not _is_test_file(_fp_c394):
                    _lang_counts394[_fi_c394.language.value] = _lang_counts394.get(_fi_c394.language.value, 0) + 1
            if _lang_counts394:
                _primary394 = max(_lang_counts394, key=lambda l: _lang_counts394[l])
                _total394 = sum(_lang_counts394.values())
                _primary_pct394 = _lang_counts394[_primary394] / _total394
                if _lang394 != _primary394 and _primary_pct394 >= 0.60:
                    out.append(
                        f"\ncross-language hotspot: {_fp394.rsplit('/', 1)[-1]} ({_lang394})"
                        f" — hotspot is in non-primary language ({_primary394} is primary);"
                        f" may have different testing and review standards"
                    )

    # S388: API endpoint hotspot — top hotspot lives in a routes/endpoints/views file.
    # API endpoint hotspots indicate that a route handler or view is accumulating logic;
    # endpoint files should be thin orchestrators, not computation hubs.
    if scores:
        _top388 = scores[0][1]
        _fp388 = _top388.file_path.lower()
        _api_patterns388 = (
            "route", "endpoint", "view", "controller", "handler",
            "rest", "graphql", "api",
        )
        _is_api388 = any(p in _fp388 for p in _api_patterns388) and not _is_test_file(_top388.file_path)
        if _is_api388:
            out.append(
                f"\nAPI hotspot: {_top388.file_path.rsplit('/', 1)[-1]} is a route/endpoint file"
                f" — endpoint files should delegate; move logic to service layer to reduce hotspot"
            )

    # S382: Deep call chain hotspot — top hotspot has 3+ direct callers AND 5+ depth-2 callers.
    # Symbols with deep fan-in are harder to refactor safely; changes propagate through
    # multiple layers, and intermediate layers may have baked-in assumptions.
    if scores:
        _top382 = scores[0][1]
        if not _is_test_file(_top382.file_path):
            _direct382 = {
                e.source_id for e in graph.edges
                if e.kind.value == "calls" and e.target_id == _top382.id
            }
            _d2_382 = {
                e.source_id for e in graph.edges
                if e.kind.value == "calls" and e.target_id in _direct382
            }
            if len(_direct382) >= 3 and len(_d2_382) >= 5:
                out.append(
                    f"\ndeep call chain: {_top382.name} has {len(_direct382)} direct callers"
                    f" and {len(_d2_382)} depth-2 callers"
                    f" — refactors propagate through multiple layers; map all call paths before changing"
                )

    # S400: Test-file hotspot — the top hotspot symbol lives inside a test file itself.
    # Test files should not be hotspots; if a test helper is the most-called symbol it has
    # leaked production logic into tests, or tests are overly interdependent.
    if scores:
        _top400 = scores[0][1]
        if _is_test_file(_top400.file_path):
            _callers400 = {
                e.source_id for e in graph.edges
                if e.kind.value == "calls" and e.target_id == _top400.id
            }
            out.append(
                f"\ntest-file hotspot: {_top400.name} (in {_top400.file_path.rsplit('/', 1)[-1]})"
                f" is the top hotspot with {len(_callers400)} caller(s)"
                f" — test helpers should not accumulate logic; extract shared helpers to a src/ utility"
            )

    # S406: Init-file hotspot — the top hotspot symbol lives inside an __init__.py.
    # __init__.py hotspots indicate the package initializer has become a logic hub;
    # init files should only re-export symbols, not hold business logic.
    if scores:
        _top406 = scores[0][1]
        _fname406 = _top406.file_path.rsplit("/", 1)[-1].lower()
        if _fname406 in ("__init__.py", "index.js", "index.ts", "index.tsx"):
            _callers406 = {
                e.source_id for e in graph.edges
                if e.kind.value == "calls" and e.target_id == _top406.id
            }
            if len(_callers406) >= 2:
                out.append(
                    f"\ninit-file hotspot: {_top406.name} (in {_fname406}) is the top hotspot"
                    f" with {len(_callers406)} caller(s)"
                    f" — init files should only re-export; move logic to a dedicated module"
                )

    # S412: Hotspot with no test coverage — top hotspot file has no corresponding test file.
    # Hotspot files accumulate the most change pressure yet are the least protected when
    # they have no test counterpart; regressions in hotspots are the most costly to fix.
    if scores:
        _top412 = scores[0][1]
        if not _is_test_file(_top412.file_path):
            _name412 = _top412.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            _test_files412 = [
                fp for fp in graph.files
                if _is_test_file(fp) and _name412 in fp
            ]
            if not _test_files412:
                out.append(
                    f"\nuntested hotspot: {_top412.file_path.rsplit('/', 1)[-1]} has no"
                    f" corresponding test file"
                    f" — hotspot files change most often; add tests before the next modification"
                )

    # S418: Vendor/third-party hotspot — top hotspot file lives in a vendor/ or third_party/ dir.
    # Hotspots in vendored code are especially dangerous because the code is not authored by
    # the team; changes to it bypass normal review and will be overwritten on next vendor update.
    if scores:
        _top418 = scores[0][1]
        _vendor_dirs418 = ("vendor/", "third_party/", "vendors/", "node_modules/", "external/")
        if any(d in _top418.file_path.lower() for d in _vendor_dirs418):
            out.append(
                f"\nvendor hotspot: {_top418.file_path.rsplit('/', 1)[-1]} is in a vendor directory"
                f" — vendored hotspots will be overwritten on next vendor update; consider wrapping"
            )

    # S424: Class hotspot — the top hotspot symbol is a class (not a function or method).
    # A class appearing as a hotspot is unusual; it typically means the class is being
    # instantiated in too many places, suggesting it should be injected or turned into a singleton.
    if scores:
        _top424 = scores[0][1]
        if _top424.kind.value == "class" and not _is_test_file(_top424.file_path):
            _class_callers424 = {
                e.source_id for e in graph.edges
                if e.kind.value == "calls" and e.target_id == _top424.id
            }
            if len(_class_callers424) >= 3:
                out.append(
                    f"\nclass hotspot: {_top424.name} is a class with {len(_class_callers424)} caller(s)"
                    f" — class instantiated in many places; consider DI/singleton to reduce coupling"
                )


def _signals_hotspots_core_b_risk(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S430: High-complexity hotspot — top hotspot symbol has cyclomatic complexity >= 20.
    # High complexity means many execution paths; each path needs its own test scenario.
    # Complex hotspots are refactor targets AND test coverage bottlenecks simultaneously.
    if scores:
        _top430 = scores[0][1]
        if not _is_test_file(_top430.file_path) and (_top430.complexity or 0) >= 20:
            out.append(
                f"\nhigh-complexity hotspot: {_top430.name} has cyclomatic complexity {_top430.complexity}"
                f" — {_top430.complexity} distinct paths need test coverage; refactor before growing further"
            )

    # S436: Data-layer hotspot — top hotspot symbol lives in a DAO/repository/model/ORM file.
    # Data-layer hotspots are especially risky: changes propagate through every service that
    # reads that model, can invalidate caches across the stack, and may require migrations.
    _s436_data_keywords = ("dao", "repository", "repo", "model", "orm", "schema", "database", "db")
    if scores:
        _top436 = scores[0][1]
        _fp436 = _top436.file_path.lower().replace("\\", "/")
        if not _is_test_file(_top436.file_path) and any(kw in _fp436 for kw in _s436_data_keywords):
            out.append(
                f"\ndata-layer hotspot: {_top436.name} lives in {_top436.file_path.rsplit('/', 1)[-1]}"
                f" — data-layer changes cascade through every reader; check cache invalidation and migrations"
            )

    # S442: Churn disparity — top hotspot file is changed much more than the rest.
    # A single file that dominates the churn history is a magnet for bugs and drift;
    # it is often a god object, a catch-all module, or an underabstracted core.
    if scores and len(scores) >= 3:
        _top442 = scores[0]
        _second442 = scores[1]
        _top_cx442 = _top442[0]
        _second_cx442 = _second442[0]
        if _second_cx442 > 0 and _top_cx442 >= _second_cx442 * 3 and not _is_test_file(_top442[1].file_path):
            out.append(
                f"\nchurn disparity: {_top442[1].name} has {_top_cx442}× score vs {_second_cx442}×"
                f" for second-place — extreme outlier; likely a god object or catch-all module"
            )

    # S448: Untested hotspot file — top hotspot has no test file covering it by name.
    # The hottest file in the repo having no corresponding test is doubly risky:
    # high churn without a safety net means every change is an untested regression risk.
    if scores:
        _top448 = scores[0][1]
        if not _is_test_file(_top448.file_path):
            _stem448 = _top448.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            _has_test448 = any(
                _stem448 in fp and _is_test_file(fp)
                for fp in graph.files
            )
            if not _has_test448:
                out.append(
                    f"\nuntested hotspot: {_top448.file_path.rsplit('/', 1)[-1]} is the top hotspot"
                    f" with no corresponding test file"
                    f" — high-churn code with no safety net; add tests before modifying"
                )

    # S455: Shared fixture hotspot — top hotspot is a shared test fixture (conftest.py / fixtures/).
    # Shared fixtures are an invisible test dependency; changing conftest.py or a shared fixture
    # breaks every test that uses it, even tests in unrelated directories.
    if scores:
        _top455 = scores[0][1]
        _fp455 = _top455.file_path.lower().replace("\\", "/")
        _is_fixture455 = (
            "conftest" in _fp455
            or "fixture" in _fp455
            or _fp455.endswith("/fixtures.py")
            or "/fixtures/" in _fp455
        )
        if _is_fixture455:
            out.append(
                f"\nfixture hotspot: {_top455.file_path.rsplit('/', 1)[-1]} is a shared test fixture"
                f" — changes break every test that depends on it; refactor carefully"
            )

    # S461: Bottleneck function — single function accounts for majority of cross-file calls.
    # When one function is called from many more places than the next highest, removing or
    # changing it has outsized impact; it acts as a chokepoint for the entire dependency graph.
    if len(scores) >= 2:
        _top461 = scores[0][1]
        _second461 = scores[1][1]
        _top_callers461 = len([
            e for e in graph.edges
            if e.kind.value == "calls" and e.target_id == _top461.id
            and graph.symbols.get(e.source_id, _top461).file_path != _top461.file_path
        ])
        _second_callers461 = len([
            e for e in graph.edges
            if e.kind.value == "calls" and e.target_id == _second461.id
            and graph.symbols.get(e.source_id, _second461).file_path != _second461.file_path
        ])
        if not _is_test_file(_top461.file_path) and _second_callers461 > 0 and _top_callers461 >= _second_callers461 * 4:
            out.append(
                f"\nbottleneck function: {_top461.name} has {_top_callers461}× cross-file callers"
                f" vs {_second_callers461} for the next hotspot"
                f" — chokepoint function; changes here cascade broadly"
            )

    # S466: Cross-module hotspot — top hotspot is imported by files in 3+ directories.
    # A hotspot that spans directory boundaries is a cross-cutting concern;
    # refactoring it requires changes in every consuming directory simultaneously.
    if scores:
        _top466 = scores[0][1]
        if not _is_test_file(_top466.file_path):
            _importer466_dirs: set[str] = set()
            for _imp466 in graph.importers_of(_top466.file_path):
                _dir466 = _imp466.rsplit("/", 1)[0] if "/" in _imp466 else ""
                if _dir466 != (_top466.file_path.rsplit("/", 1)[0] if "/" in _top466.file_path else ""):
                    _importer466_dirs.add(_dir466)
            if len(_importer466_dirs) >= 3:
                out.append(
                    f"\ncross-module hotspot: {_top466.file_path.rsplit('/', 1)[-1]} is imported"
                    f" from {len(_importer466_dirs)} different directories"
                    f" — cross-cutting concern; refactoring requires coordinated changes in every consumer directory"
                )

    # S472: API hotspot — top hotspot lives in an api/ or routes/ file.
    # API hotspots are web-facing contracts; any change to the function signature
    # or return shape breaks clients even if internal callers look fine.
    if scores:
        _top472 = scores[0][1]
        _fp472 = _top472.file_path.lower().replace("\\", "/")
        _api_keywords472 = ("/api/", "/routes/", "/endpoints/", "/views/", "/handlers/", "/controllers/")
        _is_api472 = (
            any(kw in _fp472 for kw in _api_keywords472)
            or _fp472.rsplit("/", 1)[-1].startswith(("api_", "route_", "endpoint_", "view_", "handler_", "controller_"))
        )
        if _is_api472 and not _is_test_file(_top472.file_path):
            out.append(
                f"\nAPI hotspot: {_top472.name} lives in {_top472.file_path.rsplit('/', 1)[-1]}"
                f" — web-facing contract; changing signature or return shape breaks clients"
            )

    # S478: Generated-file hotspot — top hotspot lives in an auto-generated file.
    # Changes to generated files are overwritten on the next code-gen run;
    # patches should target the generator template, not the generated output.
    if scores:
        _top478 = scores[0][1]
        _fp478 = _top478.file_path.lower().replace("\\", "/")
        _gen_keywords478 = ("_gen.", "_generated.", ".gen.", ".generated.", "generated_", "autogenerated")
        _is_gen478 = any(kw in _fp478 for kw in _gen_keywords478)
        if _is_gen478 and not _is_test_file(_top478.file_path):
            out.append(
                f"\ngenerated-file hotspot: {_top478.file_path.rsplit('/', 1)[-1]} is auto-generated"
                f" — changes will be overwritten on next codegen run; patch the generator template instead"
            )

    # S486: Hotspot file has no test file — top hotspot source file has no corresponding test.
    # The most-called file in the repo has no dedicated test coverage; any change is a gamble.
    # High call-count + zero tests = maximum blast radius, minimum safety net.
    if scores:
        _top486 = scores[0][1]
        if not _is_test_file(_top486.file_path):
            _base486 = _top486.file_path.rsplit("/", 1)[-1].replace(".py", "")
            _has_test486 = any(
                f"test_{_base486}" in fp or f"{_base486}_test" in fp
                for fp in graph.files
            )
            if not _has_test486:
                out.append(
                    f"\nno test file: {_top486.file_path.rsplit('/', 1)[-1]} is the top hotspot but has no test file"
                    f" — the most-called file in the repo has zero dedicated test coverage"
                )

    # S492: Solo file hotspot — top hotspot is the only file in its directory.
    # A solo file has no sibling modules to share load with; there is no natural place
    # to extract helpers, so the file will keep growing and complexity will compound.
    if scores:
        _top492 = scores[0][1]
        if not _is_test_file(_top492.file_path):
            _dir492 = _top492.file_path.rsplit("/", 1)[0] if "/" in _top492.file_path else ""
            if _dir492:
                _siblings492 = [
                    fp for fp in graph.files
                    if not _is_test_file(fp)
                    and fp != _top492.file_path
                    and (fp.rsplit("/", 1)[0] if "/" in fp else "") == _dir492
                ]
                if not _siblings492:
                    out.append(
                        f"\nsolo file: {_top492.file_path.rsplit('/', 1)[-1]} is the only source file in {_dir492}/"
                        f" — no siblings to share load; complexity will compound with each change"
                    )


def _signals_hotspots_core_b(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    _signals_hotspots_core_b_type(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_b_concentration(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_b_structure(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_b_risk(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)



def _signals_hotspots_core_c_type(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S498: Hotspot wrapper/adapter — top hotspot filename suggests it wraps an external dependency.
    # Wrapper files couple the internal system to an external API; changes must be validated
    # against both the internal callers and the external dependency's contract.
    if scores:
        _top498 = scores[0][1]
        if not _is_test_file(_top498.file_path):
            _wrap_keywords498 = ("wrapper", "adapter", "proxy", "facade", "bridge", "client")
            _fp_lower498 = _top498.file_path.lower().replace("_", "").replace("-", "")
            if any(kw in _fp_lower498 for kw in _wrap_keywords498):
                out.append(
                    f"\nwrapper hotspot: {_top498.file_path.rsplit('/', 1)[-1]} wraps an external dependency"
                    f" — changes must satisfy both internal callers and the external API contract"
                )

    # S503: Exception class hotspot — the top hotspot is an exception/error class definition.
    # Exception classes used widely define the error taxonomy; changing hierarchy, message format,
    # or base class breaks all `except` clauses and error-handling code across consumers.
    if scores:
        _top503 = scores[0][1]
        if not _is_test_file(_top503.file_path) and _top503.kind.value == "class":
            _exc_keywords503 = ("error", "exception", "fault", "failure", "abort")
            if any(kw in _top503.name.lower() for kw in _exc_keywords503):
                _callers503 = graph.callers_of(_top503.id)
                if len(_callers503) >= 3:
                    out.append(
                        f"\nexception hotspot: {_top503.name} is an exception class with {len(_callers503)} caller(s)"
                        f" — changing the hierarchy or message format breaks all except-handlers"
                    )

    # S510: Async hotspot — top hotspot function is a coroutine (async def).
    # Async hotspots are the most-called awaitables in the system; a change to their
    # concurrency model (adding a lock, changing executor) blocks all callers.
    if scores:
        _top510 = scores[0][1]
        if not _is_test_file(_top510.file_path) and _top510.kind.value in ("function", "method"):
            _sig510 = _top510.signature or ""
            if _sig510.startswith("async "):
                out.append(
                    f"\nasync hotspot: {_top510.name} is the top-called async function"
                    f" — adding blocking calls or changing its event-loop behavior affects all awaiters"
                )

    # S535: Class hierarchy hotspot — top hotspot is a class with subclasses.
    # A base class that is also the most-instantiated symbol is a fragile foundation;
    # changes to its interface break all subclasses and require coordinated updates.
    if scores:
        _top535 = scores[0][1]
        if not _is_test_file(_top535.file_path) and _top535.kind.value == "class":
            _subs535 = [
                e for e in graph.edges
                if e.kind.value == "inherits" and e.target_id == _top535.id
            ]
            if _subs535:
                out.append(
                    f"\nclass hierarchy hotspot: {_top535.name} is the most-called class"
                    f" and has {len(_subs535)} subclass(es)"
                    f" — base class changes break all subclasses; coordinate updates"
                )


def _signals_hotspots_core_c_shape(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S529: Long hotspot function — top hotspot has 50+ lines of code.
    # A large, heavily-called function accumulates complexity over time; it is the most-used
    # yet least-testable function in the system — every caller depends on all of its behaviors.
    if scores:
        _top529 = scores[0][1]
        if not _is_test_file(_top529.file_path) and _top529.kind.value in ("function", "method"):
            _len529 = (_top529.line_end or 0) - (_top529.line_start or 0)
            if _len529 >= 50:
                out.append(
                    f"\nlong hotspot: {_top529.name} is {_len529} lines and the most-called function"
                    f" — large + hot = refactor pressure; extract sub-functions before it grows further"
                )

    # S523: Utility module hotspot — top hotspot lives in a utils/helpers/common file.
    # When a utility module becomes the most-called code, it signals responsibility creep;
    # the function has outgrown "utility" status and should be promoted to its own domain module.
    if scores:
        _top523 = scores[0][1]
        if not _is_test_file(_top523.file_path) and _top523.kind.value in ("function", "method"):
            _fp523 = _top523.file_path.lower().replace("\\", "/")
            _util_markers523 = ("utils", "helpers", "common", "shared", "tools", "misc", "util")
            if any(m in _fp523 for m in _util_markers523):
                out.append(
                    f"\nutility module hotspot: {_top523.name} is the most-called function in a utility file"
                    f" — utility hotspots signal responsibility creep; consider promoting to a domain module"
                )

    # S517: Deprecated hotspot — top hotspot symbol name suggests it is marked for removal.
    # A deprecated function that remains the most-called symbol blocks cleanup;
    # callers prevent deprecation follow-through and the warning loses urgency over time.
    if scores:
        _top517 = scores[0][1]
        if not _is_test_file(_top517.file_path) and _top517.kind.value in ("function", "method"):
            _name517 = _top517.name.lower()
            _dep_markers517 = ("_old", "_legacy", "deprecated", "_v1", "_deprecated", "_obsolete")
            if any(m in _name517 for m in _dep_markers517):
                out.append(
                    f"\ndeprecated hotspot: {_top517.name} appears deprecated but is still the most-called symbol"
                    f" — active callers block removal; plan migration path before it accumulates more callers"
                )

    # S541: Single-file hotspot cluster — top 3 hotspots all live in the same file.
    # When the most-called symbols concentrate in one file, that file is a load-bearing monolith:
    # any change to it risks cross-cutting breakage; it is both highest-value and highest-risk to refactor.
    if len(scores) >= 3:
        _top3_files541 = {s.file_path for _, s in scores[:3] if not _is_test_file(s.file_path)}
        if len(_top3_files541) == 1:
            _cluster_file541 = next(iter(_top3_files541)).rsplit("/", 1)[-1]
            out.append(
                f"\nhotspot cluster: top 3 hotspots all live in {_cluster_file541}"
                f" — concentrated complexity; that file is load-bearing; consider splitting by responsibility"
            )

    # S544: Interface file hotspot — top hotspot lives in an abstract/interface/base/protocol file.
    # Interface-level symbols define contracts for many concrete implementations; changes cascade
    # to all implementors and must be coordinated across the full class hierarchy.
    if scores:
        _top541b = scores[0][1]
        if not _is_test_file(_top541b.file_path) and _top541b.kind.value in ("function", "method"):
            _fp541b = _top541b.file_path.lower().replace("\\", "/")
            _iface_markers541 = ("abstract", "interface", "base", "protocol", "mixin", "abc")
            if any(m in _fp541b for m in _iface_markers541):
                out.append(
                    f"\ninterface file hotspot: {_top541b.name} is the top hotspot in an abstract/interface file"
                    f" — changes cascade to all implementing classes; coordinate with implementors"
                )

    # S550: Private hotspot — top hotspot is a private (_-prefixed) function heavily called externally.
    # Private symbols called from many external sites indicate an accidental public API;
    # the naming contradiction misleads maintainers about intended encapsulation.
    if scores:
        _top550 = scores[0][1]
        if (
            not _is_test_file(_top550.file_path)
            and _top550.name.startswith("_")
            and not _top550.name.startswith("__")
            and _top550.kind.value in ("function", "method")
        ):
            out.append(
                f"\nprivate hotspot: {_top550.name} is private but heavily called"
                f" — accidental public API; rename without _ or expose via a public wrapper"
            )

    # S556: Hotspot untested — top hotspot's source file has no corresponding test file.
    # The most-called symbol in the project has no test coverage; a bug here propagates
    # to every caller with no automated safety net to catch the regression.
    if scores:
        _top556 = scores[0][1]
        if not _is_test_file(_top556.file_path):
            _stem556 = _top556.file_path.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "")
            _test_files556 = {fp.replace("\\", "/").rsplit("/", 1)[-1] for fp in graph.files if _is_test_file(fp)}
            _has_test556 = (
                f"test_{_stem556}.py" in _test_files556
                or f"{_stem556}_test.py" in _test_files556
            )
            if not _has_test556:
                out.append(
                    f"\nhotspot untested: {_top556.name} is the top hotspot but its file has no test coverage"
                    f" — most-called symbol with no safety net; add tests before modifying"
                )

    # S562: Cross-package hotspot — top hotspot is called from 3+ distinct top-level directories.
    # A symbol called across many packages is a hidden global dependency; its contracts
    # affect every team/module that imports it; even small signature changes ripple broadly.
    if scores:
        _top562 = scores[0][1]
        if not _is_test_file(_top562.file_path):
            _callers562 = graph.callers_of(_top562.id)  # list[Symbol]
            _top_dirs562 = {
                s.file_path.replace("\\", "/").split("/")[0]
                for s in _callers562
                if not _is_test_file(s.file_path) and "/" in s.file_path.replace("\\", "/")
            }
            if len(_top_dirs562) >= 3:
                out.append(
                    f"\ncross-package hotspot: {_top562.name} called from {len(_top_dirs562)} different top-level packages"
                    f" — hidden global dependency; signature changes cascade across all packages"
                )

    # S568: Deep hotspot — top hotspot lives 3+ directory levels deep.
    # Hotspots buried in deep module hierarchies indicate that important shared logic
    # is hiding in a sub-component; it may need promotion to a shallower, more visible module.
    if scores:
        _top568 = scores[0][1]
        if not _is_test_file(_top568.file_path):
            _parts568 = _top568.file_path.replace("\\", "/").split("/")
            if len(_parts568) >= 4:  # ≥3 directory levels (a/b/c/file.py)
                out.append(
                    f"\ndeep hotspot: {_top568.name} is the top hotspot but buried {len(_parts568) - 1} levels deep"
                    f" — important code in a deep submodule; consider promoting to a shallower location"
                )

    # S574: Test-dominated hotspot — top hotspot has callers, but all callers are test files.
    # A symbol called only by tests is effectively internal test infrastructure;
    # it may appear production-critical but is actually test-only — safe to refactor aggressively.
    if scores:
        _top574 = scores[0][1]
        if not _is_test_file(_top574.file_path):
            _callers574 = graph.callers_of(_top574.id)
            if _callers574 and all(_is_test_file(s.file_path) for s in _callers574):
                out.append(
                    f"\ntest-dominated hotspot: {_top574.name} appears busy but all callers are test files"
                    f" — internal test utility, not production-critical; safe to refactor aggressively"
                )

    # S580: Wide-file hotspot — top hotspot file has 10+ symbols, making it a dense module.
    # High-symbol-count files concentrate many responsibilities; even small changes require
    # understanding a large context window of co-located symbols.
    if scores:
        _top580 = scores[0][1]
        if not _is_test_file(_top580.file_path) and _top580.file_path in graph.files:
            _fi580_syms = len(graph.files[_top580.file_path].symbols)
            if _fi580_syms >= 10:
                out.append(
                    f"\nwide-file hotspot: {_top580.name} is in {_top580.file_path.rsplit('/', 1)[-1]}"
                    f" which has {_fi580_syms} symbols — dense module; changes require large context window"
                )


def _signals_hotspots_core_c_classification(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S585: Low-complexity hotspot — top hotspot function has cyclomatic complexity < 3.
    # A heavily-called but trivially simple function suggests it's a routing shim or
    # dispatcher; the real complexity lives in its callees, not the hotspot itself.
    if scores:
        _top585 = scores[0][1]
        if (
            not _is_test_file(_top585.file_path)
            and _top585.kind.value in ("function", "method")
            and _top585.complexity < 3
        ):
            _caller_count585 = len(graph.callers_of(_top585.id))
            if _caller_count585 >= 3:
                out.append(
                    f"\nlow-complexity hotspot: {_top585.name} has {_caller_count585} callers"
                    f" but complexity {_top585.complexity} — likely a dispatcher or shim;"
                    f" real complexity lives in its callees"
                )

    # S591: Init-file hotspot — top hotspot symbol lives in a package __init__.py.
    # Hotspots in __init__ files indicate the package boundary is a logical chokepoint;
    # any change to the init ripples through all consumers of the package.
    if scores:
        _top591 = scores[0][1]
        _fp591 = _top591.file_path.replace("\\", "/")
        if not _is_test_file(_fp591) and (_fp591.endswith("/__init__.py") or _fp591 == "__init__.py"):
            out.append(
                f"\ninit-file hotspot: {_top591.name} is in __init__.py"
                f" — package boundary is a chokepoint; changes affect all package consumers"
            )

    # S597: Narrow hotspot spread — all top-5 hotspots share the same file.
    # When the hottest symbols are all co-located, that file is a bottleneck;
    # consider splitting responsibilities to reduce change collision risk.
    if len(scores) >= 5:
        _top5_files597 = [s.file_path for _, s in scores[:5] if not _is_test_file(s.file_path)]
        if len(set(_top5_files597)) == 1:
            _narrow_file597 = _top5_files597[0].rsplit("/", 1)[-1]
            out.append(
                f"\nnarrow hotspot spread: all top 5 hotspots are in {_narrow_file597}"
                f" — this file is a bottleneck; split responsibilities to reduce change collision"
            )

    # S604: Test hotspot — the top hotspot symbol is a test function/class (test file).
    # A test function appearing as a hotspot usually means fixtures or helpers are being
    # called by many tests rather than production code; worth reviewing for extraction.
    if scores:
        _top604 = scores[0][1]
        if _is_test_file(_top604.file_path):
            _caller_count604 = len(graph.callers_of(_top604.id))
            out.append(
                f"\ntest hotspot: {_top604.name} (in {_top604.file_path.rsplit('/', 1)[-1]})"
                f" is a test-file symbol with {_caller_count604} callers"
                f" — consider extracting to a shared fixture or helper module"
            )

    # S610: Non-Python hotspot — top hotspot file is not a Python file.
    # When the highest-churn symbol is in a JS/TS/Go/Rust file, agents should apply
    # language-specific refactoring guidance rather than Python patterns.
    if scores:
        _top610 = scores[0][1]
        _fp610 = _top610.file_path.replace("\\", "/")
        if not _is_test_file(_fp610) and not _fp610.endswith(".py"):
            _ext610 = _fp610.rsplit(".", 1)[-1] if "." in _fp610 else "unknown"
            out.append(
                f"\nnon-Python hotspot: {_top610.name} is in a {_ext610} file"
                f" — apply {_ext610}-specific refactoring patterns rather than Python conventions"
            )

    # S616: Exported hotspot — top hotspot is an exported (public) symbol.
    # Public hotspots are part of the module's API surface; callers depend on their signature
    # and behavior, making refactoring more disruptive than for private internals.
    if scores:
        _top616 = scores[0][1]
        if (
            not _is_test_file(_top616.file_path)
            and _top616.kind.value in ("function", "method", "class")
            and _top616.exported
        ):
            _caller_count616 = len(graph.callers_of(_top616.id))
            out.append(
                f"\npublic hotspot: {_top616.name} is a public symbol with {_caller_count616} callers"
                f" — part of the module API; signature changes require coordinating all callers"
            )

    # S622: Class hotspot with many methods — top hotspot is a class with 5+ method children.
    # A heavily-called class with many methods is a god object candidate; it has multiple
    # reasons to change and high cognitive load for anyone modifying it.
    if scores:
        _top622 = scores[0][1]
        if (
            not _is_test_file(_top622.file_path)
            and _top622.kind.value == "class"
        ):
            _methods622 = [
                c for c in graph.children_of(_top622.id)
                if c.kind.value in ("method", "function")
            ]
            if len(_methods622) >= 5:
                out.append(
                    f"\ngod-class hotspot: {_top622.name} has {len(_methods622)} methods"
                    f" — high-churn class with many responsibilities; consider splitting"
                )

    # S628: Hotspot cluster — top 3 hotspots are all in the same directory.
    # When multiple hotspots concentrate in one directory, that directory is a change
    # magnet; it may contain a poorly-separated subsystem that warrants its own module.
    if len(scores) >= 3:
        _top3_dirs628 = [
            s.file_path.replace("\\", "/").rsplit("/", 1)[0]
            for _, s in scores[:3]
            if not _is_test_file(s.file_path) and "/" in s.file_path.replace("\\", "/")
        ]
        if len(_top3_dirs628) == 3 and len(set(_top3_dirs628)) == 1:
            _cluster_dir628 = _top3_dirs628[0].rsplit("/", 1)[-1]
            out.append(
                f"\nhotspot cluster: top 3 hotspots are all in {_cluster_dir628}/"
                f" — change magnet directory; may warrant extraction into its own package"
            )

    # S634: Single-symbol file hotspot — hotspot symbol is the only non-test symbol in its file.
    # When a file exists solely to hold one heavily-called symbol, that symbol is the file's
    # sole reason to exist — it may be better placed in a higher-level module.
    if scores:
        _top634 = scores[0][1]
        if not _is_test_file(_top634.file_path):
            _file_syms634 = [
                s for s in graph.symbols_in_file(_top634.file_path)
                if not _is_test_file(s.file_path) and s.parent_id is None
            ]
            if len(_file_syms634) == 1:
                _caller_count634 = len(graph.callers_of(_top634.id))
                out.append(
                    f"\nsingle-symbol hotspot: {_top634.name} is the only symbol in"
                    f" {_top634.file_path.rsplit('/', 1)[-1]} ({_caller_count634} callers)"
                    f" — consider inlining into a higher-level module"
                )

    # S640: Method hotspot cluster — all top-5 non-test hotspots are methods (not functions).
    # When all hotspots are class methods, the churn concentrates inside class hierarchies;
    # this often signals a class that has accreted too many responsibilities over time.
    if len(scores) >= 5:
        _top5_kinds640 = [s.kind.value for _, s in scores[:5] if not _is_test_file(s.file_path)]
        if len(_top5_kinds640) == 5 and all(k == "method" for k in _top5_kinds640):
            _top640 = scores[0][1]
            out.append(
                f"\nmethod hotspot cluster: all top 5 hotspots are class methods"
                f" (top: {_top640.name})"
                f" — churn concentrated in class hierarchy; review for god-class patterns"
            )

    # S646: Zero-complexity hotspot — top hotspot has complexity=1 but 5+ callers (trivial dispatch).
    # A function that simply delegates to another (complexity=1) shouldn't be a hotspot;
    # if many callers use it, they may be better served calling the underlying function directly.
    if scores:
        _top646 = scores[0][1]
        if (
            not _is_test_file(_top646.file_path)
            and _top646.kind.value in ("function", "method")
            and (_top646.complexity or 1) <= 1
        ):
            _caller_count646 = len(graph.callers_of(_top646.id))
            if _caller_count646 >= 5:
                out.append(
                    f"\ntrivial hotspot: {_top646.name} has {_caller_count646} callers"
                    f" but complexity=1 — thin dispatcher; callers may benefit from calling the underlying directly"
                )

    # S652: Dead co-location — hotspot file also contains dead symbols.
    # When a high-churn file also has unused symbols, the file has both growth pressure
    # AND dead weight — it's a refactoring priority: trim dead code before adding features.
    if scores:
        _top652 = scores[0][1]
        if not _is_test_file(_top652.file_path):
            _dead652 = graph.find_dead_code()
            _dead_in_file652 = [s for s in _dead652 if s.file_path == _top652.file_path]
            if _dead_in_file652:
                _dead_names652 = ", ".join(s.name for s in _dead_in_file652[:3])
                out.append(
                    f"\ndead co-location: {_top652.file_path.rsplit('/', 1)[-1]} is a hotspot"
                    f" with {len(_dead_in_file652)} unused symbol(s) ({_dead_names652})"
                    f" — trim dead code before adding features to this file"
                )

    # S658: Repo-wide top caller — top hotspot is also the most-called symbol in the entire graph.
    # This symbol is not just a local hotspot; it dominates the entire codebase's call graph.
    # Refactoring it has global scope: every caller, test, and integration depends on it.
    if scores:
        _top658 = scores[0][1]
        if not _is_test_file(_top658.file_path):
            _max_callers658 = max(
                (len(graph.callers_of(s.id)) for s in graph.symbols.values()
                 if not _is_test_file(s.file_path)),
                default=0,
            )
            _top_callers658 = len(graph.callers_of(_top658.id))
            if _top_callers658 >= 5 and _top_callers658 == _max_callers658:
                out.append(
                    f"\nrepo-wide top caller: {_top658.name} is called by {_top_callers658} symbols"
                    f" — most-called in the entire repo; global refactoring scope"
                )

    # S664: Pure dispatcher — top hotspot has 5+ callers but makes 0 callees (calls nothing).
    # A function that many call but that calls nothing itself is a pure data processor;
    # it may be overloaded or doing implicit global-state operations that aren't visible in the graph.
    if scores and scores[0]:
        _top664 = scores[0][1]
        if (
            not _is_test_file(_top664.file_path)
            and _top664.kind.value in ("function", "method")
        ):
            _callers664 = graph.callers_of(_top664.id)
            _callees664 = graph.callees_of(_top664.id)
            if len(_callers664) >= 5 and not _callees664:
                out.append(
                    f"\npure dispatcher: {_top664.name} has {len(_callers664)} callers"
                    f" and calls nothing — pure data processor or implicit state manipulation"
                )

    # S670: Hotspot concentration — top 3 hotspots are all in the same file.
    # When multiple top hotspots share a file, that file is a global bottleneck;
    # changes anywhere in it risk cascading effects and warrant extra review focus.
    if len(scores) >= 3:
        _top3_files670 = [s[1].file_path for s in scores[:3] if s[1] is not None]
        _non_test670 = [fp for fp in _top3_files670 if not _is_test_file(fp)]
        if len(_non_test670) == 3 and len(set(_non_test670)) == 1:
            out.append(
                f"\nhotspot concentration: top 3 hotspots all in {_non_test670[0].rsplit('/', 1)[-1]}"
                f" — single-file bottleneck; changes here have outsized blast radius"
            )


def _signals_hotspots_core_c_callers(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S676: Test-only callers — top hotspot has callers but all are from test files.
    # A production symbol called exclusively from tests is a test-facing internal;
    # it may be over-exposed API or a sign that tests bypass the intended public interface.
    if scores and scores[0]:
        _top676 = scores[0][1]
        if (
            _top676 is not None
            and not _is_test_file(_top676.file_path)
            and _top676.kind.value in ("function", "method", "class")
        ):
            _callers676 = graph.callers_of(_top676.id)
            if _callers676 and all(_is_test_file(c.file_path) for c in _callers676):
                out.append(
                    f"\ntest-only callers: {_top676.name} is called exclusively from test files"
                    f" ({len(_callers676)} test caller(s)) — over-exposed internal or tests bypassing public API"
                )

    # S682: Complexity outlier — top hotspot complexity is 5x+ the second hotspot's complexity.
    # When one symbol dramatically outscores others in complexity, it's an extreme outlier;
    # it concentrates cognitive risk and is the highest-priority refactoring target.
    if len(scores) >= 2:
        _top682 = scores[0][1]
        _second682 = scores[1][1]
        if (
            _top682 is not None
            and _second682 is not None
            and not _is_test_file(_top682.file_path)
            and _second682.complexity > 0
            and _top682.complexity >= _second682.complexity * 5
        ):
            out.append(
                f"\ncomplexity outlier: {_top682.name} complexity={_top682.complexity}"
                f" vs next={_second682.complexity} — extreme outlier; highest-priority refactor target"
            )

    # S688: Solo file hotspot — top hotspot is the only non-test symbol in its file.
    # A file with a single hotspot symbol is a candidate for inlining into its callers
    # or merging into a related module to reduce file proliferation.
    if scores and scores[0]:
        _top688 = scores[0][1]
        if _top688 is not None and not _is_test_file(_top688.file_path):
            _file_syms688 = [
                s for s in graph.symbols.values()
                if s.file_path == _top688.file_path
                and s.parent_id is None
                and not _is_test_file(s.file_path)
            ]
            if len(_file_syms688) == 1:
                out.append(
                    f"\nsingle-symbol hotspot: {_top688.name} is the only symbol in"
                    f" {_top688.file_path.rsplit('/', 1)[-1]}"
                    f" — single-symbol file; consider inlining or merging"
                )

    # S694: Wrapper class hotspot — top hotspot is a method in a class with only 1-2 methods.
    # A hotspot method inside a near-empty class suggests the class is a thin wrapper;
    # the class adds abstraction overhead without providing enough behaviour to justify it.
    if scores and scores[0]:
        _top694 = scores[0][1]
        if (
            _top694 is not None
            and not _is_test_file(_top694.file_path)
            and _top694.kind.value == "method"
            and _top694.parent_id is not None
        ):
            _parent694 = graph.symbols.get(_top694.parent_id)
            if _parent694 is not None:
                _sibling_methods694 = [
                    c for c in graph.children_of(_parent694.id)
                    if c.kind.value in ("method", "function")
                ]
                if len(_sibling_methods694) <= 2:
                    out.append(
                        f"\nwrapper class hotspot: {_top694.name} is a method in"
                        f" {_parent694.name} which has only {len(_sibling_methods694)} method(s)"
                        f" — thin wrapper; consider inlining the class"
                    )

    # S700: Package cluster — top 2 hotspots are in the same directory (module-level bottleneck).
    # When the highest-ranked hotspots share a parent directory, that package is a focal point;
    # its internal coupling is high and changes to the package ripple across many consumers.
    if len(scores) >= 2:
        _top700 = scores[0][1]
        _second700 = scores[1][1]
        if (
            _top700 is not None
            and _second700 is not None
            and not _is_test_file(_top700.file_path)
            and not _is_test_file(_second700.file_path)
            and _top700.file_path != _second700.file_path
        ):
            _dir700_top = _top700.file_path.replace("\\", "/").rsplit("/", 1)[0]
            _dir700_sec = _second700.file_path.replace("\\", "/").rsplit("/", 1)[0]
            if _dir700_top and _dir700_top == _dir700_sec:
                _pkg_name700 = _dir700_top.rsplit("/", 1)[-1]
                out.append(
                    f"\npackage cluster: top 2 hotspots both in {_pkg_name700}/"
                    f" — module-level bottleneck; consider splitting or extracting an interface"
                )

    # S706: Large function body — top hotspot has byte_size > 3000 bytes.
    # A hotspot that is also physically large concentrates both traffic and logic;
    # it's the highest-priority target for extraction and complexity reduction.
    if scores and scores[0]:
        _top706 = scores[0][1]
        if (
            _top706 is not None
            and not _is_test_file(_top706.file_path)
            and _top706.kind.value in ("function", "method")
            and _top706.byte_size > 3000
        ):
            out.append(
                f"\nlarge function body: {_top706.name} is {_top706.byte_size:,} bytes"
                f" — large hotspot function; extract sub-functions to reduce complexity"
            )

    # S712: Fan-out hotspot — top hotspot calls more symbols than it has callers.
    # A hotspot that calls more things than it receives calls from is a dependency accumulator;
    # it may be a "god function" that orchestrates too many responsibilities.
    if scores and scores[0]:
        _top712 = scores[0][1]
        if (
            _top712 is not None
            and not _is_test_file(_top712.file_path)
            and _top712.kind.value in ("function", "method")
        ):
            _callers712 = graph.callers_of(_top712.id)
            _callees712 = graph.callees_of(_top712.id)
            if len(_callees712) > len(_callers712) >= 1:
                out.append(
                    f"\nfan-out hotspot: {_top712.name} calls {len(_callees712)}"
                    f" but is called by only {len(_callers712)}"
                    f" — dependency accumulator; verify it's not doing too much"
                )

    # S718: Deprecated hotspot — the top hotspot's name contains "old", "legacy", or "deprecated".
    # A deprecated hotspot is still being called frequently despite being marked for removal;
    # high call volume on deprecated code signals migration is incomplete or callers are stale.
    if scores and scores[0]:
        _top718 = scores[0][1]
        if _top718 is not None and any(kw in _top718.name.lower() for kw in ("old", "legacy", "deprecated")):
            _callers718 = graph.callers_of(_top718.id)
            out.append(
                f"\ndeprecated hotspot: {_top718.name} is a top hotspot but looks deprecated"
                f" ({len(_callers718)} callers) — migration is incomplete; audit callers and remove"
            )

    # S724: Hotspot in __init__ file — the top hotspot lives in an __init__.py.
    # __init__ hotspots are public API re-exports under high load; they are hard to change without
    # breaking consumers and signal the public surface is over-loaded.
    if scores and scores[0]:
        _top724 = scores[0][1]
        if (
            _top724 is not None
            and _top724.file_path.replace("\\", "/").rsplit("/", 1)[-1] == "__init__.py"
        ):
            _callers724 = graph.callers_of(_top724.id)
            out.append(
                f"\ninit file hotspot: {_top724.name} (in __init__.py) is a top hotspot"
                f" ({len(_callers724)} callers) — public API re-export under high load; breaking change risk"
            )

    # S730: Hotspot with no test callers — the top hotspot has callers but none are test files.
    # A heavily-called hotspot with no test coverage is a high-risk symbol; changes to it have
    # large blast radius with no safety net to catch regressions.
    if scores and scores[0]:
        _top730 = scores[0][1]
        if _top730 is not None and not _is_test_file(_top730.file_path):
            _callers730 = graph.callers_of(_top730.id)
            _test_callers730 = [c for c in _callers730 if _is_test_file(c.file_path)]
            if _callers730 and not _test_callers730:
                out.append(
                    f"\nno test coverage: {_top730.name} is a top hotspot ({len(_callers730)} callers)"
                    f" with no direct test callers — high blast radius, low safety net; add tests"
                )


def _signals_hotspots_core_c_risk(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S736: Choke point — top hotspot has 5+ callers AND 5+ callees (both high fan-in and fan-out).
    # A choke point is both widely depended-upon and widely depending-on; changes propagate
    # in both directions making it the most fragile position in the call graph.
    if scores and scores[0]:
        _top736 = scores[0][1]
        if _top736 is not None and not _is_test_file(_top736.file_path):
            _callers736 = graph.callers_of(_top736.id)
            _callees736 = graph.callees_of(_top736.id)
            if len(_callers736) >= 5 and len(_callees736) >= 5:
                out.append(
                    f"\nchoke point: {_top736.name} has {len(_callers736)} callers and {len(_callees736)} callees"
                    f" — high fan-in AND fan-out; changes propagate in all directions"
                )

    # S742: Cross-package hotspot — the top hotspot is called from 3+ different top-level directories.
    # Cross-package coupling concentrates architectural dependency at one symbol; changes to it
    # force coordinated updates across multiple subsystems.
    if scores and scores[0]:
        _top742 = scores[0][1]
        if _top742 is not None and not _is_test_file(_top742.file_path):
            _callers742 = graph.callers_of(_top742.id)
            _caller_dirs742 = {
                c.file_path.replace("\\", "/").split("/")[0]
                for c in _callers742
                if not _is_test_file(c.file_path)
            }
            if len(_caller_dirs742) >= 3:
                out.append(
                    f"\ncross-package hotspot: {_top742.name} is called from"
                    f" {len(_caller_dirs742)} different top-level directories"
                    f" — architectural dependency magnet; coupling spans the whole codebase"
                )

    # S748: Property hotspot — a top-5 hotspot is a property accessor.
    # Property accessors are called implicitly (like attribute reads) but execute code;
    # when a property ranks as a hotspot, callers are unaware of its cost and side effects.
    _prop748 = next(
        (sym for _, sym in scores[:5] if sym is not None and sym.kind.value == "property"),
        None,
    )
    if _prop748 is not None:
        _callers748 = graph.callers_of(_prop748.id)
        out.append(
            f"\nproperty hotspot: {_prop748.name} is a property accessor ranking in top hotspots"
            f" — callers don't see the cost; consider memoization"
        )

    # S754: Single-caller hotspot — the top hotspot has exactly 1 cross-file caller.
    # A hotspot with only one caller is tightly coupled to that single consumer;
    # it could be inlined into its caller or at least co-located in the same module.
    if scores:
        _top754 = scores[0][1]
        if _top754 is not None and not _is_test_file(_top754.file_path):
            _cross754 = [c for c in graph.callers_of(_top754.id) if c.file_path != _top754.file_path]
            if len(_cross754) == 1:
                out.append(
                    f"\nsingle-caller hotspot: {_top754.name} is the top hotspot but has only 1 cross-file caller"
                    f" ({_cross754[0].name}) — tight coupling; consider inlining or co-locating"
                )


def _signals_hotspots_core_c(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    _signals_hotspots_core_c_type(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_c_shape(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_c_classification(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_c_callers(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_c_risk(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)



def _signals_hotspots_core_d_kind(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S760: Classmethod hotspot — the top hotspot is a @classmethod.
    # Classmethods are called on the class itself; when one is a top hotspot, all
    # subclasses and instances share the coupling — method changes affect the whole hierarchy.
    if scores:
        _top760 = scores[0][1]
        if (
            _top760 is not None
            and _top760.kind.value in ("function", "method", "classmethod")
            and _top760.parent_id is not None
            and _top760.signature is not None
            and ("(cls," in _top760.signature or _top760.signature.endswith("(cls)") or "(cls):" in _top760.signature)
        ):
            out.append(
                f"\nclassmethod hotspot: {_top760.name} is a classmethod (takes cls) and the top hotspot"
                f" — changes affect every subclass and instance in the hierarchy"
            )

    # S796: God class hotspot — top hotspot is a class with 10+ methods.
    # Classes with many methods are often god objects accumulating responsibilities;
    # when such a class is also a hotspot (many callers), refactoring is risky.
    if scores:
        _top796 = scores[0][1]
        if _top796 is not None and _top796.kind.value == "class" and not _is_test_file(_top796.file_path):
            _methods796 = [
                s for s in graph.symbols.values()
                if s.parent_id == _top796.id and s.kind.value in ("function", "method")
            ]
            if len(_methods796) >= 10:
                out.append(
                    f"\ngod class hotspot: {_top796.name} is a class with {len(_methods796)} methods"
                    f" and is the top hotspot — god object pattern; split responsibilities before modifying"
                )

    # S808: Async hotspot — top hotspot is an async function.
    # Async functions introduce concurrency; when an async function is also the highest-risk
    # hotspot, bugs there can manifest as race conditions or non-deterministic failures.
    if scores:
        _top808 = scores[0][1]
        if _top808 is not None and not _is_test_file(_top808.file_path):
            _src808 = ""
            try:
                import linecache as _lc808
                _src808 = _lc808.getline(_top808.file_path, _top808.line_start).strip()
            except Exception:
                pass
            if _src808.startswith("async def"):
                out.append(
                    f"\nasync hotspot: {_top808.name} is an async function ranked as the top hotspot"
                    f" — concurrency bugs here manifest as race conditions; review await chains carefully"
                )

    # S850: Async hotspot — top hotspot is an async function.
    # Async hotspots introduce concurrency semantics; sync callers must use event loops
    # or adapters, and every caller participates in the async execution context.
    if scores:
        _top850 = scores[0][1]
        if _top850 is not None and not _is_test_file(_top850.file_path):
            _sig850 = (_top850.signature or "").lstrip()
            if _sig850.startswith("async ") and _top850.kind.value in ("function", "method"):
                out.append(
                    f"\nasync hotspot: {_top850.name} is an async function"
                    f" — top hotspot with async semantics; sync callers need event loop adapters"
                )

    # S862: Private hotspot — top hotspot function name starts with _ (single underscore).
    # A private function that has become the top hotspot is being used beyond its intended scope;
    # callers are coupling to implementation details that should be encapsulated.
    if scores:
        _top862 = scores[0][1]
        if (
            _top862 is not None
            and _top862.name.startswith("_")
            and not _top862.name.startswith("__")
            and not _is_test_file(_top862.file_path)
        ):
            _callers862 = graph.callers_of(_top862.id)
            out.append(
                f"\nprivate hotspot: {_top862.name} is a private function but the top hotspot ({len(_callers862)} callers)"
                f" — private implementation detail with wide usage; consider exposing it as public API"
            )

    # S952: Class-bound hotspot — the top hotspot is a class method, not a standalone function.
    # Methods carry hidden `self` state; changes may have broader impact than the signature
    # suggests if they read or modify shared instance fields visible to other methods.
    if scores:
        _top952 = scores[0][1]
        if _top952 is not None and _top952.kind.value == "method" and not _is_test_file(_top952.file_path):
            _cls952 = graph.symbols.get(_top952.parent_id) if _top952.parent_id else None
            if _cls952:
                out.append(
                    f"\nclass-bound hotspot: {_top952.name} is a method of {_cls952.name}"
                    f" — reads/writes instance state; changes may have broader impact than the signature suggests"
                )

    # S970: API-surface hotspot — the top hotspot is a public exported function.
    # Exported functions are API surface; internal refactors that seem safe may break
    # external consumers who rely on the exact signature or behavior.
    if scores:
        _top970 = scores[0][1]
        if (
            _top970 is not None
            and not _is_test_file(_top970.file_path)
            and getattr(_top970, "exported", False)
            and _top970.kind.value in ("function", "method")
        ):
            out.append(
                f"\napi-surface hotspot: {_top970.name} is a public exported function"
                f" — API surface; signature or behavior changes may break external consumers"
            )

    # S988: Private hotspot — top hotspot is a private function (starts with _).
    # A private function dominating the complexity chart is an internal detail
    # under high maintenance pressure; consider extracting it for independent testability.
    if scores:
        _top988 = scores[0][1]
        if (
            _top988 is not None
            and not _is_test_file(_top988.file_path)
            and _top988.name.startswith("_")
            and not _top988.name.startswith("__")
            and _top988.kind.value in ("function", "method")
        ):
            out.append(
                f"\nprivate hotspot: {_top988.name} is a private function and the top complexity hotspot"
                f" — internal implementation detail under high maintenance pressure; consider extraction"
            )


def _signals_hotspots_core_d_size(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S778: High-complexity hotspot — the top hotspot has cyclomatic complexity >= 10.
    # High-complexity symbols are hard to reason about and test; when they are also
    # top hotspots (many callers), bugs ripple widely and are hard to isolate.
    if scores:
        _top778 = scores[0][1]
        if (
            _top778 is not None
            and not _is_test_file(_top778.file_path)
            and _top778.complexity is not None
            and _top778.complexity >= 10
        ):
            out.append(
                f"\nhigh-complexity hotspot: {_top778.name} has cyclomatic complexity {_top778.complexity}"
                f" — top hotspot with high complexity; bugs here propagate to {len(graph.callers_of(_top778.id))} callers"
            )

    # S826: Large hotspot body — top hotspot function has many lines (50+).
    # Hotspot functions with large bodies are hard to reason about under load;
    # every caller is exposed to the full complexity of the function's internals.
    if scores:
        _top826 = scores[0][1]
        if (
            _top826 is not None
            and not _is_test_file(_top826.file_path)
            and _top826.kind.value in ("function", "method")
            and _top826.line_count is not None
            and _top826.line_count >= 50
        ):
            out.append(
                f"\nlarge hotspot body: {_top826.name} is {_top826.line_count} lines long"
                f" — top hotspot with large body; callers are exposed to full function complexity"
            )

    # S844: Deprecated hotspot — top hotspot has "deprecated" in its docstring.
    # Hotspots marked deprecated are widely called but scheduled for removal;
    # each caller is a migration debt item that must be addressed before deletion.
    if scores:
        _top844 = scores[0][1]
        if _top844 is not None and not _is_test_file(_top844.file_path):
            _doc844 = (_top844.doc or "").lower()
            if "deprecated" in _doc844 or "deprecat" in _doc844:
                _callers844 = graph.callers_of(_top844.id)
                out.append(
                    f"\ndeprecated hotspot: {_top844.name} is marked deprecated but has {len(_callers844)} caller(s)"
                    f" — deprecated symbol still heavily used; each caller is a migration debt item"
                )

    # S898: Long method hotspot — top hotspot function spans 50+ lines.
    # A long, high-complexity function is the highest-risk refactoring target;
    # extracting helpers should be done incrementally to avoid introducing regressions.
    if scores:
        _top898 = scores[0][1]
        if _top898 is not None and not _is_test_file(_top898.file_path):
            if _top898.line_count >= 50:
                out.append(
                    f"\nlong method hotspot: {_top898.name} spans {_top898.line_count} lines"
                    f" — large complex function; extract smaller helpers incrementally to reduce risk"
                )

    # S946: Oversized hotspot — the top hotspot spans 100+ lines of code.
    # A function exceeding 100 lines typically contains multiple responsibilities;
    # its length alone makes it a high-risk change target regardless of complexity score.
    if scores:
        _top946 = scores[0][1]
        if _top946 is not None and not _is_test_file(_top946.file_path):
            if _top946.line_count >= 100:
                out.append(
                    f"\noversized hotspot: {_top946.name} spans {_top946.line_count} lines"
                    f" — exceeds 100 lines; likely contains multiple responsibilities; refactor before extending"
                )

    # S982: Heavyweight hotspot — top hotspot has many callers AND many lines.
    # Double pressure: widely used AND complex code means each change carries
    # compounded risk from both broad blast radius and high implementation complexity.
    if scores:
        _top982 = scores[0][1]
        if _top982 is not None and not _is_test_file(_top982.file_path):
            _callers982 = [c for c in graph.callers_of(_top982.id) if not _is_test_file(c.file_path)]
            if len(_callers982) >= 3 and _top982.line_count >= 50:
                out.append(
                    f"\nheavyweight hotspot: {_top982.name} has {len(_callers982)} callers and spans {_top982.line_count} lines"
                    f" — widely used AND complex; changes carry compounded risk"
                )

    # S1000: Massive hotspot — top hotspot spans 150 or more lines.
    # Extreme function length multiplies the number of possible paths through the code;
    # even a small change may interact with many branches and edge cases simultaneously.
    if scores:
        _top1000 = scores[0][1]
        if _top1000 is not None and not _is_test_file(_top1000.file_path) and _top1000.line_count >= 150:
            out.append(
                f"\nmassive hotspot: {_top1000.name} spans {_top1000.line_count} lines"
                f" — extreme complexity; refactoring into smaller units would significantly reduce maintenance risk"
            )


def _signals_hotspots_core_d_callers(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S784: Package-local hotspot — top hotspot is only called from within its own directory.
    # A hotspot with all callers in the same package has high internal coupling but low
    # external exposure; refactoring is contained but the package itself is tightly coupled.
    if scores:
        _top784 = scores[0][1]
        if _top784 is not None and not _is_test_file(_top784.file_path):
            _callers784 = graph.callers_of(_top784.id)
            if len(_callers784) >= 4:
                def _dir784(fp: str) -> str:
                    n = fp.replace("\\", "/")
                    return n.rsplit("/", 1)[0] if "/" in n else ""
                _hot_dir784 = _dir784(_top784.file_path)
                _caller_dirs784 = set(_dir784(c.file_path) for c in _callers784)
                if len(_caller_dirs784) == 1 and _hot_dir784 in _caller_dirs784:
                    out.append(
                        f"\npackage-local hotspot: {_top784.name} has {len(_callers784)} callers"
                        f" all within {_hot_dir784 or 'root'} — high internal coupling;"
                        f" extract shared logic to a lower-level utility module"
                    )

    # S790: Test-only callers hotspot — top hotspot is only called from test files.
    # An exported symbol only called from tests may be an internal helper mistakenly exposed,
    # or a public API with no real consumers yet — clarify intent before making changes.
    if scores:
        _top790 = scores[0][1]
        if (
            _top790 is not None
            and not _is_test_file(_top790.file_path)
            and _top790.exported
        ):
            _callers790 = graph.callers_of(_top790.id)
            _test_callers790 = [c for c in _callers790 if _is_test_file(c.file_path)]
            _src_callers790 = [c for c in _callers790 if not _is_test_file(c.file_path)]
            if len(_test_callers790) >= 3 and not _src_callers790:
                out.append(
                    f"\ntest-only callers hotspot: {_top790.name} is exported but only called"
                    f" from {len(_test_callers790)} test file(s) — no production callers;"
                    f" verify whether this is intentional or an unused public API"
                )

    # S802: Thin wrapper hotspot — top hotspot has only 1 callee (delegates entirely to one fn).
    # A hotspot that only wraps one other function adds a call layer with no additional value;
    # callers could invoke the inner function directly, reducing coupling.
    if scores:
        _top802 = scores[0][1]
        if _top802 is not None and not _is_test_file(_top802.file_path):
            _callers802 = graph.callers_of(_top802.id)
            if len(_callers802) >= 4:
                # Find callees: symbols where _top802 appears in their callers list
                _callees802 = [
                    s for s in graph.symbols.values()
                    if any(c.id == _top802.id for c in graph.callers_of(s.id))
                    and s.id != _top802.id
                ]
                if len(_callees802) == 1:
                    out.append(
                        f"\nthin wrapper hotspot: {_top802.name} is called by {len(_callers802)} consumers"
                        f" but only calls {_callees802[0].name} — pure wrapper; callers could bypass it"
                    )

    # S814: Cross-module hotspot — top hotspot is called from 3+ distinct top-level directories.
    # When a symbol is depended upon across many structural boundaries it becomes a de-facto
    # shared infrastructure piece; any change requires coordinating across all those modules.
    if scores:
        _top814 = scores[0][1]
        if _top814 is not None and not _is_test_file(_top814.file_path):
            _callers814 = graph.callers_of(_top814.id)
            _dirs814 = {
                c.file_path.replace("\\", "/").split("/")[0]
                for c in _callers814
                if c.file_path != _top814.file_path
                and "/" in c.file_path.replace("\\", "/")
            }
            if len(_dirs814) >= 3:
                out.append(
                    f"\ncross-module hotspot: {_top814.name} is called from {len(_dirs814)} distinct top-level directories"
                    f" — de-facto shared infrastructure; changes require cross-module coordination"
                )

    # S832: Single-file hotspot callers — top hotspot is only called from one file.
    # A hotspot with all callers in a single file may be inflated by intra-file calls;
    # the real external impact is lower than the raw caller count suggests.
    if scores:
        _top832 = scores[0][1]
        if _top832 is not None and not _is_test_file(_top832.file_path):
            _callers832 = graph.callers_of(_top832.id)
            _caller_files832 = {c.file_path for c in _callers832 if c.file_path != _top832.file_path}
            if len(_callers832) >= 3 and len(_caller_files832) == 1:
                out.append(
                    f"\nsingle-file hotspot callers: {_top832.name} has {len(_callers832)} callers but all from one file"
                    f" — hotspot score may be inflated by intra-module calls; external impact is narrower"
                )

    # S868: Super-hotspot — top hotspot has 10+ direct callers.
    # A function with 10+ callers is extremely widely used; any behavioral change
    # requires coordination with all callers and is high-risk to refactor or remove.
    if scores:
        _top868 = scores[0][1]
        if _top868 is not None and not _is_test_file(_top868.file_path):
            _callers868 = graph.callers_of(_top868.id)
            if len(_callers868) >= 10:
                out.append(
                    f"\nsuper hotspot: {_top868.name} has {len(_callers868)} direct callers"
                    f" — extremely wide usage; changes require coordination across {len(_callers868)} callers"
                )

    # S874: Wide-file hotspot — top hotspot has callers from 5+ different files.
    # A hotspot with callers spread across many files is a cross-cutting concern;
    # any change ripples through a wide surface area of the codebase.
    if scores:
        _top874 = scores[0][1]
        if _top874 is not None and not _is_test_file(_top874.file_path):
            _callers874 = graph.callers_of(_top874.id)
            _caller_files874 = {c.file_path for c in _callers874}
            if len(_caller_files874) >= 5:
                out.append(
                    f"\nwide-file hotspot: {_top874.name} is called from {len(_caller_files874)} distinct files"
                    f" — cross-cutting concern; changes affect {len(_caller_files874)} files across the codebase"
                )

    # S880: Uncalled hotspot — top complexity hotspot has no recorded callers.
    # A complex function with no callers may be dead code that accumulated complexity
    # without being exercised; investigate before refactoring or deleting.
    if scores:
        _top880 = scores[0][1]
        if _top880 is not None and not _is_test_file(_top880.file_path):
            _callers880 = graph.callers_of(_top880.id)
            if not _callers880:
                out.append(
                    f"\nuncalled hotspot: {_top880.name} is the most complex symbol but has no recorded callers"
                    f" — may be dead code or an entry point called via dynamic dispatch"
                )

    # S916: Single-caller hotspot — the top hotspot is called from exactly one place.
    # A high-complexity function with only one caller may be an over-engineered extraction;
    # consider inlining it to reduce the cognitive overhead of tracking two function bodies.
    if scores:
        _top916 = scores[0][1]
        if _top916 is not None and not _is_test_file(_top916.file_path):
            _callers916 = graph.callers_of(_top916.id)
            if len(_callers916) == 1:
                out.append(
                    f"\nsingle-caller hotspot: {_top916.name} is complex but called from only 1 place"
                    f" — consider inlining to reduce indirection overhead"
                )

    # S964: Bottleneck hotspot — the top hotspot is called from 10+ distinct source files.
    # Extreme fan-in means this function is a central dependency; breaking changes here
    # cause cascading failures across unrelated subsystems simultaneously.
    if scores:
        _top964 = scores[0][1]
        if _top964 is not None and not _is_test_file(_top964.file_path):
            _callers964 = graph.callers_of(_top964.id)
            _caller_files964 = {
                c.file_path for c in _callers964
                if not _is_test_file(c.file_path) and c.file_path != _top964.file_path
            }
            if len(_caller_files964) >= 10:
                out.append(
                    f"\nbottleneck hotspot: {_top964.name} is called from {len(_caller_files964)} source files"
                    f" — extreme fan-in; breaking changes here cause simultaneous failures across many subsystems"
                )

    # S976: Wide hotspot — top hotspot callers span 3+ distinct directories.
    # A symbol called from multiple directories crosses module/subsystem boundaries;
    # changes here require coordinated updates across teams or packages.
    if scores:
        _top976 = scores[0][1]
        if _top976 is not None and not _is_test_file(_top976.file_path):
            _callers976 = graph.callers_of(_top976.id)
            _dirs976: set[str] = set()
            for _c976 in _callers976:
                if not _is_test_file(_c976.file_path):
                    _fp976 = _c976.file_path.replace("\\", "/")
                    _dirs976.add(_fp976.rsplit("/", 1)[0] if "/" in _fp976 else ".")
            if len(_dirs976) >= 3:
                out.append(
                    f"\nwide hotspot: {_top976.name} is called from {len(_dirs976)} directories"
                    f" — crosses subsystem boundaries; signature changes require cross-team coordination"
                )

    # S994: Contained hotspot — top hotspot callers are all within the same file.
    # A complex function called only internally has a contained blast radius;
    # changes affect only the hosting module, not external consumers.
    if scores:
        _top994 = scores[0][1]
        if _top994 is not None and not _is_test_file(_top994.file_path):
            _callers994 = [c for c in graph.callers_of(_top994.id) if not _is_test_file(c.file_path)]
            _external994 = [c for c in _callers994 if c.file_path != _top994.file_path]
            if _callers994 and not _external994:
                out.append(
                    f"\ncontained hotspot: {_top994.name} — all {len(_callers994)} caller(s) are within {_top994.file_path.rsplit('/', 1)[-1]}"
                    f"; blast radius limited to this file"
                )


def _signals_hotspots_core_d_coverage(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S838: Hotspot with no tests — top hotspot function has zero test-file callers.
    # High-call-count functions with no test coverage are high-risk refactoring targets;
    # any behavioral change will be invisible to the test suite.
    if scores:
        _top838 = scores[0][1]
        if _top838 is not None and not _is_test_file(_top838.file_path):
            _callers838 = graph.callers_of(_top838.id)
            _test_callers838 = [c for c in _callers838 if _is_test_file(c.file_path)]
            if _callers838 and not _test_callers838:
                out.append(
                    f"\nhotspot no tests: {_top838.name} has {len(_callers838)} callers but zero test coverage"
                    f" — top hotspot is untested; behavioral changes will not be caught by tests"
                )

    # S922: Test-imported hotspot — the top hotspot is called from test files.
    # When tests call hotspot functions directly, changes to the hotspot may break tests
    # in addition to production callers; double the verification scope when changing it.
    if scores:
        _top922 = scores[0][1]
        if _top922 is not None and not _is_test_file(_top922.file_path):
            _callers922 = graph.callers_of(_top922.id)
            _test_callers922 = [c for c in _callers922 if _is_test_file(c.file_path)]
            if _test_callers922:
                out.append(
                    f"\ntest-imported hotspot: {_top922.name} is called directly by {len(_test_callers922)} test file(s)"
                    f" — hotspot changes break tests; verify both test and production callers"
                )

    # S928: Shallow hotspot — the top hotspot is short (≤5 lines) but heavily called.
    # A short but hot function may be a performance bottleneck or a traffic concentrator;
    # inlining or caching its result can yield outsized performance gains.
    if scores:
        _top928 = scores[0][1]
        if _top928 is not None and not _is_test_file(_top928.file_path):
            _callers928 = graph.callers_of(_top928.id)
            if _top928.line_count is not None and _top928.line_count <= 5 and len(_callers928) >= 5:
                out.append(
                    f"\nshallow hotspot: {_top928.name} is only {_top928.line_count} line(s)"
                    f" but called from {len(_callers928)} places"
                    f" — short but widely called; consider inlining or caching its result"
                )

    # S934: Untested hotspot — the top hotspot has no callers from test files.
    # A heavily-used complex function with no direct test coverage is a blind spot;
    # integration tests may exercise it but targeted unit tests are recommended.
    if scores:
        _top934 = scores[0][1]
        if _top934 is not None and not _is_test_file(_top934.file_path):
            _callers934 = graph.callers_of(_top934.id)
            _has_test_callers934 = any(_is_test_file(c.file_path) for c in _callers934)
            if _callers934 and not _has_test_callers934:
                out.append(
                    f"\nuntested hotspot: {_top934.name} has no direct test callers"
                    f" — highest complexity function lacks unit test coverage; consider adding targeted tests"
                )


def _signals_hotspots_core_d_location(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S820: Test-file hotspot — the top hotspot is inside a test file.
    # A test function being the most-called symbol indicates tests are calling each other
    # (test coupling), which makes test suites fragile and hard to run in isolation.
    if scores:
        _top820 = scores[0][1]
        if _top820 is not None and _is_test_file(_top820.file_path):
            out.append(
                f"\ntest-file hotspot: {_top820.name} is a test function ranked as the top hotspot"
                f" — tests calling other tests create coupling; consider shared fixtures instead"
            )

    # S856: Hotspot in legacy file — top hotspot lives in a file named with _old/_legacy/_v1.
    # Code in legacy-named files is expected to be superseded; a hotspot here means
    # callers are still routed to the deprecated path rather than the new implementation.
    if scores:
        _top856 = scores[0][1]
        if _top856 is not None:
            _fname856 = _top856.file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            _legacy_sfxs856 = ("_old", "_legacy", "_deprecated", "_v1", "_bak")
            if any(_fname856.endswith(sfx) for sfx in _legacy_sfxs856):
                out.append(
                    f"\nlegacy file hotspot: {_top856.name} is in a legacy-named file ({_top856.file_path})"
                    f" — top hotspot in deprecated module; callers should be migrated to the new path"
                )

    # S886: Utility file hotspot — top hotspot is in a utils/helpers/common/shared file.
    # Utility hotspots are often depended on by unrelated parts of the codebase; changes
    # can cause surprising regressions across multiple feature areas simultaneously.
    if scores:
        _top886 = scores[0][1]
        if _top886 is not None and not _is_test_file(_top886.file_path):
            _fname886 = _top886.file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            _util_kws886 = ("util", "helper", "common", "shared", "base", "mixin", "core")
            if any(kw in _fname886 for kw in _util_kws886):
                out.append(
                    f"\nutility hotspot: {_fname886} is a utility/shared file with the top hotspot ({_top886.name})"
                    f" — utility hotspots cause cross-feature regressions; changes require wide test coverage"
                )

    # S940: Hotspot in deprecated file — the top hotspot is in a legacy-named file.
    # Working in deprecated code is higher risk; bugs may be intentionally left unfixed
    # and there may be pressure to avoid changes that could delay a planned migration.
    if scores:
        _top940 = scores[0][1]
        _legacy_kws940 = ("legacy", "deprecated", "old", "obsolete", "archive")
        if _top940 is not None:
            _fname940 = _top940.file_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if any(kw in _fname940 for kw in _legacy_kws940):
                out.append(
                    f"\nlegacy hotspot: {_top940.name} is the top hotspot but lives in {_top940.file_path.rsplit('/', 1)[-1]}"
                    f" — deprecated file; changes here risk introducing debt into code scheduled for removal"
                )

    # S958: Init file hotspot — the top hotspot is defined in __init__.py.
    # __init__.py symbols are part of the package's public API surface; every consumer of the
    # package imports this file implicitly, so changes here have the broadest possible blast radius.
    if scores:
        _top958 = scores[0][1]
        if _top958 is not None and not _is_test_file(_top958.file_path):
            _fp958 = _top958.file_path.replace("\\", "/")
            if _fp958.endswith("/__init__.py") or _fp958 == "__init__.py":
                out.append(
                    f"\ninit hotspot: {_top958.name} is in __init__.py"
                    f" — package-level symbol; changes alter the import surface and affect all package consumers"
                )

    # S1006: Entrypoint hotspot — top hotspot is in a well-known entrypoint file.
    # Complex code at the application entry point is doubly risky: high complexity
    # at the start of all execution means bugs affect every code path from the first call.
    _entry_bases1006 = {"main.py", "app.py", "server.py", "index.py", "run.py", "cli.py", "__main__.py", "wsgi.py", "asgi.py"}
    if scores:
        _top1006 = scores[0][1]
        if _top1006 is not None and not _is_test_file(_top1006.file_path):
            _fbase1006 = _top1006.file_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if _fbase1006 in _entry_bases1006:
                out.append(
                    f"\nentrypoint hotspot: {_top1006.name} is in {_fbase1006}"
                    f" — complex code at the start of all execution paths; changes affect every request/call"
                )

    # S1020: Singleton hotspot — top hotspot's file contains only one function.
    # A single-function file at the top of hotspots scores as a whole-file risk;
    # there is no sibling context to help understand intent, making safe changes harder.
    if scores:
        _top1020 = scores[0][1]
        if _top1020 is not None and not _is_test_file(_top1020.file_path):
            _file_syms1020 = [
                s for s in graph.symbols.values()
                if s.file_path == _top1020.file_path and s.kind.value in ("function", "method")
            ]
            if len(_file_syms1020) == 1:
                _fname1020 = _top1020.file_path.replace("\\", "/").rsplit("/", 1)[-1]
                out.append(
                    f"\nsingleton hotspot: {_top1020.name} is the only function in {_fname1020}"
                    f" — whole-file risk with no sibling context; changes are harder to scope safely"
                )


def _signals_hotspots_core_d_cluster(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    # S766: File concentration — top 3 hotspots all live in the same file (single-file bottleneck).
    # When the top hotspots are all in one file, that file is a structural bottleneck;
    # it concentrates change risk and merge conflicts into a single location.
    if len(scores) >= 3:
        _fps766 = [sym.file_path for _, sym in scores[:3] if sym is not None]
        if len(_fps766) == 3 and len(set(_fps766)) == 1 and not _is_test_file(_fps766[0]):
            out.append(
                f"\nfile concentration: top 3 hotspots all in {_fps766[0].rsplit('/', 1)[-1]}"
                f" — single-file bottleneck; split into smaller modules to reduce merge conflicts"
            )

    # S772: All-test hotspots — top 3 hotspots are all in test files (test-dominated codebase).
    # When test helpers dominate the hotspot list, the test suite has become more coupled
    # than production code — reorganize shared test logic into fixtures and conftest modules.
    if len(scores) >= 3:
        _top3_772 = [sym for _, sym in scores[:3] if sym is not None]
        if len(_top3_772) == 3 and all(_is_test_file(s.file_path) for s in _top3_772):
            out.append(
                f"\nall-test hotspots: top 3 hotspots are all in test files"
                f" — test suite is more coupled than production code; consolidate into conftest fixtures"
            )

    # S892: Hotspot cluster — 3+ of the top 10 hotspots are in the same file.
    # A file containing multiple top hotspots is an implicit coupling point; changes
    # to any one symbol can ripple through co-located hotspots in the same file.
    if len(scores) >= 3:
        _cluster_files892: dict[str, int] = {}
        for _, sym892 in scores[:10]:
            if sym892 is not None and not _is_test_file(sym892.file_path):
                _cluster_files892[sym892.file_path] = _cluster_files892.get(sym892.file_path, 0) + 1
        if _cluster_files892:
            _top_cluster_count892 = max(_cluster_files892.values())
            if _top_cluster_count892 >= 3:
                _top_cluster_fp892 = max(_cluster_files892, key=_cluster_files892.__getitem__)
                out.append(
                    f"\ncoupling hub: {_top_cluster_fp892.rsplit('/', 1)[-1]} contains"
                    f" {_top_cluster_count892} of top hotspots"
                    f" — concentrated complexity; changes here affect multiple high-impact symbols"
                )

    # S904: Test hotspot — the top complexity hotspot is a test function.
    # A complex test is a sign of over-engineered setup; this makes tests brittle and
    # hard to maintain, increasing the risk that test failures are ignored or bypassed.
    if scores:
        _top904 = scores[0][1]
        if _top904 is not None and _is_test_file(_top904.file_path):
            out.append(
                f"\ncomplex test hotspot: {_top904.name} in {_top904.file_path.rsplit('/', 1)[-1]}"
                f" — top hotspot is a test function; extract fixtures and helpers to reduce test maintenance"
            )

    # S910: Concentration hotspot — all top 5 hotspots are in the same file.
    # When every high-complexity symbol lives in a single file, changes there have
    # zero isolation from other hot code paths; this file is the highest-risk target.
    if len(scores) >= 5:
        _top5_files910 = {sym910.file_path for _, sym910 in scores[:5] if sym910 is not None}
        if len(_top5_files910) == 1:
            _sole_file910 = next(iter(_top5_files910))
            if not _is_test_file(_sole_file910):
                out.append(
                    f"\nconcentration hotspot: all top 5 hotspots are in {_sole_file910.rsplit('/', 1)[-1]}"
                    f" — extreme complexity concentration; highest-risk file in the codebase"
                )

    # S1012: Co-located hotspots — top two hotspots are both in the same source file.
    # When the highest-scoring symbols share a file, that file is a concentration risk;
    # any regression in it simultaneously degrades the two most critical code paths.
    if len(scores) >= 2:
        _top_sym1012 = scores[0][1]
        _second_sym1012 = scores[1][1]
        if (
            _top_sym1012 is not None
            and _second_sym1012 is not None
            and not _is_test_file(_top_sym1012.file_path)
            and _top_sym1012.file_path == _second_sym1012.file_path
        ):
            _fname1012 = _top_sym1012.file_path.replace("\\", "/").rsplit("/", 1)[-1]
            out.append(
                f"\nco-located hotspots: {_top_sym1012.name} and {_second_sym1012.name} are both in {_fname1012}"
                f" — hotspot concentration; a single regression here degrades two critical paths simultaneously"
            )

    # S1018: Hot cascade — top hotspot file is imported by other hot files.
    # When the top hotspot's importers are themselves hot (actively churning), a change
    # here doesn't just ripple to cold files — it disrupts files already being modified,
    # raising the risk of merge conflicts and concurrent regression.
    # Only shown when 2+ hot importers exist (single overlap could be coincidence).
    if scores and graph.hot_files:
        _top_fp1018 = scores[0][1].file_path
        if not _is_test_file(_top_fp1018):
            _importers1018 = graph.importers_of(_top_fp1018)
            _hot_importers1018 = [
                fp for fp in _importers1018
                if fp in graph.hot_files and not _is_test_file(fp)
            ]
            if len(_hot_importers1018) >= 2:
                _hi_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_hot_importers1018)[:3]]
                _hi_str = ", ".join(_hi_names)
                if len(_hot_importers1018) > 3:
                    _hi_str += f" +{len(_hot_importers1018) - 3} more"
                out.append(
                    f"\nhot cascade: {_top_fp1018.rsplit('/', 1)[-1]} is imported by {len(_hot_importers1018)} hot files"
                    f" ({_hi_str}) — a change here disrupts files already in active churn"
                )


def _signals_hotspots_core_d(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
    out: list[str],
) -> None:
    _signals_hotspots_core_d_kind(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_d_size(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_d_callers(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_d_coverage(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_d_location(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_d_cluster(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)



def _collect_hotspots_signals(
    graph: Tempo,
    scores: list[tuple[float, Symbol]],
    velocity: dict[str, float],
    velocity_14: dict[str, float],
    all_test_fps: set[str],
    top_n: int,
) -> list[str]:
    """Collect all signal annotation lines for hotspot output."""
    out: list[str] = []
    _signals_hotspots_core_a(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_b(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_c(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    _signals_hotspots_core_d(graph, scores, velocity, velocity_14, all_test_fps, top_n, out)
    return out



