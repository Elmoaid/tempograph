from __future__ import annotations

from pathlib import Path

from ..types import Tempo, EdgeKind, Symbol, SymbolKind
from ._utils import count_tokens, _is_test_file, _caller_domain, _MONOLITH_THRESHOLD, _dead_code_confidence

_MONOLITH_THRESHOLD = 1000


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
        # then cross-file nodes first, then by descending importance.
        deduped.sort(key=lambda pair: (
            pair[1],                                                  # depth ascending
            pair[0].file_path in seed_files if seed_files else True,  # cross-file first
            -_cached_importance(pair[0]),                              # importance descending
        ))

        next_level: list[tuple[Symbol, int]] = []
        for sym, depth in deduped:
            if len(ordered) >= 50:
                break
            ordered.append((sym, depth))

            if depth < _bfs_max_depth:
                caller_limit = 8 if depth == 0 else 5 if depth == 1 else 3
                callee_limit = 8 if depth == 0 else 5 if depth == 1 else 3
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
                for callee in sorted(graph.callees_of(sym.id), key=_imp_key)[:callee_limit]:
                    if callee.id not in seen_ids:
                        next_level.append((callee, depth + 1))
                if depth < 2:
                    for child in graph.children_of(sym.id)[:5]:
                        if child.id not in seen_ids:
                            next_level.append((child, depth + 1))

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
    if token_count + block_tokens > max_tokens:
        remaining = len(ordered) - len([l for l in lines if l and not l.startswith("...")])
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
    try:
        from ..git import symbol_last_modified_days as _sld  # noqa: PLC0415
        _days = _sld(graph.root, sym.file_path, sym.line_start)
        if _days is not None and _days >= 8:
            if _days >= 365:
                age_ann = " [age: 1y+]"
            elif _days >= 30:
                age_ann = f" [age: {_days // 30}m]"
            else:
                age_ann = f" [age: {_days}d]"
    except Exception:
        pass
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

    # Path 1: name-matching — find test_<seed_name>[_*] functions in test files
    name_matches: dict[str, list[str]] = {}  # basename -> [func_names]
    for s in graph.symbols.values():
        if not _is_test_file(s.file_path):
            continue
        if s.kind.value not in ("function", "method", "test"):
            continue
        sname = s.name.lower()
        if sname == prefix or sname.startswith(prefix + "_"):
            basename = s.file_path.rsplit("/", 1)[-1]
            name_matches.setdefault(basename, []).append(s.name)

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
        from ..git import symbol_last_modified_days as _sld_cd  # noqa: PLC0415
        from ..git import file_last_modified_days as _fld_cd    # noqa: PLC0415
        _seed_days = _sld_cd(graph.root, sym.file_path, sym.line_start)
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
    _file_callees = [
        c for c in graph.callees_of(sym.file_path)
        if c.file_path != sym.file_path
    ]
    if 1 <= len(_file_callees) <= 4:
        _chain_parts = [sym.name, _file_callees[0].name]
        _c1 = _file_callees[0]
        _c1_callees = [c for c in graph.callees_of(_c1.file_path) if c.file_path != _c1.file_path]
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
        s for s in graph.symbols.values()
        if s.file_path == sym.file_path and s.id != sym.id
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

    def _stale_annotation(file_path: str) -> str:
        if _fld is None:
            return ""
        if file_path not in staleness_cache:
            staleness_cache[file_path] = _fld(graph.root, file_path)
        days = staleness_cache[file_path]
        if days is None or days <= 30:
            return ""
        return " [stale: 6m+]" if days > 180 else f" [stale: {days}d]"

    def _is_kw(c: "Symbol") -> bool:
        return bool(query_tokens and any(tok in c.file_path.lower() for tok in query_tokens))

    _callers_for_display = [c for c in callers if not _is_test_file(c.file_path)]
    callers_sorted = sorted(_callers_for_display, key=lambda c: 0 if _is_kw(c) else 1)
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
    _total_for_overflow = len(_callers_for_display)

    # Ghost caller detection (depth=0 only): callers that have no callers themselves
    # and are not exported = they contribute zero production reach.
    # Cap lookup at 30 to avoid O(n) graph walks on hub symbols.
    _ghost_ids: set[str] = set()
    if depth == 0:
        _capped = _callers_for_display[:30]
        for _gc in _capped:
            if not _gc.exported and not graph.callers_of(_gc.id):
                _ghost_ids.add(_gc.id)

    caller_strs = []
    for c in shown_callers:
        _cl = (callsite_lines or {}).get((c.id, sym.id), [])
        if len(_cl) == 1:
            _line_ann = f" [line {_cl[0]}]"
        elif len(_cl) >= 2:
            _line_ann = f" [lines {_cl[0]}, {_cl[1]}]"
        else:
            _line_ann = ""
        if c.file_path in graph.hot_files:
            caller_strs.append(f"{c.qualified_name}{_line_ann} [hot]")
        else:
            _dead_ann = " [dead?]" if c.id in _ghost_ids else ""
            caller_strs.append(c.qualified_name + _line_ann + _stale_annotation(c.file_path) + _dead_ann)
    if caller_strs:
        lines.append(f"{indent}  called by: {', '.join(caller_strs)}")
        if _total_for_overflow > shown_count:
            lines[-1] += f" (+{_total_for_overflow - shown_count} more)"
        # Summarize ghost callers if any were shown
        _shown_ghost_count = sum(1 for c in shown_callers if c.id in _ghost_ids)
        if _shown_ghost_count:
            _live_shown = len(shown_callers) - _shown_ghost_count
            lines.append(
                f"{indent}    ↳ {_shown_ghost_count} caller(s) are themselves unreachable"
                f" — effective reach: {_live_shown} of {len(shown_callers)} shown"
            )
        # S50: caller domain diversity — cross-cutting signal at depth=0.
        # When non-test callers come from 3+ distinct subsystems, flag it.
        # Uses ALL callers (not just shown) to get the true domain picture.
        if depth == 0:
            _domains: set[str] = set()
            for _dc in _callers_for_display:
                _d = _caller_domain(_dc.file_path)
                if _d:
                    _domains.add(_d)
            if len(_domains) >= 3:
                _sorted_domains = sorted(_domains)[:4]
                _domain_str = ", ".join(_sorted_domains)
                _n = len(_domains)
                lines.append(
                    f"{indent}    ↳ cross-cutting: {_n} subsystem{'s' if _n != 1 else ''}"
                    f" ({_domain_str}{'...' if _n > 4 else ''})"
                )
        # S57: primary caller concentration — inverse of S50 cross-cutting.
        # When one file accounts for ≥60% of non-test callers (and total ≥4),
        # the function is effectively owned by that file. Suggests co-location.
        # Complements S50: S50 catches spread, S57 catches concentration.
        if depth == 0 and len(_callers_for_display) >= 4:
            _file_counts: dict[str, int] = {}
            for _pc in _callers_for_display:
                _file_counts[_pc.file_path] = _file_counts.get(_pc.file_path, 0) + 1
            _total_callers = len(_callers_for_display)
            _dom_file, _dom_count = max(_file_counts.items(), key=lambda x: x[1])
            if _dom_count / _total_callers >= 0.6 and _dom_file != sym.file_path:
                _dom_basename = _dom_file.rsplit("/", 1)[-1]
                lines.append(
                    f"{indent}    ↳ primary caller: {_dom_basename}"
                    f" ({_dom_count}/{_total_callers})"
                )
        # S59: caller volatility — ≥2 non-test callers in hot_files = interface under pressure.
        # Mirror of S52 (hot callee instability): S52 flags when callees are changing under you;
        # S59 flags when callers are changing around you. Together they paint a full volatility picture.
        # An agent editing a function whose callers are in active churn risks interface breakage
        # from parallel edits: callers may be adding/removing call sites or changing how they use output.
        if depth == 0 and graph.hot_files:
            _hot_callers = [
                c for c in _callers_for_display
                if c.file_path in graph.hot_files
            ]
            if len(_hot_callers) >= 2:
                _cv_names = [c.name for c in _hot_callers[:3]]
                _cv_suffix = "..." if len(_hot_callers) > 3 else ""
                lines.append(
                    f"{indent}    \u21b3 caller volatility: {len(_hot_callers)} active callers"
                    f" ({', '.join(_cv_names)}{_cv_suffix})"
                )
        # S61: upstream transitive reach — how many non-test nodes transitively call this seed.
        # Direct callers (shown above) are the visible surface. But those callers have their own
        # callers, and so on. When direct callers <= 8 but amplify to 4x+ upstream nodes, the
        # agent's blast-radius intuition is wrong: "only 2 callers" can mean 60+ transitively.
        # BFS upward max depth=4, max 200 nodes. Only fires when amplification ratio >= 4x AND
        # upstream >= 20 (strong signal only — avoid noise on well-isolated functions).
        if depth == 0 and len(_callers_for_display) <= 8:
            _direct_count = len(_callers_for_display)
            _upstream_visited: set[str] = {sym.id}
            _upstream_frontier = [sym.id]
            _upstream_capped = False
            for _ in range(4):  # max depth 4 hops
                _next: list[str] = []
                for _uid in _upstream_frontier:
                    for _uc in graph.callers_of(_uid):
                        if _uc.id not in _upstream_visited and not _is_test_file(_uc.file_path):
                            _upstream_visited.add(_uc.id)
                            _next.append(_uc.id)
                            if len(_upstream_visited) >= 201:
                                _upstream_capped = True
                                break
                    if _upstream_capped:
                        break
                _upstream_frontier = _next
                if not _upstream_frontier or _upstream_capped:
                    break
            _upstream_count = len(_upstream_visited) - 1  # exclude seed itself
            if _upstream_count >= 20 and _upstream_count >= _direct_count * 4:
                _cap_str = "+" if _upstream_capped else ""
                lines.append(
                    f"{indent}    \u21b3 upstream reach: {_upstream_count}{_cap_str} nodes"
                    f" — {_direct_count} direct caller{'s' if _direct_count != 1 else ''}"
                    f" amplif{'y' if _direct_count != 1 else 'ies'} to wider blast"
                )
    return lines


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
    # S55: pre-compute whether seed has test callers — [untested] only meaningful when seed is tested.
    _seed_test_callers = (
        [cr for cr in graph.callers_of(sym.id) if _is_test_file(cr.file_path)]
        if depth == 0 else []
    )
    _seed_is_tested = len(_seed_test_callers) > 0
    callee_strs = []
    _sole_use_callees: list = []  # S58: track for orphan cascade check
    for c in ordered_callees:
        _hot_ann = " [hot]" if c.file_path in graph.hot_files else ""
        _cx_ann = ""
        _cb_ann = ""
        _sole_ann = ""
        _recursive_ann = ""
        _untested_ann = ""
        if depth == 0:
            # S49: annotate callees with high complexity so agents see the iceberg
            if c.complexity > 15 and c.kind.value in ("function", "method"):
                _cx_ann = f" (cx={c.complexity})"
            _cb_files = len({cr.file_path for cr in graph.callers_of(c.id) if cr.file_path != c.file_path})
            if _cb_files >= 3:
                _cb_ann = f" [blast: {_cb_files}]"
            # S51: flag sole-use callees — only called from this seed (excluding tests).
            # If you refactor the seed, these become instantly orphaned.
            if c.kind.value in ("function", "method"):
                _prod_callers = [
                    cr for cr in graph.callers_of(c.id)
                    if not _is_test_file(cr.file_path)
                ]
                if len(_prod_callers) == 1 and _prod_callers[0].id == sym.id:
                    _sole_ann = " [sole-use]"
                    _sole_use_callees.append(c)  # S58
            # S54: flag self-call — recursive functions need base-case awareness when modified.
            if c.id == sym.id:
                _recursive_ann = " [recursive]"
            # S55: flag callees with zero test callers — test blind spots for the agent.
            # Only fires when the seed itself is tested; otherwise every callee would show [untested]
            # and the signal collapses to noise.
            if (
                _seed_is_tested
                and c.kind.value in ("function", "method")
                and not _is_test_file(c.file_path)
                and not any(_is_test_file(cr.file_path) for cr in graph.callers_of(c.id))
            ):
                _untested_ann = " [untested]"
        callee_strs.append(f"{c.qualified_name}{_cx_ann}{_hot_ann}{_cb_ann}{_sole_ann}{_recursive_ann}{_untested_ann}")
    lines.append(f"{indent}  calls: {', '.join(callee_strs)}")
    if len(callees) > shown:
        # S68: enrich overflow label with hidden-callee attribute summary (depth=0 only).
        # "(+25 more)" tells agents nothing. "(+25 more: 14 sole-use, 11 untested)" tells them
        # whether the hidden tail is full of private helpers / test blind spots worth digging into.
        if depth == 0:
            _hidden_cs = (hot_callees + cold_callees)[shown:]
            _h_hot = [
                c for c in _hidden_cs
                if c.file_path in graph.hot_files and not _is_test_file(c.file_path)
            ]
            _h_sole: list = []
            _h_unt: list = []
            for _hc68 in _hidden_cs:
                if _hc68.kind.value in ("function", "method"):
                    _hc68_prod = [
                        cr for cr in graph.callers_of(_hc68.id)
                        if not _is_test_file(cr.file_path)
                    ]
                    if len(_hc68_prod) == 1 and _hc68_prod[0].id == sym.id:
                        _h_sole.append(_hc68)
                    if (
                        _seed_is_tested
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
    # S54: recursive summary — fire once when seed calls itself, at depth=0 only.
    if depth == 0 and any(c.id == sym.id for c in callees):
        lines.append(f"{indent}  \u21b3 recursive \u2014 self-referential; verify base case before modifying")
    # S52: hot callee instability — ≥2 non-test callees in hot_files = volatile territory.
    # Agent editing a seed that calls 2+ recently-changed functions is walking on thin ice.
    if depth == 0 and graph.hot_files:
        _hot_non_test = [
            c for c in callees
            if c.file_path in graph.hot_files and not _is_test_file(c.file_path)
        ]
        if len(_hot_non_test) >= 2:
            _names = [c.name for c in _hot_non_test[:3]]
            _suffix = "..." if len(_hot_non_test) > 3 else ""
            lines.append(
                f"{indent}  \u21b3 instability: {len(_hot_non_test)} hot callees ({', '.join(_names)}{_suffix})"
            )
    # S62: contract drift — seed file is STABLE (not in hot_files) but ≥2 of its callees' files ARE hot.
    # S52 fires for general hot-callee instability ("volatile territory — be careful while editing").
    # S62 fires for the DRIFT case specifically: you haven't touched this code in a while, but the
    # things it calls HAVE been updated — their interface may have changed without you knowing.
    # The recommended action is different: read callee changelogs BEFORE editing, not just while editing.
    # Guard: depth=0, seed file NOT in hot_files, ≥2 non-test callees from different hot files.
    if depth == 0 and graph.hot_files and sym.file_path not in graph.hot_files:
        _drift_callees = [
            c for c in callees
            if c.file_path in graph.hot_files
            and c.file_path != sym.file_path
            and not _is_test_file(c.file_path)
            and c.kind.value in ("function", "method")
        ]
        if len(_drift_callees) >= 3:
            _drift_names = [c.name for c in _drift_callees[:3]]
            _drift_suffix = "..." if len(_drift_callees) > 3 else ""
            lines.append(
                f"{indent}  \u21b3 drift risk: {len(_drift_callees)} callees updated while seed is stable"
                f" ({', '.join(_drift_names)}{_drift_suffix}) \u2014 verify contracts still match"
            )
    # S56: coverage gap summary — when seed is tested but ≥2 eligible callees have zero test callers.
    # Per-callee [untested] annotation already fires, but agents still need to count. This summary
    # makes the coverage gap immediately scannable: "6/8 callees untested" is alarming at a glance.
    if depth == 0 and _seed_is_tested:
        _eligible = [
            c for c in callees
            if c.kind.value in ("function", "method") and not _is_test_file(c.file_path)
        ]
        _untested_cov = [
            c for c in _eligible
            if not any(_is_test_file(cr.file_path) for cr in graph.callers_of(c.id))
        ]
        if len(_untested_cov) >= 2:
            _cov_names = [c.name for c in _untested_cov[:3]]
            _cov_suffix = "..." if len(_untested_cov) > 3 else ""
            lines.append(
                f"{indent}  \u21b3 coverage gap: {len(_untested_cov)}/{len(_eligible)} callees untested"
                f" ({', '.join(_cov_names)}{_cov_suffix})"
            )
    # S58: orphan cascade — detect sole-use callees that themselves own sole-use sub-callees.
    # A single [sole-use] marker per callee understates the refactor cascade when those callees
    # also have private helpers of their own. Show the TOTAL private chain depth.
    # Scan ALL callees (not just the 8 displayed) so the count reflects the true cascade.
    # Guard: depth=0 only, ≥2 transitive sole-use sub-callees (avoids trivial 1-level cases).
    if depth == 0 and _sole_use_callees:
        # Extend with any non-displayed callees that are also sole-use
        _shown_ids = {c.id for c in ordered_callees}
        for _c in callees:
            if _c.id in _shown_ids or _c.kind.value not in ("function", "method"):
                continue
            _pc = [cr for cr in graph.callers_of(_c.id) if not _is_test_file(cr.file_path)]
            if len(_pc) == 1 and _pc[0].id == sym.id:
                _sole_use_callees.append(_c)
    if depth == 0 and _sole_use_callees:
        _transitive_sole: list = []
        for _sc in _sole_use_callees:
            for _sub in graph.callees_of(_sc.id):
                if _sub.kind.value in ("function", "method") and not _is_test_file(_sub.file_path):
                    _sub_pc = [cr for cr in graph.callers_of(_sub.id) if not _is_test_file(cr.file_path)]
                    if len(_sub_pc) == 1 and _sub_pc[0].id == _sc.id:
                        _transitive_sole.append((_sc.name, _sub.name))
        if len(_transitive_sole) >= 2:
            _total_chain = len(_sole_use_callees) + len(_transitive_sole)
            # Show hub callee names — the sole-use callees that themselves own sub-callees
            _hub_names = list(dict.fromkeys(n for n, _ in _transitive_sole))[:3]
            _hub_suffix = "..." if len(_hub_names) < len(dict.fromkeys(n for n, _ in _transitive_sole)) else ""
            lines.append(
                f"{indent}  \u21b3 orphan cascade: {_total_chain} private helpers in chain"
                f" (via {', '.join(_hub_names)}{_hub_suffix}) \u2014 refactor ripples deeper than visible callees"
            )
    # S60: callee co-change coupling — when two callees live in files that frequently
    # co-change together in git history, touching one typically means touching both.
    # Agents assume callees are independent; this reveals hidden coupling between dependencies.
    # Guard: depth=0, ≥2 distinct non-test callee files (excluding seed's own file).
    if depth == 0 and graph.root:
        try:
            from ..git import cochange_matrix as _ccm
            _matrix = _ccm(graph.root)
            if _matrix:
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
                if _coupled:
                    _coupled.sort(key=lambda x: -x[2])
                    if len(_coupled) == 1:
                        _fa0, _fb0, _ = _coupled[0]
                        lines.append(
                            f"{indent}  \u21b3 callee coupling: {Path(_fa0).name} \u2194 {Path(_fb0).name}"
                            f" \u2014 often change together, check both"
                        )
                    elif len(_coupled) == 2:
                        _fa0, _fb0, _ = _coupled[0]
                        _fa1, _fb1, _ = _coupled[1]
                        lines.append(
                            f"{indent}  \u21b3 callee coupling: {Path(_fa0).name} \u2194 {Path(_fb0).name}"
                            f", {Path(_fa1).name} \u2194 {Path(_fb1).name}"
                        )
                    else:
                        _fa0, _fb0, _ = _coupled[0]
                        lines.append(
                            f"{indent}  \u21b3 callee coupling: {len(_coupled)} coupled pairs"
                            f" ({Path(_fa0).name} \u2194 {Path(_fb0).name} strongest)"
                        )
        except Exception:
            pass
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
    if token_count < max_tokens - 40:
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
            _tcov = "\nTests:\n" + "\n".join(f"  {_tfp} ({_tcount} caller{'s' if _tcount != 1 else ''})" for _tfp, _tcount in sorted(_test_callers.items()))
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
    if _seed_syms and token_count < max_tokens - 30:
        _prim368 = _seed_syms[0] if _seed_syms else None
        if _prim368:
            _generic_names368 = {
                "run", "execute", "process", "handle", "call", "invoke",
                "start", "stop", "init", "setup", "load", "save", "get", "set",
                "update", "delete", "create", "parse", "format", "validate",
            }
            if _prim368.name.lower() in _generic_names368:
                # Count how many symbols share this exact name
                _same_name368 = [
                    s for s in graph.symbols.values()
                    if s.name.lower() == _prim368.name.lower() and s.id != _prim368.id
                ]
                if _same_name368:
                    lines.append(
                        f"\ngeneric name: '{_prim368.name}' shared by {len(_same_name368) + 1} symbols"
                        f" — highly generic; refine to intent-revealing name to reduce search ambiguity"
                    )

    # S374: Deprecated symbol — focused symbol's name contains legacy/deprecated markers.
    # Symbols named with "old_", "legacy_", "deprecated_", "v1_", "_v1" signal known tech debt;
    # callers may not know about the newer alternative, causing ongoing use of deprecated paths.
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
# Signal group helper: fn_traits
# ---------------------------------------------------------------------------
def _signals_focused_fn_traits(
    graph: Tempo, *, _seed_syms: list, token_count: int, max_tokens: int,
) -> list[str]:
    """Focused-mode signals: fn_traits."""
    lines: list[str] = []
    # S198: Leaf function — the focused symbol calls nothing externally but has many callers.
    # Zero outgoing dependencies = very stable; many callers = widely relied upon. Positive signal.
    # Only shown when seed has 0 external callees AND >= 5 total callers.
    if _seed_syms and token_count < max_tokens - 30:
        _prim198 = _seed_syms[0]
        if _prim198.kind.value in ("function", "method"):
            _ext_callees198 = [
                c for c in graph.callees_of(_prim198.id)
                if c.file_path != _prim198.file_path
            ]
            _caller_count198 = len(graph.callers_of(_prim198.id))
            if len(_ext_callees198) == 0 and _caller_count198 >= 5:
                lines.append(
                    f"\nleaf function: {_prim198.name} has {_caller_count198} callers"
                    f" and 0 external callees — stable leaf, safe to refactor internals"
                )

    # S204: Async function — the focused symbol is declared with async.
    # Async fns require await at call sites; changes affect async context propagation.
    # Only shown when seed is a fn/method and 'async' appears in its signature.
    if _seed_syms and token_count < max_tokens - 30:
        _prim204 = _seed_syms[0]
        if _prim204.kind.value in ("function", "method"):
            _sig204 = _prim204.signature or ""
            if "async" in _sig204:
                lines.append(
                    f"\nasync fn: {_prim204.name} — callers must await,"
                    f" changes affect async context propagation"
                )

    # S214: Private symbol with external callers — symbol named with leading underscore
    # is called from other files, leaking an implementation detail into the public interface.
    # Only shown when seed starts with '_' (single) and has >= 1 external non-test caller.
    if _seed_syms and token_count < max_tokens - 30:
        _prim214 = _seed_syms[0]
        if _prim214.name.startswith("_") and not _prim214.name.startswith("__"):
            _ext_callers214 = [
                c for c in graph.callers_of(_prim214.id)
                if c.file_path != _prim214.file_path and not _is_test_file(c.file_path)
            ]
            if _ext_callers214:
                lines.append(
                    f"\nprivate symbol with external callers: {_prim214.name}"
                    f" called from {len(_ext_callers214)} external file(s)"
                    f" — underscore naming convention violated"
                )

    # S221: Recursive function — the focused symbol calls itself directly.
    # Recursive fns have loop invariants and base-case contracts that break non-obviously.
    # Only shown when seed is a fn/method that appears in its own callees.
    if _seed_syms and token_count < max_tokens - 30:
        _prim221 = _seed_syms[0]
        if _prim221.kind.value in ("function", "method"):
            _is_recursive221 = any(
                c.id == _prim221.id for c in graph.callees_of(_prim221.id)
            )
            if _is_recursive221:
                lines.append(
                    f"\nrecursive fn: {_prim221.name} calls itself"
                    f" — changes must preserve loop invariants and base cases"
                )

    # S244: Property accessor — focused symbol is a @property method.
    # Callers access it like an attribute (no parentheses); renaming or changing type is
    # a breaking change even if the source looks like a function change.
    if _seed_syms and token_count < max_tokens - 30:
        _prim244 = _seed_syms[0]
        if _prim244.kind.value == "method":
            _sig244 = _prim244.signature or ""
            _name244 = _prim244.name
            # Detect property: signature starts with "@property" or name matches Python/TS getter patterns
            _is_property = (
                "@property" in _sig244
                or _sig244.strip().startswith("@property")
                or (
                    _name244.startswith("get_") and "(" in _sig244
                    and "self" in _sig244
                    and _sig244.count(",") == 0  # no params other than self
                )
            )
            if _is_property:
                lines.append(
                    f"\nproperty accessor: {_name244} is accessed as an attribute"
                    f" — type or name changes break all usages silently"
                )

    # S249: Abstract method — focused symbol is abstract (must be implemented by subclasses).
    # Any signature change cascades to ALL concrete implementations — harder blast than normal.
    # Detection: @abstractmethod in signature/decorators, or body is just `raise NotImplementedError`.
    if _seed_syms and token_count < max_tokens - 30:
        _prim249 = _seed_syms[0]
        if _prim249.kind.value in ("function", "method"):
            _sig249 = _prim249.signature or ""
            _is_abstract = (
                "abstractmethod" in _sig249
                or "@abc.abstractmethod" in _sig249
            )
            if _is_abstract:
                # Count concrete implementations (subclasses that have same-named method)
                _prim249_name = _prim249.name
                _impl249 = [
                    s for s in graph.symbols.values()
                    if s.name == _prim249_name
                    and s.file_path != _prim249.file_path
                    and s.kind.value in ("function", "method")
                ]
                _n_impl249 = len(_impl249)
                lines.append(
                    f"\nabstract method: {_prim249_name} must be implemented by all subclasses"
                    + (f" — {_n_impl249} implementation(s) found" if _n_impl249 else "")
                    + " — signature changes cascade to all concrete classes"
                )


    return lines


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
        # S576: Empty class
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
    # S428: Abstract method
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

    # S720: Deprecated caller — a caller of the focused symbol has a deprecated name.
    # If active code is being called by deprecated functions, the focused symbol may be on a
    # removal path — callers marked old/legacy/deprecated signal incomplete migration.
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
    # S786: Dunder method focus — focused symbol is a dunder (special) method.
    # Dunder methods define Python protocol behavior (__init__, __call__, __iter__, etc.);
    # changing them affects how the object participates in Python's built-in operations.
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

    # S810: Deprecated symbol focus — focused symbol has a deprecation notice in its docstring.
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
    # S858: Abstract method focus — focused method lives in an abstract/base class.
    # Abstract methods define contracts that all subclasses must implement; changing their
    # signatures requires updating every concrete implementation in the hierarchy.
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

    # S894: Deprecated file focus — focused symbol is in a file marked as deprecated or legacy.
    # Files named with legacy/deprecated patterns often contain unmaintained code;
    # modifications may conflict with replacement implementations elsewhere.
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
    lines += _signals_fn_recursion(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_oop(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_signature(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_conventions(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_quality(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_focus_props_a(graph, _seed_syms, token_count, max_tokens)
    lines += _signals_fn_focus_props_b(graph, _seed_syms, token_count, max_tokens)
    return lines



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

    def _itf(fp: str) -> bool:
        return "/test" in fp or fp.startswith("test") or "tests/" in fp or fp.endswith("_test.py")

    caller_files: set[str] = set()
    hot_callees_n = 0
    hot_callers_n = 0
    cross_callees_total = 0
    untested_cross_callees = 0
    hot = graph.hot_files or set()

    for sym in seeds:
        sym_file = sym.file_path

        # Callers (non-test only)
        callers = [c for c in graph.callers_of(sym.id) if not _itf(c.file_path)]
        for c in callers:
            if c.file_path != sym_file:
                caller_files.add(c.file_path)
        if hot:
            hot_callers_n += sum(1 for c in callers if c.file_path in hot and c.file_path != sym_file)

        # Callees (cross-file, non-test)
        callees = [c for c in graph.callees_of(sym.id)
                   if c.file_path != sym_file and not _itf(c.file_path)]
        cross_callees_total += len(callees)
        if hot:
            hot_callees_n += sum(1 for c in callees if c.file_path in hot)
        for callee in callees:
            if not any(_itf(t.file_path) for t in graph.callers_of(callee.id)):
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
    _initial_depth = 4 if _hot_seeds else 3
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

    recent_section = _render_recent_changes_section(graph, seed_file_paths)
    if recent_section:
        lines.append(recent_section)
        token_count += count_tokens(recent_section)

    cochange_section = _render_cochange_section(graph, seed_file_paths)
    if cochange_section:
        lines.append(cochange_section)
        token_count += count_tokens(cochange_section)

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


def render_focused(graph: Tempo, query: str, *, max_tokens: int = 4000) -> str:
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
    _exposure = _compute_change_exposure(graph, seeds)
    if _exposure:
        lines.append(_exposure)
        lines.append("")
    _scope_note = _compute_bfs_scope_note(ordered)
    if _scope_note:
        lines.append(_scope_note)
        lines.append("")
    seen_files: set[str] = set()
    token_count = 0

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
        token_count += block_tokens
        seen_files.add(sym.file_path)

    _seed_syms = [sym for sym, d in ordered if d == 0]

    token_count = _render_context_sections(
        graph, lines=lines, ordered=ordered, seen_files=seen_files,
        seen_ids=seen_ids, token_count=token_count, max_tokens=max_tokens,
        _seed_syms=_seed_syms, _callsite_lines=_callsite_lines,
    )

    # --- Focused signal-group helpers ---
    lines.extend(_signals_focused_test(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_complexity(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_structure(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_class_hierarchy(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_class_patterns(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_coupling(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_naming(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_fn_traits(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_fn_patterns(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))
    lines.extend(_signals_focused_fn_advanced(
        graph, _seed_syms=_seed_syms, token_count=token_count, max_tokens=max_tokens,
    ))

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
