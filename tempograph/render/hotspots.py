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

    return "\n".join(lines)
