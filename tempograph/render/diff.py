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

    # S447: Config-only change — all changed files are settings/config files.
    # Moved here (before early return) so it fires even when config files are not
    # in the graph (config files are often not parsed as source files).
    _s447_config_keywords = ("config", "settings", "conf", "env", "dotenv", "secrets", "options")
    _s447_non_config = [
        f for f in changed_files
        if not any(kw in f.rsplit("/", 1)[-1].lower() for kw in _s447_config_keywords)
        and f.rsplit("/", 1)[-1].lower() not in (".env", ".env.example")
    ]
    if changed_files and not _s447_non_config:
        _cfg_names447 = ", ".join(f.rsplit("/", 1)[-1] for f in changed_files[:2])
        lines.append(
            f"config-only diff: all {len(changed_files)} changed file(s) are configuration ({_cfg_names447})"
            f" — config changes affect runtime behavior silently; verify flag interactions and defaults"
        )

    # S477: Multi-module diff — diff spans 5+ distinct top-level directories.
    # Moved before early return so it fires even when changed files aren't in the graph.
    _s477_top_dirs: set[str] = set()
    for _f477 in changed_files:
        _parts477 = _f477.replace("\\", "/").split("/")
        _top477 = _parts477[0] if _parts477 else ""
        if _top477 and _top477 != ".":
            _s477_top_dirs.add(_top477)
    if len(_s477_top_dirs) >= 5:
        lines.append(
            f"multi-module diff: changes span {len(_s477_top_dirs)} top-level directories"
            f" — split into focused PRs per module to reduce review complexity"
        )

    # S561: Config-only diff — all changed files have config/data file extensions.
    # Moved before early return so it fires even when config files aren't indexed as source.
    _config_exts561 = (".yaml", ".yml", ".json", ".toml", ".ini", ".env", ".cfg", ".conf")
    _cfg_files561 = [f for f in changed_files if any(f.lower().endswith(e) for e in _config_exts561)]
    if changed_files and len(_cfg_files561) == len(changed_files):
        lines.append(
            f"config-only diff: all {len(changed_files)} changed file(s) are configuration files"
            f" — no code changes, but config errors can change behavior, timeouts, or security policies"
        )

    # S603: Migration file in diff — moved before early return (migration files aren't indexed).
    _migration_patterns603_early = ("/migrations/", "/migration/", "/migrate/", "/alembic/")
    _migration_exts603_early = (".sql", ".migration")
    _mig_early603 = [
        f for f in changed_files
        if any(p in f.replace("\\", "/") for p in _migration_patterns603_early)
        or any(f.lower().endswith(e) for e in _migration_exts603_early)
    ]
    if _mig_early603:
        _mig_name603_early = _mig_early603[0].rsplit("/", 1)[-1]
        lines.append(
            f"migration in diff: {_mig_name603_early} ({len(_mig_early603)} migration file(s))"
            f" — database migrations are irreversible; ensure rollback plan exists before deploying"
        )

    # S609: Wide diff — diff touches 20+ files simultaneously.
    # Moved before early return so it fires even when files aren't indexed.
    if len(changed_files) >= 20:
        lines.append(
            f"wide diff: {len(changed_files)} files changed in one diff"
            f" — large changesets are harder to review; consider splitting into smaller PRs"
        )

    # S615: Secrets/env file in diff — diff includes a .env, .envrc, or secrets file.
    # Credentials files in a diff indicate secrets may be stored in version control,
    # or that environment configuration is being leaked in a review.
    _secrets_exts615 = (".env", ".envrc", ".secret", ".secrets", ".pem", ".key", ".p12", ".pfx")
    _secrets_names615 = (".env", ".envrc", "secrets.yml", "secrets.yaml", "id_rsa", "id_ed25519")
    _secret_files615 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1].lower() in _secrets_names615
        or any(f.lower().endswith(e) for e in _secrets_exts615)
    ]
    if _secret_files615:
        _sec_name615 = _secret_files615[0].rsplit("/", 1)[-1]
        lines.append(
            f"secrets in diff: {_sec_name615} ({len(_secret_files615)} credential/env file(s))"
            f" — verify no secrets are tracked in VCS; rotate any credentials if leaked"
        )

    # S621: Test file removed — diff includes deletion of a test file (path-based heuristic).
    # Removing test files silently drops coverage; this is a high-risk operation that
    # may hide regressions in the removed tests' coverage area.
    _deleted_tests621 = [
        f for f in changed_files
        if _is_test_file(f)
        and (
            f.rsplit("/", 1)[-1].startswith("test_")
            or f.rsplit("/", 1)[-1].endswith("_test.py")
        )
        and f.endswith(".py")
    ]
    if _deleted_tests621:
        _del_name621 = _deleted_tests621[0].rsplit("/", 1)[-1]
        lines.append(
            f"test files in diff: {_del_name621} ({len(_deleted_tests621)} test file(s) changed)"
            f" — verify test removals don't silently drop coverage for modified areas"
        )

    # S627: Config file in diff — diff includes a configuration file (.cfg, .ini, .toml, .yaml, .json).
    # Config changes affect runtime behavior without touching code; they're easy to overlook
    # in code review and can change feature flags, timeouts, or connection strings silently.
    _config_exts627 = (".cfg", ".ini", ".toml", ".yaml", ".yml", ".json", ".conf", ".config")
    _config_excludes627 = ("test", "spec", "fixture", "mock", "lock", "package-lock", "yarn.lock")
    _config_files627 = [
        f for f in changed_files
        if any(f.lower().endswith(e) for e in _config_exts627)
        and not any(x in f.lower() for x in _config_excludes627)
        and not _is_test_file(f)
    ]
    if _config_files627:
        _cfg_name627 = _config_files627[0].rsplit("/", 1)[-1]
        lines.append(
            f"config in diff: {_cfg_name627} ({len(_config_files627)} config file(s) changed)"
            f" — config changes silently affect runtime behavior; review for feature flags or credentials"
        )

    # S633: Generated file in diff — diff includes auto-generated files (_pb2.py, *_generated*, etc.).
    # Generated files should not be hand-edited; their presence in a diff may indicate
    # accidental modification or a regeneration that needs review for correctness.
    _gen_suffixes633 = ("_pb2.py", "_pb2_grpc.py", "_generated.py", "_gen.py", "_auto.py")
    _gen_patterns633 = ("generated", "_pb2", "autogenerated", "do not edit", "do_not_edit")
    _gen_files633 = [
        f for f in changed_files
        if any(f.lower().endswith(s) for s in _gen_suffixes633)
        or any(p in f.lower().replace("/", "_") for p in _gen_patterns633)
    ]
    if _gen_files633:
        _gen_name633 = _gen_files633[0].rsplit("/", 1)[-1]
        lines.append(
            f"generated file in diff: {_gen_name633} ({len(_gen_files633)} auto-generated file(s))"
            f" — generated files should not be hand-edited; verify this is a regeneration"
        )

    # S639: Polyglot diff — diff spans 3+ different file language extensions.
    # A change touching many language runtimes (Python + JS + Go + SQL) has a wide
    # blast radius across toolchains and may require multiple reviewers.
    _diff_exts639: dict[str, int] = {}
    for f in changed_files:
        _ext639 = f.rsplit(".", 1)[-1].lower() if "." in f else ""
        if _ext639 and _ext639 not in ("md", "txt", "rst", "json", "yaml", "yml", "toml", "cfg", "ini"):
            _diff_exts639[_ext639] = _diff_exts639.get(_ext639, 0) + 1
    if len(_diff_exts639) >= 3:
        _ext_list639 = ", ".join(f".{e}" for e in sorted(_diff_exts639)[:5])
        lines.append(
            f"polyglot diff: {len(_diff_exts639)} languages in diff ({_ext_list639})"
            f" — cross-runtime change; may require multiple specialists to review correctly"
        )

    # S645: Lockfile in diff — diff includes a dependency lockfile.
    # Lockfile changes signal dependency updates; these deserve extra scrutiny
    # since transitive dependency updates can introduce breaking changes or vulnerabilities.
    _lock_names645 = (
        "requirements.txt", "requirements.lock", "pipfile.lock", "poetry.lock",
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock",
        "gemfile.lock", "composer.lock", "go.sum",
    )
    _lock_files645 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1].lower() in _lock_names645
    ]
    if _lock_files645:
        _lock_name645 = _lock_files645[0].rsplit("/", 1)[-1]
        lines.append(
            f"lockfile in diff: {_lock_name645} ({len(_lock_files645)} lockfile(s) changed)"
            f" — dependency update; review transitive changes for breaking or vulnerable packages"
        )

    # S651: Schema file in diff — diff includes a database schema or ORM model file.
    # Schema migrations affect database structure; any mismatch between code and schema
    # causes runtime failures that are hard to detect without migration review.
    _schema_names651 = ("schema.py", "models.py", "model.py", "tables.py", "entities.py")
    _schema_exts651 = (".sql",)
    _schema_patterns651 = ("migration", "schema", "models")
    _schema_files651 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1].lower() in _schema_names651
        or any(f.lower().endswith(e) for e in _schema_exts651)
        or any(p in f.lower().replace("/", "_") for p in _schema_patterns651)
    ]
    if _schema_files651:
        _sch_name651 = _schema_files651[0].rsplit("/", 1)[-1]
        lines.append(
            f"schema in diff: {_sch_name651} ({len(_schema_files651)} schema/model file(s) changed)"
            f" — database or ORM changes require migration review; verify schema/code parity"
        )

    # S657: CI/CD config in diff — diff includes a CI/CD or build configuration file.
    # CI changes affect the entire team's workflow; build script changes can silently
    # break the deployment pipeline and may go unnoticed until the next push.
    _ci_names657 = (
        "jenkinsfile", "makefile", "dockerfile", ".travis.yml", "circle.yml",
        "azure-pipelines.yml", "buildspec.yml", "tox.ini", "noxfile.py",
    )
    _ci_patterns657 = (".github/", ".gitlab-ci", ".circleci/", ".buildkite/", "ci/")
    _ci_files657 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1].lower() in _ci_names657
        or any(p in f.replace("\\", "/").lower() for p in _ci_patterns657)
    ]
    if _ci_files657:
        _ci_name657 = _ci_files657[0].rsplit("/", 1)[-1]
        lines.append(
            f"CI/CD config in diff: {_ci_name657} ({len(_ci_files657)} pipeline file(s) changed)"
            f" — build/deploy workflow changes; verify no pipeline regressions before merging"
        )

    # S663: Package init in diff — diff includes a __init__.py file (package restructuring).
    # Changes to __init__.py affect the entire package's public API surface;
    # symbol additions/removals or re-exports here change what consumers can import.
    _init_files663 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1] == "__init__.py" or f == "__init__.py"
    ]
    if _init_files663:
        _init_name663 = _init_files663[0].rsplit("/", 2)[-2] if "/" in _init_files663[0] else ""
        _pkg_label663 = f"{_init_name663}/" if _init_name663 else ""
        lines.append(
            f"package init in diff: {_pkg_label663}__init__.py ({len(_init_files663)} init file(s) changed)"
            f" — package public API changed; verify re-exports and downstream consumers"
        )

    # S669: Documentation file in diff — diff includes a .md, .rst, or .txt file.
    # Docs changes alongside code changes signal an intentional API or behavior update;
    # docs-only diffs with no code changes may indicate stale documentation being corrected.
    _doc_exts669 = {".md", ".rst", ".txt"}
    _doc_files669 = [
        f for f in changed_files
        if "." in f.rsplit("/", 1)[-1]
        and f.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower() in {"md", "rst", "txt"}
    ]
    if _doc_files669:
        _doc_names669 = ", ".join(f.rsplit("/", 1)[-1] for f in _doc_files669[:2])
        if len(_doc_files669) > 2:
            _doc_names669 += f" +{len(_doc_files669) - 2} more"
        lines.append(
            f"docs in diff: {_doc_names669} ({len(_doc_files669)} doc file(s))"
            f" — verify code and docs stay in sync; doc-only diffs may lag actual behavior"
        )

    # S675: Version file in diff — diff includes a version tracking file.
    # Version bumps signal a release boundary; changes alongside a version bump
    # will ship immediately and should be held to a higher quality bar.
    _version_names675 = {
        "version.py", "__version__.py", "VERSION", "VERSION.txt",
        "pyproject.toml", "package.json", "Cargo.toml", "setup.cfg",
    }
    _ver_files675 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1] in _version_names675
    ]
    if _ver_files675:
        _ver_name675 = _ver_files675[0].rsplit("/", 1)[-1]
        lines.append(
            f"version file in diff: {_ver_name675} changed"
            f" — release boundary; co-changed code ships immediately; hold to higher quality bar"
        )

    if not normalized:
        return "\n".join(lines) if len(lines) > 2 else f"None of the changed files found in graph: {changed_files}"

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

    # S212: Untested changes — changed non-test symbols with zero test callers.
    # "Tests to run" shows which test files have coverage; this shows which SPECIFIC changed
    # symbols have NONE. Complements coverage view: known coverage vs. known gap.
    # Only shown for functions/methods/classes (constants/variables aren't directly testable).
    if _all_changed_syms and token_count < max_tokens - 60:
        _callable_kinds = {"function", "method", "class"}
        _untested_changed: list[str] = []
        for _uc_sym in _all_changed_syms:
            if _uc_sym.kind.value not in _callable_kinds:
                continue
            _has_test_caller = any(_is_test_file(c.file_path) for c in graph.callers_of(_uc_sym.id))
            if not _has_test_caller:
                _uc_file = _uc_sym.file_path.rsplit("/", 1)[-1]
                _untested_changed.append(f"{_uc_sym.name} ({_uc_file})")
        if 1 <= len(_untested_changed):
            _uc_str = ", ".join(_untested_changed[:6])
            _uc_overflow = len(_untested_changed) - 6
            _uc_line = f"untested changes ({len(_untested_changed)}): {_uc_str}"
            if _uc_overflow > 0:
                _uc_line += f" +{_uc_overflow} more"
            _uc_line += " — no direct test coverage for these changed symbols"
            lines.append(_uc_line)
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

    # S160: New symbols — count brand-new fn/class symbols introduced in the diff.
    # Many new symbols = significant API growth, not just modification.
    # Only shown when 3+ new symbols are introduced.
    _s160_new_syms: list[str] = []
    for _fp160 in normalized:
        if _is_test_file(_fp160):
            continue
        _fi160 = graph.files.get(_fp160)
        if not _fi160:
            continue
        for _sid160 in _fi160.symbols:
            if _sid160 not in graph.symbols:
                continue
            _sym160 = graph.symbols[_sid160]
            if _sym160.kind.value not in ("function", "method", "class", "interface"):
                continue
            # "New" heuristic: exported symbol with 0 callers (not yet called = newly added)
            if _sym160.exported and len(graph.callers_of(_sid160)) == 0:
                _s160_new_syms.append(_sym160.name)
    if len(_s160_new_syms) >= 3:
        _s160_str = ", ".join(_s160_new_syms[:3])
        if len(_s160_new_syms) > 3:
            _s160_str += f" +{len(_s160_new_syms) - 3} more"
        lines.append(f"new symbols: {len(_s160_new_syms)} exported fns/classes with 0 callers ({_s160_str})")

    # S199: Focused change — the diff touches only 1 source file (clean, low-risk commit).
    # Single-file diffs have minimal blast radius; they're easy to review, revert, and bisect.
    # Only shown when exactly 1 non-test source file is in the diff.
    _s199_src_files = [fp for fp in normalized if not _is_test_file(fp)]
    if len(_s199_src_files) == 1:
        lines.append(
            f"focused change: only {_s199_src_files[0].rsplit('/', 1)[-1]} modified"
            f" — minimal blast radius, easy to review and revert"
        )

    # S193: Migration file — diff includes a database migration or schema file.
    # Schema changes affect data integrity; they require extra care and often need backfill work.
    # Only shown when 1+ migration-style file is in the diff.
    _s193_migration_patterns = {"migration", "migrate", "schema", "alembic", "flyway"}
    _s193_migration_files = [
        fp for fp in normalized
        if not _is_test_file(fp)
        and (
            any(p in fp.lower() for p in _s193_migration_patterns)
            or fp.endswith(".sql")
        )
    ]
    if _s193_migration_files:
        _s193_str = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s193_migration_files[:3])
        lines.append(
            f"migration change: {_s193_str}"
            f" — database/schema file modified, coordinate with data team"
        )

    # S187: Contract risk — the diff changes an exported symbol with 5+ external callers.
    # Changing a widely-called exported symbol is a potential breaking change for all callers.
    # Only shown when 1+ such high-caller exported symbol is in the changed files.
    _s187_risky: list[tuple[int, str]] = []
    for _fp187 in normalized:
        if _is_test_file(_fp187):
            continue
        for _sym187 in graph.symbols.values():
            if _sym187.file_path != _fp187 or not _sym187.exported:
                continue
            if _sym187.kind.value not in ("function", "method", "class"):
                continue
            _ext_callers187 = {
                c.file_path for c in graph.callers_of(_sym187.id)
                if c.file_path not in set(normalized)
            }
            if len(_ext_callers187) >= 5:
                _s187_risky.append((len(_ext_callers187), _sym187.name))
    if _s187_risky:
        _s187_risky.sort(reverse=True)
        _s187_top = _s187_risky[0]
        lines.append(
            f"contract risk: {_s187_top[1]} has {_s187_top[0]} external callers"
            f" — changing this exported symbol may break callers"
        )

    # S181: Test-heavy diff — the majority of changed files are test files.
    # A diff that's mostly tests without paired source changes may indicate speculative tests.
    # Only shown when >= 4 total files and >= 50% are test files.
    if len(normalized) >= 4:
        _s181_test_count = sum(1 for fp in normalized if _is_test_file(fp))
        _s181_pct = _s181_test_count / len(normalized) * 100
        if _s181_pct >= 50:
            lines.append(
                f"test-heavy diff: {_s181_test_count}/{len(normalized)} files are tests"
                f" ({_s181_pct:.0f}%) — verify paired source changes exist"
            )

    # S175: Config file change — the diff includes a configuration or settings file.
    # Config changes affect runtime behavior globally; they warrant extra scrutiny.
    # Only shown when 1+ config file is in the diff.
    _s175_config_stems = {
        "settings", "config", "configuration", "constants", "env",
        "defaults", "options", "params", "parameters",
    }
    _s175_config_files = [
        fp for fp in normalized
        if fp.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in _s175_config_stems
        and not _is_test_file(fp)
    ]
    if _s175_config_files:
        _s175_str = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s175_config_files[:3])
        lines.append(
            f"config change: {_s175_str}"
            f" — settings file modified, check for unintended global effects"
        )

    # S169: Entry point change — the diff includes an application entry point file.
    # Entry points are user-facing; changes here are immediately visible to end users.
    # Only shown when 1+ entry point filename is among the changed files.
    _s169_entry_stems = {
        "main", "app", "index", "manage", "cli", "server", "run",
        "wsgi", "asgi", "__main__", "start", "entrypoint",
    }
    _s169_entry_files = [
        fp for fp in normalized
        if fp.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in _s169_entry_stems
        and not _is_test_file(fp)
    ]
    if _s169_entry_files:
        _s169_str = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s169_entry_files[:3])
        lines.append(
            f"entry point change: {_s169_str}"
            f" — user-facing file(s) modified, immediate user impact"
        )

    # S163: Caller update needed — symbols in the diff have callers in files NOT in the diff.
    # These external call sites may need updating after the diff's logic change.
    # Only shown when 3+ distinct external caller files exist.
    _s163_changed_fps = set(normalized)
    _s163_ext_callers: set[str] = set()
    for _fp163 in normalized:
        if _is_test_file(_fp163):
            continue
        for _sym163 in graph.symbols.values():
            if _sym163.file_path != _fp163:
                continue
            for _caller163 in graph.callers_of(_sym163.id):
                if _caller163.file_path not in _s163_changed_fps:
                    _s163_ext_callers.add(_caller163.file_path)
    if len(_s163_ext_callers) >= 3:
        _s163_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_s163_ext_callers)[:3]]
        _s163_str = ", ".join(_s163_names)
        if len(_s163_ext_callers) > 3:
            _s163_str += f" +{len(_s163_ext_callers) - 3} more"
        lines.append(
            f"caller update needed: {len(_s163_ext_callers)} files call changed symbols but"
            f" aren't in this diff ({_s163_str})"
        )

    # S149: Mixed concern — diff touches both source and test files.
    # Mixing src+test changes in one commit complicates cherry-picks, bisects, and reverts.
    # Flag when the diff has 1+ source files AND 1+ test files.
    _s149_has_src = any(not _is_test_file(fp) for fp in normalized)
    _s149_has_test = any(_is_test_file(fp) for fp in normalized)
    if _s149_has_src and _s149_has_test:
        _s149_test_count = sum(1 for fp in normalized if _is_test_file(fp))
        _s149_src_count = sum(1 for fp in normalized if not _is_test_file(fp))
        lines.append(
            f"mixed concern: {_s149_src_count} source + {_s149_test_count} test files"
            f" — consider splitting into separate commits"
        )

    # S143: Cross-module impact — how many distinct top-level directories the diff touches.
    # A diff touching 3+ modules has broader coordination risk than a single-module change.
    # Only shown when the diff touches source files in 3+ distinct top-level directories.
    _s143_modules: set[str] = set()
    for _fp143 in normalized:
        if _is_test_file(_fp143):
            continue
        _parts143 = _fp143.split("/")
        _s143_modules.add(_parts143[0] if len(_parts143) > 1 else ".")
    if len(_s143_modules) >= 3:
        _s143_mods_str = ", ".join(sorted(_s143_modules)[:4])
        if len(_s143_modules) > 4:
            _s143_mods_str += f" +{len(_s143_modules) - 4} more"
        lines.append(f"cross-module impact: {len(_s143_modules)} modules touched ({_s143_mods_str})")

    # S135: Changed file size — total line count of all source files in the diff.
    # Changing large files means more surface area for unintended side effects.
    # Only shown when total changed source lines >= 500 (non-trivial file sizes).
    _s135_total_lines = sum(
        graph.files[fp].line_count for fp in normalized
        if not _is_test_file(fp) and fp in graph.files
    )
    if _s135_total_lines >= 500:
        _s135_names = [fp.rsplit("/", 1)[-1] for fp in sorted(normalized) if not _is_test_file(fp)]
        _s135_str = ", ".join(_s135_names[:3])
        if len(_s135_names) > 3:
            _s135_str += f" +{len(_s135_names) - 3} more"
        lines.append(f"changed file size: {_s135_total_lines} lines ({_s135_str})")

    # S211: Missing co-editors — files that historically change WITH the diff files
    # but are absent from the current diff. A common source of incomplete PRs.
    # Only shown when 2+ absent co-editors exist with >= 3 co-changes each.
    if graph.root:
        try:
            from ..git import cochange_pairs as _cp211, is_git_repo as _igr211
            if _igr211(graph.root):
                _diff_src211 = [fp for fp in normalized if not _is_test_file(fp)]
                _diff_set211 = set(normalized)
                _missing211: dict[str, int] = {}
                for _fp211 in _diff_src211[:3]:  # check top-3 source files to stay fast
                    for _p211 in _cp211(graph.root, _fp211, n=8):
                        if (_p211["path"] not in _diff_set211
                                and not _is_test_file(_p211["path"])
                                and _p211["count"] >= 3):
                            _missing211[_p211["path"]] = max(
                                _missing211.get(_p211["path"], 0), _p211["count"]
                            )
                if len(_missing211) >= 2:
                    _top211 = sorted(_missing211.items(), key=lambda x: -x[1])[:3]
                    _m211_str = ", ".join(fp.rsplit("/", 1)[-1] for fp, _ in _top211)
                    if len(_missing211) > 3:
                        _m211_str += f" +{len(_missing211) - 3} more"
                    lines.append(
                        f"missing co-editors: {_m211_str}"
                        f" — usually change alongside diff files but absent here"
                    )
        except Exception:
            pass

    # S205: Tests-only diff — all diff files are test files, 0 source files.
    # A commit that only touches tests may be missing the paired implementation change.
    # Only shown when >= 2 test files are in the diff and no source files.
    _s205_src_files = [fp for fp in normalized if not _is_test_file(fp)]
    _s205_test_files = [fp for fp in normalized if _is_test_file(fp)]
    if not _s205_src_files and len(_s205_test_files) >= 2:
        _t205_names = [fp.rsplit("/", 1)[-1] for fp in _s205_test_files[:3]]
        _t205_str = ", ".join(_t205_names)
        if len(_s205_test_files) > 3:
            _t205_str += f" +{len(_s205_test_files) - 3} more"
        lines.append(
            f"tests-only diff: {len(_s205_test_files)} test files ({_t205_str}),"
            f" 0 source files — may be missing implementation changes"
        )

    # S215: Wide diff — the diff spans >= 5 distinct source files.
    # More source files = higher cognitive load to review; increases chance of missed side effects.
    # Only shown when 5+ distinct non-test files are in the diff.
    _s215_src_files = [fp for fp in normalized if not _is_test_file(fp)]
    if len(_s215_src_files) >= 5:
        _s215_names = [fp.rsplit("/", 1)[-1] for fp in _s215_src_files[:3]]
        _s215_str = ", ".join(_s215_names)
        if len(_s215_src_files) > 3:
            _s215_str += f" +{len(_s215_src_files) - 3} more"
        lines.append(
            f"wide diff: {len(_s215_src_files)} source files changed ({_s215_str})"
            f" — high review surface, consider splitting"
        )

    # S222: Dependency file change — diff includes a package manifest or lockfile.
    # Changes to requirements.txt, package.json, pyproject.toml, go.mod etc. update dependencies,
    # which may introduce breaking changes or transitive security issues.
    # Only shown when 1+ dependency file is in the diff.
    _s222_dep_names = {
        "requirements.txt", "requirements.in", "package.json", "package-lock.json",
        "yarn.lock", "pyproject.toml", "poetry.lock", "go.mod", "go.sum",
        "Pipfile", "Pipfile.lock", "Cargo.toml", "Cargo.lock",
    }
    _s222_dep_files = [
        fp for fp in changed_files
        if fp.rsplit("/", 1)[-1] in _s222_dep_names
    ]
    if _s222_dep_files:
        _dep_names = [fp.rsplit("/", 1)[-1] for fp in _s222_dep_files[:3]]
        _dep_str = ", ".join(_dep_names)
        lines.append(
            f"dependency change: {_dep_str} in diff"
            f" — check for transitive breaking changes or security advisories"
        )

    # S229: Security-sensitive change — diff includes files with security-related names.
    # Auth, crypto, token, and permission files are high-risk; changes need careful review.
    # Only shown when 1+ diff file name matches security-sensitive patterns.
    _s229_sec_patterns = (
        "auth", "crypto", "password", "token", "secret", "permission", "access",
        "login", "session", "jwt", "oauth", "ssl", "cert", "key",
    )
    _s229_sec_files = [
        fp for fp in list(normalized) + [f for f in changed_files if f not in normalized]
        if any(pat in fp.rsplit("/", 1)[-1].lower() for pat in _s229_sec_patterns)
    ]
    if _s229_sec_files:
        _sec_names = [fp.rsplit("/", 1)[-1] for fp in _s229_sec_files[:3]]
        _sec_str = ", ".join(_sec_names)
        lines.append(
            f"security-sensitive change: {_sec_str} in diff"
            f" — auth/crypto/token files require careful review"
        )

    # S235: Schema/contract change — diff includes API schema or data contract files.
    # Proto, OpenAPI, GraphQL, SQL schema changes = breaking change risk for all consumers.
    # Only shown when 1+ schema file is in the diff.
    _s235_schema_exts = (".proto", ".graphql", ".gql", ".avro")
    _s235_schema_names = ("schema.sql", "openapi.yaml", "openapi.json", "swagger.yaml",
                          "swagger.json", "schema.json", "schema.graphql")
    _s235_schema_files = [
        fp for fp in list(normalized) + [f for f in changed_files if f not in normalized]
        if fp.endswith(_s235_schema_exts) or fp.rsplit("/", 1)[-1].lower() in _s235_schema_names
    ]
    if _s235_schema_files:
        _sch_names = [fp.rsplit("/", 1)[-1] for fp in _s235_schema_files[:3]]
        _sch_str = ", ".join(_sch_names)
        lines.append(
            f"schema change: {_sch_str} in diff"
            f" — API contract change, all consumers must be updated"
        )

    # S240: Changelog/release file in diff — diff includes CHANGELOG, HISTORY, or RELEASE notes.
    # These files mark public releases; changes in the same diff should be backward-compatible
    # and well-tested — treat like a release commit.
    # Only shown when 1+ changelog-style file appears in the diff.
    _s240_changelog_names = {
        "changelog", "changelog.md", "changelog.rst", "changelog.txt",
        "history", "history.md", "history.rst", "history.txt",
        "release", "release.md", "release_notes.md", "releases.md",
        "news.rst", "changes.rst",
    }
    _s240_files = [
        fp for fp in list(normalized) + [f for f in changed_files if f not in normalized]
        if fp.rsplit("/", 1)[-1].lower() in _s240_changelog_names
    ]
    if _s240_files:
        _cl_names = [fp.rsplit("/", 1)[-1] for fp in _s240_files[:2]]
        _cl_str = ", ".join(_cl_names)
        lines.append(
            f"release commit: {_cl_str} in diff"
            f" — treat as public release; changes must be backward-compatible"
        )

    # S245: Infrastructure/environment file in diff — .env.example, docker-compose.yml,
    # Dockerfile, k8s manifests, etc. These require out-of-band coordination with ops.
    # Only shown when 1+ infra/env file appears in the diff.
    _s245_infra_exts = (".yml", ".yaml", ".toml", ".tf", ".hcl")
    _s245_infra_names = {
        "dockerfile", ".env.example", ".env.template", "docker-compose.yml",
        "docker-compose.yaml", "docker-compose.override.yml",
        ".travis.yml", ".github", "makefile", "justfile",
    }
    _s245_infra_paths = ("k8s/", "kubernetes/", "helm/", "terraform/", ".github/workflows/",
                         "deploy/", "infra/", "infrastructure/")
    _s245_files = []
    for _fp245 in list(normalized) + [f for f in changed_files if f not in normalized]:
        _name245 = _fp245.rsplit("/", 1)[-1].lower()
        _fp245_lower = _fp245.lower()
        if (
            _name245 in _s245_infra_names
            or any(_fp245_lower.startswith(p) or f"/{p}" in _fp245_lower for p in _s245_infra_paths)
        ):
            _s245_files.append(_fp245)
    if _s245_files:
        _inf_names = [fp.rsplit("/", 1)[-1] for fp in _s245_files[:3]]
        _inf_str = ", ".join(_inf_names)
        if len(_s245_files) > 3:
            _inf_str += f" +{len(_s245_files) - 3} more"
        lines.append(
            f"infra change: {_inf_str} in diff"
            f" — environment/deployment files require ops coordination"
        )


    # S254: Migration file in diff — diff includes database migration files.
    # DB migrations are irreversible in production; they require DBA review and
    # deployment coordination separate from regular code review.
    _s254_mig_dirs = ("migrations/", "migration/", "alembic/versions/", "db/migrate/",
                      "db/migrations/", "database/migrations/")
    _s254_mig_files = [
        fp for fp in list(normalized) + [f for f in changed_files if f not in normalized]
        if any(d in fp.lower().replace("\\", "/") for d in _s254_mig_dirs)
    ]
    if _s254_mig_files:
        _mig_names = [fp.rsplit("/", 1)[-1] for fp in _s254_mig_files[:3]]
        _mig_str = ", ".join(_mig_names)
        if len(_s254_mig_files) > 3:
            _mig_str += f" +{len(_s254_mig_files) - 3} more"
        lines.append(
            f"migration file: {_mig_str} in diff"
            f" — DB migrations are irreversible; coordinate with DBA before deploy"
        )


    # S267: Broad diff — changed files span 3+ top-level directories.
    # Cross-module changes increase coordination risk; changes in one module may
    # invalidate assumptions in another. Each boundary crossing needs explicit validation.
    _s261_all_changed = list(normalized) + [f for f in changed_files if f not in normalized]
    _s261_top_dirs = {
        fp.split("/")[0] for fp in _s261_all_changed
        if "/" in fp and not fp.split("/")[0].startswith(".")
    }
    if len(_s261_top_dirs) >= 3:
        _dir_list261 = sorted(_s261_top_dirs)[:3]
        _dir_str261 = ", ".join(_dir_list261)
        if len(_s261_top_dirs) > 3:
            _dir_str261 += f" +{len(_s261_top_dirs) - 3} more"
        lines.append(
            f"broad diff: changes span {len(_s261_top_dirs)} modules ({_dir_str261})"
            f" — cross-module change; verify interface contracts at each boundary"
        )


    # S273: Documentation file in diff — diff includes README, docs/, or changelog files.
    # Documentation changes may indicate API or behavior changes that need broader
    # communication; or doc changes without code changes (doc-only PR, low risk).
    _s273_doc_names = {
        "readme.md", "readme.rst", "readme.txt", "changelog.md", "changelog.rst",
        "history.md", "contributing.md", "authors.md", "license", "license.md",
    }
    _s273_doc_dirs = ("docs/", "doc/", "documentation/", ".github/")
    _s273_doc_files = []
    for _fp273 in list(normalized) + [f for f in changed_files if f not in normalized]:
        _name273 = _fp273.rsplit("/", 1)[-1].lower()
        _fp273_lower = _fp273.lower()
        if (
            _name273 in _s273_doc_names
            or any(_fp273_lower.startswith(d) or f"/{d}" in _fp273_lower for d in _s273_doc_dirs)
        ):
            _s273_doc_files.append(_fp273)
    if _s273_doc_files:
        _doc_names273 = [fp.rsplit("/", 1)[-1] for fp in _s273_doc_files[:3]]
        _doc_str273 = ", ".join(_doc_names273)
        lines.append(
            f"docs in diff: {_doc_str273}"
            f" — documentation changed; verify code changes are reflected"
        )


    # S276: Hotspot in diff — a changed file is also a top hotspot (high-churn) file.
    # Editing an already-hot file increases instability and conflict risk further.
    # Show when any changed file ranks in top-5 by cross-file caller count.
    if normalized:
        _s276_scores: list[tuple[int, str]] = []
        for _fp276 in normalized:
            if _is_test_file(_fp276):
                continue
            _callers276 = len([
                s for s in graph.symbols.values()
                if s.file_path == _fp276
                and len([c for c in graph.callers_of(s.id) if c.file_path != _fp276]) >= 2
            ])
            if _callers276 > 0:
                _s276_scores.append((_callers276, _fp276))
        _s276_scores.sort(reverse=True)
        if _s276_scores and _s276_scores[0][0] >= 3:
            _s276_n, _s276_fp = _s276_scores[0]
            lines.append(
                f"hotspot in diff: {_s276_fp.rsplit('/', 1)[-1]} is a high-churn file"
                f" ({_s276_n} widely-called symbols) — extra care needed; this file changes often"
            )


    # S282: Tests removed — diff includes removal of test files (test_*.py or *_test.py).
    # Removing tests while changing code is a coverage regression; signal for teams
    # to verify the removed tests are no longer needed (not hiding failures).
    _s282_removed_tests = [
        fp for fp in changed_files
        if _is_test_file(fp)
    ]
    if _s282_removed_tests:
        _removed_names282 = [fp.rsplit("/", 1)[-1] for fp in _s282_removed_tests[:3]]
        _removed_str282 = ", ".join(_removed_names282)
        if len(_s282_removed_tests) > 3:
            _removed_str282 += f" +{len(_s282_removed_tests) - 3} more"
        lines.append(
            f"tests in diff: {_removed_str282}"
            f" — test files modified; verify coverage isn't regressing"
        )


    # S288: Version bump — diff includes version manifest files (pyproject.toml, package.json).
    # Version changes may indicate an intentional release; they require changelog review
    # and tag coordination across repos.
    _s288_version_files = {
        "pyproject.toml", "setup.cfg", "setup.py", "package.json", "cargo.toml",
        "version.py", "version.txt", "_version.py", "__version__.py",
    }
    _s288_found = [
        fp for fp in list(normalized) + [f for f in changed_files if f not in normalized]
        if fp.rsplit("/", 1)[-1].lower() in _s288_version_files
    ]
    if _s288_found:
        _ver_names288 = [fp.rsplit("/", 1)[-1] for fp in _s288_found[:3]]
        lines.append(
            f"version file: {', '.join(_ver_names288)} in diff"
            f" — version manifest changed; verify changelog and tag are updated"
        )


    # S294: CI/CD config in diff — diff includes CI/CD pipeline configuration files.
    # CI changes affect build, test, and deploy pipelines for everyone;
    # broken CI blocks all future merges until fixed.
    _s294_ci_names = {
        ".travis.yml", ".travis.yaml", "appveyor.yml", "azure-pipelines.yml",
        "bitbucket-pipelines.yml", "circle.yml", "tox.ini", "Jenkinsfile",
        ".circleci", ".drone.yml", "codeship-services.yml",
    }
    _s294_ci_dirs = (".github/workflows/", ".circleci/", ".buildkite/", ".gitlab/")
    _s294_ci_files = []
    for _fp294 in list(normalized) + [f for f in changed_files if f not in normalized]:
        _name294 = _fp294.rsplit("/", 1)[-1].lower()
        _fp294_lower = _fp294.lower()
        if (
            _name294 in _s294_ci_names
            or any(_fp294_lower.startswith(d) or f"/{d}" in _fp294_lower for d in _s294_ci_dirs)
        ):
            _s294_ci_files.append(_fp294)
    if _s294_ci_files:
        _ci_names294 = [fp.rsplit("/", 1)[-1] for fp in _s294_ci_files[:2]]
        lines.append(
            f"CI/CD config: {', '.join(_ci_names294)} in diff"
            f" — pipeline change; broken CI blocks all future merges"
        )

    # S302: Large diff — 20+ files changed in this diff.
    # Large diffs are hard to review and test; blast radius is proportionally wider
    # and the probability of unintended side effects increases.
    _s302_total = len(changed_files)
    if _s302_total >= 20:
        lines.append(
            f"large diff: {_s302_total} files changed"
            f" — hard to review; split into smaller atomic commits if possible"
        )

    # S308: Docs-only diff — all changed files are documentation (no code impact).
    # Documentation-only changes are safe to merge without re-running the full test suite
    # but may still need proofreading and link validation.
    _s308_doc_exts = {".md", ".rst", ".txt", ".ipynb", ".adoc", ".wiki"}
    _s308_doc_names = {"README", "CHANGELOG", "CONTRIBUTING", "LICENSE", "HISTORY", "AUTHORS"}
    _s308_all_docs = all(
        any(f.lower().endswith(ext) for ext in _s308_doc_exts)
        or f.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper() in _s308_doc_names
        for f in changed_files
    )
    if changed_files and _s308_all_docs:
        lines.append(
            "docs-only diff: all changed files are documentation"
            " — no code impact; skip full test suite, focus on link/prose review"
        )

    # S313: Healthy test ratio — diff has more test lines added than production lines.
    # Diffs that improve test coverage more than they add production code signal
    # healthy TDD discipline and reduce future regression risk.
    _s313_test_files = [f for f in changed_files if _is_test_file(f)]
    _s313_prod_files = [f for f in changed_files if not _is_test_file(f)
                        and not any(f.lower().endswith(ext) for ext in {".md", ".rst", ".txt"})]
    if len(_s313_test_files) >= 2 and len(_s313_prod_files) >= 1:
        _ratio313 = len(_s313_test_files) / max(len(_s313_prod_files), 1)
        if _ratio313 >= 1.5:
            lines.append(
                f"healthy test ratio: {len(_s313_test_files)} test file(s) vs"
                f" {len(_s313_prod_files)} prod file(s)"
                f" — strong test coverage for this diff; good TDD signal"
            )

    # S319: Dependency update — diff includes package manifest or lock file changes.
    # Dependency updates introduce transitive changes that are invisible in the diff;
    # a passing test suite doesn't guarantee all transitive behavior is unchanged.
    _s319_dep_names = {
        "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.cfg",
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "go.sum", "go.mod", "Cargo.lock", "Gemfile.lock", "poetry.lock",
    }
    _s319_dep_files = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1] in _s319_dep_names
    ]
    if _s319_dep_files:
        _dep_names319 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s319_dep_files[:2])
        lines.append(
            f"dependency update: {_dep_names319} in diff"
            f" — transitive changes invisible; re-run full test suite including integration tests"
        )

    # S327: Security-sensitive diff — diff touches auth/password/token/crypto-related files.
    # Security-critical code requires extra scrutiny: review for timing attacks, secrets
    # in logs, and injection surface changes even if unit tests pass.
    _s327_sec_words = (
        "auth", "password", "passwd", "token", "secret", "crypto", "cipher",
        "jwt", "oauth", "session", "credential", "permission", "rbac", "acl",
    )
    _s327_sec_files = [
        f for f in changed_files
        if any(w in f.lower() for w in _s327_sec_words)
        and not _is_test_file(f)
    ]
    if _s327_sec_files:
        _sec_names327 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s327_sec_files[:2])
        lines.append(
            f"security-sensitive: {_sec_names327} in diff"
            f" — review for timing attacks, log leaks, and injection surface"
        )

    # S333: DB migration in diff — diff includes SQL or ORM migration files.
    # Database migrations are often irreversible and affect all running instances;
    # rollback requires explicit down-migration, which is frequently not tested.
    _s333_mig_exts = {".sql", ".migration"}
    _s333_mig_dirs = ("migrations", "migration", "alembic", "flyway", "liquibase", "db")
    _s333_mig_files: list[str] = []
    for _f333 in changed_files:
        _name333 = _f333.rsplit("/", 1)[-1].lower()
        _fp333_lower = _f333.lower().replace("\\", "/")
        if (
            any(_f333.endswith(ext) for ext in _s333_mig_exts)
            or any(d + "/" in _fp333_lower or _fp333_lower.startswith(d + "/") for d in _s333_mig_dirs)
        ):
            _s333_mig_files.append(_f333)
    if _s333_mig_files:
        _mig_names333 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s333_mig_files[:2])
        lines.append(
            f"DB migration: {_mig_names333} in diff"
            f" — schema change; test rollback path and coordinate with DBA before deploy"
        )

    # S339: Feature-flag diff — diff touches feature-flag/experiment/rollout configuration files.
    # Feature flag changes affect runtime behavior without code changes;
    # ensure flag semantics (kill switch vs gradual rollout) are reviewed.
    _s339_ff_words = ("feature_flag", "featureflag", "feature_toggle", "experiment",
                      "rollout", "flag_config", "flags", "toggles")
    _s339_ff_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s339_ff_words)
    ]
    if _s339_ff_files:
        _ff_names339 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s339_ff_files[:2])
        lines.append(
            f"feature-flag change: {_ff_names339} in diff"
            f" — flag semantics affect runtime behavior; review kill-switch vs gradual rollout"
        )

    # S345: Performance-sensitive diff — diff touches cache/query/index performance-critical files.
    # Performance-sensitive code paths are often non-obviously coupled;
    # even tiny behavioral changes (key format, cache TTL) can cause latency spikes.
    _s345_perf_words = (
        "cache", "query", "index", "performance", "optimize", "benchmark",
        "profil", "latency", "throughput",
    )
    _s345_perf_files = [
        f for f in changed_files
        if any(w in f.lower() for w in _s345_perf_words)
        and not _is_test_file(f)
    ]
    if _s345_perf_files:
        _perf_names345 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s345_perf_files[:2])
        lines.append(
            f"performance-sensitive: {_perf_names345} in diff"
            f" — profile before and after; cache TTL/key changes can cause latency spikes"
        )

    # S381: Shell/CI script change — diff touches shell scripts, CI config, or Makefile.
    # Shell scripts and CI configs control build/deploy pipelines; a single wrong variable
    # or missing quotation can cause silent build failures or deployment outages.
    _s381_ci_exts = (".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd")
    _s381_ci_names = (
        ".github", "jenkinsfile", "makefile", "dockerfile", "docker-compose",
        ".gitlab-ci", ".travis", "circle", "buildkite", ".drone", "azure-pipelines",
    )
    _s381_ci_files = [
        f for f in changed_files
        if any(f.lower().endswith(e) for e in _s381_ci_exts)
        or any(p in f.lower() for p in _s381_ci_names)
    ]
    if _s381_ci_files:
        _ci_names381 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s381_ci_files[:2])
        lines.append(
            f"CI/shell change: {_ci_names381} in diff"
            f" — pipeline changes affect build/deploy; test with a dry run before merging"
        )

    # S375: Docs-heavy diff — diff exclusively touches documentation/README/docstring files.
    # A docs-only change is the inverse of S308; docs-heavy diffs rarely affect runtime
    # behavior but may indicate documentation debt being addressed after code changes.
    _s375_doc_exts = (".md", ".rst", ".txt", ".adoc")
    _s375_doc_words = ("readme", "changelog", "docs/", "documentation", "howto", "guide")
    _s375_doc_changed = [
        f for f in changed_files
        if any(f.lower().endswith(e) for e in _s375_doc_exts)
        or any(w in f.lower() for w in _s375_doc_words)
    ]
    _s375_code_changed = [
        f for f in changed_files
        if not any(f.lower().endswith(e) for e in _s375_doc_exts)
        and not any(w in f.lower() for w in _s375_doc_words)
        and not _is_test_file(f)
    ]
    if _s375_doc_changed and not _s375_code_changed:
        lines.append(
            f"docs-heavy diff: {len(_s375_doc_changed)} doc file(s) changed, no source"
            f" — documentation update; verify doc content matches current code behavior"
        )

    # S369: Large file in diff — diff includes a file with 300+ symbols (dense file added/changed).
    # A very dense changed file likely contains a large new module or refactored logic;
    # reviewers should allocate extra time for careful review of this diff.
    if changed_files:
        _s369_dense: list[tuple[str, int]] = []
        for _cf369 in changed_files:
            _file_syms369 = [s for s in graph.symbols.values() if s.file_path == _cf369]
            if len(_file_syms369) >= 20:  # 20+ symbols = dense file
                _s369_dense.append((_cf369, len(_file_syms369)))
        if _s369_dense:
            _largest369 = max(_s369_dense, key=lambda x: x[1])
            lines.append(
                f"large file in diff: {_largest369[0].rsplit('/', 1)[-1]} has {_largest369[1]} symbols"
                f" — dense file; allocate extra review time for thorough analysis"
            )

    # S363: Test-only diff — all changed files are test files (no source touched).
    # A diff that only touches tests but no source may indicate:
    # - Snapshots/fixtures were updated without verifying the underlying behavior
    # - Tests were written for code that doesn't exist yet (TDD) — flag for reviewers.
    if changed_files:
        _s363_test_changed = [f for f in changed_files if _is_test_file(f)]
        _s363_src_changed = [f for f in changed_files if not _is_test_file(f)]
        if _s363_test_changed and not _s363_src_changed:
            lines.append(
                f"test-only diff: {len(_s363_test_changed)} test file(s) changed, 0 source files"
                f" — no source modified; verify tests reflect actual behavior, not just updated snapshots"
            )

    # S357: I18n/locale diff — diff touches internationalization or locale files.
    # Locale file changes affect user-visible strings across all language builds;
    # missing translations in one locale can cause blank labels or broken UI in that region.
    _s357_i18n_patterns = (
        "locale", "i18n", "l10n", "translation", "messages", "strings",
        "lang_", "_lang", ".po", ".pot", ".ftl",
    )
    _s357_i18n_files = [
        f for f in changed_files
        if any(p in f.lower() for p in _s357_i18n_patterns)
    ]
    if _s357_i18n_files:
        _i18n_names357 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s357_i18n_files[:2])
        lines.append(
            f"i18n change: {_i18n_names357} in diff"
            f" — locale changes affect all language builds; verify completeness across all supported locales"
        )

    # S351: Config-change diff — diff modifies YAML/TOML/INI/JSON configuration files.
    # Configuration changes often have no test coverage; a typo or wrong key silently changes
    # runtime behavior in ways that only surface in staging/production environments.
    _s351_cfg_exts = (".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".json")
    _s351_cfg_files = [
        f for f in changed_files
        if any(f.lower().endswith(ext) for ext in _s351_cfg_exts)
        and not _is_test_file(f)
    ]
    if _s351_cfg_files:
        _cfg_names351 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s351_cfg_files[:2])
        lines.append(
            f"config change: {_cfg_names351} in diff"
            f" — config changes often untested; verify expected keys and value types in all environments"
        )

    # S393: Dependency downgrade diff — diff touches requirements/package files and removes version pins.
    # Downgrading or unpinning a dependency can introduce breaking changes or security vulnerabilities;
    # dependency file diffs require extra attention to understand what changed and why.
    _s393_dep_files = (
        "requirements.txt", "requirements-dev.txt", "requirements-prod.txt",
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "Gemfile", "Gemfile.lock", "Cargo.toml", "Cargo.lock",
        "go.mod", "go.sum", "pyproject.toml", "setup.py", "setup.cfg",
    )
    _s393_changed_deps = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1].lower() in (d.lower() for d in _s393_dep_files)
    ]
    if _s393_changed_deps:
        _dep_names393 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s393_changed_deps[:2])
        lines.append(
            f"dependency change: {_dep_names393} in diff"
            f" — check for version downgrades, unpinned deps, or transitive vulnerability changes"
        )

    # S387: Breaking change risk — diff touches public API definition files.
    # Public API files (routes, endpoints, openapi specs) define contracts with callers;
    # changes here may break existing clients silently if not versioned properly.
    _s387_api_patterns = (
        "routes", "endpoints", "api", "openapi", "swagger", "v1", "v2", "v3",
        "public_api", "rest_api", "graphql",
    )
    _s387_api_files = [
        f for f in changed_files
        if any(p in f.lower().replace("-", "_").replace("/", "_") for p in _s387_api_patterns)
        and not _is_test_file(f)
        and (f.endswith(".py") or f.endswith(".ts") or f.endswith(".js")
             or f.endswith(".yaml") or f.endswith(".json"))
    ]
    if _s387_api_files:
        _api_names387 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s387_api_files[:2])
        lines.append(
            f"API change: {_api_names387} in diff"
            f" — public API contract may change; ensure clients are notified or version the endpoint"
        )

    # S399: Error handling diff — diff touches files with "error", "exception", "retry" in names.
    # Changes to error handling code are high-risk; removing a try/except, changing retry limits,
    # or narrowing exception types can turn handled failures into unhandled crashes.
    _s399_err_words = (
        "error_handler", "exception_handler", "retry", "fallback",
        "circuit_breaker", "error_boundary",
    )
    _s399_err_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s399_err_words)
        and not _is_test_file(f)
    ]
    if _s399_err_files:
        _err_names399 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s399_err_files[:2])
        lines.append(
            f"error handling change: {_err_names399} in diff"
            f" — changes to error/retry handlers can turn handled failures into crashes"
        )

    # S405: Auth/security file in diff — diff touches login/auth/permission/security files.
    # Authentication and authorization code has high blast radius for mistakes;
    # even small logic inversions (>= vs >, missing NOT) can open privilege escalation paths.
    _s405_auth_words = (
        "auth", "login", "logout", "permission", "security", "token",
        "oauth", "jwt", "session", "acl", "rbac", "privilege",
    )
    _s405_auth_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s405_auth_words)
        and not _is_test_file(f)
    ]
    if _s405_auth_files:
        _auth_names405 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s405_auth_files[:2])
        lines.append(
            f"auth/security change: {_auth_names405} in diff"
            f" — auth logic errors can escalate privileges; get a second reviewer"
        )

    # S411: Database migration in diff — diff touches a migration file.
    # Migration files change the database schema; running them is irreversible in production
    # and must be tested with a database rollback plan.
    _s411_mig_patterns = (
        "migration", "migrate", "alembic", "flyway",
        "liquibase", "schema_change", "db_change",
    )
    _s411_mig_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s411_mig_patterns)
    ]
    if _s411_mig_files:
        _mig_names411 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s411_mig_files[:2])
        lines.append(
            f"db migration: {_mig_names411} in diff"
            f" — schema changes are irreversible in production; verify rollback plan before deploying"
        )

    # S417: Feature flag file in diff — diff touches feature flag or toggle configuration.
    # Feature flag changes affect runtime behavior without code deployment; a single toggle
    # can change user-visible behavior instantly across all instances.
    _s417_ff_patterns = (
        "feature_flag", "feature_toggle", "flags", "toggles",
        "feature_config", "rollout", "launch_darkly", "flipper",
    )
    _s417_ff_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s417_ff_patterns)
    ]
    if _s417_ff_files:
        _ff_names417 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s417_ff_files[:2])
        lines.append(
            f"feature flag change: {_ff_names417} in diff"
            f" — flag changes affect runtime behavior instantly; test with both flag states"
        )

    # S423: Test-only diff — all changed files are test files.
    # A diff that modifies only tests (no production code) is unusual; it may indicate
    # test-only fixes after a regression or orphaned test cleanup work.
    if changed_files:
        _s423_all_test = all(_is_test_file(f) for f in changed_files)
        if _s423_all_test and len(changed_files) >= 1:
            _test_names423 = ", ".join(f.rsplit("/", 1)[-1] for f in changed_files[:2])
            lines.append(
                f"test-only diff: {_test_names423} — all {len(changed_files)} changed file(s) are tests"
                f" — verify matching production changes aren't missing from this diff"
            )

    # S429: Infrastructure file in diff — diff touches Dockerfile, CI/CD, or infra config.
    # Infrastructure file changes affect the deployment environment, not just the code;
    # a wrong config can break all deployments or expose the service to the internet.
    _s429_infra_names = (
        "dockerfile", "docker-compose", "docker_compose", ".github",
        "kubernetes", "k8s", "terraform", "ansible", "ci.yml",
        "pipeline.yml", ".circleci", "jenkinsfile",
    )
    _s429_infra_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s429_infra_names)
    ]
    if _s429_infra_files:
        _infra_names429 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s429_infra_files[:2])
        lines.append(
            f"infra change: {_infra_names429} in diff"
            f" — deployment environment changes; test in staging before deploying to production"
        )

    # S435: Version bump in diff — diff touches version or changelog files.
    # Version bumps should be synchronised with the actual change scope; bumping a version
    # without updating dependencies or changelogs (or vice versa) causes silent drift and
    # misleads consumers of the package about what changed.
    _s435_version_names = (
        "version", "changelog", "changes", "history", "release",
    )
    _s435_version_files_exact = ("version.py", "VERSION", "version.txt", "_version.py")
    _s435_version_files = [
        f for f in changed_files
        if (
            f.rsplit("/", 1)[-1].lower() in {n.lower() for n in _s435_version_files_exact}
            or any(
                w in f.rsplit("/", 1)[-1].lower()
                for w in _s435_version_names
            )
        )
    ]
    if _s435_version_files:
        _ver_names435 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s435_version_files[:2])
        lines.append(
            f"version bump: {_ver_names435} in diff"
            f" — ensure changelog, dependencies, and semver scope are all in sync"
        )

    # S441: Serialization file in diff — diff touches serialization/deserialization logic.
    # Serialization changes are wire-protocol changes; any consumer (client, queue consumer,
    # stored data) that expects the old shape will silently corrupt on the new format.
    _s441_serial_keywords = ("serial", "deserial", "marshal", "unmarshal", "encode", "decode", "codec")
    _s441_serial_files = [
        f for f in changed_files
        if any(kw in f.rsplit("/", 1)[-1].lower() for kw in _s441_serial_keywords)
    ]
    if _s441_serial_files:
        _ser_name441 = _s441_serial_files[0].rsplit("/", 1)[-1]
        lines.append(
            f"serialization change: {_ser_name441} in diff"
            f" — wire-format changes break all existing consumers; bump version or add migration"
        )

    # S454: Auth/security diff — diff touches authentication, authorization, or cryptography code.
    # Auth changes are high-risk: a logic error can grant unauthorized access or lock out
    # legitimate users. These files need security review even for seemingly minor changes.
    _s454_auth_keywords = (
        "auth", "login", "logout", "password", "token", "session", "permission",
        "credential", "secret", "jwt", "oauth", "crypto", "encrypt", "hash",
        "access_control", "acl", "rbac",
    )
    _s454_auth_files = [
        f for f in changed_files
        if any(kw in f.lower().replace("-", "_") for kw in _s454_auth_keywords)
    ]
    if _s454_auth_files:
        _auth_names454 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s454_auth_files[:2])
        lines.append(
            f"auth/security change: {_auth_names454} in diff"
            f" — authentication/cryptography logic; requires security review before merging"
        )

    # S460: Schema migration in diff — diff touches database migration files.
    # Migrations are irreversible in production; an incorrect migration can corrupt
    # the database, and a rolled-back deploy may leave the schema in a broken state.
    _s460_migration_words = ("migration", "migrate", "alembic", "flyway", "liquibase", "schema")
    _s460_migration_files = [
        f for f in changed_files
        if any(w in f.lower().replace("-", "_") for w in _s460_migration_words)
        or "/migrations/" in f.replace("\\", "/")
        or "/migrate/" in f.replace("\\", "/")
    ]
    if _s460_migration_files:
        _mig_names460 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s460_migration_files[:2])
        lines.append(
            f"schema migration: {_mig_names460} in diff"
            f" — database schema changes are irreversible in production; test rollback path"
        )

    # S465: Large file touched — diff includes a file with 500+ lines.
    # Large files concentrate risk; any change is adjacent to unrelated logic,
    # increasing the chance of accidental breakage or merge conflicts.
    _s465_large_touched = [
        fp for fp in normalized
        if fp in graph.files and graph.files[fp].line_count and graph.files[fp].line_count >= 500
    ]
    if _s465_large_touched:
        _lg_name465 = _s465_large_touched[0].rsplit("/", 1)[-1]
        _lg_lines465 = graph.files[_s465_large_touched[0]].line_count
        lines.append(
            f"large file touched: {_lg_name465} ({_lg_lines465:,} lines)"
            f" — changes are adjacent to unrelated logic; review surrounding context carefully"
        )

    # S471: Dependency update in diff — diff includes a lock file or requirements file.
    # Lock file changes indicate transitive dependency upgrades; any indirect dependency
    # could introduce incompatible APIs or security vulnerabilities without being obvious from the diff.
    _s471_lock_names = (
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "requirements.txt", "requirements-dev.txt", "pipfile.lock",
        "poetry.lock", "cargo.lock", "go.sum", "gemfile.lock",
    )
    _s471_lock_files = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1].lower() in {n.lower() for n in _s471_lock_names}
        or f.rsplit("/", 1)[-1].lower().startswith("requirements")
    ]
    if _s471_lock_files:
        _lock_name471 = _s471_lock_files[0].rsplit("/", 1)[-1]
        lines.append(
            f"dependency update: {_lock_name471} in diff"
            f" — transitive dependency upgrades may introduce incompatible APIs or vulnerabilities"
        )

    # S485: Base class touched — diff contains a file that defines a Base* or Abstract* class.
    # Changes to base classes cascade to every subclass; a renamed method or added required
    # argument breaks all derivatives that don't override it.
    _s485_base_prefixes = ("Base", "Abstract", "Mixin", "Interface")
    _s485_base_files: list[str] = []
    for _fp485 in changed_files:
        _syms485 = [
            s for s in graph.symbols.values()
            if s.file_path == _fp485 and s.kind.value == "class"
            and any(s.name.startswith(p) for p in _s485_base_prefixes)
        ]
        if _syms485:
            _s485_base_files.append(_fp485)
    if _s485_base_files:
        _names485 = ", ".join(f.rsplit("/", 1)[-1] for f in _s485_base_files[:3])
        lines.append(
            f"base class touched: {_names485} defines a base/abstract class"
            f" — changes cascade to all subclasses; check every derivative for compatibility"
        )

    # S491: Diff touches a test fixture file — conftest.py or shared fixture module in diff.
    # Fixture changes are invisible to individual tests but propagate to every consumer;
    # a subtle fixture change can flip hundreds of test results without a clear error.
    _s491_fixture_names = ("conftest.py", "fixtures.py", "test_helpers.py", "test_utils.py")
    _s491_fixture_files = [
        f for f in changed_files
        if any(f.rsplit("/", 1)[-1].lower() == fn for fn in _s491_fixture_names)
        or f.rsplit("/", 1)[-1].lower().endswith("_fixtures.py")
    ]
    if _s491_fixture_files:
        _fix_name491 = _s491_fixture_files[0].rsplit("/", 1)[-1]
        lines.append(
            f"fixture touched: {_fix_name491} is a shared test fixture"
            f" — changes propagate silently to all dependent tests; run the full test suite"
        )

    # S497: Large diff surface — diff spans 10+ files.
    # Very wide diffs are hard to review atomically; reviewers miss interactions between distant
    # changes and the probability of a hidden regression grows with diff breadth.
    if len(changed_files) >= 10:
        lines.append(
            f"large diff: {len(changed_files)} files changed"
            f" — wide diffs increase review blind-spots; consider splitting into smaller PRs"
        )

    # S502: Public API change — diff contains a file that defines exported symbols used externally.
    # Changes to publicly exported APIs break all downstream consumers silently;
    # any rename, signature change, or removal requires a compatibility audit.
    _s502_api_keywords = ("api", "public", "interface", "export", "schema", "contract")
    _s502_api_files = [
        f for f in changed_files
        if any(kw in f.lower().replace("_", "").replace("-", "") for kw in _s502_api_keywords)
        and not _is_test_file(f)
    ]
    if _s502_api_files:
        lines.append(
            f"public API change: {len(_s502_api_files)} API-named file(s) changed"
            f" ({', '.join(f.rsplit('/', 1)[-1] for f in _s502_api_files[:2])})"
            f" — verify all downstream consumers are updated before merging"
        )

    # S509: ORM model touched — diff includes an ORM model or entity file.
    # ORM model changes affect database schema, serialization, and all query code;
    # any field rename or type change requires a migration and query audit.
    _s509_orm_keywords = ("model", "entity", "orm", "schema", "table", "record", "domain")
    _s509_orm_files = [
        f for f in changed_files
        if any(kw in f.lower().replace("_", "").replace("-", "") for kw in _s509_orm_keywords)
        and not _is_test_file(f)
    ]
    if _s509_orm_files:
        lines.append(
            f"ORM model touched: {len(_s509_orm_files)} model/entity file(s) changed"
            f" ({', '.join(f.rsplit('/', 1)[-1] for f in _s509_orm_files[:2])})"
            f" — check for required migrations and audit all queries against changed fields"
        )

    # S534: Hot path diff — diff includes a file containing a top-5 most-called symbol.
    # Changing a file with hotspot symbols risks breaking the most-used code paths;
    # even a refactor-only change to a hot file needs extra testing at the call sites.
    if graph.symbols and changed_files:
        _normalized534 = {f.replace("\\", "/") for f in changed_files}
        _caller_counts534: list[tuple[int, str, str]] = []
        for _sym534 in graph.symbols.values():
            if not _is_test_file(_sym534.file_path) and _sym534.kind.value in ("function", "method"):
                _n534 = len(graph.callers_of(_sym534.id))
                if _n534 >= 3:
                    _caller_counts534.append((_n534, _sym534.name, _sym534.file_path))
        _caller_counts534.sort(reverse=True)
        _top5_files534 = {fp for _, _, fp in _caller_counts534[:5]}
        _hot_changed534 = _top5_files534 & _normalized534
        if _hot_changed534:
            _top534 = next(
                (name for _, name, fp in _caller_counts534 if fp in _hot_changed534), "?"
            )
            _n_top534 = next(
                (n for n, _, fp in _caller_counts534 if fp in _hot_changed534), 0
            )
            lines.append(
                f"hot path diff: diff touches {next(iter(_hot_changed534)).rsplit('/', 1)[-1]}"
                f" which contains {_top534} ({_n_top534} callers) — hot path; test all call sites"
            )

    # S528: Complexity spike — diff touches the highest-complexity function in the repo.
    # Modifying the most complex symbol in the codebase is the highest-risk single change possible;
    # it has the most execution paths to test and is often already the most brittle part of the system.
    if graph.symbols:
        _s528_top = max(
            (s for s in graph.symbols.values() if not _is_test_file(s.file_path) and s.complexity),
            key=lambda s: s.complexity or 0,
            default=None,
        )
        if _s528_top and (_s528_top.complexity or 0) >= 10:
            _normalized528 = [f.replace("\\", "/") for f in changed_files]
            if _s528_top.file_path.replace("\\", "/") in _normalized528:
                lines.append(
                    f"complexity spike: diff touches {_s528_top.name} (complexity {_s528_top.complexity})"
                    f" — highest-complexity symbol in repo; most execution paths to test; proceed carefully"
                )

    # S522: Init file in diff — diff includes __init__.py package init files.
    # __init__.py changes alter a package's public re-export surface; removing or renaming
    # an exported name here breaks all direct consumers without any symbol-level change.
    _s522_init_files = [f for f in changed_files if f.rsplit("/", 1)[-1] == "__init__.py"]
    if _s522_init_files:
        lines.append(
            f"init file in diff: {len(_s522_init_files)} __init__.py file(s) changed"
            f" — package re-export surface may have changed; audit all import-from consumers"
        )

    # S516: Generated file in diff — diff includes auto-generated source files.
    # Generated files should not be manually edited; changes are overwritten on next codegen run.
    # If a generated file appears in a diff, the generator input (proto, schema, spec) should change too.
    _s516_gen_markers = ("_pb2", "_generated", "_auto.", "/generated/", "_grpc", ".auto.")
    _s516_gen_files = [
        f for f in changed_files
        if any(m in f.lower() for m in _s516_gen_markers)
    ]
    if _s516_gen_files:
        lines.append(
            f"generated file in diff: {len(_s516_gen_files)} auto-generated file(s) changed"
            f" ({', '.join(f.rsplit('/', 1)[-1] for f in _s516_gen_files[:2])})"
            f" — do not manually edit generated files; update the generator input and re-run codegen"
        )

    # S540: Test-only diff — diff contains exclusively test files with no source changes.
    # A test-only diff may still signal risk: test removals can silently drop coverage;
    # fixture changes affect all tests that share them; infra changes alter how ALL tests run.
    if changed_files:
        _s540_test_count = sum(1 for f in changed_files if _is_test_file(f))
        if _s540_test_count == len(changed_files):
            lines.append(
                f"test-only diff: {_s540_test_count} test file(s) changed, no source files touched"
                f" — verify tests still cover intended source behavior; shared fixture changes affect many tests"
            )

    # S543: Unindexed files in diff — 2+ changed files are not present in the graph.
    # Files absent from the graph were deleted, renamed, or never indexed; they can't be analyzed
    # statically — confirm removals are intentional and no consumers were missed.
    _graph_fps = {fp.replace("\\", "/") for fp in graph.files}
    _unindexed540 = [
        f for f in changed_files
        if f.replace("\\", "/") not in _graph_fps and not _is_test_file(f)
    ]
    if len(_unindexed540) >= 2:
        _ui_names540 = ", ".join(f.rsplit("/", 1)[-1] for f in _unindexed540[:3])
        if len(_unindexed540) > 3:
            _ui_names540 += f" +{len(_unindexed540) - 3} more"
        lines.append(
            f"unindexed files: {len(_unindexed540)} changed file(s) not in graph ({_ui_names540})"
            f" — deleted or renamed; confirm removals are intentional and consumers were updated"
        )

    # S549: Large diff — 8+ files changed in a single diff.
    # Broad diffs reduce reviewer attention per file and increase the probability of
    # missed errors; each additional file adds compounding review fatigue.
    if len(changed_files) >= 8:
        lines.append(
            f"large diff: {len(changed_files)} files changed"
            f" — broad diffs reduce per-file reviewer attention; consider splitting into smaller PRs"
        )

    # S555: Lock file in diff — diff includes a dependency lock file.
    # Lock file changes indicate dependency upgrades; upgrades can silently introduce
    # breaking changes, security fixes, or behavioral regressions in transitive deps.
    _lock_names555 = frozenset((
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "pipfile.lock", "poetry.lock", "pdm.lock",
        "gemfile.lock", "cargo.lock", "composer.lock",
    ))
    _lock_files555 = [
        f for f in changed_files
        if f.replace("\\", "/").rsplit("/", 1)[-1].lower() in _lock_names555
    ]
    if _lock_files555:
        _lf_name555 = _lock_files555[0].rsplit("/", 1)[-1]
        lines.append(
            f"lock file changed: {_lf_name555} modified"
            f" — dependency upgrade; audit changelogs and test transitive behavior before merging"
        )

    # S567: Schema/migration file in diff — diff includes database schema or migration files.
    # Schema changes alter the database contract; applying them without testing on staging
    # first can corrupt data, break indexes, or leave the DB in a half-migrated state.
    _s567_schema_markers = ("schema.sql", "migration", "alembic", "flyway", "liquibase", "_migrate")
    _s567_schema_files = [
        f for f in changed_files
        if any(m in f.lower() for m in _s567_schema_markers)
    ]
    if _s567_schema_files:
        lines.append(
            f"schema migration in diff: {len(_s567_schema_files)} migration/schema file(s) changed"
            f" ({', '.join(f.rsplit('/', 1)[-1] for f in _s567_schema_files[:2])})"
            f" — always test migrations on a staging copy before applying to production"
        )

    # S573: Init file in diff — diff includes a package __init__.py.
    # __init__.py changes alter the package's public interface; adding or removing
    # re-exports can silently break downstream importers that relied on the old interface.
    _init_files573 = [
        f for f in changed_files
        if f.replace("\\", "/").rsplit("/", 1)[-1] == "__init__.py"
    ]
    if _init_files573:
        _init_name573 = _init_files573[0].replace("\\", "/")
        lines.append(
            f"init file changed: {_init_name573} modified"
            f" — __init__.py changes alter the package's public API; re-export additions/removals break downstream importers"
        )

    # S579: Binary or media file in diff — diff includes image, font, or compiled binary files.
    # Binary files cannot be meaningfully reviewed in text-based code review; large binary
    # changes can bloat the repo and are irreversible once merged.
    _binary_exts579 = (
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
        ".ttf", ".woff", ".woff2", ".eot",
        ".exe", ".dll", ".so", ".dylib", ".pyc",
        ".zip", ".tar", ".gz", ".pdf",
    )
    _binary_files579 = [
        f for f in changed_files
        if any(f.lower().endswith(e) for e in _binary_exts579)
    ]
    if _binary_files579:
        _bin_name579 = _binary_files579[0].rsplit("/", 1)[-1]
        lines.append(
            f"binary file changed: {_bin_name579} ({len(_binary_files579)} binary/media file(s))"
            f" — cannot be meaningfully reviewed; large binaries bloat the repo permanently"
        )

    # S584: Version file in diff — diff includes a version declaration file.
    # Version bumps in the same diff as feature code can create ambiguous release boundaries;
    # agents should confirm whether the version change is intentional and complete.
    _version_markers584 = ("version.py", "_version.py", "VERSION", "version.txt")
    _version_files584 = [
        f for f in changed_files
        if f.rsplit("/", 1)[-1] in _version_markers584
        or f.lower().endswith("pyproject.toml")
        or f.lower().endswith("setup.cfg")
        or f.lower().endswith("package.json")
        or f.lower() == "version"
    ]
    if _version_files584:
        _ver_name584 = _version_files584[0].rsplit("/", 1)[-1]
        lines.append(
            f"version file in diff: {_ver_name584} changed alongside code"
            f" — confirm version bump is intentional and changelog is updated"
        )

    # S590: Cross-module diff — changed files span 3+ distinct top-level packages/directories.
    # Diffs that touch many separate modules simultaneously are harder to review atomically
    # and increase the risk of subtle interaction bugs between the changed areas.
    if changed_files:
        _top_dirs590 = {
            f.replace("\\", "/").split("/")[0]
            for f in changed_files
            if "/" in f.replace("\\", "/")
        }
        if len(_top_dirs590) >= 3:
            _dir_list590 = ", ".join(sorted(_top_dirs590)[:4])
            lines.append(
                f"cross-module diff: {len(changed_files)} files across {len(_top_dirs590)} top-level packages ({_dir_list590})"
                f" — wide-scope diff; review each module's invariants independently"
            )

    # S596: Changelog or readme in diff — diff includes documentation files.
    # Documentation changes alongside code are good; documentation-only changes
    # (without source) may indicate stale docs being retroactively updated.
    _doc_markers596 = ("CHANGELOG", "CHANGES", "HISTORY", "RELEASE", "README", "CONTRIBUTING", "NOTICE")
    _doc_files596 = [
        f for f in changed_files
        if any(f.rsplit("/", 1)[-1].upper().startswith(m) for m in _doc_markers596)
    ]
    if _doc_files596:
        _doc_name596 = _doc_files596[0].rsplit("/", 1)[-1]
        _has_src596 = any(not _is_test_file(f) and f not in _doc_files596 for f in changed_files)
        _paired596 = "paired with source changes" if _has_src596 else "no accompanying source changes"
        lines.append(
            f"docs in diff: {_doc_name596} changed ({_paired596})"
            f" — verify documentation accurately reflects the current code state"
        )

    return "\n".join(lines)
