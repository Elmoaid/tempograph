from __future__ import annotations

import bisect
from pathlib import Path

from ..types import Tempo, EdgeKind, Symbol, SymbolKind
from ._utils import count_tokens, _is_test_file, _caller_domain, _MONOLITH_THRESHOLD, _dead_code_confidence
from .focused_signals import (  # noqa: F401
    _signals_focused_test_coverage,
    _signals_focused_test,
    _signals_focused_complexity,
    _signals_focused_structure,
    _signals_focused_class_hierarchy,
    _signals_focused_class_patterns,
    _signals_focused_coupling,
    _signals_focused_naming,
    _signals_focused_fn_traits,
    _signals_focused_fn_patterns,
    _signals_focused_fn_advanced,
)

_TEST_INDEX_ATTR = "_focused_test_index"


def _get_test_index(graph: Tempo) -> list[tuple[str, Symbol]]:
    """Return (or build) sorted test-symbol index for *graph*.

    Stored directly on the Tempo instance so the index is invalidated whenever
    the graph object is replaced — no id-collision risk, no memory leak.

    Index entries are ``(lower_name, symbol)`` for symbols in test files whose
    kind is function / method / test, sorted by lower_name for bisect.
    """
    idx = graph.__dict__.get(_TEST_INDEX_ATTR)
    if idx is not None:
        return idx
    TEST_KINDS = frozenset(("function", "method", "test"))
    pairs: list[tuple[str, Symbol]] = [
        (s.name.lower(), s)
        for s in graph.symbols.values()
        if _is_test_file(s.file_path) and s.kind.value in TEST_KINDS
    ]
    pairs.sort(key=lambda p: p[0])
    graph.__dict__[_TEST_INDEX_ATTR] = pairs
    return pairs


def _extract_focus_files(focus_output: str, task_keywords: list[str] | None = None) -> list[str]:
    """Extract unique file paths from a render_focused output string.

    Returns up to 15 paths sorted by: (1) source vs example/test tier,
    (2) hub penalty (files dominating >30% of mentions w/o keyword match → demoted),
    (3) task keyword match (filename stem contains a keyword), (4) frequency.

    Hub detection: files like fastify.js appear in 63% of fastify focus outputs
    regardless of task, polluting KEY FILES. Evidence: hub removal cut -0.061 F1
    harm on fastify corpus (n=30). Keyword-matched files are exempt from hub penalty.
    Primary-match files (from direct ● symbol lines) are also exempt from hub penalty:
    the file that contains the directly-searched symbol must always be considered relevant.
    Evidence: fastify reply-not-found — setNotFoundHandler is in fastify.js (14/25 = 56%
    mentions → hub), but fastify.js IS a changed file. Hub penalty incorrectly demoted it.
    """
    import re
    pattern = r'\b(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|rb)\b'
    all_paths = re.findall(pattern, focus_output)
    freq: dict[str, int] = {}
    for p in all_paths:
        freq[p] = freq.get(p, 0) + 1

    # Primary-match files: files referenced on direct symbol lines (● symbol — file:N-M).
    # These are the files that actually contain the searched symbol → never apply hub penalty.
    primary_files: set[str] = set()
    for line in focus_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("●"):
            m = re.search(r'—\s+(\S+\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|rb)):\d', stripped)
            if m:
                primary_files.add(m.group(1))

    kw_lower = [k.lower() for k in (task_keywords or [])]
    total_mentions = sum(freq.values())

    def _is_hub(path: str, stem: str) -> bool:
        if path in primary_files:
            return False  # Never penalize directly-matched symbol files
        if total_mentions <= 6:
            return False
        has_kw = any(kw in stem for kw in kw_lower if len(kw) > 3)
        return not has_kw and (freq[path] / total_mentions) > 0.30

    def _sort_key(path: str) -> tuple[int, int, int]:
        lower = path.lower()
        if any(x in lower for x in ("example", "tutorial", "demo", "sample")):
            tier = 2
        elif any(x in lower for x in ("test", "spec", "fixture")):
            tier = 1
        else:
            tier = 0
        stem = lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        hub_penalty = 1 if _is_hub(path, stem) else 0
        kw_rank = 0 if kw_lower and any(kw in stem for kw in kw_lower if len(kw) > 3) else 1
        return (tier + hub_penalty, kw_rank, -freq[path])

    return sorted(freq.keys(), key=_sort_key)[:15]



from ..keywords import _extract_cl_keywords  # noqa: F401 (re-exported for backward compat)

def _is_docs_branch_task(task: str) -> bool:
    """Return True when the PR is a docs/version/infra PR that should skip overview injection.

    Three cases where overview injection misleads (changes files outside the code graph):
    1. Docs branches: docs-javascript, docs/#4574, readme-fix → README/conf.py changes
    2. Version-release branches: version-0.1.5, v1.2.3 → changelog/pyproject changes
    3. Pure-infra body: "Pin versions of dependencies" → requirements.txt changes

    Evidence: flask "docs-javascript" overview → F1 0.556→0.154 (-0.402 delta).
              fastapi "fix-10" (Pin versions) overview → F1 0.500→0.286 (-0.214 delta).
              fastapi "docs/edit-timer-in-middleware" (Merge branch into docs/X) → overview injected
              because "Merge branch" format wasn't matched — overview misleads to docs_src/ files.
    """
    import re
    # "Merge pull request #N from user/branch-name" format
    m = re.search(r'Merge pull request \S+ from [^/\s]+/(\S+)', task)
    if not m:
        # "Merge branch 'name' into target" format — also check for docs branch in target
        m2 = re.search(r"[Mm]erge branch '([^']+)' into ([^\s]+)", task)
        if m2:
            # The TARGET branch (after "into") may be the docs branch
            target = m2.group(2).lower().strip("'\"")
            source = m2.group(1).lower().strip("'\"")
            # Use the more specific branch (the one that isn't 'master'/'main')
            branch = target if target not in ("master", "main", "develop") else source
        else:
            return False
    else:
        branch = m.group(1).lower()
    branch = branch.strip("'\"")  # strip any trailing quote chars
    leaf = branch.split('/')[-1]
    # Docs branches: "docs" as a hyphen/underscore/slash-separated component anywhere.
    # Matches: docs-javascript, auth-docs, 5309-docs-viewset (DRF-style mid-name).
    # Does NOT match: docstring-update (component is "docstring", not "docs").
    _DOC_COMPONENT = re.compile(r'(?:^|[-_/])docs?(?:[-_/]|$)')
    if (bool(_DOC_COMPONENT.search(leaf))
            or any(re.search(r'(?:^|[-_/])' + kw + r'(?:[-_/]|$)', leaf)
                   for kw in ("readme", "changelog", "documentation"))
            or branch.startswith("docs/")
            or branch.startswith("doc/")):
        return True
    # Version-release branches: "version-X.Y.Z", "v1.2.3", "release-1.0" in branch leaf.
    # These change pyproject.toml / CHANGELOG, not source files.
    if (re.search(r'(?:^|[-_/])v?\d+\.\d+', leaf)
            or re.search(r'(?:^|[-_/])version(?:[-_/]|$)', leaf)
            or re.search(r'(?:^|[-_/])release(?:[-_/]|$)', leaf)):
        return True
    # Pure-infrastructure body: ticket-ref branch where body ONLY contains infra words.
    # "fix-10" + "Pin versions of dependencies and bump version" → requirements.txt.
    # Keyword extraction already returns [] for these; overview adds no code-graph signal.
    _is_ticket = bool(
        re.match(r'^(?:issue|ticket|bug|patch|pr|fix|hotfix)[-_]?\d+', leaf)
        or re.match(r'^\d+[-_]', leaf)
    )
    if _is_ticket:
        body = task[task.find('\n') + 1:].strip() if '\n' in task else ''
        _INFRA_ONLY = frozenset({
            "pin", "pinned", "pinning", "bump", "bumped", "bumping",
            "version", "versions", "versioning", "release", "releases",
            "dependency", "dependencies", "deps", "package", "packages",
            "upgrade", "upgraded", "upgrading", "downgrade", "downgraded",
            "install", "installation", "requirements", "freeze", "frozen",
            "and", "the", "a", "an", "of", "to", "for", "in", "with", "from",
        })
        body_words = set(re.findall(r'[a-zA-Z]+', body.lower()))
        if body_words and body_words <= _INFRA_ONLY:
            return True
    return False



def _suggest_alternatives(graph: Tempo, query: str, max_suggestions: int = 5) -> str:
    """Build a 'did you mean?' hint when a focus query has no matches.

    Splits the query into tokens and searches for each, returning the
    top-scoring symbols as suggestions to try instead.
    """
    import re
    tokens = [t for t in re.split(r'[^a-zA-Z0-9]+', query) if len(t) > 2]
    if not tokens:
        return ""
    seen_ids: set[str] = set()
    candidates: list[tuple[float, Symbol]] = []
    for token in tokens:
        for score, sym in graph.search_symbols_scored(token)[:10]:
            if sym.id not in seen_ids:
                seen_ids.add(sym.id)
                candidates.append((score, sym))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: -x[0])
    lines = [f"No exact match for '{query}'. Closest symbols:"]
    for _, sym in candidates[:max_suggestions]:
        file_short = sym.file_path.split("/")[-1] if "/" in sym.file_path else sym.file_path
        lines.append(f"  {sym.name} ({sym.kind.value}) — {file_short}:{sym.line_start}")
    lines.append(f"\nTry: focus('{candidates[0][1].name}')")
    return "\n".join(lines)


def _cochange_orbit(
    repo_root: str, seed_files: list[str], seen_files: set[str], n: int = 3
) -> list[tuple[str, float, int]]:
    """Return top N co-change partners for seed_files not already in seen_files.

    Returns (file_path, decayed_score, days_since_last_cochange).
    Uses recency-weighted scoring: recent co-changes outrank stale ones.
    Empty list if repo has no git history or no meaningful coupling.
    """
    try:
        from ..git import cochange_matrix_recency, is_git_repo
        if not repo_root or not is_git_repo(repo_root):
            return []
        matrix = cochange_matrix_recency(repo_root, n_commits=200)
    except Exception:
        return []

    seed_set = set(seed_files)
    partners: dict[str, tuple[float, int]] = {}
    for sf in seed_files:
        for partner, score, days in matrix.get(sf, []):
            if partner not in seed_set and partner not in seen_files:
                if partner not in partners or score > partners[partner][0]:
                    partners[partner] = (score, days)

    return [(fp, score, days) for fp, (score, days) in
            sorted(partners.items(), key=lambda x: -x[1][0])[:n]]


def _find_orbit_seeds(
    graph: "Tempo",
    query_tokens: list[str],
    orbit_pairs: list[tuple[str, float, int]],
) -> list[tuple["Symbol", float]]:
    """Find the best-matching symbol in each orbit file by query token overlap.

    Returns (symbol, coupling_freq) pairs — up to 1 per orbit file, max 3 total.
    Only files with at least one symbol matching a query token are included.
    This is how git-coupled files that aren't in the call graph become BFS seeds."""
    if not query_tokens:
        return []

    results: list[tuple["Symbol", float]] = []
    for fp, freq, _days in orbit_pairs:
        syms = graph.symbols_in_file(fp)
        best_sym: "Symbol | None" = None
        best_score = 0
        for sym in syms:
            name_lower = sym.name.lower()
            score = sum(1 for tok in query_tokens if tok in name_lower)
            if score > best_score:
                best_score = score
                best_sym = sym
        if best_sym and best_score > 0:
            results.append((best_sym, freq))

    return results[:3]


