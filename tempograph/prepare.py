"""Batch context preparation for agent task dispatch.

render_prepare() is the primary entry point: it assembles a token-budgeted
context block (overview + focus + hotspots + diff) optimised for change-
localisation and general coding tasks.

Extracted from render.py Phase 2 to keep render.py as a pure rendering module.
"""
from __future__ import annotations

import re
from pathlib import Path

from .keywords import _extract_cl_keywords
from .types import Tempo


def _get_cochange_related(
    repo_root: str, key_files: list[str], repo_files: set[str]
) -> list[tuple[str, float]]:
    """Get files that frequently co-change with the given key files.

    Returns [(file_path, max_frequency), ...] sorted by frequency desc.
    Only includes files that exist in the current repo graph.
    Deduplicates against key_files themselves.
    """
    try:
        from .git import cochange_matrix, is_git_repo
        if not is_git_repo(repo_root):
            return []
        matrix = cochange_matrix(repo_root, n_commits=100)
    except Exception:
        return []

    key_set = set(key_files)
    related: dict[str, float] = {}
    for kf in key_files:
        for partner, freq in matrix.get(kf, []):
            if partner not in key_set and partner in repo_files:
                related[partner] = max(related.get(partner, 0), freq)

    return sorted(related.items(), key=lambda x: -x[1])


def _extract_focus_ranges(focus_output: str, key_files: list[str]) -> dict[str, str]:
    """Map key file paths to their first line range from focus output.

    Returns dict of filepath → "start-end" (e.g., {"render.py": "1496-1745"}).
    Parses '— filepath:start-end' patterns; first occurrence wins (depth 0 = most relevant).
    """
    ranges: dict[str, str] = {}
    for m in re.finditer(r'— (\S+):(\d+-\d+)', focus_output):
        fp = m.group(1)
        if fp not in ranges:
            ranges[fp] = m.group(2)
    return {kf: ranges[kf] for kf in key_files if kf in ranges}


def _is_change_localization(task: str, task_type: str) -> bool:
    """Detect if a task is a change-localization task (PR title, commit message, issue ref).

    Change-localization tasks benefit from the per-keyword focus algorithm.
    General coding tasks ("add login feature") should use the default multi-token approach.
    """
    if task_type in ("changelocal", "debug", "bugfix"):
        return True
    lower = task.lower()
    if "merge pull request" in lower or re.match(r"merge branch '", lower):
        return True
    # Conventional commit prefix
    if re.match(r'^(fix|feat|refactor|chore|perf|style|build|ci|docs|test)(\(.+\))?:', lower):
        return True
    # PR title with issue reference: "Fix #1234" or "Fix teardown (#5928)"
    if re.search(r'\(#\d+\)\s*$', task) or re.search(r'^\w.*#\d+', task):
        return True
    return False


_TEST_MARKERS = ("test", "spec", "fixture", "example", "tutorial", "demo", "sample")
_ASSET_DIRS = ("/templates/", "/static/")


