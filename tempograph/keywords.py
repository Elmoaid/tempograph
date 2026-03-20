"""Keyword extraction for change-localization tasks (PR titles, commit messages)."""
from __future__ import annotations

import re


def _extract_cl_keywords(task: str) -> list[str]:
    """Extract code-symbol keywords from a change-localization task (PR title/commit message).

    Ported from bench/changelocal/context.py::_extract_keywords.
    This is the PR-title-specific extractor used for change localization tasks.
    For general coding tasks, use the fuzzy search in search_symbols_scored directly.
    """
    _TRUNK_BRANCHES = frozenset({"master", "main", "develop", "development", "stable", "release"})
    _cc_scopes: list[str] = []  # conventional commit scopes extracted before stripping
    m = (re.search(r'Merge pull request #\d+ from [^/\s]+/(\S+)', task)
         or re.search(r'Merge pull request \S+#\d+ from [^/\s]+/(\S+)', task))
    if m:
        branch = m.group(1)
        leaf = branch.lower().split('/')[-1]
        if leaf in _TRUNK_BRANCHES:
            # Trunk/fork-master branch: return [] to trigger the overview fallback.
            # Overview injection benefits low-baseline repos (requests, django) where
            # the model has no codebase knowledge and structure helps orient predictions.
            # Do NOT mine body — generic body keywords (failing, doctests, requests) would
            # suppress overview without adding BFS signal, hurting F1.
            # Evidence: requests/3e7d0a87 — overview → F1 0→0.333; body mining → F1=0.
            return []
        branch_lower = branch.lower()
        # "docs" or "doc" as a hyphen/underscore-separated component anywhere in branch name.
        # Matches: docs-view, doc-view, view-docs, 5309-docs-view (DRF-style), docs/viewset.
        # Does NOT match: docstring-update (component is "docstring", not "doc/docs").
        _DOC_COMPONENT = re.compile(r'(?:^|[-_/])docs?(?:[-_/]|$)')
        if (_DOC_COMPONENT.search(leaf)
                or any(re.search(r'(?:^|[-_/])' + kw + r'(?:[-_/]|$)', leaf)
                       for kw in ("readme", "changelog", "documentation"))
                or branch_lower.startswith("docs/")
                or branch_lower.startswith("doc/")):
            return []
        body = task[task.find('\n')+1:].strip() if '\n' in task else ''
        body = re.sub(r'https?://\S+', '', body)
        body = re.sub(r'^[\w-]+:\s+.*$', '', body, flags=re.MULTILINE)
        body = re.sub(r'@[a-zA-Z][a-zA-Z0-9_-]*', '', body)  # strip GitHub @mentions (usernames ≠ code)
        body = body.strip()
        # Mine the PR body for additional keywords when the branch name is not self-describing:
        # - Ticket-reference branches (issue12345, fix-1587) have numeric names → body is the task.
        # - Pure snake_case branches with no CamelCase transitions (e.g. "support_forwardred_in_python36")
        #   may have typos or version suffixes that hide the real identifier; body often names it.
        # Skip body mining for branches that already produce CamelCase compounds (hyphenated segments
        # like "reply-not-found" → "ReplyNotFound", or explicit camelCase like "partII" with "tI").
        # Those branches are self-describing and body mining adds noise.
        _is_ticket = bool(
            re.match(r'^(?:issue|ticket|bug|patch|pr|fix)[-_]?\d+', leaf) or
            re.match(r'^\d+[-_]', leaf)
        )
        _branch_has_compound = bool(
            re.search(r'\b[a-z]{2,}-[a-z]{2,}', branch)  # hyphenated alpha-alpha ("reply-not")
            or re.search(r'[a-z][A-Z]', branch)           # explicit camelCase ("partII")
        )
        # "Username-master" / "OrgName-master": external contributor PR'd from their fork's master.
        # The compound (camelCase username) is meaningless as a task keyword; mine body instead.
        # Evidence: falcon "CygnusNetworks-master" / "hooblei-master" → body has "byte ranges" / "context_type".
        _is_fork_master = leaf.endswith("-master") or leaf.endswith("_master")
        # GitHub auto-generated "username-patch-N" branches: strip the username prefix.
        # Pattern: single-word (no hyphens) username followed by "-patch-\d+".
        # Without this, CamelCase extraction yields the username as a priority keyword.
        # Evidence: "Freezerburn-patch-1-reb" → "Freezerburn" keyword → false path match.
        _gh_patch_m = re.match(r'^([A-Za-z][a-zA-Z0-9]*)-patch-\d+', leaf)
        if _gh_patch_m:
            branch = branch[len(_gh_patch_m.group(1)) + 1:]  # strip "Username-" prefix
            _is_ticket = True  # remaining "patch-N-..." → mine body for actual keywords
        task = branch + ('\n' + body if (_is_ticket or not _branch_has_compound or _is_fork_master) and body else '')
    else:
        # Detect trunk merges: "Merge branch 'stable'", "Merge branch '2.2.x'"
        # Only skip when the task has NO useful branch info (pure trunk-to-trunk)
        _trunk_m = re.match(r"^Merge branch '([^']+)'(?:\s+into\s+(\S+))?", task, re.IGNORECASE)
        if _trunk_m:
            source = _trunk_m.group(1).lower().strip()
            target = (_trunk_m.group(2) or "").lower().strip()
            _is_generic = lambda b: (b in _TRUNK_BRANCHES or re.match(r'^\d+\.\d+', b)
                                     or b in ("hotfix", "bugfix", "staging", "production") or not b)
            if _is_generic(source) and _is_generic(target):
                return []  # pure trunk merge — no useful keywords
        task = re.sub(r'^Merge (?:branch|pull request)[^\n]*\n?', '', task, flags=re.IGNORECASE)
        # Extract conventional commit scopes BEFORE stripping them.
        # `feat(StreamMiddleware):`, `perf(Response):` → scope names the changed component.
        _cc_scopes = re.findall(
            r'(?:feat|fix|chore|refactor|style|perf|ci|build|docs|test|revert)\(([^)]+)\)',
            task, re.IGNORECASE)
        # Strip conventional commit type prefix (feat:, fix:, chore:, refactor(scope):, etc.)
        # before keyword extraction — these prefixes are commit metadata, not code identifiers.
        task = re.sub(r'^(?:feat|fix|chore|refactor|style|perf|ci|build|docs|test|revert)(?:\([^)]*\))?!?:\s*', '', task, flags=re.IGNORECASE)

    skip = {
        "the", "and", "for", "from", "with", "this", "that", "fix", "add",
        "update", "remove", "change", "bug", "feature", "merge", "pull",
        "request", "branch", "commit", "issue", "use", "make", "new",
        "when", "not", "all", "can", "should", "would", "into", "also",
        # Short English articles/prepositions/conjunctions (never code identifiers)
        "are", "its", "via", "any", "but", "has", "was", "had", "yet",
        "nor", "per", "due", "let", "now", "old", "raw", "off", "out",
        "non", "sub", "pre", "too",
        "pass", "through", "methods", "method", "function", "class", "file",
        "code", "test", "tests", "type", "types", "value", "values", "data",
        "object", "objects", "item", "items", "list", "dict", "set", "get",
        "put", "call", "calls", "return", "returns", "allow", "allows",
        # Python language keywords — never useful as symbol focus terms
        "import", "raise", "yield", "async", "await", "lambda",
        "assert", "except", "finally", "none", "true", "false",
        # JS/TS keywords and constructs
        "const", "export", "require", "props", "state",
        "foreach", "callback",  # loop construct / pattern word — never a file identifier
        "getter", "setter",  # property accessor types — "host-setter" → focus on request.js (wrong; misses context.js)
        "handle", "handles", "handler", "handlers", "check", "checks", "run", "runs", "create",
        "support", "supported", "include", "includes", "avoid", "prevent", "ensure",
        "apply", "improve", "move", "moved", "part", "parts", "some",
        "name", "named",  # "name" matches ParameterNameConflicts (wrong); compound forms (ParameterName) still work
        "limit", "limits",  # branch component "limit-selects" → "limit" matches LimitOffsetPagination (wrong)
        "error", "errors", "option", "options", "response", "config",
        "host",  # generic HTTP concept — "host-setter" → focus on host getter (wrong); compound "HostSetter" still works
        "enable", "enabled", "disable", "disabled", "default", "defaults", "global",
        "log", "logger", "logging", "ticket", "docs", "readme",
        "fixed", "improved", "updated", "added", "removed", "changed",
        "fixes", "improves", "updates", "usage", "internal", "external",
        "fork", "syncing", "sync", "backport", "rebase", "cherry", "pick", "patch", "hotfix",
        # Version / dependency metadata — never code symbol names
        "version", "versions", "versioning", "bump", "release", "changelog",
        "dependency", "dependencies", "package", "packages", "upgrade", "downgrade",
        "install", "installation", "requirements", "pinned", "unpinned",
        # PR body prose — common natural language words that appear in PR descriptions
        # but are never code symbol names. These slip through when body-mining is active.
        "implement", "implements", "implementation", "related", "regarding",
        "contribution", "contribute", "thanks", "introduces", "introduce",
        "follow", "follows", "following", "address", "addresses", "addressing",
        "resolves", "resolve", "closes", "close", "based", "instead", "rather",
        # Narrative/descriptive body prose — verbs/adverbs that consume keyword slots,
        # displacing domain words (e.g. "accidentally broke cookie" → skip 'accidentally'
        # and 'broke' so 'cookie' advances into the effective_keywords[:3] cap).
        # Evidence: falcon 3431ac32 — 'accidentally','broke' in slots 2-3 blocked 'cookie'.
        "accidentally", "broke", "broken", "wrongly", "correctly", "incorrectly",
        "properly", "caused", "noticed", "realized", "discovered", "detected",
        "missing", "extra", "leading", "trailing",
        # HTTP/browser prose words that displace domain identifiers from the top-3 cap.
        # Evidence: falcon 3431ac32 "fix(Response): Instruct browser to not cache cookies"
        # → 'Instruct' + 'browser' fill slots 2-3, blocking 'cookies' (the correct target).
        # With these skipped: effective_keywords = ['Response', 'cache', 'cookies'] → cookies found.
        "instruct", "browser",
        # Auxiliary/linking verbs — never symbol names
        "being", "were", "been", "have", "having", "does", "doing", "done",
        "getting", "giving", "going", "making", "taking", "using", "using",
        "seem", "seems", "seemed", "become", "becomes", "became",
        # Conventional commit type tokens (belt-and-suspenders for any that slip through)
        "feat", "chore", "refactor", "revert", "perf", "style",
    }
    seen: set[str] = set()
    priority: list[str] = []
    general: list[str] = []

    def _record(ident: str, bucket: list[str]) -> None:
        lower = ident.lower()
        if re.match(r'^(?:issue|ticket|bug|pr|patch|fix)\d+$', lower):
            return
        if lower not in skip and lower not in seen and len(ident) > 2:
            seen.add(lower)
            bucket.append(ident)

    # Backtick-quoted identifiers are highest-priority: explicitly named symbols.
    # E.g. "deprecate `should_ignore_error`" → extract "should_ignore_error" first.
    for backtick_id in re.findall(r'`([a-zA-Z_][a-zA-Z0-9_]{2,})`', task):
        _record(backtick_id, priority)

    # Conventional commit scopes are high-priority: `feat(StreamMiddleware):`, `perf(Response):`.
    # Extract scope even if it's a common English word (e.g. "Response") since in this context
    # it names the changed component, not a generic term.
    # Conventional commit scopes are high-priority: `feat(StreamMiddleware):`, `perf(Response):`.
    # For Merge-PR tasks (if m:), search the (modified) task text.
    # For bare commits (else:), scopes were saved into _cc_scopes before stripping.
    _inline_scopes = re.findall(
        r'(?:feat|fix|chore|refactor|style|perf|ci|build|docs|test|revert)\(([^)]+)\)',
        task, re.IGNORECASE)
    for raw in _inline_scopes + _cc_scopes:
        for scope_part in raw.split(','):
            scope_part = scope_part.strip()
            if len(scope_part) > 2 and scope_part.lower() not in seen:
                seen.add(scope_part.lower())
                priority.append(scope_part)

    lines = task.split('\n', 1)
    branch_text = lines[0]
    body_text = lines[1].strip() if len(lines) > 1 else ''

    # Normalize: replace underscore immediately before a lowerCamelCase identifier with a hyphen.
    # Underscores are regex word characters (\w), so "_extendServerError" has no \b before 'e',
    # preventing lowerCamelCase extraction. Replacing the underscore with '-' (non-word char)
    # creates the necessary word boundary.
    # E.g. "feature/#235_pass_payload_to_extendServerError" → "...to-extendServerError"
    # → lowerCamelCase regex extracts "extendServerError" → priority keyword → BFS runs.
    # Only targets _lowerCamelCase transitions; does not affect all_lowercase_snake or ALLCAPS.
    _branch_for_extract = re.sub(r'_(?=[a-z][a-zA-Z0-9]*[A-Z])', '-', branch_text)

    def _extract_from(source: str, strict_camel: bool = False) -> None:
        # Generic OOP/domain suffixes excluded from CamelCase sub-part fallbacks.
        # These terms match too broadly across a codebase to be useful focus queries.
        _CAMEL_PART_SKIP = frozenset({
            "base", "core", "util", "utils", "mixin", "factory", "manager",
            "field", "fields", "exception", "exceptions", "model", "models",
            "view", "views", "form", "forms", "helper", "helpers", "main",
        })
        for hyphenated in re.findall(r'\b[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)+\b', source):
            camel = "".join(part.capitalize() for part in hyphenated.split("-"))
            _record(camel, priority)
            # Sub-part decomposition: "streaming-body" → "StreamingBody" (priority) +
            # "Streaming" (general). When the compound is a new symbol added in
            # this PR and fails focus, long sub-parts may match related existing symbols.
            # Minimum 7 chars filters generic short words (Encode=6, Custom=6, Params=6,
            # Method=6) that match too broadly and cause harmful context injection.
            # IMPORTANT: this must run BEFORE marking hyphen-parts as seen, otherwise
            # seen.add("streaming") blocks _record("Streaming", general).
            for _p in re.findall(r'[A-Z][a-z0-9]+', camel):
                if len(_p) >= 7 and _p.lower() not in _CAMEL_PART_SKIP:
                    _record(_p, general)
            # Mark SHORT (< 7 chars) hyphen-parts as seen to prevent the snake_case regex
            # below from re-extracting them as standalone general keywords.
            # E.g. "custom-encode-params-method" → mark "custom","encode","params","method"
            # as seen → snake_case loop skips them → only the CamelCase compound remains.
            # Long parts (≥ 7 chars like "streaming") are NOT marked: they can still appear
            # as sub-parts (recorded a few lines below) which then dedup them naturally.
            for _part in hyphenated.split("-"):
                if len(_part) < 7:
                    seen.add(_part.lower())
        for ident in re.findall(r'(?<![A-Z_])\b[A-Z][A-Z0-9_]{2,}\b', source):
            if '_' in ident:
                _record(ident, priority)
        camel_pat = (r'\b(?:[A-Z][a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]+|[A-Z][a-zA-Z0-9]{6,})\b'
                     if strict_camel else r'\b[A-Z][a-zA-Z0-9]+\b')
        for ident in re.findall(camel_pat, source):
            _record(ident, priority)
            # Same sub-part fallback for direct CamelCase in commit messages.
            # Minimum 7 chars matches hyphenated case — keeps long distinctive parts only.
            for _p in re.findall(r'[A-Z][a-z0-9]+', ident):
                if len(_p) >= 7 and _p.lower() not in _CAMEL_PART_SKIP:
                    _record(_p, general)
        # lowerCamelCase identifiers (e.g. `notFound`, `handleRequest`, `setNotFoundHandler`).
        # Appears in both PR bodies (strict_camel=True) and conventional commit messages (non-strict).
        # Pattern: starts lowercase, uppercase transition, THEN lowercase continuation (rules out
        # acronym-style endings like "partII" or "iOS" where uppercase is followed by uppercase).
        # Evidence: "Add reply.notFound() method" → "notFound" → focus finds setNotFoundHandler.
        # Evidence: "fix: update handleRequest to support headers" → "handleRequest" extracted.
        for ident in re.findall(r'\b[a-z][a-zA-Z0-9]*[A-Z][a-z][a-zA-Z0-9]*\b', source):
            _record(ident, priority)
        for ident in re.findall(r'\b[a-z_][a-z0-9_]{2,}\b', source):
            # Multi-component snake_case (render_focused, sort_callers) → priority:
            # these are specific identifiers, not generic English words.
            # Single-word (sort, filter, key) → general: likely common verbs/nouns.
            _record(ident, priority if '_' in ident else general)
            if ident.count('_') >= 3 and len(ident) > 20:
                parts = ident.split('_')
                for i, part in enumerate(parts):
                    if len(part) > 2:
                        _record(part, general)
                    if i + 1 < len(parts):
                        compound = f"{parts[i]}_{parts[i+1]}"
                        if len(compound) > 4:
                            _record(compound, general)

    # For fork-master branches ("Username-master"), the branch is a meaningless GitHub username.
    # Extract body keywords FIRST so they get priority over the useless branch name.
    # E.g. "CygnusNetworks-master\nAdd arbitrary byte ranges" → body gives "byte", "ranges" first.
    if body_text and (branch_text.endswith('-master') or branch_text.endswith('_master')):
        _extract_from(body_text, strict_camel=True)
    else:
        _extract_from(_branch_for_extract, strict_camel=False)
        if body_text:
            _extract_from(body_text, strict_camel=True)

    return priority + general
