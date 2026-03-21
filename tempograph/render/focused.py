from __future__ import annotations

from pathlib import Path

from ..types import Tempo, EdgeKind, Symbol, SymbolKind
from ._utils import count_tokens, _is_test_file, _MONOLITH_THRESHOLD, _dead_code_confidence

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
    # Blast annotation for depth-0 seed: number of unique files that call this symbol.
    # Gives agents immediate risk context — "[blast: 7 files]" = 7 files need review.
    _blast_ann = ""
    _hub_ann = ""
    _age_ann = ""
    _doc_ann = ""
    _param_ann = ""
    _depth_from_entry_ann = ""
    _class_size_ann = ""
    if depth == 0:
        _blast_files = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}
        if len(_blast_files) >= 3:
            _blast_ann = f" [blast: {len(_blast_files)} files]"
        elif len(_blast_files) == 1:
            # Exactly 1 external caller file → tightly owned by that file.
            # Agents can safely change this without reviewing other files.
            _sole_file = next(iter(_blast_files))
            _blast_ann = f" [owned by: {_sole_file.rsplit('/', 1)[-1]}]"
        elif len(_blast_files) == 0 and sym.exported and sym.kind.value in ("function", "method", "class"):
            # Exported but no cross-file callers — likely a CLI/API entry point.
            # Agents need this to distinguish "safe to change, nothing calls it internally"
            # from a dead API (which would be non-exported with 0 callers).
            _blast_ann = " [entry point]"
        # Symbol-level age: when was this specific function last changed?
        # Uses git log -L for per-line precision; falls back to file-level.
        # Skipped for symbols changed < 8 days ago (not actionable — treat as "fresh").
        try:
            from ..git import symbol_last_modified_days as _sld  # noqa: PLC0415
            _days = _sld(graph.root, sym.file_path, sym.line_start)
            if _days is not None and _days >= 8:
                if _days >= 365:
                    _age_ann = " [age: 1y+]"
                elif _days >= 30:
                    _age_ann = f" [age: {_days // 30}m]"
                else:
                    _age_ann = f" [age: {_days}d]"
        except Exception:
            pass
    # Callee count annotation: if seed calls >= 5 distinct functions, show [calls: N].
    # High callee count signals broad side-effects — risky to change.
    _callee_ann = ""
    _depth_ann = ""
    _async_ann = ""
    _recursive_label = ""
    if depth == 0:
        _callee_ids = {
            e.target_id for e in graph.edges
            if e.kind == EdgeKind.CALLS and e.source_id == sym.id
        }
        if len(_callee_ids) >= 5:
            _callee_ann = f" [calls: {len(_callee_ids)}]"
        # Callee depth: longest forward call chain from seed.
        # Signals how far changes propagate — [callee depth: 4] means 4 levels of calls.
        # BFS capped at 60 nodes / depth 8 to avoid O(N²) on large graphs.
        _bfs_q: list[tuple[str, int]] = [(sym.id, 0)]
        _bfs_seen: set[str] = {sym.id}
        _max_callee_depth = 0
        while _bfs_q and len(_bfs_seen) < 60:
            _cur_id, _cur_lvl = _bfs_q.pop(0)
            if _cur_lvl > _max_callee_depth:
                _max_callee_depth = _cur_lvl
            if _cur_lvl >= 8:
                continue
            for _e in graph.edges:
                if _e.kind == EdgeKind.CALLS and _e.source_id == _cur_id and _e.target_id not in _bfs_seen:
                    _bfs_seen.add(_e.target_id)
                    _bfs_q.append((_e.target_id, _cur_lvl + 1))
        if _max_callee_depth >= 3:
            _depth_ann = f" [callee depth: {_max_callee_depth}]"
        # S81: Async annotation — mark async functions/methods in the focus header.
        # Agents working with async code need to track await chains and error propagation differently.
        if sym.kind.value in ("function", "method") and sym.signature.startswith("async "):
            _async_ann = " [async]"
        # S68: Undocumented annotation — exported fn with 3+ external caller files but no docstring.
        # Signals missing API docs on a widely-used symbol.
        _doc_ann = ""
        if sym.exported and not sym.doc and sym.kind.value in ("function", "method"):
            _ext_caller_files = {c.file_path for c in graph.callers_of(sym.id) if c.file_path != sym.file_path}
            if len(_ext_caller_files) >= 3:
                _doc_ann = " [undocumented]"
        # S71: Parameter count annotation — functions with >= 5 params are hard to call/mock.
        # Counts top-level commas inside the first (...) of the signature.
        _param_ann = ""
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
                    _pcount = _pc + 1
                    if _pcount >= 5:
                        _param_ann = f" [params: {_pcount}]"
        # S79: Class size annotation — for CLASS seeds, show [methods: N] in header.
        # Helps agents assess class complexity before reading; large classes need grep not read.
        _class_size_ann = ""
        if sym.kind.value in ("class", "interface", "component"):
            _children = graph.children_of(sym.id)
            _methods = [c for c in _children if c.kind.value in ("method", "function")]
            _props = [c for c in _children if c.kind.value in ("field", "property", "variable")]
            if len(_methods) >= 5:
                _class_size_ann = f" [methods: {len(_methods)}]"
                if _props:
                    _class_size_ann += f"[props: {len(_props)}]"
        # S75: Import depth from entry point — BFS backwards through import graph
        # to find the shortest path from any known entry file to the seed's file.
        # [depth: N] tells agents how deeply nested this file is; N>=4 = hard to trace.
        _depth_from_entry_ann = ""
        _FOCUS_ENTRY_NAMES = {
            "__main__.py", "main.py", "app.py", "manage.py", "cli.py",
            "server.py", "wsgi.py", "asgi.py", "run.py", "index.js",
            "index.ts", "index.tsx", "main.ts", "main.tsx", "main.go",
        }
        _entry_fps = {fp for fp in graph.files if fp.rsplit("/", 1)[-1] in _FOCUS_ENTRY_NAMES}
        if _entry_fps and sym.file_path not in _entry_fps:
            # Build import adjacency (target → importers) for reverse BFS
            _rev_adj: dict[str, list[str]] = {}
            for _e in graph.edges:
                if _e.kind == EdgeKind.IMPORTS and _e.source_id in graph.files and _e.target_id in graph.files:
                    _rev_adj.setdefault(_e.target_id, []).append(_e.source_id)
            # BFS backwards from seed's file toward entry files
            _bfs_imp: list[tuple[str, int]] = [(sym.file_path, 0)]
            _seen_imp: set[str] = {sym.file_path}
            _found_depth: int | None = None
            while _bfs_imp and _found_depth is None:
                _cur_fp, _cur_d = _bfs_imp.pop(0)
                if _cur_d >= 8:
                    continue
                for _imp in _rev_adj.get(_cur_fp, []):
                    if _imp in _entry_fps:
                        _found_depth = _cur_d + 1
                        break
                    if _imp not in _seen_imp and len(_seen_imp) < 80:
                        _seen_imp.add(_imp)
                        _bfs_imp.append((_imp, _cur_d + 1))
            if _found_depth is not None and _found_depth >= 4:
                _depth_from_entry_ann = f" [depth: {_found_depth}]"
        # Recursion detection: self-recursion or mutual recursion via direct callees.
        # Recursive functions need care before memoizing, splitting, or inlining.
        if sym.kind.value in ("function", "method"):
            _seed_callee_ids = {
                e.target_id for e in graph.edges
                if e.kind == EdgeKind.CALLS and e.source_id == sym.id
            }
            if sym.id in _seed_callee_ids:
                # Direct self-call — e.g. fibonacci(n-1)
                _recursive_label = "[recursive]"
            else:
                # Mutual recursion: callee calls back to seed
                _mutual_partner: str | None = None
                for _callee_s in graph.callees_of(sym.id)[:10]:
                    _callee_callees = {
                        e.target_id for e in graph.edges
                        if e.kind == EdgeKind.CALLS and e.source_id == _callee_s.id
                    }
                    if sym.id in _callee_callees:
                        _mutual_partner = _callee_s.name
                        break
                _recursive_label = (
                    f"[recursive: mutual with {_mutual_partner}]" if _mutual_partner else ""
                )
        else:
            _recursive_label = ""
    elif depth >= 1:
        # Hub annotation: deeply-imported utilities used across 15+ files.
        # Tells agents this is a widely-shared symbol — don't expect to find
        # all its callers in the focus output (BFS suppresses their expansion).
        _hub_caller_files = {
            c.file_path for c in graph.callers_of(sym.id)
            if c.file_path != sym.file_path
        }
        if len(_hub_caller_files) >= 15:
            _hub_ann = f" [hub: {len(_hub_caller_files)} files]"
    block_lines = [f"{prefix} {sym.kind.value} {sym.qualified_name}{_blast_ann}{_hub_ann}{_age_ann}{_callee_ann}{_depth_ann}{_async_ann}{_doc_ann}{_param_ann}{_depth_from_entry_ann}{_class_size_ann} — {loc}{orbit_note}"]
    # S61: "also in:" — warn when same symbol name exists in other files.
    # Prevents agents from fixing the wrong copy in multi-file refactors.
    if depth == 0:
        _dupes = [s for s in graph.find_symbol(sym.name) if s.id != sym.id and not _is_test_file(s.file_path)]
        if _dupes:
            _dupe_strs = [f"{s.file_path.rsplit('/', 1)[-1]}:{s.line_start}" for s in _dupes[:3]]
            block_lines.append(f"{indent}  also in: {', '.join(_dupe_strs)}")
    # S83: "implements:" — show parent classes/interfaces for class seeds via INHERITS edges.
    # Tells agents about the class hierarchy before they look at the call graph.
    if depth == 0 and sym.kind.value in ("class", "interface", "struct"):
        _parent_ids = [
            e.target_id for e in graph.edges
            if e.kind == EdgeKind.INHERITS and e.source_id == sym.id
        ]
        _parents = [graph.symbols[pid].name for pid in _parent_ids if pid in graph.symbols]
        if not _parents:
            # Fallback: bare target_id for unresolved parent names (no "::" = likely a simple name)
            _parents = [
                e.target_id for e in graph.edges
                if e.kind == EdgeKind.INHERITS and e.source_id == sym.id
                and "::" not in e.target_id
            ]
        if _parents:
            block_lines.append(f"{indent}  implements: {', '.join(_parents[:4])}")
    # Recursion annotation: emit [recursive] or [recursive: mutual with X] as sub-line.
    if depth == 0 and _recursive_label:
        block_lines.append(f"{indent}  {_recursive_label}")
    # Test coverage hint: show which test file(s) directly call this symbol.
    # If exported and no test callers → warn agents there's no safety net.
    # Only shown for functions/methods; skipped for classes/modules/constants.
    if depth == 0 and sym.kind.value in ("function", "method"):
        _all_callers = graph.callers_of(sym.id)
        _test_callers = [c for c in _all_callers if _is_test_file(c.file_path)]
        if _test_callers:
            _t_files = sorted({c.file_path.rsplit("/", 1)[-1] for c in _test_callers})
            block_lines.append(f"{indent}  tested: {', '.join(_t_files[:3])}")
            # S81: Show test scenario names — what cases are already covered.
            # Agents use this to avoid writing duplicate tests or spotting gaps.
            _scenario_names = sorted({
                c.name for c in _test_callers
                if c.name.startswith("test_") and c.kind.value in ("function", "method", "test")
            })
            if _scenario_names:
                _sc_str = ", ".join(_scenario_names[:3])
                if len(_scenario_names) > 3:
                    _sc_str += f" +{len(_scenario_names) - 3} more"
                block_lines.append(f"{indent}  scenarios: {_sc_str}")
        elif sym.exported:
            block_lines.append(f"{indent}  no tests — exported but never called from a test file")
    # S85: Caller coverage — what fraction of this symbol's callers have test coverage?
    # Key safety signal for change risk: 30 callers with only 5 tested = high blast risk.
    # Only shown for depth-0 functions/methods with 5+ non-test callers.
    if depth == 0 and sym.kind.value in ("function", "method"):
        _all_src_callers = [
            c for c in graph.callers_of(sym.id)
            if not _is_test_file(c.file_path) and c.file_path != sym.file_path
        ]
        if len(_all_src_callers) >= 3:
            _tested_callers = [
                c for c in _all_src_callers
                if any(_is_test_file(t.file_path) for t in graph.callers_of(c.id))
            ]
            _cc_pct = int(len(_tested_callers) / len(_all_src_callers) * 100)
            block_lines.append(
                f"{indent}  caller coverage: {len(_tested_callers)}/{len(_all_src_callers)} callers tested ({_cc_pct}%)"
            )
    # Container annotation for methods: show parent class with caller count.
    # Helps agents understand the class context of the focused method.
    if depth == 0 and sym.kind.value == "method" and "::" in sym.id:
        _name_part = sym.id.split("::", 1)[1]
        if "." in _name_part:
            _class_id = sym.id.rsplit(".", 1)[0]  # "file.py::ClassName"
            _class_sym = graph.symbols.get(_class_id)
            if _class_sym:
                _c_callers = len(graph.callers_of(_class_id))
                _c_methods = len([c for c in graph.children_of(_class_id) if c.kind.value == "method"])
                _c_ann = f"{_c_callers} callers" if _c_callers else "no callers"
                block_lines.append(f"{indent}  container: {_class_sym.kind.value} {_class_sym.name} ({_c_ann}, {_c_methods} methods)")
    # S81: Sibling hot annotation — other hot functions (3+ cross-file callers) in same file.
    # When multiple sibling functions are hot, agents know the whole file is a hotspot.
    if depth == 0 and sym.kind.value in ("function", "method") and sym.file_path in graph.files:
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
            block_lines.append(f"{indent}  also hot: {', '.join(_hs_strs)}")
    # Recent commit messages: last 2 commits that touched the seed symbol's file.
    # Gives agents instant "why was this last changed" context without running git log.
    if depth == 0 and graph.root:
        try:
            from ..git import recent_file_commits as _rfc  # noqa: PLC0415
            _commits = _rfc(graph.root, sym.file_path, n=2)
            if _commits:
                _commit_parts = [f"{c['days_ago']}d \"{c['message']}\"" for c in _commits]
                block_lines.append(f"{indent}  recent: {', '.join(_commit_parts)}")
        except Exception:
            pass
    # Callee drift: seed is >=30d old but calls things changed in the last 14d.
    # Flags potential "stale wrapper" — function may not reflect its dependency changes.
    # Uses file-level age for callees (fast — no per-line git log overhead).
    if depth == 0 and graph.root:
        try:
            from ..git import symbol_last_modified_days as _sld_cd  # noqa: PLC0415
            from ..git import file_last_modified_days as _fld_cd    # noqa: PLC0415
            _seed_days = _sld_cd(graph.root, sym.file_path, sym.line_start)
            if _seed_days is not None and _seed_days >= 30:
                _callees_cd = graph.callees_of(sym.id)
                _drifted: list[tuple[int, str]] = []
                for _c in _callees_cd[:15]:  # cap to avoid subprocess spam
                    if _c.file_path == sym.file_path:
                        continue  # same-file callees usually updated together
                    if _c.file_path not in staleness_cache:
                        staleness_cache[_c.file_path] = _fld_cd(graph.root, _c.file_path)
                    _c_days = staleness_cache[_c.file_path]
                    if _c_days is not None and _c_days < 14:
                        _drifted.append((_c_days, _c.name))
                if _drifted:
                    _drifted.sort()  # most recently changed first
                    _drift_strs = [f"{n} ({d}d)" for d, n in _drifted[:3]]
                    _drift_overflow = f" +{len(_drifted) - 3} more" if len(_drifted) > 3 else ""
                    block_lines.append(
                        f"{indent}  ⚠ callee drift: {len(_drifted)} dep(s) changed after your last edit"
                        f" — {', '.join(_drift_strs)}{_drift_overflow}"
                    )
        except Exception:
            pass
    # Co-change buddy: which file most frequently appears in commits with the seed's file?
    # Warns agents they'll likely need to update that file too when modifying the seed.
    # Only shown for git repos with enough history. One file only (avoid noise).
    if depth == 0 and graph.root:
        try:
            from ..git import cochange_pairs as _ccp  # noqa: PLC0415
            _buddies = _ccp(graph.root, sym.file_path, n=1, min_count=4)
            if _buddies:
                _buddy = _buddies[0]
                _buddy_fp = _buddy["path"]
                if _buddy_fp in graph.files and not _is_test_file(_buddy_fp):
                    _buddy_name = _buddy_fp.rsplit("/", 1)[-1]
                    block_lines.append(
                        f"{indent}  co-changes with: {_buddy_name} ({_buddy['count']}x)"
                    )
        except Exception:
            pass
    # Inline TODO/FIXME scanner: scan the focused function's source lines for
    # open issues. Agents making changes NEED to see these — don't let them
    # implement something that's already flagged as broken or incomplete.
    if depth == 0 and sym.kind.value in ("function", "method") and graph.root:
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
                    block_lines.append(f"{indent}  {_tag.lower()}: L{_lineno}{_suffix}")
        except Exception:
            pass
    # S82: Side-effect scanner — detect I/O patterns in the function body.
    # Pure functions are safest to refactor; DB/file/network functions need more care.
    # Regex-based, no AST needed; scans the function's source lines.
    if depth == 0 and sym.kind.value in ("function", "method") and graph.root:
        try:
            import os as _os2, re as _re2  # noqa: PLC0415
            _fp2 = _os2.path.join(graph.root, sym.file_path)
            if _os2.path.isfile(_fp2):
                with open(_fp2, encoding="utf-8", errors="replace") as _fh2:
                    _body = "".join(_fh2.readlines()[sym.line_start - 1:sym.line_end])
                _effects: list[str] = []
                # DB: SQL queries, ORM calls, cursor operations
                if _re2.search(r'(execute|cursor|session[.]query|db[.]|[.]save[(]|[.]commit[(]|SELECT|INSERT|UPDATE|DELETE)', _body, _re2.IGNORECASE):
                    _effects.append("db")
                # File I/O
                if _re2.search(r'(open[(]|write[(]|read[(]|os[.]path|shutil[.]|pathlib|json[.]dump|json[.]load|yaml[.])', _body):
                    _effects.append("file")
                # Network / HTTP
                if _re2.search(r'(requests[.]|httpx[.]|aiohttp[.]|urllib[.]|fetch[(]|http[.]|socket[.]|grpc[.])', _body):
                    _effects.append("network")
                # Subprocess / shell
                if _re2.search(r'(subprocess[.]|os[.]system[(]|os[.]popen[(]|Popen[(])', _body):
                    _effects.append("subprocess")
                # Mutation of shared state (class attributes, globals)
                if _re2.search(r'self[.]\w+\s*=(?!=)', _body) or _re2.search(r'\bglobal\b', _body):
                    _effects.append("mutates state")
                if _effects:
                    block_lines.append(f"{indent}  effects: {', '.join(_effects)}")
                # S85: Throws detection — find explicit `raise` statements in the function body.
                # Tells agents what exceptions callers must handle; critical for error-path coverage.
                # Detects `raise ExceptionType`, `raise ExceptionType(...)`, and `raise some_var` patterns.
                _raise_pat = _re2.compile(r'\braise\s+([A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*)*)')
                _exc_names = list(dict.fromkeys(_raise_pat.findall(_body)))  # dedup, preserve order
                # Filter out noise: re-raises (`raise` alone) and overly common bases
                _exc_names = [e for e in _exc_names if e not in ("Exception", "BaseException")][:4]
                if _exc_names:
                    block_lines.append(f"{indent}  throws: {', '.join(_exc_names)}")
        except Exception:
            pass

    # Callee chain: show the first-hop cross-file callees for depth-0 functions.
    # Helps agents trace execution flow without reading all callee source files.
    # Shows "callee chain: process → parse → tokenize" (seed → callee → callee's callee).
    # Uses file-level CALLS index (source_id = file path) to find callees.
    if depth == 0 and sym.kind.value in ("function", "method"):
        _file_callees = [
            c for c in graph.callees_of(sym.file_path)
            if c.file_path != sym.file_path  # cross-file only
        ]
        if 1 <= len(_file_callees) <= 4:
            _chain_parts = [sym.name, _file_callees[0].name]
            # Add second hop: first cross-file callee of the first callee
            _c1 = _file_callees[0]
            _c1_callees = [
                c for c in graph.callees_of(_c1.file_path)
                if c.file_path != _c1.file_path
            ]
            if _c1_callees:
                _chain_parts.append(_c1_callees[0].name)
            block_lines.append(f"{indent}  callee chain: {' → '.join(_chain_parts)}")

    if sym.signature and depth < 2:
        block_lines.append(f"{indent}  sig: {sym.signature[:150]}")
    if sym.doc and depth == 0:
        block_lines.append(f"{indent}  doc: {sym.doc}")
    # File siblings: other symbols defined in the same file at depth 0.
    # Gives agents immediate context about what surrounds this symbol
    # without requiring a separate file read.
    if depth == 0:
        _siblings = [
            s for s in graph.symbols.values()
            if s.file_path == sym.file_path and s.id != sym.id
            and s.kind.value in ("class", "function", "method", "interface", "module")
            and s.parent_id is None  # top-level only
        ]
        if len(_siblings) >= 2:
            # Prioritize: classes first, then functions/methods
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
            block_lines.append(
                f"{indent}  in this file: {len(_siblings)} others ({_sib_str})"
            )
    if depth <= 1:
        warnings = []
        if sym.line_count > 500:
            warnings.append(f"LARGE ({sym.line_count} lines — use grep, don't read)")
        if sym.complexity > 50:
            # Show complexity relative to file average for functions/methods
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
                block_lines.append(f"{indent}  [likely entry point — wired externally, not dead]")
            elif not sym.exported and _dead_code_confidence(sym, graph) >= 40:
                warnings.append("POSSIBLY DEAD — 0 callers, not exported (run dead_code mode to confirm)")
        # Test-only callers: symbol has callers but ALL are test files.
        # Production code never calls this — likely test helper or fixture, not real API.
        if depth == 0:
            _callers = graph.callers_of(sym.id)
            if len(_callers) >= 2 and all(_is_test_file(c.file_path) for c in _callers):
                warnings.append("TEST-ONLY CALLERS — not called from production code")
        # Circular import: if the seed's file is in a circular import chain, flag it.
        # Agents need to know this to avoid making the cycle worse or getting confused
        # about why re-imports behave unexpectedly.
        if depth == 0 and graph.root:
            _cycles = graph.detect_circular_imports()
            for _cycle in _cycles:
                if sym.file_path in _cycle:
                    _names = [fp.rsplit("/", 1)[-1] for fp in _cycle]
                    warnings.append(f"CIRCULAR IMPORT — {' → '.join(_names)}")
                    break
        if warnings:
            block_lines.append(f"{indent}  ⚠ {', '.join(warnings)}")

        def _caller_priority(c: "Symbol") -> int:
            path_lower = c.file_path.lower()
            return 0 if query_tokens and any(tok in path_lower for tok in query_tokens) else 1

        from ..git import file_last_modified_days as _fld  # noqa: PLC0415

        def _stale_annotation(file_path: str) -> str:
            if file_path not in staleness_cache:
                staleness_cache[file_path] = _fld(graph.root, file_path)
            days = staleness_cache[file_path]
            if days is None or days <= 30:
                return ""
            if days > 180:
                return " [stale: 6m+]"
            return f" [stale: {days}d]"

        callers = graph.callers_of(sym.id)
        if callers:
            # S118: For depth-0 seeds, exclude test callers from inline 'called by:'.
            # Test callers are already shown in 'tested:' and 'scenarios:' lines above.
            # Filtering them reveals production callers immediately, reduces noise.
            _callers_for_display = (
                [c for c in callers if not _is_test_file(c.file_path)]
                if depth == 0
                else callers
            )
            callers_sorted = sorted(_callers_for_display, key=_caller_priority)
            kw_callers = [c for c in callers_sorted if _caller_priority(c) == 0]
            other_callers = [c for c in callers_sorted if _caller_priority(c) != 0]
            hot_other = [c for c in other_callers if c.file_path in graph.hot_files]
            cold_other = [c for c in other_callers if c.file_path not in graph.hot_files]
            max_other = 3 if kw_callers else (8 if depth == 0 else 5)
            shown_other = (hot_other + cold_other)[:max_other]
            shown_callers = kw_callers + shown_other
            shown_count = len(kw_callers) + max_other
            _total_for_overflow = len(_callers_for_display)
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
                    caller_strs.append(c.qualified_name + _line_ann + _stale_annotation(c.file_path))
            if caller_strs:
                block_lines.append(f"{indent}  called by: {', '.join(caller_strs)}")
                if _total_for_overflow > shown_count:
                    block_lines[-1] += f" (+{_total_for_overflow - shown_count} more)"
        callees = graph.callees_of(sym.id)
        if callees:
            shown = 8 if depth == 0 else 5
            hot_callees = [c for c in callees if c.file_path in graph.hot_files]
            cold_callees = [c for c in callees if c.file_path not in graph.hot_files]
            ordered_callees = (hot_callees + cold_callees)[:shown]
            callee_strs = []
            for c in ordered_callees:
                _hot_ann = " [hot]" if c.file_path in graph.hot_files else ""
                _cb_ann = ""
                if depth == 0:
                    _cb_files = len({
                        cr.file_path for cr in graph.callers_of(c.id)
                        if cr.file_path != c.file_path
                    })
                    if _cb_files >= 3:
                        _cb_ann = f" [blast: {_cb_files}]"
                callee_strs.append(f"{c.qualified_name}{_hot_ann}{_cb_ann}")
            block_lines.append(f"{indent}  calls: {', '.join(callee_strs)}")
            if len(callees) > shown:
                block_lines[-1] += f" (+{len(callees) - shown} more)"
        if depth == 0:
            children = graph.children_of(sym.id)
            if children:
                _child_strs = []
                for c in children[:10]:
                    _c_callers = len(graph.callers_of(c.id))
                    _c_ann = f" ({_c_callers})" if _c_callers >= 1 else ""
                    _child_strs.append(f"{c.kind.value[:4]} {c.name}{_c_ann}")
                block_lines.append(f"{indent}  contains: {', '.join(_child_strs)}")

            # Implementors: classes/traits that extend or implement this symbol.
            # Shown only for CLASS/INTERFACE seeds to surface the inheritance fanout.
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
                    block_lines.append(_sub_line)

            # Similar functions: other functions sharing ≥2 callees with this seed.
            # Helps agents find related implementations that may need parallel changes.
            if sym.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                # Exclude class/type constructors — ubiquitous and create false positives
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
                        block_lines.append(f"{indent}  similar: {', '.join(_sim_strs)}")
    return block_lines