def _cl_path_fallback(graph: "Tempo", kw: str) -> list[str]:
    """Return path-matched files for a keyword that failed symbol focus.

    Tries three strategies in order, returning the first that yields <=5 source files:
    1. Plain substring match on file paths.
    2. Snake_case decomposition: "config_from_object" → try "config", "object", etc.
    3. CamelCase decomposition: "RequestStreamingSupport" → try "Streaming", "Support", etc.

    Returns an empty list when no strategy finds a tight match (<=5 non-test files).
    """
    def _source_files(paths: list[str]) -> list[str]:
        return [p for p in paths if not any(x in p.lower() for x in _TEST_MARKERS)]

    def _path_hits(token: str) -> list[str]:
        t = token.lower()
        hits = sorted(set(
            sym.file_path for sym in graph.symbols.values()
            if t in sym.file_path.lower()
            and not any(d in sym.file_path for d in _ASSET_DIRS)
            and not sym.file_path.startswith(("templates/", "static/"))
        ))
        return _source_files(hits)

    # Strategy 1: plain keyword
    plain = _path_hits(kw)
    if plain and len(plain) <= 5:
        return plain[:5]

    if "_" in kw:
        # Strategy 2: snake_case components
        _PATH_SNAKE_SKIP = frozenset({
            "response", "request", "error", "errors", "option", "options",
            "handler", "handlers", "helper", "helpers", "server", "client", "router",
            "static", "analysis", "middleware", "security",
        })
        for part in kw.split("_"):
            if len(part) >= 4 and part.lower() not in _PATH_SNAKE_SKIP:
                hits = _path_hits(part)
                if hits and len(hits) <= 5:
                    return hits
    else:
        # Strategy 3: CamelCase parts
        _PATH_CAMEL_SKIP = frozenset({
            "import", "test", "tests", "type", "types", "base", "core", "util",
            "utils", "data", "form", "list", "dict", "object", "class", "model",
            "models", "view", "views", "helper", "helpers", "mixin", "mixins",
            "return", "raise", "yield", "async", "await", "error", "errors",
            "init", "main", "common", "factory", "manager",
            "field", "fields", "exception", "exceptions",
            "json", "xml",
            "host", "method", "setter", "getter",
        })
        parts: list[str] = []
        cur: list[str] = []
        for ch in kw:
            if ch.isupper() and cur:
                parts.append("".join(cur))
                cur = [ch]
            else:
                cur.append(ch)
        if cur:
            parts.append("".join(cur))
        for part in parts:
            if len(part) >= 4 and part.lower() not in _PATH_CAMEL_SKIP:
                hits = _path_hits(part)
                if hits and len(hits) <= 5:
                    return hits

    return []


def _coverage_line(query_tokens: list[str], graph: Tempo, context_files: list[str]) -> str:
    """Build a one-line coverage signal for prepare_context output.

    Returns empty string when there's nothing meaningful to report.
    """
    if not query_tokens:
        return ""

    # Build set of all symbol names in the graph (lowercased for case-insensitive match)
    sym_names = {sym.name.lower() for sym in graph.symbols.values()}

    resolved = sum(1 for t in query_tokens if t.lower() in sym_names)
    total = len(query_tokens)
    n_files = len(context_files)

    if total == 0:
        return ""

    pct = resolved / total
    if pct > 0.75:
        confidence = "high"
    elif pct >= 0.40:
        confidence = "medium"
    else:
        confidence = "low"

    return (
        f"Context coverage: {resolved} of {total} changed symbols resolved "
        f"({pct:.0%}) | {n_files} key files identified | confidence: {confidence}"
    )