def _collect_seeds(
    graph: Tempo, query: str
) -> tuple[list[Symbol], set[str], list[str]]:
    """Tokenize query, search for seed symbols, apply quality gate.

    Returns (seeds, seed_files, query_tokens).
    seeds is empty when there are no matches (caller should return early).
    seed_files contains paths of monolith-sized files (>= _MONOLITH_THRESHOLD lines)
    that host at least one seed symbol — used to bias BFS toward cross-file edges."""
    import re as _re
    # Split query tokens and expand CamelCase: "ReplyNotFound" → ["reply", "not", "found"]
    # so "reply" matches "test/internals/reply.test.js" even when query is CamelCase.
    _raw_tokens = _re.split(r'[^a-zA-Z0-9]+', query)
    _camel_tokens: list[str] = []
    for tok in _raw_tokens:
        parts = _re.sub(r'([A-Z][a-z]+)', r' \1', _re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', tok)).split()
        _camel_tokens.extend(parts if len(parts) > 1 else [tok])
    query_tokens = [t.lower() for t in _camel_tokens if len(t) >= 3]

    scored = graph.search_symbols_scored(query)
    if not scored:
        return [], set(), query_tokens

    # Quality gate: drop seeds with much lower scores than the best match
    top_score = scored[0][0]
    threshold = max(top_score * 0.3, 2.0)  # at least 30% of best, minimum 2.0
    all_seeds = [sym for score, sym in scored if score >= threshold][:10]
    # Prefer non-test seeds when available — test functions named "test_foo_bar"
    # match queries like "foo_bar" but they're not useful as BFS starting points.
    non_test_seeds = [sym for sym in all_seeds if not _is_test_file(sym.file_path)]
    seeds = non_test_seeds if non_test_seeds else all_seeds

    seed_files: set[str] = set()
    for s in seeds:
        fi = graph.files.get(s.file_path)
        if fi and fi.line_count >= _MONOLITH_THRESHOLD:
            seed_files.add(s.file_path)

    return seeds, seed_files, query_tokens


def _is_utility_callee(sym: Symbol, graph: "Tempo") -> bool:
    """Symbol is a utility/hub if it has 10+ unique cross-file caller files."""
    callers = graph._callers.get(sym.id, [])
    if len(callers) < 10:
        return False
    cross_file_fps: set[str] = set()
    for cid in callers:
        if cid in graph.symbols and graph.symbols[cid].file_path != sym.file_path:
            cross_file_fps.add(graph.symbols[cid].file_path)
            if len(cross_file_fps) >= 10:
                return True
    return False


def _sym_importance(graph: "Tempo", sym: Symbol) -> int:
    """Structural importance score for BFS prioritisation.

    Score = cross_file_callers * 2 + (1 if exported) + (2 if hot_file).
    Uses raw ``_callers`` index to avoid creating Symbol objects per lookup."""
    caller_ids = graph._callers.get(sym.id, [])
    cross = sum(
        1 for cid in caller_ids
        if cid in graph.symbols and graph.symbols[cid].file_path != sym.file_path
    )
    return cross * 2 + (1 if sym.exported else 0) + (2 if sym.file_path in graph.hot_files else 0)


def _bfs_expand(
    graph: Tempo,
    seeds: list[Symbol],
    seed_files: set[str],
    secondary_seeds: list[Symbol] | None = None,
    max_depth: int | None = None,
) -> tuple[list[tuple[Symbol, int]], set[str]]:
    """BFS from seed symbols following caller/callee/child edges.

    Returns (ordered, seen_ids).
    ordered is the BFS traversal sequence with (symbol, depth) pairs.
    seen_ids is the full set of visited symbol IDs — callers use it for
    file-context deduplication so already-shown symbols are excluded.

    secondary_seeds are orbit-derived symbols injected at depth 1 — they
    expand the BFS into git-coupled files that aren't reachable via call edges.

    max_depth overrides the hot-seeds heuristic when provided (used by adaptive
    sparse-neighborhood expansion in render_focused).

    Within each depth level, candidates are sorted by structural importance so
    that when the 50-node cap truncates, hub nodes (many cross-file callers,
    exported, in hot files) survive over orphan/internal-only nodes."""
    _hot_seeds = any(s.file_path in graph.hot_files for s in seeds)
    _bfs_max_depth = max_depth if max_depth is not None else (4 if _hot_seeds else 3)
    seen_ids: set[str] = set()
    ordered: list[tuple[Symbol, int]] = []

    # Level-by-level BFS: collect all candidates per depth, sort by importance,
    # then expand.  This guarantees depth ordering (BFS topology unchanged)
    # while ensuring high-importance nodes within a depth are visited first.
    current_level: list[tuple[Symbol, int]] = [(s, 0) for s in seeds]
    if secondary_seeds:
        primary_ids = {s.id for s in seeds}
        for s in secondary_seeds:
            if s.id not in primary_ids:
                current_level.append((s, 1))

    # Pre-compute importance scores once per candidate (cache across levels)
    _imp_cache: dict[str, int] = {}

    def _cached_importance(sym: Symbol) -> int:
        score = _imp_cache.get(sym.id)
        if score is None:
            score = _sym_importance(graph, sym)
            _imp_cache[sym.id] = score
        return score

    while current_level and len(ordered) < 50:
        # Deduplicate and remove already-seen before sorting
        deduped: list[tuple[Symbol, int]] = []
        for sym, d in current_level:
            if sym.id not in seen_ids:
                deduped.append((sym, d))
                seen_ids.add(sym.id)

        # Sort within this batch: lower depth first (preserves BFS layering),
        # then cross-file nodes first, then hot-file nodes first (S1029),
        # then domain callees before utility/hub callees, then by descending importance.
        deduped.sort(key=lambda pair: (
            pair[1],                                                  # depth ascending
            pair[0].file_path in seed_files if seed_files else True,  # cross-file first
            pair[0].file_path not in graph.hot_files,                 # S1029: hot files first
            _is_utility_callee(pair[0], graph),                       # domain (False=0) before utility (True=1)
            -_cached_importance(pair[0]),                              # importance descending
        ))

        next_level: list[tuple[Symbol, int]] = []
        for sym, depth in deduped:
            if len(ordered) >= 50:
                break
            ordered.append((sym, depth))

            if depth < _bfs_max_depth:
                if depth == 0:
                    # Hub-adaptive caller budget: seeds called from many files
                    # get a tighter caller limit so callees (query-relevant
                    # dependencies) are not crowded out of the 50-node cap.
                    _cross_callers = sum(
                        1 for cid in graph._callers.get(sym.id, [])
                        if cid in graph.symbols
                        and graph.symbols[cid].file_path != sym.file_path
                    )
                    caller_limit = 12 if _cross_callers < 15 else 3 if _cross_callers < 25 else 1
                    callee_limit = 6
                else:
                    caller_limit = 6 if depth == 1 else 3
                    callee_limit = 4 if depth == 1 else 2
                _imp_key = lambda s: -_cached_importance(s)
                # Hub suppression: skip expanding callers of widely-used utility symbols.
                # A symbol used across 15+ unique files is a global hub — expanding its
                # callers would flood the BFS with irrelevant cross-module context.
                # Callees and children are still expanded (those are the symbol's dependencies).
                _expand_callers = True
                if depth >= 1:
                    _hub_cfiles = {
                        graph.symbols[cid].file_path
                        for cid in graph._callers.get(sym.id, [])
                        if cid in graph.symbols and graph.symbols[cid].file_path != sym.file_path
                    }
                    if len(_hub_cfiles) >= 15:
                        _expand_callers = False
                if _expand_callers:
                    for caller in sorted(graph.callers_of(sym.id), key=_imp_key)[:caller_limit]:
                        if caller.id not in seen_ids:
                            next_level.append((caller, depth + 1))
                for callee in sorted(
                    graph.callees_of(sym.id),
                    key=lambda s: (_is_utility_callee(s, graph), -_cached_importance(s)),
                )[:callee_limit]:
                    if callee.id not in seen_ids:
                        next_level.append((callee, depth + 1))
                if depth < 2:
                    for child in graph.children_of(sym.id)[:5]:
                        if child.id not in seen_ids:
                            next_level.append((child, depth + 1))

                # INHERITS: subclasses of the current symbol (if it's a class/interface)
                if sym.kind.value in ("class", "interface", "struct", "trait"):
                    _subtypes = graph.subtypes_of(sym.name)
                    _cross_file_subtypes = [
                        s for s in _subtypes
                        if s.file_path != sym.file_path and s.id not in seen_ids
                    ]
                    subtype_limit = 5 if depth == 0 else (3 if depth == 1 else 0)
                    for st in _cross_file_subtypes[:subtype_limit]:
                        if len(ordered) + len(next_level) < 50:
                            next_level.append((st, depth + 1))

                # RENDERS: components that render this component
                _renderers = graph.renderers_of(sym.id)
                _cross_file_renderers = [
                    s for s in _renderers
                    if s.file_path != sym.file_path and s.id not in seen_ids
                ]
                renderer_limit = 4 if depth == 0 else (2 if depth == 1 else 0)
                for r in _cross_file_renderers[:renderer_limit]:
                    if len(ordered) + len(next_level) < 50:
                        next_level.append((r, depth + 1))

        current_level = next_level

    return ordered, seen_ids


def _handle_overflow(
    lines: list[str],
    ordered: list[tuple[Symbol, int]],
    block: str,
    token_count: int,
    max_tokens: int,
    *,
    graph: "Tempo | None" = None,
    current_idx: int = 0,
) -> tuple[bool, int]:
    """Check whether adding block would exceed the token budget.

    Returns (should_break, block_tokens).
    When should_break is True a truncation message has already been appended
    to lines — the caller should stop the rendering loop immediately.

    If graph is provided, lists up to 5 high-importance (score >= 3) dropped
    symbol names so agents know which hub symbols were truncated."""
    block_tokens = count_tokens(block)
    if token_count + block_tokens + 1 > max_tokens:  # +1 for separator "\n" in final join
        remaining = len(ordered) - current_idx
        if remaining > 0:
            hi_names: list[tuple[int, str]] = []
            if graph is not None:
                for sym_d, _depth in ordered[current_idx:]:
                    imp = _sym_importance(graph, sym_d)
                    if imp >= 3:
                        hi_names.append((imp, sym_d.name))
                hi_names.sort(key=lambda x: -x[0])
            if hi_names:
                names = ", ".join(n for _, n in hi_names[:5])
                lines.append(f"... ({remaining} more symbols — high-importance: {names})")
            else:
                lines.append(f"... truncated ({remaining} more symbols)")
        return True, block_tokens
    return False, block_tokens


def _extend_tracked(lines: list[str], new_lines: list[str], token_count: int) -> int:
    """Extend lines with new_lines and return updated token_count.

    Used in render_focused to keep a running token budget across all
    _signals_focused_* sections so each section sees the correct remaining budget
    and output does not silently overflow max_tokens."""
    if new_lines:
        lines.extend(new_lines)
        token_count += count_tokens("\n".join(new_lines))
    return token_count


def _render_cochange_section(graph, seed_file_paths: list[str]) -> str:
    """Build the 'Co-changed with (basename):' section for render_focused."""
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from ..git import cochange_pairs
        pairs = cochange_pairs(graph.root, seed_file_paths[0])
        if pairs:
            basename = Path(seed_file_paths[0]).name
            parts = [f"\nCo-changed with ({basename}):"]
            for p in pairs:
                parts.append(f"  \u2022 {p['path']} \u2014 {p['count']} commits together")
            return "\n".join(parts)
    except Exception:
        pass
    return ""


def _render_cochange_cohort_section(
    graph, seed_file_paths: list[str], seen_files: set[str]
) -> str:
    """Build the 'Co-change cohort:' section — files historically co-edited with the
    seed file that are NOT already shown in the focus BFS output (S46).

    Unlike _render_cochange_section (no seen_files filter) and
    _render_cochange_orbit_section (recency-weighted), this section uses raw commit
    counts and filters seen_files — surfacing structurally coupled files that BFS
    missed (no direct call edges) even when coupling is old/stale.

    Only shown if ≥1 qualifying file exists (min 3 co-changes, not in seen_files).
    Cap at 5 files.
    """
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from ..git import cochange_pairs
        pairs = cochange_pairs(graph.root, seed_file_paths[0], n=10, min_count=3)
        if not pairs:
            return ""
        basename = Path(seed_file_paths[0]).name
        filtered = [p for p in pairs if p["path"] not in seen_files][:5]
        if not filtered:
            return ""
        parts = [f"\nCo-change cohort (files often edited alongside {basename}):"]
        for p in filtered:
            parts.append(f"  {p['path']} (co-changed {p['count']} times)")
        return "\n".join(parts)
    except Exception:
        return ""


def _render_all_callers_section(
    graph, seeds: list, callsite_lines: dict, token_count: int = 0, max_tokens: int = 0
) -> str:
    """Complete callers section — all callers of seed symbols, grouped by file.

    Shows which source files call the seed symbol and where (line numbers).
    Useful for rename/refactor impact: agents see every call site at once.
    Test callers are excluded (already shown in Tests section).
    Triggered when total source callers >= 2. Capped at 5 files / 3 names each."""

    # Collect all source (non-test) callers across all seeds
    by_file: dict[str, list[tuple[str, str, int]]] = {}  # file → [(caller_name, caller_id, seed_id)]
    for seed in seeds:
        for caller in graph.callers_of(seed.id):
            if _is_test_file(caller.file_path):
                continue
            entry = (caller.name, caller.id, seed.id)
            by_file.setdefault(caller.file_path, []).append(entry)

    if not by_file:
        return ""

    total = sum(len(v) for v in by_file.values())
    if total < 2:
        return ""

    if max_tokens > 0 and token_count > max_tokens - 100:
        return ""

    # Sort files by number of callers (most first), take top 5
    sorted_files = sorted(by_file.items(), key=lambda kv: -len(kv[1]))
    n_files_total = len(sorted_files)
    shown_files = sorted_files[:5]
    hidden_files = n_files_total - len(shown_files)

    parts = [f"\nCallers ({total} in {n_files_total} file{'s' if n_files_total != 1 else ''}):"]
    # S54: module span — top-level directory distribution when callers come from 3+ dirs
    _mod_counts: dict[str, int] = {}
    for fp in by_file:
        _mod = fp.split("/")[0] if "/" in fp else ""
        if _mod:
            _mod_counts[_mod] = _mod_counts.get(_mod, 0) + 1
    if len(_mod_counts) >= 3:
        _span_parts = [f"{m}/ ({n})" for m, n in sorted(_mod_counts.items(), key=lambda x: -x[1])[:4]]
        parts.append(f"  span: {', '.join(_span_parts)}")
    for fp, entries in shown_files:
        # De-duplicate by caller name, preserve order
        seen_names: set[str] = set()
        unique: list[tuple[str, str, str]] = []
        for name, cid, sid in entries:
            if name not in seen_names:
                seen_names.add(name)
                unique.append((name, cid, sid))

        shown = unique[:3]
        overflow = len(unique) - len(shown)

        caller_strs = []
        for name, cid, sid in shown:
            lines_list = callsite_lines.get((cid, sid), [])
            if lines_list:
                loc = f"[line {lines_list[0]}]" if len(lines_list) == 1 else f"[lines {', '.join(str(l) for l in lines_list[:2])}]"
                caller_strs.append(f"{name} {loc}")
            else:
                caller_strs.append(name)

        suffix = f", +{overflow} more" if overflow else ""
        parts.append(f"  {fp}: {', '.join(caller_strs)}{suffix}")

    if hidden_files:
        parts.append(f"  ... and {hidden_files} more file{'s' if hidden_files != 1 else ''}")

    return "\n".join(parts)


def _render_hot_callers_section(
    graph, seeds: list, token_count: int, max_tokens: int
) -> str:
    """Build the 'Hot callers:' section — callers of seed symbols in recently-modified files.

    Helps agents understand which callers are actively being changed, critical
    context for avoiding merge conflicts and understanding in-progress work.
    Does NOT filter by seen_ids: even callers already shown in BFS benefit from
    the focused summary (the inline [hot] tag is easy to miss in long output)."""
    if token_count > max_tokens - 80 or not graph.hot_files:
        return ""

    hot_entries: list[tuple[str, str, int]] = []  # (caller_name, file_path, line)
    seen_files_dedup: set[str] = set()
    for seed in seeds:
        for caller in graph.callers_of(seed.id):
            if caller.file_path in graph.hot_files and caller.file_path not in seen_files_dedup:
                hot_entries.append((caller.name, caller.file_path, caller.line_start))
                seen_files_dedup.add(caller.file_path)

    if not hot_entries:
        return ""

    # Sort by file path for stable output, cap at 5
    hot_entries.sort(key=lambda e: e[1])
    hot_entries = hot_entries[:5]

    parts = ["\nHot callers:"]
    for name, fp, line in hot_entries:
        basename = Path(fp).name
        parts.append(f"  {name} ({basename}:{line}) \u2014 last seen in hot file")
    return "\n".join(parts)


def _render_dependency_files_section(
    graph, ordered: list[tuple], seen_files: set[str], token_count: int, max_tokens: int
) -> str:
    """Build the 'Depends on:' section — outgoing dependency files for seed symbols.

    Collects callees of depth-0 (seed) symbols, groups by file, and shows
    up to 3 callee names per file. Skipped when fewer than 2 dependency files
    remain after filtering the seed's own file."""
    if token_count > max_tokens - 50:
        return ""

    seed_files = {sym.file_path for sym, depth in ordered if depth == 0}
    dep_map: dict[str, list[str]] = {}
    for sym, depth in ordered:
        if depth != 0:
            continue
        for callee in graph.callees_of(sym.id):
            fp = callee.file_path
            if fp in seed_files:
                continue
            if fp not in dep_map:
                dep_map[fp] = []
            if callee.name not in dep_map[fp]:
                dep_map[fp].append(callee.name)

    if len(dep_map) < 2:
        return ""

    sorted_deps = sorted(dep_map.items(), key=lambda x: -len(x[1]))[:6]
    parts = ["\nDepends on:"]
    for fp, names in sorted_deps:
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f", +{len(names) - 3} more"
        parts.append(f"  {fp} ({shown})")
    return "\n".join(parts)


def _render_recent_changes_section(graph, seed_file_paths: list[str]) -> str:
    """Build the 'Recent changes (basename):' section for render_focused."""
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from ..git import recent_file_commits
        primary_file = seed_file_paths[0]
        commits = recent_file_commits(graph.root, primary_file)
        if commits:
            basename = Path(primary_file).name
            parts = [f"\nRecent changes ({basename}):"]
            for c in commits:
                parts.append(f"  \u2022 {c['days_ago']}d ago: {c['message']}")
            return "\n".join(parts)
    except Exception:
        pass
    return ""


def _render_volatility_section(graph, seed_file_paths: list[str], token_count: int, max_tokens: int) -> str:
    """Build the 'Volatile:' section for render_focused."""
    if not graph.root or not seed_file_paths:
        return ""
    try:
        from ..git import file_commit_counts
        churn = file_commit_counts(graph.root)
        _VOLATILE_THRESHOLD = 10
        volatile = [(fp, churn.get(fp, 0)) for fp in seed_file_paths
                     if churn.get(fp, 0) >= _VOLATILE_THRESHOLD]
        if volatile:
            parts = [f"{fp} ({count}/200 commits)" for fp, count in volatile]
            return f"\nVolatile: {', '.join(parts)} \u2014 high-churn file(s), re-read before editing"
    except Exception:
        pass
    return ""


def _render_cochange_orbit_section(graph, seed_file_paths: list[str], seen_files: set[str],
                                    token_count: int, max_tokens: int) -> str:
    """Build the 'Co-change orbit:' section for render_focused."""
    orbit = _cochange_orbit(graph.root, seed_file_paths, seen_files)
    if not orbit or token_count >= max_tokens - 80:
        return ""

    def _recency_label(days: int) -> str:
        if days < 45:
            return "recent"
        elif days < 120:
            return "aging"
        return "stale"

    orbit_parts = [f"{fp} ({score:.0%} {_recency_label(days)})" for fp, score, days in orbit]
    return (f"\nCo-change orbit: {', '.join(orbit_parts)}\n"
            "  (files that historically change with these \u2014 check if your change affects them)")


def _render_blast_risk_section(graph, ordered: list, token_count: int, max_tokens: int) -> str:
    """Build the 'High impact:' blast risk badge for render_focused."""
    _BLAST_FILE_THRESHOLD = 5
    _blast_hits: list[tuple] = []
    for _bs, _bd in ordered[:5]:
        if _bd != 0:
            continue
        _ext_files = {c.file_path for c in graph.callers_of(_bs.id) if c.file_path != _bs.file_path}
        if len(_ext_files) > _BLAST_FILE_THRESHOLD:
            _blast_hits.append((_bs, len(_ext_files)))
    if _blast_hits and token_count < max_tokens - 60:
        _blast_hits.sort(key=lambda x: -x[1])
        _top_sym, _top_count = _blast_hits[0]
        return f"\nHigh impact: {_top_count} files depend on {_top_sym.qualified_name} \u2014 run blast mode before editing"
    return ""


def _render_related_files_section(graph, ordered: list, seen_files: set[str]) -> str:
    """Build the 'Related files:' section for render_focused."""
    related = _find_related_files(graph, [s for s, _ in ordered[:10]])
    unseen = related - seen_files
    if not unseen:
        return ""
    parts = ["\nRelated files:"]
    for fp in sorted(unseen)[:10]:
        fi = graph.files.get(fp)
        if fi:
            tag = " [grep-only]" if fi.line_count > 500 else ""
            parts.append(f"  {fp} ({fi.line_count} lines){tag}")
    return "\n".join(parts)


def _render_file_context_section(graph, seen_files: set[str], seen_ids: set[str],
                                  token_count: int, max_tokens: int) -> tuple[str, int]:
    """Build the 'Also in these files:' section for render_focused.
    Returns (section_text, token_cost)."""
    file_context: list[str] = []
    for fp in sorted(seen_files):
        fi = graph.files.get(fp)
        if not fi or len(fi.symbols) < 3:
            continue
        file_syms = [graph.symbols[sid] for sid in fi.symbols if sid in graph.symbols and sid not in seen_ids]
        important = [s for s in file_syms if s.exported and s.kind in (
            SymbolKind.FUNCTION, SymbolKind.CLASS, SymbolKind.COMPONENT, SymbolKind.HOOK
        )][:5]
        if important:
            names = ", ".join(f"{s.name} L{s.line_start}" for s in important)
            file_context.append(f"  {fp}: also has {names}")
    if file_context:
        ctx_block = "\nAlso in these files:\n" + "\n".join(file_context)
        ctx_tokens = count_tokens(ctx_block)
        if token_count + ctx_tokens <= max_tokens:
            return ctx_block, ctx_tokens
    return "", 0


def _render_monolith_section(graph, ordered: list, token_count: int, max_tokens: int) -> tuple[str, int]:
    """Build monolith neighborhood sections for render_focused.
    Returns (section_text, token_cost)."""
    parts: list[str] = []
    total_tokens = 0
    for sym, depth in ordered:
        if depth > 0:
            break
        fi = graph.files.get(sym.file_path)
        if not fi or fi.line_count < _MONOLITH_THRESHOLD:
            continue
        neighborhood = _monolith_neighborhood(graph, sym)
        if neighborhood:
            nb_block = "\n".join(neighborhood)
            nb_tokens = count_tokens(nb_block)
            if token_count + total_tokens + nb_tokens <= max_tokens:
                parts.append("")
                parts.extend(neighborhood)
                total_tokens += nb_tokens
    if parts:
        return "\n".join(parts), total_tokens
    return "", 0


# ---------------------------------------------------------------------------
# _build_symbol_block_lines helper: depth-0 header annotation sub-helpers
# ---------------------------------------------------------------------------

def _compute_blast_age_anns(
    sym: "Symbol",
    graph: "Tempo",
    staleness_cache: dict,
) -> tuple[str, str]:
    """Compute blast-radius and symbol-age annotation strings for the depth-0 header."""
    _blast_files = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}
    if len(_blast_files) >= 3:
        blast_ann = f" [blast: {len(_blast_files)} files]"
    elif len(_blast_files) == 1:
        _sole_file = next(iter(_blast_files))
        blast_ann = f" [owned by: {_sole_file.rsplit('/', 1)[-1]}]"
    elif len(_blast_files) == 0 and sym.exported and sym.kind.value in ("function", "method", "class"):
        blast_ann = " [entry point]"
    else:
        blast_ann = ""
    age_ann = ""
    # C15: use pre-fetched file-level age from staleness_cache (avoids git log -L subprocess)
    _days = staleness_cache.get(sym.file_path) if staleness_cache else None
    if _days is not None and _days >= 8:
        if _days >= 365:
            age_ann = " [age: 1y+]"
        elif _days >= 30:
            age_ann = f" [age: {_days // 30}m]"
        else:
            age_ann = f" [age: {_days}d]"
    return blast_ann, age_ann


def _compute_callee_depth_anns(
    sym: "Symbol",
    graph: "Tempo",
) -> tuple[str, str]:
    """Compute callee-count and callee-depth annotation strings for the depth-0 header."""
    _direct_callees = graph.callees_of(sym.id)
    callee_ann = f" [calls: {len(_direct_callees)}]" if len(_direct_callees) >= 5 else ""
    _bfs_q: list[tuple[str, int]] = [(sym.id, 0)]
    _bfs_seen: set[str] = {sym.id}
    _max_callee_depth = 0
    while _bfs_q and len(_bfs_seen) < 60:
        _cur_id, _cur_lvl = _bfs_q.pop(0)
        if _cur_lvl > _max_callee_depth:
            _max_callee_depth = _cur_lvl
        if _cur_lvl >= 8:
            continue
        for _callee in graph.callees_of(_cur_id):
            if _callee.id not in _bfs_seen:
                _bfs_seen.add(_callee.id)
                _bfs_q.append((_callee.id, _cur_lvl + 1))
    depth_ann = f" [callee depth: {_max_callee_depth}]" if _max_callee_depth >= 3 else ""
    return callee_ann, depth_ann


def _compute_async_doc_param_anns(
    sym: "Symbol",
    graph: "Tempo",
) -> tuple[str, str, str]:
    """Compute async, undocumented, and param-count annotation strings for depth-0 header."""
    async_ann = ""
    if sym.kind.value in ("function", "method") and sym.signature.startswith("async "):
        async_ann = " [async]"
    doc_ann = ""
    if sym.exported and not sym.doc and sym.kind.value in ("function", "method"):
        _ext_caller_files = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}
        if len(_ext_caller_files) >= 3:
            doc_ann = " [undocumented]"
    param_ann = ""
    if sym.kind.value in ("function", "method") and sym.signature:
        _s_open = sym.signature.find("(")
        _s_close = sym.signature.rfind(")")
        if _s_open != -1 and _s_close > _s_open:
            _ps = sym.signature[_s_open + 1:_s_close].strip()
            if _ps:
                _pd, _pc = 0, 0
                for _ch in _ps:
                    if _ch in "([{":
                        _pd += 1
                    elif _ch in ")]}":
                        _pd -= 1
                    elif _ch == "," and _pd == 0:
                        _pc += 1
                if _pc + 1 >= 5:
                    param_ann = f" [params: {_pc + 1}]"
    return async_ann, doc_ann, param_ann


def _compute_structure_anns(
    sym: "Symbol",
    graph: "Tempo",
) -> tuple[str, str]:
    """Compute class-size and import-depth annotation strings for depth-0 header."""
    class_size_ann = ""
    if sym.kind.value in ("class", "interface", "component"):
        _children = graph.children_of(sym.id)
        _methods = [c for c in _children if c.kind.value in ("method", "function")]
        _props = [c for c in _children if c.kind.value in ("field", "property", "variable")]
        if len(_methods) >= 5:
            class_size_ann = f" [methods: {len(_methods)}]"
            if _props:
                class_size_ann += f"[props: {len(_props)}]"
    depth_from_entry_ann = ""
    _FOCUS_ENTRY_NAMES = {
        "__main__.py", "main.py", "app.py", "manage.py", "cli.py",
        "server.py", "wsgi.py", "asgi.py", "run.py", "index.js",
        "index.ts", "index.tsx", "main.ts", "main.tsx", "main.go",
    }
    _entry_fps = {fp for fp in graph.files if fp.rsplit("/", 1)[-1] in _FOCUS_ENTRY_NAMES}
    if _entry_fps and sym.file_path not in _entry_fps:
        _bfs_imp: list[tuple[str, int]] = [(sym.file_path, 0)]
        _seen_imp: set[str] = {sym.file_path}
        _found_depth: int | None = None
        while _bfs_imp and _found_depth is None:
            _cur_fp, _cur_d = _bfs_imp.pop(0)
            if _cur_d >= 8:
                continue
            for _imp in graph.importers_of(_cur_fp):
                if _imp in _entry_fps:
                    _found_depth = _cur_d + 1
                    break
                if _imp not in _seen_imp and len(_seen_imp) < 80:
                    _seen_imp.add(_imp)
                    _bfs_imp.append((_imp, _cur_d + 1))
        if _found_depth is not None and _found_depth >= 4:
            depth_from_entry_ann = f" [depth: {_found_depth}]"
    return class_size_ann, depth_from_entry_ann


def _compute_recursion_label(
    sym: "Symbol",
    graph: "Tempo",
) -> str:
    """Detect self or mutual recursion; return the annotation label string."""
    if sym.kind.value not in ("function", "method"):
        return ""
    _seed_callees = graph.callees_of(sym.id)
    _seed_callee_ids = {c.id for c in _seed_callees}
    if sym.id in _seed_callee_ids:
        return "[recursive]"
    for _callee_s in _seed_callees[:10]:
        if sym.id in {c.id for c in graph.callees_of(_callee_s.id)}:
            return f"[recursive: mutual with {_callee_s.name}]"
    return ""


def _compute_seed_annotations(
    sym: "Symbol",
    graph: "Tempo",
    staleness_cache: dict,
) -> dict[str, str]:
    """Compute all annotation strings for a depth-0 symbol header.

    Returns dict: blast, age, callee, depth, async_, doc, param,
    class_size, depth_entry, recursive. Hub is always '' at depth 0."""
    blast_ann, age_ann = _compute_blast_age_anns(sym, graph, staleness_cache)
    callee_ann, depth_ann = _compute_callee_depth_anns(sym, graph)
    async_ann, doc_ann, param_ann = _compute_async_doc_param_anns(sym, graph)
    class_size_ann, depth_from_entry_ann = _compute_structure_anns(sym, graph)
    recursive_label = _compute_recursion_label(sym, graph)
    return {
        "blast": blast_ann,
        "age": age_ann,
        "callee": callee_ann,
        "depth": depth_ann,
        "async_": async_ann,
        "doc": doc_ann,
        "param": param_ann,
        "class_size": class_size_ann,
        "depth_entry": depth_from_entry_ann,
        "recursive": recursive_label,
    }


# ---------------------------------------------------------------------------
# _build_symbol_block_lines helper: depth-0 sub-line builders
# ---------------------------------------------------------------------------

def _build_seed_identity_lines(
    sym: "Symbol",
    graph: "Tempo",
    recursive_label: str,
    indent: str,
) -> list[str]:
    """Build also-in (S61), implements (S83), and recursion sub-lines."""
    lines: list[str] = []
    # S61: warn when same symbol name exists in other files
    _dupes = [s for s in graph.find_symbol(sym.name) if s.id != sym.id and not _is_test_file(s.file_path)]
    if _dupes:
        _dupe_strs = [f"{s.file_path.rsplit('/', 1)[-1]}:{s.line_start}" for s in _dupes[:3]]
        lines.append(f"{indent}  also in: {', '.join(_dupe_strs)}")
    # S83: show parent classes/interfaces via INHERITS edges
    if sym.kind.value in ("class", "interface", "struct"):
        _parent_ids = [
            e.target_id for e in graph.edges
            if e.kind == EdgeKind.INHERITS and e.source_id == sym.id
        ]
        _parents = [graph.symbols[pid].name for pid in _parent_ids if pid in graph.symbols]
        if not _parents:
            _parents = [
                e.target_id for e in graph.edges
                if e.kind == EdgeKind.INHERITS and e.source_id == sym.id
                and "::" not in e.target_id
            ]
        if _parents:
            lines.append(f"{indent}  implements: {', '.join(_parents[:4])}")
    if recursive_label:
        lines.append(f"{indent}  {recursive_label}")
    return lines