def render_focused(graph: Tempo, query: str, *, max_tokens: int = 4000) -> str:
    """Task-focused rendering with BFS graph traversal.
    Starts from search results, then follows call/render/import edges
    to build a connected subgraph relevant to the query.

    For monolith files (>1000 lines), adds intra-file neighborhood context
    and biases BFS toward cross-file edges to avoid getting trapped in one file.

    Supports multi-symbol focus via '|' separator: "authMiddleware | loginHandler"
    merges seeds from each query and runs a single combined BFS."""
    # Multi-symbol: split on '|', collect seeds for each query, merge results.
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
            return _suggest_alternatives(graph, query) or f"No symbols matching '{query}'"
    else:
        seeds, seed_files, query_tokens = _collect_seeds(graph, query)
        if not seeds:
            return _suggest_alternatives(graph, query) or f"No symbols matching '{query}'"

    # Orbit-BFS seeding: inject symbols from git-coupled files as depth-1 seeds.
    # Call graph misses test files (they call INTO src, not tracked as callers).
    # Git orbit catches those — if tests always change with render.py, seed them too.
    orbit_seed_meta: dict[str, tuple[str, float]] = {}  # sym.id → (orbit_file, coupling_freq)
    orbit_secondary: list[Symbol] = []
    if graph.root:
        _primary_fps = [s.file_path for s in seeds]
        _primary_fp_set = {s.file_path for s in seeds}
        _orbit_pairs = _cochange_orbit(graph.root, _primary_fps, _primary_fp_set, n=3)
        for sym, freq in _find_orbit_seeds(graph, query_tokens, _orbit_pairs):
            orbit_secondary.append(sym)
            orbit_seed_meta[sym.id] = (sym.file_path, freq)

    # Determine initial BFS depth: 4 for hot seeds, 3 otherwise.
    _hot_seeds = any(s.file_path in graph.hot_files for s in seeds)
    _initial_depth = 4 if _hot_seeds else 3
    ordered, seen_ids = _bfs_expand(
        graph, seeds, seed_files, secondary_seeds=orbit_secondary or None,
        max_depth=_initial_depth,
    )

    # Sparse-neighborhood adaptive expansion: if BFS returned fewer than 20 nodes
    # and didn't saturate the 50-node cap, the neighborhood is small enough that
    # going one level deeper costs little and gives agents genuine extra context.
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

    _focus_header = f"Focus: {' | '.join(_parts)}" if len(_parts) > 1 else f"Focus: {query}"
    if _depth_extended:
        _focus_header += f"  [depth +1 — sparse ({len(ordered)} nodes)]"
    lines = [_focus_header, ""]
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

    # File context: for each file touched, show key co-located symbols
    ctx_block, ctx_tokens = _render_file_context_section(graph, seen_files, seen_ids, token_count, max_tokens)
    if ctx_block:
        lines.append(ctx_block)
        token_count += ctx_tokens

    # Monolith neighborhood: for seed symbols in large files, show nearby symbols
    mono_block, mono_tokens = _render_monolith_section(graph, ordered, token_count, max_tokens)
    if mono_block:
        lines.append(mono_block)
        token_count += mono_tokens

    # Related files with size warnings
    related_section = _render_related_files_section(graph, ordered, seen_files)
    if related_section:
        lines.append(related_section)
        token_count += count_tokens(related_section)

    # Blast risk badge: count unique downstream files for seed symbols.
    blast_section = _render_blast_risk_section(graph, ordered, token_count, max_tokens)
    if blast_section:
        lines.append(blast_section)
        token_count += count_tokens(blast_section)

    # Co-change orbit: git history reveals which files change together with seed files.
    seed_file_paths = [s.file_path for s, d in ordered if d == 0]
    orbit_section = _render_cochange_orbit_section(graph, seed_file_paths, seen_files, token_count, max_tokens)
    if orbit_section:
        lines.append(orbit_section)
        token_count += count_tokens(orbit_section)

    # File volatility: flag seed files that are actively changing.
    volatile_section = _render_volatility_section(graph, seed_file_paths, token_count, max_tokens)
    if volatile_section:
        lines.append(volatile_section)
        token_count += count_tokens(volatile_section)

    # Recent changes: show last 3 commits for the primary seed file.
    recent_section = _render_recent_changes_section(graph, seed_file_paths)
    if recent_section:
        lines.append(recent_section)
        token_count += count_tokens(recent_section)

    # Co-change suggestions: which source files historically move with the primary file?
    cochange_section = _render_cochange_section(graph, seed_file_paths)
    if cochange_section:
        lines.append(cochange_section)
        token_count += count_tokens(cochange_section)

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
    _seed_syms = [sym for sym, d in ordered if d == 0]
    callers_section = _render_all_callers_section(graph, _seed_syms, _callsite_lines, token_count, max_tokens)
    if callers_section:
        lines.append(callers_section)
        token_count += count_tokens(callers_section)

    # Outgoing dependency files: what files do the seed symbols depend on?
    dep_section = _render_dependency_files_section(graph, ordered, seen_files, token_count, max_tokens)
    if dep_section:
        lines.append(dep_section)
        token_count += count_tokens(dep_section)

    # Hot callers: callers of seed symbols that live in recently-modified files.
    hot_section = _render_hot_callers_section(graph, _seed_syms, token_count, max_tokens)
    if hot_section:
        lines.append(hot_section)
        token_count += count_tokens(hot_section)

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

    # S186: Cross-file callee — the focused symbol calls functions in 3+ distinct external files.
    # Reaching out to many files means this fn is a coordination point; changes ripple widely.
    # Only shown when seed is a fn/method with callees in 3+ different files.
    if _seed_syms and token_count < max_tokens - 30:
        _prim186 = _seed_syms[0]
        if _prim186.kind.value in ("function", "method"):
            _callee_files186 = {
                c.file_path for c in graph.callees_of(_prim186.id)
                if c.file_path != _prim186.file_path
            }
            if len(_callee_files186) >= 3:
                _cf_names186 = [fp.rsplit("/", 1)[-1] for fp in sorted(_callee_files186)[:3]]
                _cf_str186 = ", ".join(_cf_names186)
                if len(_callee_files186) > 3:
                    _cf_str186 += f" +{len(_callee_files186) - 3} more"
                lines.append(
                    f"\ncross-file callee: {_prim186.name} calls into {len(_callee_files186)} files"
                    f" ({_cf_str186}) — coordination fn, changes ripple to many modules"
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

    # S210: Cochange partners outside static graph — files that co-change with the seed
    # file in git history but have NO import/call edge to it (hidden coupling).
    # Git history catches runtime coupling, config coupling, and test fixture coupling
    # that static analysis misses entirely.
    # Only shown when 2+ such hidden co-editors exist with >= 3 co-changes each.
    if _seed_syms and graph.root and token_count < max_tokens - 30:
        try:
            from ..git import cochange_pairs as _cp210, is_git_repo as _igr210
            from ..types import EdgeKind as _EK210
            if _igr210(graph.root):
                _seed_fp210 = _seed_syms[0].file_path
                # Files connected via any static edge to the seed file
                _static_neighbors210: set[str] = set()
                for _e210 in graph.edges:
                    if _e210.kind in (_EK210.CALLS, _EK210.IMPORTS):
                        _src210 = _e210.source_id.split("::")[0]
                        _tgt210 = _e210.target_id.split("::")[0]
                        if _src210 == _seed_fp210:
                            _static_neighbors210.add(_tgt210)
                        elif _tgt210 == _seed_fp210:
                            _static_neighbors210.add(_src210)
                _pairs210 = _cp210(graph.root, _seed_fp210, n=10)
                _hidden210 = [
                    p for p in _pairs210
                    if p["path"] not in _static_neighbors210
                    and p["path"] != _seed_fp210
                    and not _is_test_file(p["path"])
                    and p["count"] >= 3
                ]
                if len(_hidden210) >= 2:
                    _h210_names = [p["path"].rsplit("/", 1)[-1] for p in _hidden210[:3]]
                    _h210_str = ", ".join(_h210_names)
                    if len(_hidden210) > 3:
                        _h210_str += f" +{len(_hidden210) - 3} more"
                    lines.append(
                        f"\ncochange partners (not in call graph): {_h210_str}"
                        f" — co-edit history suggests hidden coupling"
                    )
        except Exception:
            pass

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


    # S266: Circular call — focused symbol and one of its callees also call back to it.
    # Circular calls create hidden coupling and make execution order unpredictable;
    # they can cause infinite loops under certain conditions.
    if _seed_syms and token_count < max_tokens - 30:
        _prim260 = _seed_syms[0]
        if _prim260.kind.value in ("function", "method"):
            _callers260 = {c.id for c in graph.callers_of(_prim260.id)}
            _callees260 = {c.id for c in graph.callees_of(_prim260.id)}
            _mutual260 = _callers260 & _callees260
            if _mutual260:
                _mutual_name260 = next(
                    (graph.symbols[sid].name for sid in _mutual260 if sid in graph.symbols),
                    None
                )
                if _mutual_name260:
                    lines.append(
                        f"\ncircular call: {_prim260.name} ↔ {_mutual_name260} call each other"
                        f" — mutual dependency; changes must maintain protocol on both sides"
                    )


    # S272: High callee fan-out — focused function calls 5+ distinct external functions.
    # High fan-out increases coupling surface: changes to any callee may ripple back.
    # Also makes the function harder to test in isolation (many dependencies to mock).
    if _seed_syms and token_count < max_tokens - 30:
        _prim272 = _seed_syms[0]
        if _prim272.kind.value in ("function", "method"):
            _callees272 = [
                c for c in graph.callees_of(_prim272.id)
                if c.file_path != _prim272.file_path
            ]
            _unique272 = {c.name for c in _callees272}
            if len(_unique272) >= 5:
                lines.append(
                    f"\nhigh fan-out: {_prim272.name} calls {len(_unique272)} distinct external fns"
                    f" — many dependencies; consider dependency injection for testability"
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

    # S281: Undocumented public function — exported fn/method with 3+ callers has no docstring.
    # Public functions without documentation create maintenance risk; callers must infer
    # behavior from implementation, making changes more dangerous.
    if _seed_syms and token_count < max_tokens - 30:
        _prim281 = _seed_syms[0]
        if (
            _prim281.kind.value in ("function", "method")
            and _prim281.exported
            and not _is_test_file(_prim281.file_path)
        ):
            _sig281 = _prim281.signature or ""
            _has_doc281 = '"""' in _sig281 or "'''" in _sig281
            if not _has_doc281:
                _ext_callers281 = [
                    c for c in graph.callers_of(_prim281.id)
                    if c.file_path != _prim281.file_path
                ]
                if len(_ext_callers281) >= 3:
                    lines.append(
                        f"\nundocumented: {_prim281.name} is public with {len(_ext_callers281)} callers"
                        f" but has no docstring — callers must infer behavior from code"
                    )


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

    # S309: Re-exported symbol — focused symbol is also exported from an __init__ or index file.
    # Re-exported symbols have two blast radii: direct imports from the definition file
    # and indirect imports via the facade/index module.
    if _seed_syms and token_count < max_tokens - 30:
        _prim309 = _seed_syms[0]
        if _prim309.exported:
            _reexport309 = [
                s for s in graph.symbols.values()
                if s.name == _prim309.name
                and s.file_path != _prim309.file_path
                and s.exported
                and (
                    s.file_path.endswith("__init__.py")
                    or s.file_path.rsplit("/", 1)[-1].startswith("index.")
                )
            ]
            if _reexport309:
                _facade_name309 = _reexport309[0].file_path.rsplit("/", 1)[-1]
                lines.append(
                    f"\nre-exported: {_prim309.name} also exported from {_facade_name309}"
                    f" — dual blast radius; importers of the facade are also affected"
                )

    # S314: High caller count — focused symbol is called from 10+ distinct files.
    # Symbols with very high caller counts are de-facto stable APIs;
    # even minor behavior changes (not just signatures) can break unknown callers.
    if _seed_syms and token_count < max_tokens - 30:
        _prim314 = _seed_syms[0]
        _callers314 = graph.callers_of(_prim314.id)
        _caller_files314 = {c.file_path for c in _callers314 if c.file_path != _prim314.file_path}
        if len(_caller_files314) >= 10:
            lines.append(
                f"\nhigh caller count: {_prim314.name} called from {len(_caller_files314)} files"
                f" — de-facto stable API; behavior changes break callers even without signature change"
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