def render_prepare(graph: Tempo, task: str, max_tokens: int = 6000, task_type: str = "",
                   baseline_predicted_files: list[str] | None = None,
                   precision_filter: bool = False,
                   definition_first: bool = False) -> str:
    """Batch context preparation: overview + focus + hotspots + diff in one token-budgeted output.

    If L2 learned insights exist for task_type, includes extra modes (dead code, quality)
    that the data shows are helpful for that task category.
    """
    from .git import changed_files_unstaged, is_git_repo
    from .render import (
        _extract_focus_files,
        _is_docs_branch_task,
        count_tokens,
        render_dead_code,
        render_diff_context,
        render_focused,
        render_hotspots,
        render_overview,
        render_skills,
    )

    sections: list[str] = []
    token_count = 0
    # Track query tokens and resolved files for coverage signal
    _query_tokens: list[str] = []
    _context_files: list[str] = []

    # Load L2 insights to customize which supplemental modes to include
    l2_best_modes: set[str] = set()
    try:
        from tempo.plugins.learn import TaskMemory
        mem = TaskMemory(str(Path(graph.root).resolve()))
        if task_type:
            rec = mem.get_recommendation(task_type)
            if rec and rec.get("sample_size", 0) >= 2:
                l2_best_modes = set(rec["best_modes"])
    except Exception:
        pass

    s = graph.stats
    sections.append(f"## Repo: {s['files']} files, {s['symbols']} symbols, {s['total_lines']:,} lines")
    token_count += 20

    if _is_change_localization(task, task_type):
        # Change-localization path: per-keyword focus + breadth filter + selective overview.
        # Bench evidence (n=111, Phase 5.26): +9-12% F1 improvement vs raw task passthrough.
        # Key differences from general path:
        #   - Extract code-symbol keywords from PR title/branch name (not raw task string)
        #   - Run separate focus per keyword (up to 3) → better symbol targeting
        #   - Breadth filter: skip keyword if focus returns >10 files (too generic)
        #   - Selective overview: only inject when keywords=[] (vague task, no code signal)
        #   - "Keywords found but focus failed" → inject nothing (model uses training knowledge)
        keywords = _extract_cl_keywords(task)
        focus_budget = max_tokens // 2
        focus_parts: list[str] = []
        path_fallback_files: list[str] = []  # collected when symbol focus is too broad
        # Skip keywords shorter than 4 chars before taking the top-3 cap.
        # Short tokens can't trigger path fallback (which requires len>=4) and rarely match
        # specific symbols. This prevents "req" (len=3) from blocking "resp" (len=4).
        effective_keywords = [kw for kw in keywords if len(kw) >= 4][:3]
        _query_tokens = effective_keywords
        for kw in effective_keywords:
            focused = render_focused(graph, kw, max_tokens=focus_budget)
            no_match = not focused or "No symbols matching" in focused or "No exact match" in focused
            if not no_match:
                kw_files = _extract_focus_files(focused)
            too_broad = not no_match and len(kw_files) > 10
            if no_match or too_broad:
                # No symbol match OR too broad — try path-based fallback.
                # Handles: (a) directory/module keywords (e.g. "demo" → demos/),
                # (b) keyword is a module name but not a symbol (e.g. "config" → sanic/config.py).
                if len(kw) >= 4 and not path_fallback_files:
                    path_fallback_files = _cl_path_fallback(graph, kw)
                # Definition-first fallback: when focus is too_broad and all path matching found nothing,
                # return just the DEFINING file(s) of the top-ranked symbol.
                # Handles: "redirect" → flask/helpers.py (where redirect() lives) rather than all callers.
                # Gated behind definition_first=True (bench evidence required before enabling by default).
                if definition_first and too_broad and not path_fallback_files:
                    scored = graph.search_symbols_scored(kw)
                    if scored:
                        top_score = scored[0][0]
                        if top_score >= 10.0:
                            threshold = top_score * 0.6
                            def_hits = sorted(set(
                                sym.file_path for score, sym in scored
                                if score >= threshold
                                and not any(x in sym.file_path.lower() for x in _TEST_MARKERS)
                                and "/templates/" not in sym.file_path
                                and not sym.file_path.startswith("templates/")
                                and "/static/" not in sym.file_path
                                and not sym.file_path.startswith("static/")
                            ))
                            if 1 <= len(def_hits) <= 2:
                                path_fallback_files = def_hits
                continue
            focus_parts.append(focused)

        if focus_parts:
            for fp in focus_parts:
                sections.append(fp)
                token_count += count_tokens(fp)
            key_files = _extract_focus_files("\n\n".join(focus_parts), task_keywords=keywords)
            # Inject path-fallback hits that aren't already in key_files.
            # When focus was dominated by test/hub files, path-matched source files
            # (e.g. keywords.py for "extract-cl-keywords") get lost. Prepend them.
            if path_fallback_files:
                _kf_set = set(key_files)
                for _pf in path_fallback_files:
                    if _pf not in _kf_set:
                        key_files.insert(0, _pf)
            _context_files = key_files
            # Precision gate: >4 key files → topic too broad → skip injection.
            # Bench evidence (Phase 5.26, n=111): precision_filter=+3.9% (p=0.085, ns).
            if precision_filter and len(key_files) > 4:
                return ""  # Too broad — skip context entirely
            # Adaptive gating v5: skip injection when baseline predicts >=2 files.
            # Bench evidence (Phase 5.30, n=114): v5 +7.6% F1, p=0.013 (significant).
            # pred>=2 means the model is confident enough — context adds noise.
            # Eliminates ALL repo-level regressions (express -3.8% → +15.4%).
            if baseline_predicted_files is not None:
                if len(baseline_predicted_files) >= 2:
                    return ""  # model already confident — skip injection
            if key_files:
                kf_ranges = _extract_focus_ranges("\n\n".join(focus_parts), key_files[:5])
                kf_lines = [f"  {f}:{kf_ranges[f]}" if f in kf_ranges else f"  {f}" for f in key_files[:5]]
                kf_section = "KEY FILES REFERENCED ABOVE:\n" + "\n".join(kf_lines)
                sections.append(kf_section)
                token_count += count_tokens(kf_section)

                # Co-change prediction: files that frequently change alongside key files
                cochange = _get_cochange_related(graph.root, key_files[:5], set(graph.files.keys()))
                if cochange and token_count + 50 < max_tokens:
                    cc_lines = [f"  {f} ({freq:.0%} co-change)" for f, freq in cochange[:5]]
                    cc_section = "RELATED FILES (frequently change together):\n" + "\n".join(cc_lines)
                    sections.append(cc_section)
                    token_count += count_tokens(cc_section)
        elif path_fallback_files:
            # All symbol searches were too broad, but path matching found specific files.
            # E.g. "demo" fails symbol focus (15+ matches) but path match → demos/ directory.
            if baseline_predicted_files is not None and len(baseline_predicted_files) >= 2:
                return ""  # v5 gate: model confident — skip path fallback too
                # Path-only context (no BFS graph) is weak when model already has a focused prediction.
                # If baseline predicted exactly 1 file with no overlap to path-match, the model is
                # likely correct on that file and the path hint would redirect it incorrectly.
                # Evidence (DRF authtoken-import): baseline=0.5 (auth.py, pred=1, correct),
                # path=authtoken/models.py (non-overlapping) → injection drops F1 to 0.
                if overlap == 0 and len(predicted_set) == 1:
                    return ""  # single focused prediction doesn't align with path hint → risky
            if precision_filter and len(path_fallback_files) > 4:
                return ""  # Too broad (path match) — skip context entirely
            _context_files = path_fallback_files[:5]
            kf_section = "KEY FILES (path match):\n" + "\n".join(f"  {f}" for f in path_fallback_files[:5])
            sections.append(kf_section)
            token_count += count_tokens(kf_section)
        elif not keywords:
            # Truly vague task (no keywords extracted) — overview provides structure, UNLESS the
            # task is a docs-named branch (docs-javascript, docs/#4574, readme-fix, etc.).
            # Docs branches often change both docs AND code; overview focuses the model on generic
            # structure (conf.py, README) instead of the actual code paths changed.
            # Evidence: flask "docs-javascript" overview → F1 0.556→0.154 (-0.402 delta).
            # Without overview, model uses training knowledge → ties (~0 delta, not regression).
            # Low-baseline repos (requests, django) use trunk-branch tasks for overview, not doc branches.
            if not _is_docs_branch_task(task):
                overview_fallback = render_overview(graph)
                sections.append(overview_fallback)
                token_count += count_tokens(overview_fallback)
        # else: keywords exist but focus found nothing → inject nothing; model uses training knowledge.
        # Evidence: overview hurts high-baseline repos (pydantic -40%, starlette -11%) when
        # focus fails on non-empty keywords.

    else:
        # General coding task path: multi-token fuzzy search + always-overview fallback.
        # Suitable for: "add login feature", "fix broken test", "explain this function".
        _query_tokens = [w for w in re.split(r'[\s/\-_]+', task) if len(w) >= 4]
        focus_output = render_focused(graph, task, max_tokens=int(max_tokens * 0.6))

        # Large-scope heuristic: bench data shows focused context hurts for 8+ file tasks.
        _BROAD_SCOPE_MARKERS = {"all", "every", "entire", "throughout", "global", "across",
                                "everywhere", "whole", "each"}
        _BROAD_ACTION_MARKERS = {"refactor", "migrate", "update", "port", "convert", "rename",
                                 "replace", "remove", "delete", "rewrite"}
        _task_set = set(task.lower().split())
        _is_large_scope = bool(
            _task_set & _BROAD_SCOPE_MARKERS
            and _task_set & _BROAD_ACTION_MARKERS
        )
        if _is_large_scope:
            sections.append(
                "⚠ LARGE SCOPE: task appears to span many files. "
                "Bench data shows focused context hurts F1 for 8+ file changes. "
                "Use `overview` for orientation; skip focused context injection."
            )
            token_count += 25

        _no_match = not focus_output or "No symbols matching" in focus_output or "No exact match" in focus_output
        if _no_match and not _is_large_scope:
            overview_fallback = render_overview(graph)
            sections.append(overview_fallback)
            token_count += count_tokens(overview_fallback)
        else:
            sections.append(focus_output)
            token_count += count_tokens(focus_output)

            if not _no_match:
                key_files = _extract_focus_files(focus_output)
                _context_files = key_files
                if key_files:
                    if len(key_files) > 10:
                        sections.append(
                            "⚠ BROAD MATCH: query matched many files — results may include "
                            "loosely related code. Consider re-querying with a more specific "
                            "symbol name or function for a tighter focus."
                        )
                        token_count += 20
                    kf_ranges = _extract_focus_ranges(focus_output, key_files[:5])
                    kf_lines = [f"  {f}:{kf_ranges[f]}" if f in kf_ranges else f"  {f}" for f in key_files[:5]]
                    kf_section = "KEY FILES REFERENCED ABOVE:\n" + "\n".join(kf_lines)
                    sections.append(kf_section)
                    token_count += count_tokens(kf_section)

                    # Co-change prediction for general task path too
                    cochange = _get_cochange_related(graph.root, key_files[:5], set(graph.files.keys()))
                    if cochange and token_count + 50 < max_tokens:
                        cc_lines = [f"  {f} ({freq:.0%} co-change)" for f, freq in cochange[:5]]
                        cc_section = "RELATED FILES (frequently change together):\n" + "\n".join(cc_lines)
                        sections.append(cc_section)
                        token_count += count_tokens(cc_section)

    hotspot_budget = int(max_tokens * 0.15)
    if token_count < max_tokens - 100:
        hotspot_output = render_hotspots(graph, top_n=5)
        ht = count_tokens(hotspot_output)
        if ht <= hotspot_budget:
            sections.append("\n## Hotspots (top 5 riskiest)")
            sections.append(hotspot_output)
            token_count += ht + 10

    # L2-guided: include dead code analysis if learned to be useful for this task_type
    if token_count < max_tokens - 200 and "dead" in l2_best_modes:
        dead_budget = min(1500, max_tokens - token_count - 100)
        dead_output = render_dead_code(graph, max_symbols=10, max_tokens=dead_budget)
        dt = count_tokens(dead_output)
        if dt <= dead_budget:
            sections.append("\n## Dead Code (L2: relevant for this task type)")
            sections.append(dead_output)
            token_count += dt + 10

    # Skills: include coding conventions for feature/refactor tasks so agents write convention-native code
    if token_count < max_tokens - 200 and task_type in ("feature", "refactor"):
        skills_budget = min(800, max_tokens - token_count - 100)
        skills_output = render_skills(graph, max_tokens=skills_budget)
        st = count_tokens(skills_output)
        if st <= skills_budget + 50:
            sections.append("\n## Coding Conventions (follow these when writing new code)")
            sections.append(skills_output)
            token_count += st + 10

    if token_count < max_tokens - 100:
        is_change = any(w in task.lower() for w in (
            "fix", "bug", "change", "modify", "update", "refactor", "add", "remove",
            "delete", "rename", "move", "migrate",
        ))
        if is_change or task_type in ("debug", "refactor", "feature"):
            repo_path = str(Path(graph.root).resolve())
            if is_git_repo(repo_path):
                try:
                    changed = changed_files_unstaged(repo_path)
                    if changed:
                        diff_budget = max_tokens - token_count
                        diff_output = render_diff_context(graph, changed, max_tokens=diff_budget)
                        dt = count_tokens(diff_output)
                        if dt <= diff_budget + 100:  # allow small overflow
                            sections.append(f"\n## Uncommitted changes ({len(changed)} files)")
                            sections.append(diff_output)
                            token_count += dt + 10
                except Exception:
                    pass

    coverage = _coverage_line(_query_tokens, graph, _context_files)
    if coverage:
        sections.append(coverage)

    sections.append("---\nCall report_feedback after using this context to improve future recommendations.")
    return "\n\n".join(sections)