def _build_seed_test_lines(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build test-coverage and caller-coverage sub-lines for a depth-0 fn/method."""
    lines: list[str] = []
    if sym.kind.value not in ("function", "method"):
        return lines
    _all_callers = graph.callers_of(sym.id)
    _test_callers = [c for c in _all_callers if _is_test_file(c.file_path)]
    if _test_callers:
        _t_files = sorted({c.file_path.rsplit("/", 1)[-1] for c in _test_callers})
        lines.append(f"{indent}  tested: {', '.join(_t_files[:3])}")
        # S81: show test scenario names
        _scenario_names = sorted({
            c.name for c in _test_callers
            if c.name.startswith("test_") and c.kind.value in ("function", "method", "test")
        })
        if _scenario_names:
            _sc_str = ", ".join(_scenario_names[:3])
            if len(_scenario_names) > 3:
                _sc_str += f" +{len(_scenario_names) - 3} more"
            lines.append(f"{indent}  scenarios: {_sc_str}")
    elif sym.exported:
        lines.append(f"{indent}  no tests — exported but never called from a test file")
    # S85: caller coverage fraction
    _all_src_callers = [
        c for c in _all_callers
        if not _is_test_file(c.file_path) and c.file_path != sym.file_path
    ]
    if len(_all_src_callers) >= 3:
        _tested_callers = [
            c for c in _all_src_callers
            if any(_is_test_file(t.file_path) for t in graph.callers_of(c.id))
        ]
        _cc_pct = int(len(_tested_callers) / len(_all_src_callers) * 100)
        lines.append(
            f"{indent}  caller coverage: {len(_tested_callers)}/{len(_all_src_callers)} callers tested ({_cc_pct}%)"
        )
    return lines


def _build_seed_name_test_lines(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build supplementary test coverage via name-pattern + import detection (S47).

    Finds coverage that may not appear in the call graph:
    - Test functions named test_<seed_name>[_*] in any test file (naming convention)
    - Test files that import the seed's file (import-level coupling)

    Only emits when NET-NEW coverage is found (not already shown by caller-based
    _build_seed_test_lines). No negative signal — absent = unknown.
    Skips symbols that ARE test functions.
    """
    if _is_test_file(sym.file_path):
        return []
    if sym.kind.value not in ("function", "method", "class"):
        return []

    seed_name = sym.name.lower()
    prefix = f"test_{seed_name}"

    # Path 1: name-matching — find test_<seed_name>[_*] functions in test files.
    # Uses a lazily-built sorted index + bisect for O(log N) lookup instead of
    # an O(N) full-symbol scan (7k+ symbols).
    name_matches: dict[str, list[str]] = {}  # basename -> [func_names]
    _idx = _get_test_index(graph)
    _i = bisect.bisect_left(_idx, (prefix,))
    while _i < len(_idx):
        _lower, _s = _idx[_i]
        if _lower == prefix or _lower.startswith(prefix + "_"):
            name_matches.setdefault(_s.file_path.rsplit("/", 1)[-1], []).append(_s.name)
            _i += 1
        else:
            break

    # Path 2: import-based — test files that import the seed's source file
    import_basenames: set[str] = {
        fp.rsplit("/", 1)[-1]
        for fp in graph.importers_of(sym.file_path)
        if _is_test_file(fp)
    }

    # Files already shown by caller-based _build_seed_test_lines — skip duplicates
    caller_basenames: set[str] = {
        c.file_path.rsplit("/", 1)[-1]
        for c in graph.callers_of(sym.id)
        if _is_test_file(c.file_path)
    }

    # Merge: name-matched files + import-only files not already in name_matches
    all_new: dict[str, list[str]] = {
        bn: fns for bn, fns in name_matches.items()
        if bn not in caller_basenames
    }
    for bn in import_basenames:
        if bn not in all_new and bn not in caller_basenames:
            all_new[bn] = []  # import-based, no specific function names

    if not all_new:
        return []

    # Build compact output: cap at 2 files, 3 function names each
    parts: list[str] = []
    for bn, fns in sorted(all_new.items())[:2]:
        if fns:
            fn_str = ", ".join(sorted(fns)[:3])
            if len(fns) > 3:
                fn_str += f" +{len(fns) - 3} more"
            parts.append(f"{bn} ({fn_str})")
        else:
            parts.append(bn)

    line = f"{indent}  tests found: {'; '.join(parts)}"
    if len(all_new) > 2:
        line += f" +{len(all_new) - 2} more"

    return [line]


def _build_seed_method_ctx_lines(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build container and hot-siblings sub-lines for a depth-0 symbol."""
    lines: list[str] = []
    # Container annotation for methods
    if sym.kind.value == "method" and "::" in sym.id:
        _name_part = sym.id.split("::", 1)[1]
        if "." in _name_part:
            _class_id = sym.id.rsplit(".", 1)[0]
            _class_sym = graph.symbols.get(_class_id)
            if _class_sym:
                _c_callers = len(graph.callers_of(_class_id))
                _c_methods = len([c for c in graph.children_of(_class_id) if c.kind.value == "method"])
                _c_ann = f"{_c_callers} callers" if _c_callers else "no callers"
                lines.append(f"{indent}  container: {_class_sym.kind.value} {_class_sym.name} ({_c_ann}, {_c_methods} methods)")
    # S81: sibling hot annotation
    if sym.kind.value in ("function", "method") and sym.file_path in graph.files:
        _file_sids = graph.files[sym.file_path].symbols
        _hot_siblings: list[tuple[int, str]] = []
        for _fsid in _file_sids:
            if _fsid == sym.id or _fsid not in graph.symbols:
                continue
            _fs = graph.symbols[_fsid]
            if _fs.kind.value not in ("function", "method") or _is_test_file(_fs.file_path):
                continue
            _fs_cross = len({c.file_path for c in graph.callers_of(_fsid) if c.file_path != _fs.file_path})
            if _fs_cross >= 3:
                _hot_siblings.append((_fs_cross, _fs.name))
        _hot_siblings.sort(reverse=True)
        if len(_hot_siblings) >= 2:
            _hs_strs = [f"{name} ({n})" for n, name in _hot_siblings[:3]]
            lines.append(f"{indent}  also hot: {', '.join(_hs_strs)}")
    return lines


def _build_seed_git_ctx_lines(
    sym: "Symbol",
    graph: "Tempo",
    staleness_cache: dict,
    indent: str,
) -> list[str]:
    """Build recent-commits, callee-drift, and co-change sub-lines for a depth-0 symbol."""
    lines: list[str] = []
    if not graph.root:
        return lines
    # Recent commit messages
    try:
        from ..git import recent_file_commits as _rfc  # noqa: PLC0415
        _commits = _rfc(graph.root, sym.file_path, n=2)
        if _commits:
            _commit_parts = [f"{c['days_ago']}d \"{c['message']}\"" for c in _commits]
            lines.append(f"{indent}  recent: {', '.join(_commit_parts)}")
    except Exception:
        pass
    # Callee drift: seed is old but calls recently changed deps
    try:
        from ..git import file_last_modified_days as _fld_cd    # noqa: PLC0415
        # C15: use pre-fetched file-level age from staleness_cache (avoids git log -L subprocess)
        _seed_days = staleness_cache.get(sym.file_path) if staleness_cache else None
        if _seed_days is not None and _seed_days >= 30:
            _drifted: list[tuple[int, str]] = []
            for _c in graph.callees_of(sym.id)[:15]:
                if _c.file_path == sym.file_path:
                    continue
                if _c.file_path not in staleness_cache:
                    staleness_cache[_c.file_path] = _fld_cd(graph.root, _c.file_path)
                _c_days = staleness_cache[_c.file_path]
                if _c_days is not None and _c_days < 14:
                    _drifted.append((_c_days, _c.name))
            if _drifted:
                _drifted.sort()
                _drift_strs = [f"{n} ({d}d)" for d, n in _drifted[:3]]
                _drift_overflow = f" +{len(_drifted) - 3} more" if len(_drifted) > 3 else ""
                lines.append(
                    f"{indent}  ⚠ callee drift: {len(_drifted)} dep(s) changed after your last edit"
                    f" — {', '.join(_drift_strs)}{_drift_overflow}"
                )
    except Exception:
        pass
    # Co-change buddy
    try:
        from ..git import cochange_pairs as _ccp  # noqa: PLC0415
        _buddies = _ccp(graph.root, sym.file_path, n=1, min_count=4)
        if _buddies:
            _buddy = _buddies[0]
            _buddy_fp = _buddy["path"]
            if _buddy_fp in graph.files and not _is_test_file(_buddy_fp):
                _buddy_name = _buddy_fp.rsplit("/", 1)[-1]
                lines.append(
                    f"{indent}  co-changes with: {_buddy_name} ({_buddy['count']}x)"
                )
    except Exception:
        pass
    return lines


def _build_seed_todo_lines(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Scan the seed function's source for inline TODO/FIXME/HACK/BUG comments."""
    lines: list[str] = []
    if sym.kind.value not in ("function", "method") or not graph.root:
        return lines
    try:
        import os as _os, re as _re  # noqa: PLC0415
        _full_path = _os.path.join(graph.root, sym.file_path)
        if _os.path.isfile(_full_path):
            with open(_full_path, encoding="utf-8", errors="replace") as _fh:
                _src_lines = _fh.readlines()
            _todo_pat = _re.compile(
                r"#.*\b(TODO|FIXME|HACK|XXX|BUG)\b[:\s]*(.*)", _re.IGNORECASE
            )
            _hits: list[tuple[int, str, str]] = []
            for _li in range(sym.line_start - 1, min(sym.line_end, len(_src_lines))):
                _m = _todo_pat.search(_src_lines[_li])
                if _m:
                    _tag = _m.group(1).upper()
                    _note = _m.group(2).strip()[:80]
                    _hits.append((_li + 1, _tag, _note))
            for _lineno, _tag, _note in _hits[:3]:
                _suffix = f': "{_note}"' if _note else ""
                lines.append(f"{indent}  {_tag.lower()}: L{_lineno}{_suffix}")
    except Exception:
        pass
    return lines


def _build_seed_effects_lines(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Detect side effects and raise statements in the seed function body."""
    lines: list[str] = []
    if sym.kind.value not in ("function", "method") or not graph.root:
        return lines
    try:
        import os as _os2, re as _re2  # noqa: PLC0415
        _fp2 = _os2.path.join(graph.root, sym.file_path)
        if _os2.path.isfile(_fp2):
            with open(_fp2, encoding="utf-8", errors="replace") as _fh2:
                _body = "".join(_fh2.readlines()[sym.line_start - 1:sym.line_end])
            _effects: list[str] = []
            if _re2.search(r'(execute|cursor|session[.]query|db[.]|[.]save[(]|[.]commit[(]|SELECT|INSERT|UPDATE|DELETE)', _body, _re2.IGNORECASE):
                _effects.append("db")
            if _re2.search(r'(open[(]|write[(]|read[(]|os[.]path|shutil[.]|pathlib|json[.]dump|json[.]load|yaml[.])', _body):
                _effects.append("file")
            if _re2.search(r'(requests[.]|httpx[.]|aiohttp[.]|urllib[.]|fetch[(]|http[.]|socket[.]|grpc[.])', _body):
                _effects.append("network")
            if _re2.search(r'(subprocess[.]|os[.]system[(]|os[.]popen[(]|Popen[(])', _body):
                _effects.append("subprocess")
            if _re2.search(r'self[.]\w+\s*=(?!=)', _body) or _re2.search(r'\bglobal\b', _body):
                _effects.append("mutates state")
            if _effects:
                lines.append(f"{indent}  effects: {', '.join(_effects)}")
            # S85: throws detection
            _raise_pat = _re2.compile(r'\braise\s+([A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*)*)')
            _exc_names = list(dict.fromkeys(_raise_pat.findall(_body)))
            _exc_names = [e for e in _exc_names if e not in ("Exception", "BaseException")][:4]
            if _exc_names:
                lines.append(f"{indent}  throws: {', '.join(_exc_names)}")
    except Exception:
        pass
    return lines


def _build_seed_callee_chain_line(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build the callee-chain annotation line for a depth-0 fn/method."""
    lines: list[str] = []
    if sym.kind.value not in ("function", "method"):
        return lines
    _sym_callees = graph.callees_of(sym.id)
    if not _sym_callees:
        _sym_callees = graph.callees_of(sym.file_path)
    _file_callees = [
        c for c in _sym_callees
        if c.file_path != sym.file_path
    ]
    if 1 <= len(_file_callees) <= 4:
        _chain_parts = [sym.name, _file_callees[0].name]
        _c1 = _file_callees[0]
        _c1_callees = graph.callees_of(_c1.id) or graph.callees_of(_c1.file_path)
        _c1_callees = [c for c in _c1_callees if c.file_path != _c1.file_path]
        if _c1_callees:
            _chain_parts.append(_c1_callees[0].name)
        lines.append(f"{indent}  callee chain: {' → '.join(_chain_parts)}")
    return lines


def _build_seed_apex_line(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """BFS upward through callers to find the nearest apex symbol (S45).

    An apex is a symbol with zero non-test callers — it sits at the top of a
    call chain. Distance to apex tells agents how deeply embedded this symbol is:

      'apex: main [1 hop]'   → one level below the CLI entry, change carefully
      'apex: self [entry]'   → this IS an entry point (nothing non-test calls it)
      'apex: app.run [3 hops]' → 3 levels deep, safer to refactor
      'apex: 5+ hops'        → very deeply buried, safest to change

    Only fires for functions/methods at depth=0 (the queried symbol).
    Suppressed when all callers are test files (no non-test chain to find).
    Cost: BFS capped at depth=5 with global visited-node limit of 150.
    """
    if sym.kind.value not in ("function", "method"):
        return []

    non_test_callers = [c for c in graph.callers_of(sym.id) if not _is_test_file(c.file_path)]

    if not non_test_callers:
        # No non-test callers — either a true entry point or only called from tests
        all_callers = graph.callers_of(sym.id)
        if not all_callers:
            # Truly uncalled (dead or CLI entry) — call it an entry point
            return [f"{indent}  apex: self [entry]"]
        # Only called from tests — suppress (no interesting chain to show)
        return []

    # BFS upward through non-test callers to find the nearest apex
    from collections import deque  # noqa: PLC0415
    MAX_DEPTH = 5
    MAX_FAN = 8      # fan-out limit per node (avoids O(n²) on hub symbols)
    MAX_VISITED = 150  # global cap to bound total work

    seen: set[str] = {sym.id}
    # Each queue entry: (sym_id, depth, symbol_name)
    q: deque[tuple[str, int, str]] = deque()
    for c in non_test_callers[:MAX_FAN]:
        if c.id not in seen:
            seen.add(c.id)
            q.append((c.id, 1, c.name))

    best: tuple[int, str] | None = None  # (depth, apex_name)

    while q and len(seen) < MAX_VISITED:
        curr_id, depth, curr_name = q.popleft()
        curr_sym = graph.symbols.get(curr_id)
        if curr_sym is None:
            continue

        curr_callers = [c for c in graph.callers_of(curr_id) if not _is_test_file(c.file_path)]

        if not curr_callers:
            # curr_sym has no non-test callers → it's an apex
            if best is None or depth < best[0]:
                best = (depth, curr_name)
            continue  # don't expand past an apex

        if depth >= MAX_DEPTH:
            continue  # too deep; stop expanding

        for c in curr_callers[:MAX_FAN]:
            if c.id not in seen:
                seen.add(c.id)
                q.append((c.id, depth + 1, c.name))

    if best is None:
        return [f"{indent}  apex: {MAX_DEPTH}+ hops"]

    hop_word = "hop" if best[0] == 1 else "hops"
    return [f"{indent}  apex: {best[1]} [{best[0]} {hop_word}]"]


def _build_fan_out_line(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """S48: Fan-out risk indicator for depth-0 seeds.

    Counts outgoing calls from sym that cross file boundaries (call targets in a
    different file). Classifies by number of unique target files:
      - HIGH  ≥ 8 unique target files
      - MEDIUM 4-7 unique target files
      - LOW/NONE < 4 unique target files → suppressed (not worth the noise)

    Helps agents see cascade-prone refactor targets before they start.
    Skipped for test files and test functions.
    """
    # Only functions/methods at depth=0
    if sym.kind.value not in ("function", "method"):
        return []
    # Skip test files and test functions (they call everything by design)
    if _is_test_file(sym.file_path):
        return []
    if sym.name.startswith("test_"):
        return []

    # Count cross-file outgoing calls via the indexed callees API
    cross_file_callees = [
        c for c in graph.callees_of(sym.id)
        if c.file_path != sym.file_path
    ]
    if not cross_file_callees:
        return []

    target_files = {c.file_path for c in cross_file_callees}
    unique_module_count = len(target_files)
    total_calls = len(cross_file_callees)

    if unique_module_count < 4:
        return []  # LOW — not worth mentioning

    level = "HIGH" if unique_module_count >= 8 else "MEDIUM"
    mod_word = "module" if unique_module_count == 1 else "modules"
    return [f"{indent}  fan-out: {level} ({total_calls} calls to {unique_module_count} {mod_word})"]


# ---------------------------------------------------------------------------
# _build_symbol_block_lines helper: structural section builders
# ---------------------------------------------------------------------------

def _build_siblings_block(
    sym: "Symbol",
    depth: int,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build the 'in this file: N others' sibling summary line."""
    lines: list[str] = []
    if depth != 0:
        return lines
    _siblings = [
        s for s in graph.symbols_in_file(sym.file_path)
        if s.id != sym.id
        and s.kind.value in ("class", "function", "method", "interface", "module")
        and s.parent_id is None
    ]
    if len(_siblings) >= 2:
        _sib_sorted = sorted(
            _siblings,
            key=lambda s: (0 if s.kind.value in ("class", "interface", "module") else 1, s.name)
        )
        _kind_abbr = {"function": "fn", "method": "fn", "class": "cls",
                      "interface": "iface", "module": "mod"}
        _sib_parts = [
            f"{_kind_abbr.get(s.kind.value, s.kind.value)} {s.name}" for s in _sib_sorted[:3]
        ]
        _sib_overflow = len(_siblings) - 3
        _sib_str = ", ".join(_sib_parts)
        if _sib_overflow > 0:
            _sib_str += f" +{_sib_overflow} more"
        lines.append(f"{indent}  in this file: {len(_siblings)} others ({_sib_str})")
    return lines


def _build_warnings_block(
    sym: "Symbol",
    depth: int,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build all warning annotations (LARGE, HIGH COMPLEXITY, POSSIBLY DEAD, etc.)."""
    if depth > 1:
        return []
    lines: list[str] = []
    warnings: list[str] = []
    if sym.line_count > 500:
        warnings.append(f"LARGE ({sym.line_count} lines — use grep, don't read)")
    if sym.complexity > 50:
        _file_fns = [
            s for s in graph.symbols.values()
            if s.file_path == sym.file_path
            and s.kind.value in ("function", "method")
            and s.complexity > 0
        ]
        _cx_rel = ""
        if len(_file_fns) >= 3:
            _avg_cx = sum(s.complexity for s in _file_fns) / len(_file_fns)
            if _avg_cx > 0:
                _ratio = sym.complexity / _avg_cx
                if _ratio >= 1.5:
                    _cx_rel = f" ({_ratio:.1f}x file avg)"
        warnings.append(f"HIGH COMPLEXITY (cx={sym.complexity}{_cx_rel})")
    if depth == 0 and not graph.callers_of(sym.id):
        _name_lower = sym.name.lower()
        _entry_patterns = ("handle_", "on_", "run", "start", "main", "execute", "dispatch",
                           "route", "command", "hook", "middleware", "plugin", "setup", "teardown")
        _is_entry = any(_name_lower.startswith(p) or _name_lower == p for p in _entry_patterns)
        if _is_entry:
            lines.append(f"{indent}  [likely entry point — wired externally, not dead]")
        elif not sym.exported and _dead_code_confidence(sym, graph) >= 40:
            warnings.append("POSSIBLY DEAD — 0 callers, not exported (run dead_code mode to confirm)")
    if depth == 0:
        _callers = graph.callers_of(sym.id)
        if len(_callers) >= 2 and all(_is_test_file(c.file_path) for c in _callers):
            warnings.append("TEST-ONLY CALLERS — not called from production code")
    if depth == 0 and graph.root:
        _cycles = graph.detect_circular_imports()
        for _cycle in _cycles:
            if sym.file_path in _cycle:
                _names = [fp.rsplit("/", 1)[-1] for fp in _cycle]
                warnings.append(f"CIRCULAR IMPORT — {' → '.join(_names)}")
                break
    if warnings:
        lines.append(f"{indent}  ⚠ {', '.join(warnings)}")
    return lines


def _build_callers_block(
    sym: "Symbol",
    depth: int,
    graph: "Tempo",
    query_tokens: list[str],
    staleness_cache: dict,
    callsite_lines: "dict[tuple[str,str], list[int]] | None",
    indent: str,
) -> list[str]:
    """Build the 'called by:' section."""
    lines: list[str] = []
    callers = graph.callers_of(sym.id)
    if not callers:
        return lines
    try:
        from ..git import file_last_modified_days as _fld  # noqa: PLC0415
    except Exception:
        _fld = None
    callers_for_display, shown_callers, shown_count, total_for_overflow = _callers_sort_filter(
        callers, query_tokens, graph, depth
    )
    ghost_ids = _callers_detect_ghosts(callers_for_display, graph, depth)
    caller_strs = _callers_format_list(
        shown_callers, sym, callsite_lines, ghost_ids, _fld, staleness_cache, graph
    )
    if caller_strs:
        lines.append(f"{indent}  called by: {', '.join(caller_strs)}")
        if total_for_overflow > shown_count:
            lines[-1] += f" (+{total_for_overflow - shown_count} more)"
        _callers_ghost_summary(lines, shown_callers, ghost_ids, indent)
        _callers_domain_diversity(lines, callers_for_display, depth, indent)
        _callers_primary_concentration(lines, callers_for_display, sym, depth, indent)
        _callers_volatility_signal(lines, callers_for_display, graph, depth, indent)
        _callers_upstream_reach(lines, sym, callers_for_display, graph, depth, indent)
    return lines


def _callers_sort_filter(
    callers: list,
    query_tokens: list[str],
    graph: "Tempo",
    depth: int,
) -> "tuple[list, list, int, int]":
    """Sort/filter callers; returns (callers_for_display, shown_callers, shown_count, total)."""
    def _is_kw(c: "Symbol") -> bool:
        return bool(query_tokens and any(tok in c.file_path.lower() for tok in query_tokens))
    callers_for_display = [c for c in callers if not _is_test_file(c.file_path)]
    callers_sorted = sorted(callers_for_display, key=lambda c: 0 if _is_kw(c) else 1)
    kw_callers = [c for c in callers_sorted if _is_kw(c)]
    other_callers = [c for c in callers_sorted if not _is_kw(c)]
    hot_other = [c for c in other_callers if c.file_path in graph.hot_files]
    cold_other = [c for c in other_callers if c.file_path not in graph.hot_files]
    max_other = 3 if kw_callers else (8 if depth == 0 else 5)
    # Cap kw_callers to prevent hub symbols with 100+ keyword-matching callers
    # from producing unreadable called_by lines. Freed budget used for more BFS symbols.
    # Bench: -3.4% tokens across 20 queries; -10% for render/* queries at cap.
    kw_callers = kw_callers[:8]
    shown_callers = kw_callers + (hot_other + cold_other)[:max_other]
    shown_count = len(kw_callers) + max_other
    return callers_for_display, shown_callers, shown_count, len(callers_for_display)


def _callers_detect_ghosts(
    callers_for_display: list,
    graph: "Tempo",
    depth: int,
) -> set:
    """Detect callers that are themselves unreachable (not exported, no callers); depth=0 only."""
    ghost_ids: set = set()
    if depth == 0:
        for gc in callers_for_display[:30]:  # cap at 30 to avoid O(n) graph walks on hub symbols
            if not gc.exported and not graph.callers_of(gc.id):
                ghost_ids.add(gc.id)
    return ghost_ids


def _callers_stale_ann(
    file_path: str,
    fld: object,
    staleness_cache: dict,
    graph_root: str,
) -> str:
    """Return staleness annotation string for a caller's file."""
    if fld is None:
        return ""
    if file_path not in staleness_cache:
        staleness_cache[file_path] = fld(graph_root, file_path)  # type: ignore[operator]
    days = staleness_cache[file_path]
    if days is None or days <= 30:
        return ""
    return " [stale: 6m+]" if days > 180 else f" [stale: {days}d]"


def _callers_format_list(
    shown_callers: list,
    sym: "Symbol",
    callsite_lines: "dict[tuple[str,str], list[int]] | None",
    ghost_ids: set,
    fld: object,
    staleness_cache: dict,
    graph: "Tempo",
) -> list[str]:
    """Format display strings for each shown caller with line/hot/stale/dead annotations."""
    caller_strs = []
    for c in shown_callers:
        cl = (callsite_lines or {}).get((c.id, sym.id), [])
        if len(cl) == 1:
            line_ann = f" [line {cl[0]}]"
        elif len(cl) >= 2:
            line_ann = f" [lines {cl[0]}, {cl[1]}]"
        else:
            line_ann = ""
        if c.file_path in graph.hot_files:
            caller_strs.append(f"{c.qualified_name}{line_ann} [hot]")
        else:
            dead_ann = " [dead?]" if c.id in ghost_ids else ""
            caller_strs.append(
                c.qualified_name + line_ann
                + _callers_stale_ann(c.file_path, fld, staleness_cache, graph.root)
                + dead_ann
            )
    return caller_strs


def _callers_ghost_summary(
    lines: list[str],
    shown_callers: list,
    ghost_ids: set,
    indent: str,
) -> None:
    """Append ghost caller summary line if unreachable callers were shown."""
    shown_ghost_count = sum(1 for c in shown_callers if c.id in ghost_ids)
    if shown_ghost_count:
        live_shown = len(shown_callers) - shown_ghost_count
        lines.append(
            f"{indent}    ↳ {shown_ghost_count} caller(s) are themselves unreachable"
            f" — effective reach: {live_shown} of {len(shown_callers)} shown"
        )


def _callers_domain_diversity(
    lines: list[str],
    callers_for_display: list,
    depth: int,
    indent: str,
) -> None:
    """S50: Cross-cutting signal — flags 3+ distinct caller subsystems at depth=0."""
    if depth != 0:
        return
    domains: set = set()
    for dc in callers_for_display:
        d = _caller_domain(dc.file_path)
        if d:
            domains.add(d)
    if len(domains) >= 3:
        sorted_domains = sorted(domains)[:4]
        domain_str = ", ".join(sorted_domains)
        n = len(domains)
        lines.append(
            f"{indent}    ↳ cross-cutting: {n} subsystem{'s' if n != 1 else ''}"
            f" ({domain_str}{'...' if n > 4 else ''})"
        )


def _callers_primary_concentration(
    lines: list[str],
    callers_for_display: list,
    sym: "Symbol",
    depth: int,
    indent: str,
) -> None:
    """S57: Primary caller concentration — one file ≥60% of callers at depth=0 (min 4)."""
    if depth != 0 or len(callers_for_display) < 4:
        return
    file_counts: dict = {}
    for pc in callers_for_display:
        file_counts[pc.file_path] = file_counts.get(pc.file_path, 0) + 1
    total_callers = len(callers_for_display)
    dom_file, dom_count = max(file_counts.items(), key=lambda x: x[1])
    if dom_count / total_callers >= 0.6 and dom_file != sym.file_path:
        dom_basename = dom_file.rsplit("/", 1)[-1]
        lines.append(
            f"{indent}    ↳ primary caller: {dom_basename}"
            f" ({dom_count}/{total_callers})"
        )


def _callers_volatility_signal(
    lines: list[str],
    callers_for_display: list,
    graph: "Tempo",
    depth: int,
    indent: str,
) -> None:
    """S59: Caller volatility — ≥2 non-test callers in hot_files at depth=0."""
    if depth != 0 or not graph.hot_files:
        return
    hot_callers = [c for c in callers_for_display if c.file_path in graph.hot_files]
    if len(hot_callers) >= 2:
        cv_names = [c.name for c in hot_callers[:3]]
        cv_suffix = "..." if len(hot_callers) > 3 else ""
        lines.append(
            f"{indent}    \u21b3 caller volatility: {len(hot_callers)} active callers"
            f" ({', '.join(cv_names)}{cv_suffix})"
        )


def _callers_upstream_reach(
    lines: list[str],
    sym: "Symbol",
    callers_for_display: list,
    graph: "Tempo",
    depth: int,
    indent: str,
) -> None:
    """S61: Upstream transitive reach — BFS depth=4, fires when amplification ≥4x and upstream ≥20."""
    if depth != 0 or len(callers_for_display) > 8:
        return
    direct_count = len(callers_for_display)
    upstream_visited: set = {sym.id}
    upstream_frontier = [sym.id]
    upstream_capped = False
    for _ in range(4):  # max depth 4 hops
        next_frontier: list = []
        for uid in upstream_frontier:
            for uc in graph.callers_of(uid):
                if uc.id not in upstream_visited and not _is_test_file(uc.file_path):
                    upstream_visited.add(uc.id)
                    next_frontier.append(uc.id)
                    if len(upstream_visited) >= 201:
                        upstream_capped = True
                        break
            if upstream_capped:
                break
        upstream_frontier = next_frontier
        if not upstream_frontier or upstream_capped:
            break
    upstream_count = len(upstream_visited) - 1  # exclude seed itself
    if upstream_count >= 20 and upstream_count >= direct_count * 4:
        cap_str = "+" if upstream_capped else ""
        lines.append(
            f"{indent}    \u21b3 upstream reach: {upstream_count}{cap_str} nodes"
            f" — {direct_count} direct caller{'s' if direct_count != 1 else ''}"
            f" amplif{'y' if direct_count != 1 else 'ies'} to wider blast"
        )


def _callees_format_list(
    ordered_callees: list,
    sym: "Symbol",
    depth: int,
    graph: "Tempo",
    seed_is_tested: bool,
) -> tuple[list[str], list]:
    """Format display strings for each callee and collect sole-use callees (S49/S51/S54/S55)."""
    callee_strs = []
    sole_use_callees: list = []
    for c in ordered_callees:
        _hot_ann = " [hot]" if c.file_path in graph.hot_files else ""
        _cx_ann = ""
        _cb_ann = ""
        _sole_ann = ""
        _recursive_ann = ""
        _untested_ann = ""
        if depth == 0:
            if c.complexity > 15 and c.kind.value in ("function", "method"):
                _cx_ann = f" (cx={c.complexity})"
            _cb_files = len({cr.file_path for cr in graph.callers_of(c.id) if cr.file_path != c.file_path})
            if _cb_files >= 3:
                _cb_ann = f" [blast: {_cb_files}]"
            if c.kind.value in ("function", "method"):
                _prod_callers = [cr for cr in graph.callers_of(c.id) if not _is_test_file(cr.file_path)]
                if len(_prod_callers) == 1 and _prod_callers[0].id == sym.id:
                    _sole_ann = " [sole-use]"
                    sole_use_callees.append(c)
            if c.id == sym.id:
                _recursive_ann = " [recursive]"
            if (
                seed_is_tested
                and c.kind.value in ("function", "method")
                and not _is_test_file(c.file_path)
                and not any(_is_test_file(cr.file_path) for cr in graph.callers_of(c.id))
            ):
                _untested_ann = " [untested]"
        callee_strs.append(f"{c.qualified_name}{_cx_ann}{_hot_ann}{_cb_ann}{_sole_ann}{_recursive_ann}{_untested_ann}")
    return callee_strs, sole_use_callees


def _callees_overflow_enrich(
    lines: list[str],
    callees: list,
    shown: int,
    depth: int,
    sym: "Symbol",
    seed_is_tested: bool,
    graph: "Tempo",
    hot_callees: list,
    cold_callees: list,
    indent: str,
) -> None:
    """Enrich the overflow count with hidden-callee attribute summary (S68)."""
    if len(callees) <= shown:
        return
    if depth == 0:
        _hidden_cs = (hot_callees + cold_callees)[shown:]
        _h_hot = [c for c in _hidden_cs if c.file_path in graph.hot_files and not _is_test_file(c.file_path)]
        _h_sole: list = []
        _h_unt: list = []
        for _hc68 in _hidden_cs:
            if _hc68.kind.value in ("function", "method"):
                _hc68_prod = [cr for cr in graph.callers_of(_hc68.id) if not _is_test_file(cr.file_path)]
                if len(_hc68_prod) == 1 and _hc68_prod[0].id == sym.id:
                    _h_sole.append(_hc68)
                if (
                    seed_is_tested
                    and not _is_test_file(_hc68.file_path)
                    and not any(_is_test_file(cr.file_path) for cr in graph.callers_of(_hc68.id))
                ):
                    _h_unt.append(_hc68)
        _h_parts: list[str] = []
        if _h_hot:
            _h_parts.append(f"{len(_h_hot)} hot")
        if _h_sole:
            _h_parts.append(f"{len(_h_sole)} sole-use")
        if _h_unt:
            _h_parts.append(f"{len(_h_unt)} untested")
        _overflow68 = f" (+{len(_hidden_cs)} more"
        if _h_parts:
            _overflow68 += f": {', '.join(_h_parts)}"
        _overflow68 += ")"
        lines[-1] += _overflow68
    else:
        lines[-1] += f" (+{len(callees) - shown} more)"


def _callees_instability_lines(
    sym: "Symbol",
    depth: int,
    callees: list,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """S52: hot callee instability warning."""
    if not (depth == 0 and graph.hot_files):
        return []
    _hot_non_test = [c for c in callees if c.file_path in graph.hot_files and not _is_test_file(c.file_path)]
    if len(_hot_non_test) < 2:
        return []
    _names = [c.name for c in _hot_non_test[:3]]
    _suffix = "..." if len(_hot_non_test) > 3 else ""
    return [f"{indent}  \u21b3 instability: {len(_hot_non_test)} hot callees ({', '.join(_names)}{_suffix})"]


def _callees_drift_lines(
    sym: "Symbol",
    depth: int,
    callees: list,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """S62: contract drift — seed is stable but its callees have been updated."""
    if not (depth == 0 and graph.hot_files and sym.file_path not in graph.hot_files):
        return []
    _drift_callees = [
        c for c in callees
        if c.file_path in graph.hot_files
        and c.file_path != sym.file_path
        and not _is_test_file(c.file_path)
        and c.kind.value in ("function", "method")
    ]
    if len(_drift_callees) < 3:
        return []
    _drift_names = [c.name for c in _drift_callees[:3]]
    _drift_suffix = "..." if len(_drift_callees) > 3 else ""
    return [
        f"{indent}  \u21b3 drift risk: {len(_drift_callees)} callees updated while seed is stable"
        f" ({', '.join(_drift_names)}{_drift_suffix}) \u2014 verify contracts still match"
    ]


def _callees_coverage_lines(
    sym: "Symbol",
    depth: int,
    callees: list,
    seed_is_tested: bool,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """S56: coverage gap summary — seed tested but callees are not."""
    if not (depth == 0 and seed_is_tested):
        return []
    _eligible = [c for c in callees if c.kind.value in ("function", "method") and not _is_test_file(c.file_path)]
    _untested_cov = [
        c for c in _eligible
        if not any(_is_test_file(cr.file_path) for cr in graph.callers_of(c.id))
    ]
    if len(_untested_cov) < 2:
        return []
    _cov_names = [c.name for c in _untested_cov[:3]]
    _cov_suffix = "..." if len(_untested_cov) > 3 else ""
    return [
        f"{indent}  \u21b3 coverage gap: {len(_untested_cov)}/{len(_eligible)} callees untested"
        f" ({', '.join(_cov_names)}{_cov_suffix})"
    ]


def _callees_orphan_lines(
    sym: "Symbol",
    depth: int,
    callees: list,
    sole_use_callees: list,
    ordered_callees: list,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """S58: orphan cascade — sole-use callees that own sole-use sub-callees."""
    if not (depth == 0 and sole_use_callees):
        return []
    # Extend with non-displayed callees that are also sole-use
    _shown_ids = {c.id for c in ordered_callees}
    for _c in callees:
        if _c.id in _shown_ids or _c.kind.value not in ("function", "method"):
            continue
        _pc = [cr for cr in graph.callers_of(_c.id) if not _is_test_file(cr.file_path)]
        if len(_pc) == 1 and _pc[0].id == sym.id:
            sole_use_callees.append(_c)
    if not sole_use_callees:
        return []
    _transitive_sole: list = []
    for _sc in sole_use_callees:
        for _sub in graph.callees_of(_sc.id):
            if _sub.kind.value in ("function", "method") and not _is_test_file(_sub.file_path):
                _sub_pc = [cr for cr in graph.callers_of(_sub.id) if not _is_test_file(cr.file_path)]
                if len(_sub_pc) == 1 and _sub_pc[0].id == _sc.id:
                    _transitive_sole.append((_sc.name, _sub.name))
    if len(_transitive_sole) < 2:
        return []
    _total_chain = len(sole_use_callees) + len(_transitive_sole)
    _hub_names = list(dict.fromkeys(n for n, _ in _transitive_sole))[:3]
    _hub_suffix = "..." if len(_hub_names) < len(dict.fromkeys(n for n, _ in _transitive_sole)) else ""
    return [
        f"{indent}  \u21b3 orphan cascade: {_total_chain} private helpers in chain"
        f" (via {', '.join(_hub_names)}{_hub_suffix}) \u2014 refactor ripples deeper than visible callees"
    ]


def _callees_cochange_lines(
    sym: "Symbol",
    depth: int,
    callees: list,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """S60: callee co-change coupling from git history."""
    if not (depth == 0 and graph.root):
        return []
    try:
        from ..git import cochange_matrix as _ccm
        _matrix = _ccm(graph.root)
        if not _matrix:
            return []
        _callee_files = list(dict.fromkeys(
            c.file_path for c in callees
            if c.file_path != sym.file_path and not _is_test_file(c.file_path)
        ))
        _coupled: list[tuple[str, str, float]] = []
        for _i, _fa in enumerate(_callee_files):
            _partners = {fp: freq for fp, freq in _matrix.get(_fa, [])}
            for _fb in _callee_files[_i + 1:]:
                if _fb in _partners and _partners[_fb] >= 0.2:
                    _coupled.append((_fa, _fb, _partners[_fb]))
        if not _coupled:
            return []
        _coupled.sort(key=lambda x: -x[2])
        if len(_coupled) == 1:
            _fa0, _fb0, _ = _coupled[0]
            return [
                f"{indent}  \u21b3 callee coupling: {Path(_fa0).name} \u2194 {Path(_fb0).name}"
                f" \u2014 often change together, check both"
            ]
        if len(_coupled) == 2:
            _fa0, _fb0, _ = _coupled[0]
            _fa1, _fb1, _ = _coupled[1]
            return [
                f"{indent}  \u21b3 callee coupling: {Path(_fa0).name} \u2194 {Path(_fb0).name}"
                f", {Path(_fa1).name} \u2194 {Path(_fb1).name}"
            ]
        _fa0, _fb0, _ = _coupled[0]
        return [
            f"{indent}  \u21b3 callee coupling: {len(_coupled)} coupled pairs"
            f" ({Path(_fa0).name} \u2194 {Path(_fb0).name} strongest)"
        ]
    except Exception:
        return []


def _build_callees_block(
    sym: "Symbol",
    depth: int,
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build the 'calls:' section."""
    lines: list[str] = []
    callees = graph.callees_of(sym.id)
    if not callees:
        return lines
    shown = 8 if depth == 0 else 5
    hot_callees = [c for c in callees if c.file_path in graph.hot_files]
    cold_callees = [c for c in callees if c.file_path not in graph.hot_files]
    ordered_callees = (hot_callees + cold_callees)[:shown]
    _seed_is_tested = depth == 0 and any(
        _is_test_file(cr.file_path) for cr in graph.callers_of(sym.id)
    )
    callee_strs, sole_use_callees = _callees_format_list(
        ordered_callees, sym, depth, graph, _seed_is_tested
    )
    lines.append(f"{indent}  calls: {', '.join(callee_strs)}")
    _callees_overflow_enrich(lines, callees, shown, depth, sym, _seed_is_tested, graph, hot_callees, cold_callees, indent)
    if depth == 0 and any(c.id == sym.id for c in callees):
        lines.append(f"{indent}  \u21b3 recursive \u2014 self-referential; verify base case before modifying")
    lines.extend(_callees_instability_lines(sym, depth, callees, graph, indent))
    lines.extend(_callees_drift_lines(sym, depth, callees, graph, indent))
    lines.extend(_callees_coverage_lines(sym, depth, callees, _seed_is_tested, graph, indent))
    lines.extend(_callees_orphan_lines(sym, depth, callees, sole_use_callees, ordered_callees, graph, indent))
    lines.extend(_callees_cochange_lines(sym, depth, callees, graph, indent))
    return lines


def _build_children_block(
    sym: "Symbol",
    graph: "Tempo",
    indent: str,
) -> list[str]:
    """Build contains, implementors, and similar sections for a depth-0 symbol."""
    lines: list[str] = []
    children = graph.children_of(sym.id)
    if children:
        _child_strs = []
        for c in children[:10]:
            _c_callers = len(graph.callers_of(c.id))
            _c_ann = f" ({_c_callers})" if _c_callers >= 1 else ""
            _child_strs.append(f"{c.kind.value[:4]} {c.name}{_c_ann}")
        lines.append(f"{indent}  contains: {', '.join(_child_strs)}")
    if sym.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE):
        _subtypes = graph.subtypes_of(sym.id)
        if _subtypes:
            _sub_strs = [
                f"{s.qualified_name} ({s.file_path.rsplit('/', 1)[-1]}:{s.line_start})"
                for s in _subtypes[:8]
            ]
            _overflow_sub = len(_subtypes) - 8
            _sub_line = f"{indent}  implementors: {', '.join(_sub_strs)}"
            if _overflow_sub > 0:
                _sub_line += f" (+{_overflow_sub} more)"
            lines.append(_sub_line)
    if sym.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
        _seed_callees = {
            cid for cid in graph._callees.get(sym.id, [])
            if cid in graph.symbols and graph.symbols[cid].kind.value not in ("class", "type_alias", "enum")
        }
        if len(_seed_callees) >= 2:
            _overlap: dict[str, int] = {}
            for _callee_id in _seed_callees:
                for _sibling_id in graph._callers.get(_callee_id, []):
                    if _sibling_id != sym.id:
                        _sib = graph.symbols.get(_sibling_id)
                        if _sib and _sib.kind.value in ("function", "method"):
                            _overlap[_sibling_id] = _overlap.get(_sibling_id, 0) + 1
            _similar = [
                (cnt, graph.symbols[sid])
                for sid, cnt in _overlap.items()
                if cnt >= 2 and sid in graph.symbols
            ]
            if _similar:
                _similar.sort(key=lambda x: -x[0])
                _sim_strs = [
                    f"{s.qualified_name} ({s.file_path.rsplit('/', 1)[-1]}:{s.line_start}, {n} shared)"
                    for n, s in _similar[:4]
                ]
                lines.append(f"{indent}  similar: {', '.join(_sim_strs)}")
    return lines


def _build_symbol_block_lines(
    sym: "Symbol",
    depth: int,
    orbit_note: str,
    graph: "Tempo",
    query_tokens: list[str],
    staleness_cache: dict,
    callsite_lines: "dict[tuple[str,str], list[int]] | None" = None,
) -> list[str]:
    """Render one BFS symbol into display lines (header + annotations).

    Returns a list of lines; caller joins them and checks token overflow."""
    indent = "  " * depth if depth > 0 else ""
    prefix = ["●", "  →", "    ·", "      "][min(depth, 3)]
    loc = f"{sym.file_path}:{sym.line_start}-{sym.line_end}"
    if depth == 0:
        anns = _compute_seed_annotations(sym, graph, staleness_cache)
        ann_str = (
            anns["blast"] + anns["age"] + anns["callee"] + anns["depth"]
            + anns["async_"] + anns["doc"] + anns["param"]
            + anns["depth_entry"] + anns["class_size"]
        )
        recursive_label = anns["recursive"]
    else:
        ann_str = ""
        recursive_label = ""
        if depth >= 1:
            _hub_files = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}
            if len(_hub_files) >= 15:
                ann_str = f" [hub: {len(_hub_files)} files]"
    block_lines = [f"{prefix} {sym.kind.value} {sym.qualified_name}{ann_str} — {loc}{orbit_note}"]
    if depth == 0:
        block_lines += _build_seed_identity_lines(sym, graph, recursive_label, indent)
        block_lines += _build_seed_test_lines(sym, graph, indent)
        block_lines += _build_seed_name_test_lines(sym, graph, indent)  # S47: name+import coverage
        block_lines += _build_seed_method_ctx_lines(sym, graph, indent)
        block_lines += _build_seed_git_ctx_lines(sym, graph, staleness_cache, indent)
        block_lines += _build_seed_todo_lines(sym, graph, indent)
        block_lines += _build_seed_effects_lines(sym, graph, indent)
        block_lines += _build_seed_callee_chain_line(sym, graph, indent)
        block_lines += _build_seed_apex_line(sym, graph, indent)
        block_lines += _build_fan_out_line(sym, graph, indent)
    if sym.signature and depth < 2:
        block_lines.append(f"{indent}  sig: {sym.signature[:150]}")
    if sym.doc and depth == 0:
        block_lines.append(f"{indent}  doc: {sym.doc}")
    block_lines += _build_siblings_block(sym, depth, graph, indent)
    block_lines += _build_warnings_block(sym, depth, graph, indent)
    block_lines += _build_callers_block(sym, depth, graph, query_tokens, staleness_cache, callsite_lines, indent)
    block_lines += _build_callees_block(sym, depth, graph, indent)
    if depth == 0:
        block_lines += _build_children_block(sym, graph, indent)
    return block_lines



_PAIR_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("start", "stop"),
    ("open", "close"),
    ("create", "destroy"),
    ("create", "delete"),
    ("acquire", "release"),
    ("begin", "end"),
    ("connect", "disconnect"),
    ("lock", "unlock"),
    ("push", "pop"),
    ("encode", "decode"),
    ("serialize", "deserialize"),
    ("encrypt", "decrypt"),
    ("load", "unload"),
    ("register", "unregister"),
    ("enable", "disable"),
    ("add", "remove"),
    ("enter", "exit"),
    ("subscribe", "unsubscribe"),
    ("activate", "deactivate"),
    ("attach", "detach"),
    ("bind", "unbind"),
    ("mount", "unmount"),
    ("watch", "unwatch"),
    ("run", "stop"),
)


def _compute_paired_functions(
    graph: "Tempo",
    seeds: "list[Symbol]",
    seen_ids: "set[str]",
) -> str:
    """S69: Paired function detection — complement operations absent from BFS.

    Functions come in semantic pairs: start/stop, open/close, acquire/release.
    When you focus on one half, the complement is invisible unless BFS happened
    to include it. This fires only when the complement EXISTS but is NOT in BFS —
    the exact gap where agents forget the cleanup or symmetric counterpart.

    Prefers same-class match (for methods), then same-file, then any file.
    Only triggers for function/method symbols. Silent when both sides are already
    in BFS (no noise if the pair is already visible).
    """
    if not seeds:
        return ""

    # Step 1: Compute target swap names from seed names.
    # Most seeds have no antonym words (94% of fn/method symbols) → early exit
    # avoids the full corpus scan for typical focus queries.
    per_seed_swaps: dict[str, list[str]] = {}
    target_names: set[str] = set()
    for seed in seeds:
        parts = seed.name.lower().split("_")
        swaps: list[str] = []
        seen_complements: set[str] = set()
        for a, b in _PAIR_ANTONYMS:
            if a in parts:
                new_parts = list(parts)
                new_parts[parts.index(a)] = b
                swap = "_".join(new_parts)
            elif b in parts:
                new_parts = list(parts)
                new_parts[parts.index(b)] = a
                swap = "_".join(new_parts)
            else:
                continue
            if swap == seed.name.lower() or swap in seen_complements:
                continue
            seen_complements.add(swap)
            swaps.append(swap)
            target_names.add(swap)
        per_seed_swaps[seed.id] = swaps

    if not target_names:
        return ""

    # Step 2: Targeted corpus scan — only collect fn/method symbols whose name
    # is in target_names. O(1) set check filters 94% of symbols before kind check.
    by_name: dict[str, list] = {}
    for sym in graph.symbols.values():
        n = sym.name.lower()
        if n not in target_names:
            continue
        if sym.kind.value not in ("function", "method"):
            continue
        by_name.setdefault(n, []).append(sym)

    found_pairs: list[tuple] = []
    for seed in seeds:
        for swap in per_seed_swaps.get(seed.id, []):
            candidates = [c for c in by_name.get(swap, []) if c.id not in seen_ids]
            if not candidates:
                continue

            same_class = [c for c in candidates if seed.parent_id and c.parent_id == seed.parent_id]
            same_file = [c for c in candidates if c.file_path == seed.file_path]
            best = (same_class or same_file or candidates)[0]
            found_pairs.append((seed, best))

    if not found_pairs:
        return ""

    seen_comp_ids: set[str] = set()
    parts_out: list[str] = []
    for _seed, comp in found_pairs:
        if comp.id in seen_comp_ids:
            continue
        seen_comp_ids.add(comp.id)
        comp_file = comp.file_path.rsplit("/", 1)[-1]
        short_id = comp.id.rsplit("::", 1)[-1] if "::" in comp.id else comp.name
        parts_out.append(f"{short_id} ({comp_file}:L{comp.line_start})")

    if not parts_out:
        return ""

    label = "paired op" if len(parts_out) == 1 else "paired ops"
    return f"↳ {label}: {', '.join(parts_out)} — not in BFS"


def _compute_bfs_module_diversity(
    graph: "Tempo",
    seeds: "list[Symbol]",
    ordered: "list[tuple[Symbol, int]]",
) -> str:
    """S70: BFS module diversity — detect when BFS spans 3+ distinct top-level modules.

    BFS follows both callers and callees. When the traversal crosses into multiple
    top-level directories (e.g., bench/, tempo/, tempograph/), the function has
    cross-layer scope: a change here ripples through modules that may have different
    owners, release cycles, or test suites.

    Different from 'cross-cutting' (which counts caller subsystems — incoming blast).
    This measures outgoing + incoming BFS breadth across the whole traversal.

    Fires only when ≥3 distinct non-test modules appear, excluding the seed's own module.
    Silent for single-module codebases and shallow BFS results.
    """
    root = getattr(graph, "root", "") or ""
    root_norm = root.replace("\\", "/").rstrip("/")

    def _module_of(file_path: str) -> str:
        fp = file_path.replace("\\", "/")
        if root_norm and fp.startswith(root_norm + "/"):
            rel = fp[len(root_norm) + 1:]
        else:
            # Fallback: use last 2+ components
            rel = fp
        parts = rel.split("/")
        return parts[0] if parts else ""

    seed_modules = {_module_of(s.file_path) for s in seeds}

    bfs_modules: dict[str, int] = {}
    for sym, _depth in ordered:
        mod = _module_of(sym.file_path)
        # Skip test directories and seed's own modules
        if not mod or mod.lower() in ("tests", "test", "spec") or mod in seed_modules:
            continue
        bfs_modules[mod] = bfs_modules.get(mod, 0) + 1

    if len(bfs_modules) < 3:
        return ""

    # Show top modules by symbol count (most represented first)
    sorted_mods = sorted(bfs_modules, key=lambda m: -bfs_modules[m])
    shown = sorted_mods[:4]
    overflow = len(sorted_mods) - len(shown)
    mod_str = ", ".join(shown)
    if overflow:
        mod_str += f" +{overflow} more"

    n = len(bfs_modules)
    return (
        f"↳ cross-module BFS: {n} modules in call graph ({mod_str})"
        f" — change scope spans layers"
    )


def _compute_change_exposure(graph: "Tempo", seeds: "list[Symbol]") -> str:
    """One-line change risk synthesizer.

    Aggregates across seeds and returns a summary line (MEDIUM/HIGH/CRITICAL)
    when the neighborhood has meaningful risk factors. Returns empty string for
    low-risk queries so the line is absent rather than reassuring noise.

    Factors (each counts once):
    - caller_files >= 8: high blast, many dependents
    - hot_callees >= 2: actively-changing territory downstream
    - hot_callers >= 3: actively-changing territory upstream
    - coverage gap: >=50% of cross-file callees are untested (when >=3 callees)
    - seed in hot file: the symbol itself is in an actively-modified file
    """
    if not seeds or not graph:
        return ""

    caller_files: set[str] = set()
    hot_callees_n = 0
    hot_callers_n = 0
    cross_callees_total = 0
    untested_cross_callees = 0
    hot = graph.hot_files or set()

    for sym in seeds:
        sym_file = sym.file_path

        # Callers (non-test only)
        callers = [c for c in graph.callers_of(sym.id) if not _is_test_file(c.file_path)]
        for c in callers:
            if c.file_path != sym_file:
                caller_files.add(c.file_path)
        if hot:
            hot_callers_n += sum(1 for c in callers if c.file_path in hot and c.file_path != sym_file)

        # Callees (cross-file, non-test)
        callees = [c for c in graph.callees_of(sym.id)
                   if c.file_path != sym_file and not _is_test_file(c.file_path)]
        cross_callees_total += len(callees)
        if hot:
            hot_callees_n += sum(1 for c in callees if c.file_path in hot)
        for callee in callees:
            if not any(_is_test_file(t.file_path) for t in graph.callers_of(callee.id)):
                untested_cross_callees += 1

    seed_in_hot = bool(hot and any(s.file_path in hot for s in seeds))

    factors: list[str] = []
    caller_file_count = len(caller_files)
    if caller_file_count >= 8:
        factors.append(f"{caller_file_count} caller files")
    if hot_callees_n >= 2:
        factors.append(f"{hot_callees_n} hot callee{'s' if hot_callees_n != 1 else ''}")
    if hot_callers_n >= 3:
        factors.append(f"{hot_callers_n} hot callers")
    if cross_callees_total >= 3 and untested_cross_callees / cross_callees_total >= 0.5:
        factors.append(f"coverage gap ({untested_cross_callees}/{cross_callees_total} callees untested)")
    if seed_in_hot:
        factors.append("seed in active file")

    n = len(factors)
    if n == 0:
        return ""
    level = "CRITICAL" if n >= 3 else ("HIGH" if n >= 2 else "MEDIUM")
    return f"change exposure: {level}  ← {', '.join(factors)}"


def _collect_multi_seeds(
    graph: Tempo, query: str,
) -> tuple[list[Symbol], set[str], list[str], list[str]] | None:
    """Collect seeds for a (possibly multi-symbol) query.

    Returns (seeds, seed_files, query_tokens, parts) or None if no matches.
    When None, caller should use _suggest_alternatives.
    """
    _parts = [p.strip() for p in query.split("|") if p.strip()] if "|" in query else [query]
    if len(_parts) > 1:
        _seen_seed_ids: set[str] = set()
        seeds: list[Symbol] = []
        seed_files: set[str] = set()
        query_tokens: list[str] = []
        for _part in _parts:
            _s, _sf, _qt = _collect_seeds(graph, _part)
            for _sym in _s:
                if _sym.id not in _seen_seed_ids:
                    seeds.append(_sym)
                    _seen_seed_ids.add(_sym.id)
            seed_files |= _sf
            query_tokens.extend(t for t in _qt if t not in query_tokens)
        if not seeds:
            return None
    else:
        seeds, seed_files, query_tokens = _collect_seeds(graph, query)
        if not seeds:
            return None
    return seeds, seed_files, query_tokens, _parts


def _adaptive_bfs_depth(graph: Tempo, seeds: list[Symbol], hot_seeds: bool) -> int:
    """Choose initial BFS depth based on seed connectivity profile.

    - Wide hub (≥15 unique cross-file caller files): depth=2 — BFS budget is
      exhausted by depth-1/2 callers anyway; avoid pointless depth-3 expansion.
    - Deep chain (≥6 callees, ≤3 cross-file caller files): depth=4 or 5 to
      trace long callee chains that are the core of the query's relevance.
    - Default: 4 if hot files, else 3 (preserves prior behaviour).
    """
    caller_files: set[str] = set()
    callee_count = 0
    seed_fps = {s.file_path for s in seeds}
    for seed in seeds:
        for cid in graph._callers.get(seed.id, []):
            if cid in graph.symbols and graph.symbols[cid].file_path not in seed_fps:
                caller_files.add(graph.symbols[cid].file_path)
        callee_count += len(graph._callees.get(seed.id, []))

    n_caller_files = len(caller_files)
    if n_caller_files >= 15:
        return 2  # Hub: BFS floods at depth 1-2 regardless; cap early
    if callee_count >= 6 and n_caller_files <= 3:
        return 5 if hot_seeds else 4  # Deep chain: trace callee tree further
    return 4 if hot_seeds else 3


def _run_bfs_with_orbit(
    graph: Tempo, seeds: list[Symbol], seed_files: set[str],
    query_tokens: list[str],
) -> tuple[list[tuple[Symbol, int]], set[str], dict[str, tuple[str, float]], bool]:
    """Run orbit-seeded BFS with adaptive depth expansion.

    Returns (ordered, seen_ids, orbit_seed_meta, depth_extended).
    """
    orbit_seed_meta: dict[str, tuple[str, float]] = {}
    orbit_secondary: list[Symbol] = []
    if graph.root:
        _primary_fps = [s.file_path for s in seeds]
        _primary_fp_set = {s.file_path for s in seeds}
        _orbit_pairs = _cochange_orbit(graph.root, _primary_fps, _primary_fp_set, n=3)
        for sym, freq in _find_orbit_seeds(graph, query_tokens, _orbit_pairs):
            orbit_secondary.append(sym)
            orbit_seed_meta[sym.id] = (sym.file_path, freq)

    _hot_seeds = any(s.file_path in graph.hot_files for s in seeds)
    _initial_depth = _adaptive_bfs_depth(graph, seeds, _hot_seeds)
    ordered, seen_ids = _bfs_expand(
        graph, seeds, seed_files, secondary_seeds=orbit_secondary or None,
        max_depth=_initial_depth,
    )

    _depth_extended = False
    _SPARSE_THRESHOLD = 20
    _MAX_ADAPTIVE_DEPTH = 5
    if len(ordered) < _SPARSE_THRESHOLD and _initial_depth < _MAX_ADAPTIVE_DEPTH:
        _ext_depth = _initial_depth + 1
        _ext_ordered, _ext_seen = _bfs_expand(
            graph, seeds, seed_files, secondary_seeds=orbit_secondary or None,
            max_depth=_ext_depth,
        )
        if len(_ext_ordered) > len(ordered):
            ordered, seen_ids = _ext_ordered, _ext_seen
            _depth_extended = True

    # Post-BFS re-expansion for underrepresented seeds: if a seed is the only
    # BFS node in its file, it likely got starved by better-connected neighbors.
    # Give it +1 depth re-expansion so its local neighborhood appears.
    if len(seeds) > 1:
        _file_node_counts: dict[str, int] = {}
        for node, _d in ordered:
            fp = node.file_path
            _file_node_counts[fp] = _file_node_counts.get(fp, 0) + 1

        _underrep = [
            s for s in seeds
            if _file_node_counts.get(s.file_path, 0) <= 1
        ]
        if _underrep and len(ordered) < 50 - 3:
            _reexp_depth = _initial_depth + 1
            if _depth_extended:
                _reexp_depth = _initial_depth + 2
            for s in _underrep[:3]:
                _extra, _extra_seen = _bfs_expand(
                    graph, [s], {s.file_path}, max_depth=_reexp_depth,
                )
                for node, depth in _extra:
                    if node.id not in seen_ids and len(ordered) < 50:
                        ordered.append((node, depth))
                        seen_ids.add(node.id)

    return ordered, seen_ids, orbit_seed_meta, _depth_extended


def _render_context_sections(
    graph: Tempo, *, lines: list[str], ordered: list[tuple[Symbol, int]],
    seen_files: set[str], seen_ids: set[str], token_count: int, max_tokens: int,
    _seed_syms: list[Symbol], _callsite_lines: dict[tuple[str, str], list[int]],
) -> int:
    """Render all context sections (file context, monolith, related, blast, orbit, etc.).

    Returns updated token_count.
    """
    ctx_block, ctx_tokens = _render_file_context_section(graph, seen_files, seen_ids, token_count, max_tokens)
    if ctx_block:
        lines.append(ctx_block)
        token_count += ctx_tokens

    mono_block, mono_tokens = _render_monolith_section(graph, ordered, token_count, max_tokens)
    if mono_block:
        lines.append(mono_block)
        token_count += mono_tokens

    if token_count < max_tokens - 150:
        related_section = _render_related_files_section(graph, ordered, seen_files)
        if related_section:
            lines.append(related_section)
            token_count += count_tokens(related_section)

    blast_section = _render_blast_risk_section(graph, ordered, token_count, max_tokens)
    if blast_section:
        lines.append(blast_section)
        token_count += count_tokens(blast_section)

    seed_file_paths = [s.file_path for s, d in ordered if d == 0]
    orbit_section = _render_cochange_orbit_section(graph, seed_file_paths, seen_files, token_count, max_tokens)
    if orbit_section:
        lines.append(orbit_section)
        token_count += count_tokens(orbit_section)

    volatile_section = _render_volatility_section(graph, seed_file_paths, token_count, max_tokens)
    if volatile_section:
        lines.append(volatile_section)
        token_count += count_tokens(volatile_section)

    if token_count < max_tokens - 100:
        recent_section = _render_recent_changes_section(graph, seed_file_paths)
        if recent_section:
            lines.append(recent_section)
            token_count += count_tokens(recent_section)

    if token_count < max_tokens - 100:
        cochange_section = _render_cochange_section(graph, seed_file_paths)
        if cochange_section:
            lines.append(cochange_section)
            token_count += count_tokens(cochange_section)

    if token_count < max_tokens - 100:
        cohort_section = _render_cochange_cohort_section(graph, seed_file_paths, seen_files)
        if cohort_section:
            lines.append(cohort_section)
            token_count += count_tokens(cohort_section)

    _tcov_lines = _signals_focused_test_coverage(
        graph, ordered=ordered, token_count=token_count, max_tokens=max_tokens,
    )
    lines.extend(_tcov_lines)
    token_count += sum(count_tokens(l) for l in _tcov_lines)

    callers_section = _render_all_callers_section(graph, _seed_syms, _callsite_lines, token_count, max_tokens)
    if callers_section:
        lines.append(callers_section)
        token_count += count_tokens(callers_section)

    dep_section = _render_dependency_files_section(graph, ordered, seen_files, token_count, max_tokens)
    if dep_section:
        lines.append(dep_section)
        token_count += count_tokens(dep_section)

    hot_section = _render_hot_callers_section(graph, _seed_syms, token_count, max_tokens)
    if hot_section:
        lines.append(hot_section)
        token_count += count_tokens(hot_section)

    return token_count


def _compute_bfs_scope_note(ordered: "list[tuple[Symbol, int]]") -> str:
    """S66: BFS scope signal — fires when BFS never reached depth=3.

    When a hub function's depth-1 and depth-2 neighbors fill all 50 BFS slots,
    depth=3 is completely excluded. The agent sees a truncated picture without
    knowing it. This tells them to use blast_radius for the full scope.

    Condition: total nodes = 50 AND zero depth-3 nodes collected.
    This means the BFS was so dense at depth-1/2 that it exhausted the cap
    before reaching depth=3 at all — a true hub truncation, not just a
    deep-graph cutoff.

    Suppressed for sparse neighborhoods (< 50 nodes, or depth=3 was reached)
    since those already got a complete or extended BFS picture.
    """
    total = len(ordered)
    if total < 50:
        return ""
    depth_3_count = sum(1 for _, d in ordered if d == 3)
    if depth_3_count > 0:
        return ""
    depth_1_count = sum(1 for _, d in ordered if d == 1)
    return (
        f"↳ hub BFS: {depth_1_count} depth-1 neighbors"
        f" — depth=3 cut (50-node cap); use blast_radius for full scope"
    )


def _compute_hot_cluster_note(graph: "Tempo", ordered: "list[tuple[Symbol, int]]") -> str:
    """S1029: Hot cluster at BFS depth 1 — fires when ≥2 depth-1 BFS neighbors are in
    actively-modified (hot) files.

    The BFS sort key (S1029) already promotes hot-file symbols within each depth tier.
    This synthesis note tells agents WHY the top depth-1 symbols appeared first — it
    makes the implicit ordering explicit and identifies the hot cluster by file.

    Condition: ≥2 distinct depth-1 symbols whose file_path is in graph.hot_files.
    Excludes depth-0 (seed) and depth-2+ (noise at those levels).
    Silent when hot_files is empty (non-git repo or no recent activity).
    """
    if not graph.hot_files:
        return ""
    depth1_hot = [sym for sym, d in ordered if d == 1 and sym.file_path in graph.hot_files]
    if len(depth1_hot) < 2:
        return ""
    # Collect unique hot files in display order (parent/file.py format to avoid basename collisions)
    seen_fps: dict[str, str] = {}
    for sym in depth1_hot:
        if sym.file_path not in seen_fps:
            parts = sym.file_path.rsplit("/", 2)
            seen_fps[sym.file_path] = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    display_names = list(seen_fps.values())
    shown = display_names[:3]
    suffix = f" +{len(display_names) - 3} more" if len(display_names) > 3 else ""
    files_str = ", ".join(shown) + suffix
    n = len(depth1_hot)
    return (
        f"↳ hot cluster at depth 1: {n} neighbor{'s' if n != 1 else ''}"
        f" in actively-modified files ({files_str}) — prioritized in BFS below"
    )


_WALL_ENTRY_NAMES: frozenset[str] = frozenset({
    "main", "run_server", "run", "app", "start", "serve", "cli",
    "create_app", "entrypoint", "entry_point",
})


def _compute_depth_wall_lookahead(
    graph: "Tempo",
    ordered: "list[tuple[Symbol, int]]",
    seen_ids: "set[str]",
    seeds: "list[Symbol]",
) -> str:
    """S1031: Depth wall lookahead — fires when entry-point or hot-file callers of the seed
    were cut by BFS caller_limit and are invisible to the agent.

    BFS adds at most 12 callers of the seed (depth-0 caller_limit). If the seed has >12 callers,
    the remaining ones are silently dropped. Agents don't know what's in the invisible portion.
    If any dropped callers are entry points (main, run_server, etc.) or exported symbols in
    hot files (actively worked files NOT already in BFS), this signal fires to say:
    "N callers of the seed were not shown; some are entry points."

    This is NOT the same as S66 (hub truncation on 50-node cap) or S65 (change exposure):
    - S66: fires when the 50-node cap cuts depth=3 entirely
    - S65: quantifies change exposure via callers + import fan-in
    - S1031: fires specifically when ENTRY POINTS or HOT callers were cut at the seed level

    Conditions:
    - Seed has >12 callers total (the BFS caller_limit at depth=0) — otherwise all callers were shown.
    - Among the unseen callers: ≥1 is an entry point (name in _WALL_ENTRY_NAMES or __main__.py)
      OR ≥1 is an exported top-level symbol in a hot file not already represented in the BFS.
    - Test callers are excluded (high noise, expected to not be shown).
    - Seed itself must not be an entry point (trivially true if it's already main/run).
    """
    if not seeds:
        return ""
    seed_sym = seeds[0]
    # Skip if seed is itself an entry point — the "missing callers" are expected to be sparse
    if seed_sym.name in _WALL_ENTRY_NAMES:
        return ""

    all_callers = graph.callers_of(seed_sym.id)
    # If all callers fit within the BFS limit, nothing was cut
    if len(all_callers) <= 12:
        return ""

    seen_files = {sym.file_path for sym, _ in ordered}
    critical: list[tuple[str, str]] = []  # (name, reason)
    seen_names: set[str] = set()

    for caller in all_callers:
        if caller.id in seen_ids:
            continue  # Already in BFS — agent can see it
        if _is_test_file(caller.file_path):
            continue
        if caller.parent_id:
            continue  # Skip nested methods — we want top-level callers
        if caller.name in seen_names:
            continue
        reason = ""
        if caller.name in _WALL_ENTRY_NAMES:
            reason = "entry point"
        elif caller.file_path.rsplit("/", 1)[-1] == "__main__.py":
            reason = "entry point"
        elif (graph.hot_files and caller.file_path in graph.hot_files
              and caller.exported and caller.file_path not in seen_files):
            reason = "hot file"
        if reason:
            critical.append((caller.name, reason))
            seen_names.add(caller.name)

    if not critical:
        return ""

    # Sort: entry points first, then hot-file
    entry_pts = [(n, r) for n, r in critical if r == "entry point"]
    hot_pts = [(n, r) for n, r in critical if r == "hot file"]
    ordered_crit = entry_pts[:2] + hot_pts[:1]
    ordered_crit = ordered_crit[:3]

    # Count non-test callers that weren't shown (the interesting hidden count)
    non_test_hidden = sum(
        1 for c in all_callers
        if c.id not in seen_ids and not _is_test_file(c.file_path) and not c.parent_id
    )

    parts = [f"{n} ({r})" for n, r in ordered_crit]
    suffix = f" +{len(critical) - len(ordered_crit)} more" if len(critical) > len(ordered_crit) else ""
    syms_str = ", ".join(parts) + suffix
    hidden_str = f" — {non_test_hidden} non-test callers hidden" if non_test_hidden > len(critical) else ""
    return (
        f"↳ depth wall: {syms_str} cut by BFS caller limit{hidden_str}; use blast_radius for full scope"
    )


def _get_naming_stem(name: str) -> str:
    """Extract a meaningful naming stem from a symbol name for clustering.

    Rules:
    - Dunder methods (e.g. __init__) → empty (too generic)
    - Strip leading _ to find first word, then reattach prefix
    - Minimum first-word length: 4 chars
    - Returns e.g. "_compute" for "_compute_bfs_scope_note"
                       "render" for "render_focused"
                       "_signals" for "_signals_diff_pre_a"
    """
    if name.startswith("__") and name.endswith("__"):
        return ""
    prefix = "_" if name.startswith("_") else ""
    clean = name.lstrip("_")
    parts = [p for p in clean.split("_") if p]
    if not parts or len(parts[0]) < 4:
        return ""
    return prefix + parts[0]


def _compute_bfs_naming_clusters(
    seeds: "list[Symbol]",
    ordered: "list[tuple[Symbol, int]]",
) -> str:
    """S1032: BFS naming cluster — fires when ≥3 depth-1 BFS neighbors share a naming stem.

    Groups depth-1 BFS symbols by their first meaningful underscore-word component.
    When 3+ neighbors share a stem (e.g., "_compute_*", "_signals_*", "render_*"),
    reveals the structural family the seed is embedded in.

    Example outputs:
      ↳ naming cluster at depth 1: 7 helpers share "_compute_" stem — likely sub-computation family
      ↳ naming cluster at depth 1: 5 callees share "_signals_" stem — likely signal-family dispatch

    Different from:
    - S70 (BFS module diversity): groups by top-level MODULE path, not symbol name
    - S1029 (hot cluster): groups by hot_files (temporal), not naming pattern
    - S57 (primary caller concentration): caller FILE concentration, not name clustering

    Conditions:
    - ≥3 distinct depth-1 symbols (by name) share a stem of ≥4 chars
    - Seed itself excluded from the count (depth 0)
    - Test-file symbols excluded (test_ stems are trivially clustered and add no signal)
    """
    seed_names = {s.name for s in seeds}
    # Collect depth-1 symbols, unique by name, excluding seeds and test files
    seen_names: set[str] = set()
    depth1_syms: list["Symbol"] = []
    for sym, d in ordered:
        if d != 1:
            continue
        if sym.name in seed_names:
            continue
        if _is_test_file(sym.file_path):
            continue
        if sym.name not in seen_names:
            seen_names.add(sym.name)
            depth1_syms.append(sym)

    if len(depth1_syms) < 3:
        return ""

    from collections import Counter as _Counter
    stem_counter: _Counter[str] = _Counter()
    stem_to_examples: dict[str, list[str]] = {}
    for sym in depth1_syms:
        stem = _get_naming_stem(sym.name)
        if not stem:
            continue
        stem_counter[stem] += 1
        stem_to_examples.setdefault(stem, []).append(sym.name)

    if not stem_counter:
        return ""

    # Find most dominant cluster (largest count)
    top_stem, top_count = stem_counter.most_common(1)[0]
    if top_count < 3:
        return ""

    examples = stem_to_examples[top_stem][:3]
    examples_str = ", ".join(examples)
    overflow = f" +{len(stem_to_examples[top_stem]) - 3} more" if len(stem_to_examples[top_stem]) > 3 else ""
    return (
        f"↳ naming cluster at depth 1: {top_count} neighbors share \"{top_stem}_\" stem"
        f" — likely {top_stem.lstrip('_')}-family helpers ({examples_str}{overflow})"
    )


def _compute_variant_group(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1033: Variant group — fires when a seed is part of an A/B/C alphabetical or numeric series.

    Functions like `_signals_hotspots_core_a/b/c/d` or `render_diff_1/2` are parallelism variants:
    same-base names with a trailing single letter or digit suffix. BFS won't show them unless they
    have direct call edges, but an agent fixing a bug in `_foo_b` almost always needs to check `_foo_a`.

    Detection: seed name matches `^(.+)_([a-z]|[0-9]+)$` where the suffix is a SINGLE letter (a-z)
    or a sequence of digits, and the base has ≥ 5 chars. Same-file symbols matching the same pattern
    with the same base are "variant group members."

    Example outputs:
      ↳ variant group: _signals_hotspots_core_a, _c, _d — A/B series, check all 4 on edits
      ↳ variant group: render_diff_1 (+1 more) — numeric series, check all 3 on edits

    Distinct from S1032 (naming clusters): that fires when DEPTH-1 BFS neighbors share a stem.
    This fires when the SEED ITSELF belongs to a named series — a totally different (and orthogonal)
    signal. You can have both: seed is in a variant series AND its neighbors form a cluster.

    Conditions:
    - Seed name ends in `_[a-z]` (single letter) or `_[0-9]+` (digits)
    - Base component has ≥ 5 chars (avoids trivial short names)
    - ≥ 1 other same-file symbol shares the same base + variant suffix pattern
    - Test-file seeds excluded (too noisy, `test_foo_a` is just pytest parameterization)
    """
    import re as _re
    _VARIANT_RE = _re.compile(r"^(.+)_([a-z]|[0-9]+)$")

    for seed in seeds:
        if _is_test_file(seed.file_path):
            continue
        m = _VARIANT_RE.match(seed.name)
        if not m:
            continue
        base, suffix = m.group(1), m.group(2)
        # Base must be substantial — avoids matching short generic names like `_foo_a` where base=`_foo`
        if len(base.lstrip("_")) < 5:
            continue

        # Find same-file symbols matching same base + any variant suffix
        fi = graph.files.get(seed.file_path)
        if not fi:
            continue

        variants: list[str] = []
        for sid in fi.symbols:
            if sid == seed.id or sid not in graph.symbols:
                continue
            other = graph.symbols[sid]
            if other.kind.value not in ("function", "method"):
                continue
            om = _VARIANT_RE.match(other.name)
            if not om:
                continue
            other_base, other_suffix = om.group(1), om.group(2)
            if other_base != base:
                continue
            # Both suffixes must be the same TYPE (both alpha or both numeric)
            if suffix.isdigit() != other_suffix.isdigit():
                continue
            variants.append(other.name)

        if not variants:
            continue

        # Determine variant type label
        variant_type = "numeric series" if suffix.isdigit() else "A/B series"
        total = len(variants) + 1  # seed + variants

        # Show up to 3 variant names (excluding seed)
        shown = variants[:3]
        overflow = f" (+{len(variants) - 3} more)" if len(variants) > 3 else ""
        shown_str = ", ".join(shown)
        return (
            f"↳ variant group: {shown_str}{overflow}"
            f" — {variant_type}, check all {total} on edits"
        )

    return ""


def _compute_cross_file_siblings(
    seeds: "list[Symbol]",
    graph: "Tempo",
    seen_ids: "set[str]",
) -> str:
    """S1034: Cross-file sibling family — fires when the seed belongs to a naming family
    that spans multiple files in the same directory.

    BFS shows callers and callees. It does NOT show parallel siblings — functions with the
    same role-prefix that live in sibling files. When you edit render_focused to add a new
    feature, you might also need to update render_dead_code and render_diff_context.
    Nothing in the BFS output tells you that.

    Example outputs:
      ↳ sibling family: 6 parallel render_* functions in tempograph/render/ — consider parallel changes in render_diff_context, render_dead_code, render_blast_radius (+3 more)
      ↳ sibling family: 3 parallel build_* functions in tempograph/ — consider parallel changes in build_indexes, build_file_map

    Distinct from:
    - S1032 (naming cluster): depth-1 BFS NEIGHBORS share a stem — what you SEE in BFS output
    - S1033 (variant group): same-FILE A/B/C/D suffix variants of the seed

    Conditions:
    - Seed not in a test file
    - Stem ≥ 5 bare chars (to filter out noisy short names like "get", "set", "run")
    - 3+ sibling symbols in same parent directory, different file, not in seen_ids
    - Siblings not in test files
    """
    import os as _os

    if not seeds:
        return ""
    seed = seeds[0]
    if _is_test_file(seed.file_path):
        return ""

    stem = _get_naming_stem(seed.name)
    if not stem or len(stem.lstrip("_")) < 5:
        return ""

    seed_dir = _os.path.dirname(_os.path.abspath(seed.file_path))
    seed_file = _os.path.abspath(seed.file_path)
    stem_prefix = f"{stem}_"

    sibling_names: list[str] = []
    seen_names: set[str] = set()
    for sym in graph.symbols.values():
        if sym.id in seen_ids:
            continue
        if not sym.name.startswith(stem_prefix):
            continue
        abs_path = _os.path.abspath(sym.file_path)
        if abs_path == seed_file:
            continue
        if _is_test_file(sym.file_path):
            continue
        if _os.path.dirname(abs_path) != seed_dir:
            continue
        if sym.name not in seen_names:
            seen_names.add(sym.name)
            sibling_names.append(sym.name)

    if len(sibling_names) < 3:
        return ""

    # Sort: public before private, then alphabetical
    sibling_names.sort(key=lambda n: (n.startswith("_"), n))
    examples = sibling_names[:3]
    overflow = f" (+{len(sibling_names) - 3} more)" if len(sibling_names) > 3 else ""
    examples_str = ", ".join(examples)

    # Display relative path: last 2 path components of seed_dir
    _parts = seed_dir.replace("\\", "/").split("/")
    _display_dir = "/".join(_parts[-2:]) if len(_parts) >= 2 else seed_dir

    return (
        f"↳ sibling family: {len(sibling_names)} parallel {stem}_* functions in {_display_dir}/ "
        f"(not in BFS) — parallel changes may be needed in {examples_str}{overflow}"
    )


def _compute_orchestrator_advisory(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1035: Orchestrator advisory — fires when seed has many cross-file callees but few callers.

    An orchestrator is a coordination function: it calls many things, but is called by few.
    When you focus on one, the depth-3 BFS expands mostly DOWNSTREAM — callee after callee
    after callee — and the agent sees lots of context that's 2-3 hops removed from their actual
    change. The advisory reframes what the BFS output means for this topology.

    Example outputs:
      ↳ orchestrator: 16 cross-file callees, 3 callers — BFS expands mostly downstream; focus on direct callees when making changes
      ↳ orchestrator: 10 cross-file callees, 2 callers — BFS expands mostly downstream; focus on direct callees when making changes

    Distinct from:
    - S65 (change_exposure): quantifies RISK (caller files, hot callees) — not topology role
    - S198 (leaf function): opposite topology (many callers, 0 callees) — already shows "stable leaf"
    - apex signal (S45): fires for entry points (0 callers) — already handled inline per-symbol
    - S180 (complex hub): fires when cx≥8 AND callers≥5 — hub, not orchestrator

    Conditions:
    - Seed is a function/method, not in test files
    - Cross-file callees >= 6 (enough downstream complexity)
    - Non-test callers 1–4 (few enough to be orchestrator, not utility/hub; 0 = entry point, apex handles)
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("function", "method"):
        return ""
    if _is_test_file(seed.file_path):
        return ""

    seed_file = seed.file_path

    # Count cross-file callees (non-test)
    cross_callees = [
        c for c in graph.callees_of(seed.id)
        if c.file_path != seed_file and not _is_test_file(c.file_path)
    ]
    if len(cross_callees) < 6:
        return ""

    # Count non-test callers
    nt_callers = [
        c for c in graph.callers_of(seed.id)
        if not _is_test_file(c.file_path)
    ]
    if len(nt_callers) < 1 or len(nt_callers) > 4:
        return ""

    callee_count = len(cross_callees)
    caller_count = len(nt_callers)
    return (
        f"↳ orchestrator: {callee_count} cross-file callees, {caller_count} caller{'s' if caller_count != 1 else ''}"
        f" — BFS expands mostly downstream; focus on direct callees when making changes"
    )


def _compute_relay_point(
    seeds: "list[Symbol]",
    graph: "Tempo",
    ordered: "list[tuple[Symbol, int]]",
) -> str:
    """S1036: Relay point — a depth-1 callee that dominates downstream reach.

    The seed calls several depth-1 functions. Each of those calls more functions downstream.
    If ONE depth-1 callee is responsible for ≥65% of the combined cross-file downstream reach
    of all depth-1 callees, that intermediate is a hidden chokepoint: changing it cascades
    through the majority of the seed's transitive call surface — even though BFS presents it
    as 'just one of several depth-1 callees.'

    Uses graph structure (not BFS output) for the reach calculation, so the 50-node BFS cap
    doesn't interfere. Only cross-file callees count (intra-file calls are implementation detail).

    This is NOT the same as S1035 (orchestrator advisory):
    - S1035: seed ITSELF has many callees, few callers — "you're an orchestrator"
    - S1036: one INTERMEDIATE depth-1 callee dominates downstream reach — "this one is load-bearing"

    Example outputs:
      ↳ relay point: render_diff_context accounts for 37/40 downstream reach (92%) — treat as load-bearing
      ↳ relay point: search_symbols_scored accounts for 19/20 downstream reach (95%) — treat as load-bearing

    Conditions:
    - Seed is a function/method, not in test files
    - Seed has ≥ 2 cross-file callees visible in BFS (otherwise no comparison possible)
    - Total combined cross-file reach (callees of depth-1 callees) ≥ 8 (meaningful scale)
    - Best relay's share ≥ 65% of total reach AND ≥ 5 callees in absolute count
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("function", "method"):
        return ""
    if "test" in seed.file_path:
        return ""

    # Use graph structure directly (not BFS output) to identify cross-file callees.
    # BFS can promote co-matched symbols to depth=0 seeds, so depth-based filtering
    # on ordered is unreliable. Instead, read callee relationships from the index.
    seed_callees_ids: set[str] = set(graph._callees.get(seed.id, []))
    seen_seed_ids: set[str] = {s.id for s in seeds}

    d1_callees: list["Symbol"] = [
        graph.symbols[c_id]
        for c_id in seed_callees_ids
        if c_id in graph.symbols
        and graph.symbols[c_id].file_path != seed.file_path
        and c_id not in seen_seed_ids   # exclude co-seeds (already at depth=0)
    ]

    if len(d1_callees) < 2:
        return ""

    # For each cross-file callee, compute its cross-file downstream reach (graph data, not BFS).
    reach_by_id: dict[str, int] = {}
    total_reach = 0
    for d1 in d1_callees:
        cross_callees = sum(
            1 for c_id in graph._callees.get(d1.id, [])
            if graph.symbols.get(c_id, d1).file_path != d1.file_path
        )
        reach_by_id[d1.id] = cross_callees
        total_reach += cross_callees

    if total_reach < 8:
        return ""

    best_relay = max(d1_callees, key=lambda s: reach_by_id[s.id])
    best_reach = reach_by_id[best_relay.id]

    if best_reach < 5:
        return ""

    ratio = best_reach / total_reach
    if ratio < 0.65:
        return ""

    pct = int(ratio * 100)
    return (
        f"↳ relay point: {best_relay.name} accounts for {best_reach}/{total_reach}"
        f" downstream reach ({pct}%) — treat as load-bearing; changes cascade"
    )


def _compute_subclass_exposure(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1037: Subclass exposure — fires when seed is a base class with cross-file subclasses.

    BFS follows CALLS and CONTAINS edges only — INHERITS/IMPLEMENTS edges are invisible.
    When you focus on a base class or mixin, its subclasses never appear in the BFS output.
    Agents editing the base class don't know they're editing a shared interface.

    Example outputs:
      ↳ subclass: FileParser (parser.py) — interface changes propagate
      ↳ 3 subclasses: ConfigLoader (config.py), LocalConfig (+2 more) — interface changes propagate to all

    Distinct from:
    - S65 (change_exposure): fires on caller-file blast risk — not inheritance visibility
    - S1034 (cross-file siblings): naming-family siblings in same dir — not inheritance
    - S1035 (orchestrator): callee/caller topology role — not inheritance

    Conditions:
    - Seed is a class (not function/method)
    - Not a test file
    - Has ≥ 1 cross-file subclass (different file from seed) via INHERITS or IMPLEMENTS edges
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("class", "interface"):
        return ""
    if _is_test_file(seed.file_path):
        return ""

    all_subs = graph.subtypes_of(seed.name)
    cross_file_subs = [s for s in all_subs if s.file_path != seed.file_path and not _is_test_file(s.file_path)]

    if not cross_file_subs:
        return ""

    n = len(cross_file_subs)
    # Format: "ClassName (filename.py)" for first 2, then overflow count
    def _short(s: "Symbol") -> str:
        return f"{s.name} ({s.file_path.rsplit('/', 1)[-1]})"

    if n == 1:
        return f"↳ subclass: {_short(cross_file_subs[0])} — interface changes propagate"

    shown = cross_file_subs[:2]
    parts = ", ".join(_short(s) for s in shown)
    overflow = f" +{n - 2} more" if n > 2 else ""
    return f"↳ {n} subclasses: {parts}{overflow} — interface changes propagate to all"


def _compute_hub_callee_warning(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1039: Hub callee warning — fires when a direct callee of the seed is itself a hub.

    When editing a function, you may decide to also modify one of its callees to support
    the change. But BFS doesn't show HOW WIDELY those callees are used across the codebase.
    A callee with 10+ files depending on it is shared infrastructure — changing its signature
    or behavior is a much larger blast radius than editing the seed alone.

    Example outputs:
      ↳ hub callee: build_graph (31 caller files) — shared infrastructure; changes here ripple broadly
      ↳ hub callees: count_tokens (24 files), Config.get (52 files) — shared contracts; changes ripple broadly

    Distinct from:
    - S65 (change_exposure): SEED's own blast radius (its callers)
    - S66 (hub BFS scope): SEED is a hub and BFS is truncated
    - S1035 (orchestrator advisory): SEED has many callees, few callers
    - S1036 (relay point): ONE callee dominates DOWNSTREAM reach

    S1039 answers a different question: "if I decide to modify a callee during this edit,
    how widely would that change propagate?"

    Conditions:
    - Seed is a function/method, not in test files
    - At least one cross-file callee has >= 10 unique non-test caller files
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("function", "method"):
        return ""
    if _is_test_file(seed.file_path):
        return ""

    seen_seed_ids: set[str] = {s.id for s in seeds}

    # Cross-file callees of the seed (excluding co-seeds)
    d1_callees: list["Symbol"] = [
        graph.symbols[c_id]
        for c_id in graph._callees.get(seed.id, [])
        if c_id in graph.symbols
        and graph.symbols[c_id].file_path != seed.file_path
        and c_id not in seen_seed_ids
    ]

    if not d1_callees:
        return ""

    _HUB_THRESHOLD = 10

    # For each cross-file callee, count unique non-test caller files (excluding callee's own file)
    hub_callees: list[tuple["Symbol", int]] = []
    for callee in d1_callees:
        caller_files: set[str] = set()
        for caller_id in graph._callers.get(callee.id, []):
            caller_sym = graph.symbols.get(caller_id)
            if caller_sym is None:
                continue
            if _is_test_file(caller_sym.file_path):
                continue
            if caller_sym.file_path == callee.file_path:
                continue
            caller_files.add(caller_sym.file_path)
        if len(caller_files) >= _HUB_THRESHOLD:
            hub_callees.append((callee, len(caller_files)))

    if not hub_callees:
        return ""

    # Sort by caller count descending, show top 2
    hub_callees.sort(key=lambda x: x[1], reverse=True)
    shown = hub_callees[:2]
    overflow_count = len(hub_callees) - 2

    if len(shown) == 1:
        callee, count = shown[0]
        return (
            f"↳ hub callee: {callee.qualified_name} ({count} caller files)"
            f" — shared infrastructure; if you modify this callee, changes ripple broadly"
        )
    else:
        parts = ", ".join(f"{c.qualified_name} ({n} files)" for c, n in shown)
        overflow = f" (+{overflow_count} more)" if overflow_count > 0 else ""
        return (
            f"↳ hub callees: {parts}{overflow}"
            f" — shared infrastructure; changes to these ripple broadly"
        )


def _compute_cross_language_callees(
    seeds: "list[Symbol]",
    ordered: "list[tuple[Symbol, int]]",
    graph: "Tempo",
) -> str:
    """S1040: Cross-language callee warning — fires when a direct callee of the seed is in
    a different language than the seed.

    Graph-level symbol matching can produce false edges: Python code like
    ``cfg_path.exists()`` matches a TypeScript ``AmbientStatus.exists`` property
    because both use the method name "exists".  These phantom cross-language edges
    exist in the graph's _callees index and may mislead agents into treating them as
    real dependencies.

    Fires when:
    - The seed is a non-test Python/Rust/Go function or method
    - At least one direct callee is in a different language (TypeScript, JavaScript, etc.)

    Output example:
      ↳ cross-language callees: AmbientStatus.exists (TypeScript) — may be symbol-name
        collision (Python .exists() → TS property); verify or suppress with exclude_dirs

    Does NOT fire when the seed itself is TypeScript/JavaScript (cross-language rendering
    edges like JSX are expected in that context).

    Note: `ordered` is accepted for backwards compatibility but not used — cross-language
    edges are not traversed by BFS, so they never appear in ordered. We read _callees
    directly (same approach as _compute_hub_callee_warning).
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("function", "method"):
        return ""
    if _is_test_file(seed.file_path):
        return ""

    _SCRIPT_LANGS = {"typescript", "javascript", "tsx", "jsx"}
    _BACKEND_LANGS = {"python", "rust", "go", "java", "c", "cpp", "ruby", "csharp"}
    seed_lang = seed.language.value.lower()

    # Only fire when seed is a backend language — frontend cross-language edges are expected
    if seed_lang not in _BACKEND_LANGS:
        return ""

    cross_lang: list[tuple["Symbol", str]] = []

    # Iterate direct callees via _callees index (BFS doesn't traverse cross-language edges,
    # so using `ordered` would always produce an empty result for this signal)
    for cid in graph._callees.get(seed.id, []):
        sym = graph.symbols.get(cid)
        if sym is None:
            continue
        if _is_test_file(sym.file_path):
            continue
        callee_lang = sym.language.value.lower()
        if callee_lang not in _SCRIPT_LANGS:
            continue
        cross_lang.append((sym, callee_lang))

    if not cross_lang:
        return ""

    _lang_display = {"typescript": "TypeScript", "javascript": "JavaScript",
                     "tsx": "TypeScript", "jsx": "JavaScript"}
    parts = ", ".join(
        f"{sym.qualified_name} ({_lang_display.get(lang, lang.capitalize())})"
        for sym, lang in cross_lang[:3]
    )
    overflow = f" +{len(cross_lang) - 3} more" if len(cross_lang) > 3 else ""
    return (
        f"↳ cross-language callees: {parts}{overflow}"
        f" — may be symbol-name collision (e.g. Python .exists() → TS property);"
        f" verify or suppress with exclude_dirs"
    )


def _compute_decomp_candidate(
    seeds: "list[Symbol]",
    graph: "Tempo",
) -> str:
    """S1041: Complexity advisory — fires when the seed is F-grade complexity (cx >= 26).

    Radon complexity grade F = cx >= 26. Functions at this level are candidates
    for decomposition: extracting focused helpers reduces cognitive load and makes
    future changes easier to reason about.

    Only fires when cross-file blast radius is manageable (≤ 5 non-test caller files).
    High-blast-radius functions are excluded because decomposition there requires
    more coordination than a simple signal can capture.

    Fires when:
    - Seed is a function or method (not a class, variable, etc.)
    - Not a test file
    - cx >= 26 (F-grade by Radon scale)
    - ≤ 5 unique cross-file non-test caller files

    Output examples:
      ↳ complexity: cx=43 (F-grade) — no cross-file callers; safe to extract helpers
      ↳ complexity: cx=60 (F-grade) — 3 caller files; coordinate changes before decomposing

    Distinct from:
    - S1035 (orchestrator): fires when MANY callees AND few callers (orchestrator topology)
    - S65 (change_exposure): quantifies caller count/blast radius
    - hotspots mode: identifies complex files/functions globally
    S1041 fires on the seed itself and gives a per-function actionable hint.
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("function", "method"):
        return ""
    if _is_test_file(seed.file_path):
        return ""

    _F_GRADE = 26
    if seed.complexity < _F_GRADE:
        return ""

    # Count unique cross-file non-test caller files
    non_test_cross_callers: set[str] = {
        graph.symbols[cid].file_path
        for cid in graph._callers.get(seed.id, [])
        if cid in graph.symbols
        and not _is_test_file(graph.symbols[cid].file_path)
        and graph.symbols[cid].file_path != seed.file_path
    }

    _MAX_CALLERS = 5
    if len(non_test_cross_callers) > _MAX_CALLERS:
        return ""  # High blast radius — decomp is risky; don't over-advise

    if len(non_test_cross_callers) == 0:
        tail = "no cross-file callers; safe to extract helpers"
    else:
        n = len(non_test_cross_callers)
        tail = f"{n} caller file{'s' if n != 1 else ''}; coordinate changes before decomposing"

    return f"↳ complexity: cx={seed.complexity} (F-grade) — {tail}"


def _compute_component_render_tree(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1038: Component render tree — makes JSX/React RENDERS edges visible in focus output.

    BFS traverses CALLS edges only — JSX component composition via <FooBar /> uses RENDERS
    edges that are completely invisible to BFS. When focusing on a React component, the parent
    components that render it and the child components it renders never appear in the BFS output.

    Example outputs:
      ↳ JSX renders: Avatar, Button, LoadingSpinner — children hidden from BFS
      ↳ JSX rendered by: Dashboard, UserPage (+1 more) — your props interface is their contract

    Distinct from:
    - S1037 (subclass exposure): INHERITS edges for class hierarchy — not JSX composition
    - S65 (change_exposure): caller-file blast risk from CALLS edges — not RENDERS edges
    - S1034 (cross-file siblings): naming-family siblings — not component tree

    Conditions:
    - Seed has outgoing RENDERS edges (renders cross-file child components)
      OR graph.renderers_of(seed.id) returns cross-file parents
    - Seed is not in a test file
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if _is_test_file(seed.file_path):
        return ""

    from ..types import EdgeKind as _EK

    # Children: outgoing RENDERS edges from this seed to other components
    child_names: list[str] = []
    seen_children: set[str] = set()
    for edge in graph.edges:
        if edge.kind is not _EK.RENDERS:
            continue
        if edge.source_id != seed.id:
            continue
        tgt = edge.target_id
        if tgt in graph.symbols:
            child_sym = graph.symbols[tgt]
            if child_sym.file_path != seed.file_path and not _is_test_file(child_sym.file_path):
                if child_sym.name not in seen_children:
                    seen_children.add(child_sym.name)
                    child_names.append(child_sym.name)
        elif tgt and tgt[0:1].isupper():
            # Unresolved PascalCase name — still a component reference worth showing
            if tgt not in seen_children:
                seen_children.add(tgt)
                child_names.append(tgt)

    # Parents: what renders this seed (renderers_of gives RENDERS edge sources)
    parents = [
        s for s in graph.renderers_of(seed.id)
        if s.file_path != seed.file_path and not _is_test_file(s.file_path)
    ]

    if not child_names and not parents:
        return ""

    output_lines: list[str] = []

    if child_names:
        n = len(child_names)
        shown = child_names[:4]
        parts = ", ".join(shown)
        overflow = f" +{n - 4} more" if n > 4 else ""
        output_lines.append(
            f"↳ JSX renders: {parts}{overflow} — children hidden from BFS"
        )

    if parents:
        n = len(parents)
        shown_syms = parents[:3]
        parts = ", ".join(s.name for s in shown_syms)
        overflow = f" +{n - 3} more" if n > 3 else ""
        output_lines.append(
            f"↳ JSX rendered by: {parts}{overflow} — your props interface is their contract"
        )

    return "\n".join(output_lines)


def _compute_dead_seed_note(graph: "Tempo", seeds: "list[Symbol]") -> str:
    """S67: Dead seed annotation — fires when the focus seed itself is a dead candidate.

    Running focus on dead code is the meta-error nobody catches: you get a full BFS expansion
    of callees, coverage gaps, upstream reach — all analyzing something nobody calls.
    This fires early (before BFS blocks) so agents can recalibrate before reading further.

    Condition: ≥1 seed has dead_code_confidence ≥ 50.
    Threshold 50 = at minimum: no callers (+30) + no file importers (+25) — a truly isolated symbol.
    Test-file seeds are excluded by _dead_code_confidence (-50 penalty).

    For multi-seed focus, only dead-candidate seeds are named individually.
    Single-seed case omits the name (already shown in the Focus header).
    """
    dead_seeds: list[tuple[Symbol, int]] = []
    for sym in seeds:
        # Skip symbols in entry-point scripts: files with no importers are standalone runners
        # (main.py, analyze.py, etc.) — they're not called from code, so "no callers" is expected.
        if not graph.importers_of(sym.file_path):
            continue
        conf = _dead_code_confidence(sym, graph)
        if conf >= 50:
            dead_seeds.append((sym, conf))

    if not dead_seeds:
        return ""

    if len(dead_seeds) == 1 and len(seeds) == 1:
        _, conf = dead_seeds[0]
        return (
            f"↳ dead candidate: no callers (confidence: {conf}%)"
            f" — verify intent; full analysis in dead_code mode"
        )

    parts = ", ".join(f"{sym.name} ({conf}%)" for sym, conf in dead_seeds)
    return (
        f"↳ dead candidates: {parts}"
        f" — these seeds have no callers; verify intent; full analysis in dead_code mode"
    )


def _compute_indirect_reachability(graph: "Tempo", seeds: "list[Symbol]") -> str:
    """S1042: Indirect reachability — fires when seed has 0 direct callers but parent is live.

    Addresses the two dead-code scanner false-positive patterns confirmed in cycle-254:

    1. **Python class methods**: `obj.method()` calls are not captured as direct CALLS edges
       from external callers because the variable type (`obj`) can't be inferred statically.
       If the parent CLASS has external callers, the method is likely reachable via instance.

    2. **TypeScript hook-returned functions**: `const {fn} = useHook()` — functions returned
       by a React hook are called via destructuring, not as direct call targets. The CALLS edge
       for the hook call exists (`caller → useHook`) but not for the returned member.

    Conditions (per seed):
    - kind in {method, function} and not in a test file
    - 0 non-test direct callers (appears dead to static analysis)
    - Has a parent_id pointing to a CLASS or HOOK symbol
    - Complexity ≥ 5 for class methods (filters trivial getters); no floor for hook children
    - Parent has ≥1 non-test external callers (parent is live from outside its own file)

    Output forms:
    - SINGLETON: seed + fewer than _S1042_FAMILY_THRESHOLD siblings match →
        "↳ instance method: ClassName has N callers — method may be called via instance dispatch"
    - FAMILY: seed + ≥_S1042_FAMILY_THRESHOLD sibling methods also match (same parent, 0 callers) →
        "↳ instance method family: ClassName — method and N siblings have 0 direct callers..."

    Fires for the first qualifying seed only (avoids redundancy in multi-seed focus).
    Distinct from S67 (dead candidate): S67 uses confidence score; S1042 is structural — looks
    at the parent's liveness, not the seed's isolation score. They can fire together.
    """
    _S1042_FAMILY_THRESHOLD = 5

    for seed in seeds:
        if _is_test_file(seed.file_path):
            continue
        if seed.kind.value not in ("method", "function"):
            continue
        if not seed.parent_id or seed.parent_id not in graph.symbols:
            continue

        parent = graph.symbols[seed.parent_id]
        if parent.kind.value not in ("class", "hook"):
            continue

        # Complexity floor: class methods need cx ≥ 5 (filter trivial getters/setters).
        # Hook-returned functions can be cx=0 (arrow functions) — no floor applied.
        if parent.kind.value == "class" and seed.complexity < 5:
            continue

        # Seed must have 0 non-test direct callers
        non_test_callers = [c for c in graph.callers_of(seed.id) if not _is_test_file(c.file_path)]
        if non_test_callers:
            continue

        # Parent must have ≥1 non-test caller from OUTSIDE its own file
        parent_ext_callers = [
            c for c in graph.callers_of(parent.id)
            if not _is_test_file(c.file_path) and c.file_path != parent.file_path
        ]
        if not parent_ext_callers:
            continue

        n_parent_callers = len(parent_ext_callers)

        # Count sibling methods/functions under the same parent that also have 0 non-test callers
        siblings_0cal = [
            s for s in graph.symbols.values()
            if s.parent_id == seed.parent_id
            and s.id != seed.id
            and s.kind.value in ("method", "function")
            and not _is_test_file(s.file_path)
            and not graph._callers.get(s.id)  # fast path: any callers (includes test)
        ]
        # Refine: only count siblings with 0 NON-test callers
        siblings_0cal = [
            s for s in siblings_0cal
            if not any(
                not _is_test_file(graph.symbols[cid].file_path)
                for cid in graph._callers.get(s.id, [])
                if cid in graph.symbols
            )
        ]
        total_indirect = 1 + len(siblings_0cal)

        caller_word = "caller" if n_parent_callers == 1 else "callers"

        if parent.kind.value == "hook":
            # TypeScript hook-return pattern
            if total_indirect >= _S1042_FAMILY_THRESHOLD:
                return (
                    f"\u21b3 hook-returned family: {parent.name} has {n_parent_callers} {caller_word}"
                    f" \u2014 {seed.name} and {len(siblings_0cal)} siblings returned via destructuring"
                    f" (e.g. `const {{{seed.name}}} = {parent.name}()`);"
                    f" no direct CALL edges; dead scanner may report FP"
                )
            return (
                f"\u21b3 hook-returned: {seed.name} is inside {parent.name}"
                f" ({n_parent_callers} {caller_word})"
                f" \u2014 invoked via destructuring `const {{{seed.name}}} = {parent.name}()`;"
                f" no direct CALL edges; dead scanner may report FP"
            )

        # Python class instance method pattern
        if total_indirect >= _S1042_FAMILY_THRESHOLD:
            return (
                f"\u21b3 instance method family: {parent.name} has {n_parent_callers} {caller_word}"
                f" \u2014 {seed.name} and {len(siblings_0cal)} sibling methods have 0 direct callers;"
                f" all likely called via `instance.method()` dispatch; static graph misses external"
                f" calls through variable-typed receivers"
            )
        return (
            f"\u21b3 instance method: {parent.name} has {n_parent_callers} external {caller_word}"
            f" \u2014 {seed.name} may be called as `instance.{seed.name}()`;"
            f" static call graph doesn\u2019t resolve variable-typed receiver dispatch"
        )

    return ""


def _compute_stability_mismatch(graph: "Tempo", seeds: "list[Symbol]") -> str:
    """S71: Stability mismatch — stable seed with hot callers.

    Fires when ALL seeds are in stable (non-hot) files but ≥2 distinct hot
    (recently-modified) files call into them. The seed hasn't changed but the
    active code around it has — callers may be evolving their expectations.

    Distinct from S59 (caller volatility, fires per-symbol inside BFS at any stability)
    and S62 (contract drift, hot callees). This fires only when the seed is
    specifically STABLE under caller pressure from ≥2 hot files — signalling that
    the stable API may need to adapt to the active callers around it.
    """
    hot = graph.hot_files
    if not hot or not seeds:
        return ""
    if any(s.file_path in hot for s in seeds):
        return ""

    hot_caller_files: set[str] = set()
    for sym in seeds:
        for c in graph.callers_of(sym.id):
            if c.file_path in hot and not _is_test_file(c.file_path) and c.file_path != sym.file_path:
                hot_caller_files.add(c.file_path)
    if len(hot_caller_files) < 2:
        return ""
    return (
        f"↳ stability mismatch: stable seed, {len(hot_caller_files)} hot caller files"
        f" — callers are changing; review for API pressure"
    )


def _compute_hidden_coupling(seeds, graph):
    """Detect files with high git co-change but zero graph edges (hidden coupling)."""
    if not graph.root or not seeds:
        return ""
    from ..git import file_cochange_pairs

    seed_fps = list({s.file_path for s in seeds})[:3]  # Cap at 3 files
    hidden = []
    for fp in seed_fps:
        pairs = file_cochange_pairs(graph.root, fp)
        for other, ratio in pairs:
            if other not in graph.files:
                continue
            # Check for graph edges between these files
            has_import = (other in graph.importers_of(fp) or
                         fp in graph.importers_of(other))
            has_out_import = (other in graph.outgoing_imports_of(fp) or
                            fp in graph.outgoing_imports_of(other))
            if not has_import and not has_out_import:
                hidden.append((fp, other, ratio))

    if not hidden:
        return ""

    lines = []
    for fp, other, ratio in hidden[:3]:
        fp_short = fp.rsplit("/", 1)[-1]
        other_short = other.rsplit("/", 1)[-1]
        pct = int(ratio * 100)
        lines.append(
            f"hidden coupling: {fp_short} and {other_short} co-change {pct}% of commits"
            f" but have no import/call edges — likely coupled via config, events, or convention"
        )
    return "\n".join(lines)


def _compute_stale_callers(seeds, graph, *, _file_ages=None):
    """Detect callers not updated after callee change — potential API drift."""
    if not graph.root or not seeds:
        return ""

    from ..git import file_last_modified_days as _fld_sc  # noqa: PLC0415

    def _age(fp):
        if _file_ages is not None:
            return _file_ages.get(fp)
        try:
            return _fld_sc(graph.root, fp)
        except Exception:
            return None

    stale = []
    for s in seeds[:3]:
        seed_age = _age(s.file_path)
        if seed_age is None or seed_age >= 30:
            continue  # Seed not recently modified

        callers = graph.callers_of(s.id)
        cross_file = [c for c in callers if c.file_path != s.file_path]
        for c in cross_file[:15]:
            caller_age = _age(c.file_path)
            if caller_age is not None and caller_age >= 90:
                stale.append((c, int(caller_age)))

    if not stale:
        return ""

    stale.sort(key=lambda x: -x[1])
    top = stale[:3]
    names = ", ".join(f"{c.name} ({age}d)" for c, age in top)
    extra = f" +{len(stale) - 3} more" if len(stale) > 3 else ""
    return (
        f"stale callers: {names}{extra} — not modified in 90+ days"
        f" but seed changed recently; verify API compatibility"
    )


def _compute_async_gap(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1043: Async gap — async function whose body contains no await.

    An async function with no await runs synchronously but returns a
    coroutine/Promise, misleading callers about its execution model.
    Common causes: forgot to await an I/O call, or the function should
    just be a regular synchronous function.

    Conditions (per seed):
    - kind in {function, method}
    - signature contains 'async ' (Python: async def, TS: async function / arrow)
    - line_count >= 3 (skip trivial 1–2-line async shims — often intentional)
    - body (lines after the def line) has NO 'await' token
    - body has NO 'yield' token (async generators are valid without await)
    - not a test file

    Reads source file to inspect body. Skips on I/O error or missing file.
    """
    import re as _re  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    _AWAIT_PAT = _re.compile(r"\bawait\b")
    _YIELD_PAT = _re.compile(r"\byield\b")

    gaps = []
    for sym in seeds:
        if _is_test_file(sym.file_path):
            continue
        if sym.kind.value not in ("function", "method"):
            continue
        if "async " not in sym.signature:
            continue
        if sym.line_count < 3:  # 1-2-line async shims are usually intentional type aliases
            continue

        try:
            full_path = _os.path.join(graph.root, sym.file_path)
            if not _os.path.isfile(full_path):
                continue
            with open(full_path, encoding="utf-8", errors="replace") as _fh:
                source_lines = _fh.readlines()
        except OSError:
            continue

        # Body = lines after the def/signature line (line_start is 1-indexed,
        # source_lines is 0-indexed, so body starts at index sym.line_start).
        body_lines = source_lines[sym.line_start : sym.line_end]
        if not body_lines:
            continue
        body_text = "".join(body_lines)

        if _AWAIT_PAT.search(body_text):
            continue  # correct async usage
        if _YIELD_PAT.search(body_text):
            continue  # async generator — valid without await

        gaps.append(sym)

    if not gaps:
        return ""

    names = ", ".join(sym.qualified_name for sym in gaps[:3])
    extra = f" +{len(gaps) - 3} more" if len(gaps) > 3 else ""
    return (
        f"async gap: {names}{extra}"
        f" — async but no await in body; runs synchronously, consider removing async"
    )


def _compute_call_cycle(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1044: Call cycle — seed participates in an indirect 3-hop call cycle (A→B→C→A).

    The existing inline [recursive: mutual with X] annotation catches 2-hop direct
    mutual recursion (A→B→A) at depth-0. For 3-hop indirect cycles (A→B→C→A),
    there is NO current warning — the cycle is invisible to the agent.

    This fires PRE-BFS so the agent sees it before reading the BFS tree, which may
    already be truncated by the 50-node cap and miss one leg of the cycle entirely.

    Conditions (per seed):
    - kind in {function, method}
    - not a test file
    - seed calls callee B (B ≠ seed)
    - B calls callee C (C ≠ seed, C ≠ B)
    - C calls back to seed (forming A→B→C→A)
    - B and C are not test files
    - Callee scan capped at 15 per hop to stay fast (O(15²) per seed)

    Distinct from:
    - [recursive] — self-recursion (A calls A directly)
    - [recursive: mutual with X] — direct 2-hop (A→B→A), inline at depth-0 node
    This signal: indirect 3-hop, pre-BFS placement, invisible to inline annotation.
    """
    cycles: list[tuple["Symbol", "Symbol", "Symbol"]] = []  # (seed, mid, far)
    seen_triples: set[tuple[str, str, str]] = set()

    for sym in seeds:
        if _is_test_file(sym.file_path):
            continue
        if sym.kind.value not in ("function", "method"):
            continue

        direct_callees = graph.callees_of(sym.id)
        direct_ids = {c.id for c in direct_callees}

        for mid in direct_callees[:15]:
            if mid.id == sym.id:
                continue  # self-loop — handled by [recursive]
            if _is_test_file(mid.file_path):
                continue

            # Check if mid directly calls back to sym (2-hop) — skip, handled inline
            mid_callees = graph.callees_of(mid.id)
            mid_callee_ids = {c.id for c in mid_callees}
            if sym.id in mid_callee_ids:
                continue  # direct mutual recursion — already annotated inline

            for far in mid_callees[:15]:
                if far.id == sym.id:
                    continue  # already caught by mid→sym check above
                if far.id == mid.id:
                    continue
                if _is_test_file(far.file_path):
                    continue

                far_callees = {c.id for c in graph.callees_of(far.id)[:20]}
                if sym.id in far_callees:
                    triple = tuple(sorted([sym.id, mid.id, far.id]))
                    if triple not in seen_triples:
                        seen_triples.add(triple)
                        cycles.append((sym, mid, far))
                    break  # one cycle per (seed, mid) pair is enough

    if not cycles:
        return ""

    parts = []
    for sym, mid, far in cycles[:2]:
        path = f"{sym.name} → {mid.name} → {far.name} → {sym.name}"
        parts.append(path)
    extra = f" (+{len(cycles) - 2} more)" if len(cycles) > 2 else ""
    return (
        f"call cycle: {'; '.join(parts)}{extra}"
        f" — 3-hop indirect recursion; modifying any node affects the entire loop"
    )


def _compute_dead_params(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1045: Dead parameters — function accepts a parameter but never uses it in the body.

    Callers are silently passing values that have zero effect on computation. Common causes:
    backward-compat shims (param removed from logic but kept in signature), planned-but-never-wired
    parameters, or refactors that removed the feature but not the parameter.

    When a heavily-called function has a dead parameter, ALL those callers are passing a value
    that is silently discarded. The agent should know before editing — the dead param may need
    to be wired in, or removed from all call sites.

    Example outputs:
      ↳ dead param: lang — in _extract_signature, never used in body (31 callers pass this silently)
      ↳ dead params: verbose (in build_index, 8 callers); timeout (in fetch_data, 5 callers)

    Conditions (per seed):
    - kind in {function, method}
    - not a test file
    - ≥3 cross-file non-test callers (ensures meaningful public API)
    - ≥2 extractable non-trivial parameters (excludes self/cls/_* prefix, *args/**kwargs)
    - line_count ≥ 3 (excludes trivial stubs/shims)
    - body is readable and non-empty
    - ≥1 parameter name does not appear as a whole word (\\b boundary) anywhere in the body

    Exclusions:
    - Parameters starting with '_': Python convention for intentionally-unused
    - self, cls: standard OOP — never "used" explicitly
    - *args, **kwargs: variadic — may be forwarded without explicit mention
    """
    import re as _re  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    def _parse_param_names(sig: str) -> list:
        """Extract plain parameter names from a function signature string."""
        m = _re.search(r"\((.+)\)", sig, _re.DOTALL)
        if not m:
            return []
        inner = m.group(1)
        parts: list = []
        depth = 0
        cur = ""
        for ch in inner:
            if ch in "([{":
                depth += 1
                cur += ch
            elif ch in ")]}":
                depth -= 1
                cur += ch
            elif ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur.strip())
        names: list = []
        for part in parts:
            part = part.strip()
            # Skip *args, **kwargs, bare * separator
            if part.startswith("**") or part == "*":
                continue
            p = part.lstrip("*")
            name_m = _re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", p)
            if not name_m:
                continue
            name = name_m.group(1)
            if name in ("self", "cls"):
                continue
            if name.startswith("_"):
                continue
            names.append(name)
        return names

    # Accumulate (sym_name, param_name, cross_caller_count) per seed
    dead_hits: list = []

    for sym in seeds:
        if _is_test_file(sym.file_path):
            continue
        if sym.kind.value not in ("function", "method"):
            continue
        if not sym.signature:
            continue
        if sym.line_count < 3:
            continue

        params = _parse_param_names(sym.signature)
        if len(params) < 2:
            continue

        cross_callers = [
            c for c in graph.callers_of(sym.id)
            if c.file_path != sym.file_path and not _is_test_file(c.file_path)
        ]
        if len(cross_callers) < 3:
            continue

        try:
            full_path = _os.path.join(graph.root, sym.file_path)
            if not _os.path.isfile(full_path):
                continue
            with open(full_path, encoding="utf-8", errors="replace") as _fh:
                source_lines = _fh.readlines()
        except OSError:
            continue

        # Body = lines after the def/signature line (same convention as _compute_async_gap:
        # line_start is 1-indexed, source_lines is 0-indexed → body starts at index line_start)
        body_lines = source_lines[sym.line_start : sym.line_end]
        if not body_lines:
            continue
        body_text = "".join(body_lines)
        if not body_text.strip():
            continue

        dead = [
            p for p in params
            if not _re.search(r"\b" + _re.escape(p) + r"\b", body_text)
        ]
        if dead:
            for dp in dead[:2]:  # cap at 2 dead params per function
                dead_hits.append((sym.name, dp, len(cross_callers)))

    if not dead_hits:
        return ""

    dead_hits.sort(key=lambda x: -x[2])  # most-called first

    if len(dead_hits) == 1:
        sym_name, param, callers = dead_hits[0]
        return (
            f"↳ dead param: {param} — in {sym_name} signature, never referenced in body"
            f" ({callers} callers pass this silently)"
        )

    # Multiple: group by sym_name for readability
    seen_sym: set = set()
    parts: list = []
    for sym_name, param, callers in dead_hits[:4]:
        if sym_name not in seen_sym:
            seen_sym.add(sym_name)
            same_sym_params = [p for s, p, _ in dead_hits if s == sym_name]
            params_str = ", ".join(same_sym_params[:2])
            parts.append(f"{params_str} (in {sym_name}, {callers} callers)")
    extra = f" +{max(0, len(seen_sym) - 4)} more" if len(seen_sym) > 4 else ""
    return (
        f"↳ dead params: {'; '.join(parts)}{extra}"
        f" — in signatures but never used in body; callers pass these silently"
    )


def _compute_broker_warning(seeds: "list[Symbol]", graph: "Tempo") -> str:
    """S1046: Broker warning — fires when seed has many cross-file callers AND callees.

    A broker is a function that sits at the intersection of two dependency flows:
    it's widely called (many callers) AND calls widely (many callees). Unlike an
    orchestrator (few callers + many callees) or a utility leaf (many callers + few
    callees), the broker channels traffic bidirectionally — it's both a blast radius
    sink and a dispatch hub simultaneously.

    Example outputs:
      ↳ broker: 10 callers ↔ 11 callees — bidirectional hub; changes ripple both upstream and downstream
      ↳ broker: 7 callers ↔ 12 callees — bidirectional hub; changes ripple both upstream and downstream

    Distinct from:
    - S1035 (orchestrator): fires for ≥6 callees AND 1–4 callers — broker requires ≥5 callers
    - S65 (change_exposure): aggregates risk factors (hot files, coverage gaps); doesn't capture topology
    - S1039 (hub callee): fires when a CALLEE of the seed is itself a hub; broker = the seed IS the hub
    - S198 (leaf): opposite topology (0-1 callees)

    Conditions:
    - kind in {function, method}
    - not a test file
    - ≥5 cross-file non-test callers (distinct from orchestrator which requires ≤4)
    - ≥5 cross-file non-test callees
    """
    if not seeds:
        return ""
    seed = seeds[0]
    if seed.kind.value not in ("function", "method"):
        return ""
    if _is_test_file(seed.file_path):
        return ""

    _BROKER_MIN = 5

    cross_callers = [
        c for c in graph.callers_of(seed.id)
        if c.file_path != seed.file_path and not _is_test_file(c.file_path)
    ]
    if len(cross_callers) < _BROKER_MIN:
        return ""

    cross_callees = [
        c for c in graph.callees_of(seed.id)
        if c.file_path != seed.file_path and not _is_test_file(c.file_path)
    ]
    if len(cross_callees) < _BROKER_MIN:
        return ""

    nc = len(cross_callers)
    ne = len(cross_callees)
    return (
        f"↳ broker: {nc} callers ↔ {ne} callees"
        f" — bidirectional hub; changes ripple both upstream and downstream"
    )


def render_focused(graph: Tempo, query: str, *, max_tokens: int = 4000, _staleness_map: "dict[str, int | None] | None" = None) -> str:
    """Task-focused rendering with BFS graph traversal.
    Starts from search results, then follows call/render/import edges
    to build a connected subgraph relevant to the query.

    For monolith files (>1000 lines), adds intra-file neighborhood context
    and biases BFS toward cross-file edges to avoid getting trapped in one file.

    Supports multi-symbol focus via '|' separator: "authMiddleware | loginHandler"
    merges seeds from each query and runs a single combined BFS."""
    result = _collect_multi_seeds(graph, query)
    if result is None:
        return _suggest_alternatives(graph, query) or f"No symbols matching '{query}'"
    seeds, seed_files, query_tokens, _parts = result

    ordered, seen_ids, orbit_seed_meta, _depth_extended = _run_bfs_with_orbit(
        graph, seeds, seed_files, query_tokens,
    )

    _focus_header = f"Focus: {' | '.join(_parts)}" if len(_parts) > 1 else f"Focus: {query}"
    if _depth_extended:
        _focus_header += f"  [depth +1 — sparse ({len(ordered)} nodes)]"
    lines = [_focus_header, ""]
    _dead_note = _compute_dead_seed_note(graph, seeds)
    if _dead_note:
        lines.append(_dead_note)
        lines.append("")
    _indirect = _compute_indirect_reachability(graph, seeds)
    if _indirect:
        lines.append(_indirect)
        lines.append("")
    _exposure = _compute_change_exposure(graph, seeds)
    if _exposure:
        lines.append(_exposure)
        lines.append("")
    _scope_note = _compute_bfs_scope_note(ordered)
    if _scope_note:
        lines.append(_scope_note)
        lines.append("")
    _pair_note = _compute_paired_functions(graph, seeds, seen_ids)
    if _pair_note:
        lines.append(_pair_note)
        lines.append("")
    _module_diversity = _compute_bfs_module_diversity(graph, seeds, ordered)
    if _module_diversity:
        lines.append(_module_diversity)
        lines.append("")
    _stability_mismatch = _compute_stability_mismatch(graph, seeds)
    if _stability_mismatch:
        lines.append(_stability_mismatch)
        lines.append("")
    _hidden = _compute_hidden_coupling(seeds, graph)
    if _hidden:
        lines.append(_hidden)
        lines.append("")
    _stale = _compute_stale_callers(seeds, graph)
    if _stale:
        lines.append(_stale)
        lines.append("")
    _async_gap = _compute_async_gap(seeds, graph)
    if _async_gap:
        lines.append(_async_gap)
        lines.append("")
    _call_cycle = _compute_call_cycle(seeds, graph)
    if _call_cycle:
        lines.append(_call_cycle)
        lines.append("")
    _dead_params = _compute_dead_params(seeds, graph)
    if _dead_params:
        lines.append(_dead_params)
        lines.append("")
    _broker = _compute_broker_warning(seeds, graph)
    if _broker:
        lines.append(_broker)
        lines.append("")
    _hot_cluster = _compute_hot_cluster_note(graph, ordered)
    if _hot_cluster:
        lines.append(_hot_cluster)
        lines.append("")
    _wall_note = _compute_depth_wall_lookahead(graph, ordered, seen_ids, seeds)
    if _wall_note:
        lines.append(_wall_note)
        lines.append("")
    _naming_cluster = _compute_bfs_naming_clusters(seeds, ordered)
    if _naming_cluster:
        lines.append(_naming_cluster)
        lines.append("")
    _variant_group = _compute_variant_group(seeds, graph)
    if _variant_group:
        lines.append(_variant_group)
        lines.append("")
    _sibling_family = _compute_cross_file_siblings(seeds, graph, seen_ids)
    if _sibling_family:
        lines.append(_sibling_family)
        lines.append("")
    _orchestrator = _compute_orchestrator_advisory(seeds, graph)
    if _orchestrator:
        lines.append(_orchestrator)
        lines.append("")
    _decomp = _compute_decomp_candidate(seeds, graph)
    if _decomp:
        lines.append(_decomp)
        lines.append("")
    _relay = _compute_relay_point(seeds, graph, ordered)
    if _relay:
        lines.append(_relay)
        lines.append("")
    _subclass_exp = _compute_subclass_exposure(seeds, graph)
    if _subclass_exp:
        lines.append(_subclass_exp)
        lines.append("")
    _render_tree = _compute_component_render_tree(seeds, graph)
    if _render_tree:
        lines.append(_render_tree)
        lines.append("")
    _hub_callee = _compute_hub_callee_warning(seeds, graph)
    if _hub_callee:
        lines.append(_hub_callee)
        lines.append("")
    _cross_lang = _compute_cross_language_callees(seeds, ordered, graph)
    if _cross_lang:
        lines.append(_cross_lang)
        lines.append("")
    seen_files: set[str] = set()
    # Count header sections already written to lines (change_exposure, scope_note,
    # pair_note, module_diversity) so the BFS loop and signal sections start with
    # an accurate token budget rather than assuming zero overhead.
    token_count = count_tokens("\n".join(lines))

    # Callsite line index: (caller_id, callee_id) → sorted non-zero line numbers.
    _callsite_lines: dict[tuple[str, str], list[int]] = {}
    for _edge in graph.edges:
        if _edge.kind is EdgeKind.CALLS and _edge.line > 0:
            _key = (_edge.source_id, _edge.target_id)
            _callsite_lines.setdefault(_key, []).append(_edge.line)
    for _key in _callsite_lines:
        _callsite_lines[_key] = sorted(set(_callsite_lines[_key]))

    _staleness_cache: dict[str, int | None] = {}
    # S40: pre-populate staleness cache with one batch git call instead of N individual
    # `git log -1 -- <file>` subprocesses (one per unique caller file in BFS).
    # C7: if caller (e.g. render_prepare) already ran batch_file_modification_map once
    # and passes the result via _staleness_map, skip the subprocess entirely.
    if _staleness_map is not None:
        _staleness_cache.update(_staleness_map)
    else:
        try:
            from ..git import batch_file_modification_map as _bfmm  # noqa: PLC0415
            _staleness_cache.update(_bfmm(graph.root))
        except Exception:
            pass

    for _sym_idx, (sym, depth) in enumerate(ordered):
        orbit_note = ""
        if sym.id in orbit_seed_meta and depth == 1:
            _orb_fp, _orb_freq = orbit_seed_meta[sym.id]
            orbit_note = f"  [orbit {_orb_freq:.0%}]"
        block_lines = _build_symbol_block_lines(sym, depth, orbit_note, graph, query_tokens, _staleness_cache, _callsite_lines)
        block = "\n".join(block_lines)
        should_break, block_tokens = _handle_overflow(lines, ordered, block, token_count, max_tokens, graph=graph, current_idx=_sym_idx)
        if should_break:
            break
        lines.append(block)
        token_count += block_tokens + 1  # +1 for separator "\n" in final join
        seen_files.add(sym.file_path)

    _seed_syms = [sym for sym, d in ordered if d == 0]

    # BFS block tokens were summed individually; "\n".join(lines) adds separator newlines
    # that aren't counted. Recount once for accuracy before remaining budget checks.
    token_count = count_tokens("\n".join(lines))

    token_count = _render_context_sections(
        graph, lines=lines, ordered=ordered, seen_files=seen_files,
        seen_ids=seen_ids, token_count=token_count, max_tokens=max_tokens,
        _seed_syms=_seed_syms, _callsite_lines=_callsite_lines,
    )

    # --- Focused signal-group helpers ---
    # token_count is updated after each section so later sections see the correct
    # remaining budget and output stays within max_tokens.
    token_count = _extend_tracked(lines, _signals_focused_test(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_complexity(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_structure(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_class_hierarchy(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_class_patterns(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_coupling(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_naming(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_fn_traits(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    token_count = _extend_tracked(lines, _signals_focused_fn_patterns(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)
    _extend_tracked(lines, _signals_focused_fn_advanced(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ), token_count)

    return "\n".join(lines)



def _monolith_neighborhood(graph: Tempo, seed: Symbol) -> list[str]:
    """For a symbol in a large file, show its local neighborhood:
    parent scope, siblings, and nearby symbols by line proximity."""
    all_syms = graph.symbols_in_file(seed.file_path)
    if len(all_syms) < 3:
        return []

    fi = graph.files.get(seed.file_path)
    lines = [f"Neighborhood in {seed.file_path} ({fi.line_count if fi else '?'} lines):"]

    # Parent scope
    if seed.parent_id and seed.parent_id in graph.symbols:
        parent = graph.symbols[seed.parent_id]
        lines.append(f"  parent: {parent.kind.value} {parent.qualified_name} (L{parent.line_start}-{parent.line_end})")

    # Siblings: same parent, sorted by line
    siblings = [s for s in all_syms if s.parent_id == seed.parent_id and s.id != seed.id]
    siblings.sort(key=lambda s: s.line_start)
    if siblings:
        # Show nearest siblings (up to 5 before and 5 after by line number)
        before = [s for s in siblings if s.line_start < seed.line_start][-5:]
        after = [s for s in siblings if s.line_start > seed.line_start][:5]
        nearby = before + after
        if nearby:
            lines.append(f"  siblings ({len(siblings)} total, showing nearest):")
            for s in nearby:
                rel = "↑" if s.line_start < seed.line_start else "↓"
                dist = abs(s.line_start - seed.line_start)
                lines.append(f"    {rel} {s.kind.value} {s.name} L{s.line_start} ({dist}L away)")

    # Top-level symbols by size (helps orient in the file).
    # Suppress for top-of-file imports/variables: they sit at L1-20, so all landmarks
    # are "below" and semantically unrelated — showing them misleads the model into
    # predicting files associated with the landmark instead of the matched symbol.
    file_lines = fi.line_count if fi else 1
    is_top_import = (
        seed.kind.value in ("variable", "imports")
        and seed.line_start <= max(20, file_lines * 0.05)
    )
    if not is_top_import:
        top_level = sorted(
            [s for s in all_syms if s.parent_id is None and s.line_count > 20],
            key=lambda s: -s.line_count,
        )[:8]
        if top_level:
            lines.append(f"  landmarks:")
            for s in top_level:
                marker = " ← YOU" if s.id == seed.id else ""
                lines.append(f"    {s.kind.value} {s.name} L{s.line_start}-{s.line_end} ({s.line_count}L){marker}")

    return lines

def _find_related_files(graph: Tempo, symbols: list[Symbol]) -> set[str]:
    """Find files related to a set of symbols via edges."""
    files: set[str] = set()
    for sym in symbols:
        for caller in graph.callers_of(sym.id):
            files.add(caller.file_path)
        for callee in graph.callees_of(sym.id):
            files.add(callee.file_path)
        # Include __init__.py importers — package re-exports are often changed
        # alongside the module they expose (key source of over-narrowing)
        for importer_fp in graph.importers_of(sym.file_path):
            if importer_fp.endswith("__init__.py"):
                files.add(importer_fp)
    return files
