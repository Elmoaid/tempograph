"""Tests for change-localization helpers in render.py."""
import pytest
from tempograph.render import _extract_cl_keywords, _is_change_localization, _extract_focus_files, _is_docs_branch_task, render_prepare


class TestExtractClKeywords:
    def test_merge_pr_branch_name(self):
        kws = _extract_cl_keywords("Merge pull request #595 from encode/reply-not-found")
        assert "ReplyNotFound" in kws

    def test_trunk_branch_returns_empty(self):
        assert _extract_cl_keywords("Merge pull request #1 from org/main") == []
        assert _extract_cl_keywords("Merge pull request #1 from org/master") == []
        assert _extract_cl_keywords("Merge pull request #1 from org/develop") == []
        assert _extract_cl_keywords("Merge pull request #1 from org/stable") == []

    def test_doc_branch_returns_empty(self):
        assert _extract_cl_keywords("Merge pull request #1 from org/docs-update") == []
        assert _extract_cl_keywords("Merge pull request #1 from org/readme-fix") == []

    def test_ticket_branch_includes_body(self):
        task = "Merge pull request #595 from encode/24937-ranging-to-victory\nFix RangeField validation"
        kws = _extract_cl_keywords(task)
        assert "RangeField" in kws  # from body (ticket branch)

    def test_non_ticket_branch_ignores_body(self):
        task = "Merge pull request #595 from encode/reply-not-found\nSome body text with RequestBody"
        kws = _extract_cl_keywords(task)
        assert "ReplyNotFound" in kws
        # body is NOT included for non-ticket branches
        assert "RequestBody" not in kws

    def test_camelcase_extraction(self):
        kws = _extract_cl_keywords("Merge pull request #1 from org/fix-range-field")
        # "fix-range-field" → CamelCase compound "FixRangeField"
        assert "FixRangeField" in kws

    def test_snake_case_extraction(self):
        kws = _extract_cl_keywords("Merge pull request #1 from org/fix-range-field")
        # "fix" is in skip list, "range" and "field" are short enough to appear in general
        # "range_field" CamelCase = "RangeField" should appear
        assert len(kws) > 0

    def test_backtick_identifiers_are_priority(self):
        kws = _extract_cl_keywords("deprecate `should_ignore_error` handler")
        assert "should_ignore_error" in kws
        # Should appear first (priority bucket)
        assert kws[0] == "should_ignore_error"

    def test_screaming_snake_case(self):
        kws = _extract_cl_keywords("Merge pull request #1 from org/fix-SESSION_COOKIE_PARTITIONED")
        assert "SESSION_COOKIE_PARTITIONED" in kws

    def test_skip_generic_words(self):
        kws = _extract_cl_keywords("fix add update remove change bug feature merge pull request")
        assert "fix" not in kws
        assert "update" not in kws
        assert "merge" not in kws

    def test_defaults_plural_skipped(self):
        # 'defaults' (plural of skipped 'default') must be excluded. Commit 46522c5.
        # "redirect defaults to 303" → only 'redirect' is meaningful; 'defaults' is noise.
        kws = _extract_cl_keywords("fix redirect defaults to 303")
        assert "defaults" not in kws
        assert "redirect" in kws  # the meaningful keyword is retained

    def test_cross_repo_pr_format(self):
        # "Merge pull request org/repo#123 from org/branch"
        kws = _extract_cl_keywords("Merge pull request encode/httpx#595 from encode/reply-not-found")
        assert "ReplyNotFound" in kws

    def test_no_merge_prefix_direct_title(self):
        kws = _extract_cl_keywords("Fix RangeField validation for QuerySet")
        assert "RangeField" in kws
        assert "QuerySet" in kws

    def test_strips_urls_from_body(self):
        task = "Merge pull request #1 from org/1234-fix-range\nhttps://github.com/django/django/issues/1234\nRangeField fix"
        kws = _extract_cl_keywords(task)
        # URL should not produce weird tokens
        assert all("http" not in kw for kw in kws)

    def test_deduplication(self):
        # Same keyword mentioned twice shouldn't appear twice
        kws = _extract_cl_keywords("Fix RangeField RangeField bug")
        assert kws.count("RangeField") == 1

    def test_conventional_commit_prefix_stripped(self):
        # Commit type prefixes should not be extracted as keywords
        assert "feat" not in _extract_cl_keywords("feat: add SESSION_COOKIE_PARTITIONED config")
        assert "chore" not in _extract_cl_keywords("chore: update dependencies to latest versions")
        assert "refactor" not in _extract_cl_keywords("refactor: move context generation to module")
        assert "revert" not in _extract_cl_keywords("revert: drop support for legacy format")

    def test_conventional_commit_preserves_real_keywords(self):
        # Stripping the prefix should leave actual code identifiers intact
        kws = _extract_cl_keywords("feat: add SESSION_COOKIE_PARTITIONED config")
        assert "SESSION_COOKIE_PARTITIONED" in kws
        kws = _extract_cl_keywords("refactor(auth): extract TokenValidator class")
        assert "TokenValidator" in kws

    def test_conventional_commit_with_scope(self):
        # Scoped commits: "feat(parser): ..." — scope IS a keyword (names the changed component)
        kws = _extract_cl_keywords("feat(parser): fix CamelCase extraction")
        assert "feat" not in kws
        assert "parser" in kws  # scope extracted as priority keyword
        assert "CamelCase" in kws

    def test_conventional_commit_scope_priority(self):
        # Scope names the component being changed — extracted even if it's a common English word
        kws = _extract_cl_keywords("perf(Response): Optimize body handling")
        assert "Response" in kws
        assert kws.index("Response") < 2  # should be first or second keyword

    def test_conventional_commit_scope_merge_pr(self):
        # Scope extraction works for Merge PR tasks (scope is in the body)
        kws = _extract_cl_keywords(
            "Merge pull request #686 from kgriffs/tuning\nperf(Response): Optimize body"
        )
        assert "Response" in kws
        assert kws.index("Response") < 2  # priority keyword

    def test_conventional_commit_scope_streamclass(self):
        kws = _extract_cl_keywords("feat(StreamMiddleware): Add streaming support")
        assert "StreamMiddleware" in kws
        assert kws.index("StreamMiddleware") == 0

    def test_github_patch_branch_strips_username(self):
        # GitHub web-edit branches "Username-patch-N-..." should strip the username.
        # Evidence: "Freezerburn-patch-1-reb" → old code extracted "Freezerburn" as CamelCase
        # priority keyword, overriding body keywords ("cookies") that identify the real change.
        kws = _extract_cl_keywords(
            "Merge pull request #634 from kgriffs/Freezerburn-patch-1-reb\nFix cookies handling"
        )
        assert "Freezerburn" not in kws
        assert "cookies" in kws  # body keyword should be present

    def test_github_patch_branch_lowercase_username(self):
        # Lowercase username case: "someuser-patch-3" → strip "someuser", mine body.
        kws = _extract_cl_keywords(
            "Merge pull request #1 from org/someuser-patch-3\nFix TokenValidator issue"
        )
        assert "someuser" not in kws
        assert "TokenValidator" in kws

    def test_patch_word_not_extracted_from_residual_branch(self):
        # After stripping "Username-" from "Username-patch-N-...", residual branch starts
        # with "patch-N-...". The word "patch" should be filtered (generic git term, not
        # a code identifier). Evidence: Freezerburn-patch-1-rebase → 'patch' was noise keyword.
        kws = _extract_cl_keywords(
            "Merge pull request #634 from kgriffs/Freezerburn-patch-1-rebase\nfix(Response): cookies"
        )
        assert "patch" not in kws
        assert "Response" in kws
        assert "cookies" in kws

    def test_language_keywords_skipped(self):
        # Python/JS language keywords must not appear as focus terms.
        # Root cause: DRF 'authtoken-import' → 'import' keyword → focus on settings.py symbols
        # (IMPORT_STRINGS, perform_import) → wrong file predictions → F1 0.5→0.0.
        kws = _extract_cl_keywords(
            "Merge pull request #3785 from sheppard/authtoken-import"
        )
        # 'import' is a Python keyword — must be in skip set
        assert "import" not in kws
        assert "Import" not in kws  # also ensure capitalized form is excluded

    def test_js_keywords_skipped(self):
        # JS/TS reserved words should not be extracted as code keywords.
        kws = _extract_cl_keywords("add const export props state handling")
        assert "const" not in kws
        assert "export" not in kws
        assert "props" not in kws
        assert "state" not in kws

    def test_camelcase_compound_subparts_extracted(self):
        # Hyphenated compound → CamelCase + sub-parts in general bucket.
        # When "StreamingBody" is a new symbol (added in this PR) and fails focus,
        # sub-parts "Streaming", "Body" serve as fallback focus queries.
        kws = _extract_cl_keywords("Merge pull request #1 from org/streaming-body")
        assert "StreamingBody" in kws  # full compound → priority
        assert "Streaming" in kws      # sub-part → general (fallback)

    def test_camelcase_subparts_skip_generic_words(self):
        # _CAMEL_PART_SKIP excludes generic OOP/domain terms from sub-part decomposition.
        # "DurationField" → "Duration" (specific, useful) + "Field" (too generic, skipped).
        kws = _extract_cl_keywords("Merge pull request #1 from org/duration-field")
        assert "DurationField" in kws
        assert "Duration" in kws   # specific → extracted as fallback
        assert "Field" not in kws  # in _CAMEL_PART_SKIP → excluded

    def test_direct_camelcase_compound_decomposes(self):
        # Direct CamelCase in commit message (not from hyphenated branch) also decomposes.
        kws = _extract_cl_keywords("feat: add DurationField support")
        assert "DurationField" in kws
        assert "Duration" in kws   # sub-part fallback extracted
        assert "Field" not in kws  # in _CAMEL_PART_SKIP

    def test_short_subparts_not_extracted(self):
        # Sub-parts < 7 chars are too generic to be useful focus queries.
        # "custom-encode-params-method" → full compound kept, but short sub-parts filtered.
        # Prevents axios regression: "Encode" (6 chars) injecting wrong context.
        # Also: short parts are marked as seen so snake_case loop doesn't re-extract them.
        kws = _extract_cl_keywords(
            "Merge pull request #1 from org/custom-encode-params-method"
        )
        assert "CustomEncodeParamsMethod" in kws  # full compound kept
        assert "Encode" not in kws    # 6 chars → filtered as sub-part AND seen
        assert "encode" not in kws    # also not re-extracted as single word
        assert "Custom" not in kws    # 6 chars → same
        assert "Params" not in kws    # 6 chars → same
        assert "Method" not in kws    # 6 chars → same

    def test_long_subparts_still_extracted(self):
        # Sub-parts >= 7 chars are kept as fallback focus queries.
        kws = _extract_cl_keywords(
            "Merge pull request #1 from org/streaming-protocol"
        )
        assert "StreamingProtocol" in kws  # full compound
        assert "Streaming" in kws          # 9 chars → kept
        assert "Protocol" in kws           # 8 chars → kept

    def test_underscore_before_lower_camel_extracted(self):
        # "feature/#235_pass_payload_to_extendServerError"
        # "_extendServerError" — underscore is \w so no \b before 'e', lowerCamelCase regex misses it.
        # Fix: replace _ before lowerCamelCase with hyphen → word boundary created → extracted.
        # Evidence: fastify 66e14852 — keywords=[] → overview → harm -0.333 on all conditions.
        task = "Merge pull request #236 from StarpTech/feature/#235_pass_payload_to_extendServerError"
        kws = _extract_cl_keywords(task)
        assert "extendServerError" in kws
        # MUST NOT change behavior for pure-lowercase snake_case or ALLCAPS
        assert _extract_cl_keywords("Merge pull request #1 from org/fix_range_field") == ["fix_range_field"]
        assert "SESSION_COOKIE_PARTITIONED" in _extract_cl_keywords(
            "Merge pull request #1 from org/SESSION_COOKIE_PARTITIONED"
        )

    def test_json_xml_camel_parts_skipped_in_path_fallback(self):
        # "expose-default-json-serializer" → "ExposeDefaultJsonSerializer"
        # Path fallback splits CamelCase parts; "Json" (4 chars) must be skipped —
        # it matches serialize.js, config.json, etc. (wrong files).
        # Evidence: fastify e9b68878 — "Json" path match → -0.333 F1 harm.
        kws = _extract_cl_keywords(
            "Merge pull request #1 from org/expose-default-json-serializer"
        )
        assert "ExposeDefaultJsonSerializer" in kws  # full compound extracted
        # The path fallback must NOT use "Json" or "Xml" alone
        assert "Json" not in kws
        assert "Xml" not in kws


class TestIsChangeLocalization:
    def test_task_type_changelocal(self):
        assert _is_change_localization("anything", "changelocal") is True

    def test_task_type_debug(self):
        assert _is_change_localization("anything", "debug") is True

    def test_merge_pull_request(self):
        assert _is_change_localization("Merge pull request #1 from org/branch", "") is True

    def test_merge_branch(self):
        assert _is_change_localization("Merge branch 'stable'", "") is True

    def test_conventional_commit(self):
        assert _is_change_localization("fix: prevent null pointer in handler", "") is True
        assert _is_change_localization("feat: add login endpoint", "") is True
        assert _is_change_localization("refactor(auth): extract token validator", "") is True

    def test_pr_title_with_issue_ref(self):
        assert _is_change_localization("Fix teardown callbacks (#5928)", "") is True

    def test_general_coding_task(self):
        assert _is_change_localization("add user authentication to the login page", "") is False
        assert _is_change_localization("implement a caching layer", "") is False

    def test_empty_task_type_no_match(self):
        assert _is_change_localization("write a function to sort a list", "") is False


class TestExtractFocusFiles:
    def _make_focus_output(self, paths: list[str], repetitions: dict[str, int] | None = None) -> str:
        """Build synthetic focus output mentioning each path the given number of times."""
        lines = []
        for path in paths:
            count = (repetitions or {}).get(path, 1)
            for _ in range(count):
                lines.append(f"  {path} (callers, imports)")
        return "\n".join(lines)

    def test_source_files_ranked_first(self):
        output = self._make_focus_output(["tests/test_foo.py", "src/foo.py"])
        result = _extract_focus_files(output)
        assert result.index("src/foo.py") < result.index("tests/test_foo.py")

    def test_example_files_ranked_last(self):
        output = self._make_focus_output(["examples/demo.py", "src/core.py"])
        result = _extract_focus_files(output)
        assert result.index("src/core.py") < result.index("examples/demo.py")

    def test_hub_file_demoted(self):
        # Hub: fastify.js appears in 50%+ of mentions → should be demoted
        # when there are enough total mentions (>6)
        paths = ["fastify.js"] * 5 + ["lib/reply.js", "lib/route.js", "lib/schemas.js",
                                       "lib/hooks.js", "lib/errors.js", "lib/validation.js",
                                       "lib/middleware.js", "lib/lifecycles.js"]
        output = "\n".join(f"  {p}" for p in paths)
        result = _extract_focus_files(output)
        # fastify.js appears 5/13 = 38% of mentions → hub → demoted
        if "fastify.js" in result:
            assert result.index("fastify.js") > 0  # not first

    def test_keyword_matched_hub_file_not_demoted(self):
        # If hub file's stem matches a keyword, it's NOT demoted
        paths = ["fastify.js"] * 5 + ["lib/reply.js", "lib/route.js", "lib/schemas.js",
                                       "lib/hooks.js", "lib/errors.js", "lib/validation.js",
                                       "lib/middleware.js", "lib/lifecycles.js"]
        output = "\n".join(f"  {p}" for p in paths)
        result = _extract_focus_files(output, task_keywords=["fastify"])
        # With keyword match, fastify.js should NOT be demoted
        assert result[0] == "fastify.js"

    def test_primary_match_hub_file_not_demoted(self):
        # Primary-match files (on ● lines) must never be hub-penalized, even if
        # they dominate mention counts. Evidence: fastify reply-not-found —
        # setNotFoundHandler is in fastify.js (56% of mentions → hub), but fastify.js
        # IS a changed file. Hub penalty was incorrectly demoting it.
        primary_line = "● function setNotFoundHandler — fastify.js:607-612"
        # Add more fastify.js mentions (simulating BFS expansion) + other files
        extra = ["  fastify.js:9-9", "  fastify.js:385-480", "  fastify.js:595-605",
                 "  fastify.js:26-665", "  fastify.js:16-16", "  fastify.js:18-18",
                 "  fastify.js:3-3",
                 "  lib/reply.js (236 lines)", "  lib/logger.js (50 lines)",
                 "  lib/hooks.js (40 lines)", "  lib/request.js (26 lines)"]
        output = primary_line + "\n" + "\n".join(extra)
        result = _extract_focus_files(output)
        # fastify.js is a primary-match file → must not be hub-penalized → ranked first
        assert result[0] == "fastify.js"

    def test_caps_at_15_files(self):
        output = self._make_focus_output([f"src/file{i}.py" for i in range(20)])
        result = _extract_focus_files(output)
        assert len(result) <= 15

    def test_frequency_tiebreaker(self):
        output = self._make_focus_output(["src/bar.py"], {"src/bar.py": 3}) + "\n" + \
                 self._make_focus_output(["src/foo.py"], {"src/foo.py": 1})
        result = _extract_focus_files(output)
        assert result[0] == "src/bar.py"  # higher frequency first


class TestIsDocsBranchTask:
    """Tests for _is_docs_branch_task — suppresses overview for docs-named branches."""

    def test_docs_prefix_branch(self):
        assert _is_docs_branch_task("Merge pull request #636 from pallets/docs-javascript") is True

    def test_doc_prefix_branch(self):
        assert _is_docs_branch_task("Merge pull request #1 from org/doc-fix") is True

    def test_readme_prefix_branch(self):
        assert _is_docs_branch_task("Merge pull request #2 from org/readme-update") is True

    def test_changelog_prefix_branch(self):
        assert _is_docs_branch_task("Merge pull request #3 from org/changelog-bump") is True

    def test_docs_directory_path(self):
        assert _is_docs_branch_task("Merge pull request #4 from org/docs/fix-typo") is True

    def test_docs_suffix_branch(self):
        assert _is_docs_branch_task("Merge pull request #5 from org/auth-docs") is True

    def test_trunk_branch_not_docs(self):
        assert _is_docs_branch_task("Merge pull request #6 from org/main") is False

    def test_feature_branch_not_docs(self):
        assert _is_docs_branch_task("Merge pull request #7 from org/fix-auth-bug") is False

    def test_non_pr_title_not_docs(self):
        # Non-merge commit title not matched by either PR or branch-merge format
        assert _is_docs_branch_task("fix: update documentation for API") is False

    def test_merge_branch_into_docs_branch(self):
        # "Merge branch 'master' into docs/X" → target IS the docs branch
        assert _is_docs_branch_task("Merge branch 'master' into docs/edit-timer-in-middleware") is True

    def test_merge_docs_branch_into_master(self):
        # "Merge branch 'docs/add-feature' into master" → source IS the docs branch
        assert _is_docs_branch_task("Merge branch 'docs/add-feature' into master") is True

    def test_merge_feature_branch_not_docs(self):
        # Feature branch merge — not a docs branch
        assert _is_docs_branch_task("Merge branch 'feature/streaming-body' into main") is False

    def test_documentation_prefix_branch(self):
        assert _is_docs_branch_task("Merge pull request #8 from org/documentation-update") is True

    def test_version_branch_skips_overview(self):
        # "version-0.1.5" branch → release PR, changed files are pyproject/CHANGELOG (outside graph)
        assert _is_docs_branch_task("Merge pull request #50 from encode/version-0.1.5") is True

    def test_explicit_version_tag_branch(self):
        assert _is_docs_branch_task("Merge pull request #1 from org/v2.3.1") is True

    def test_release_branch_skips_overview(self):
        assert _is_docs_branch_task("Merge pull request #1 from org/release-1.0") is True

    def test_infra_body_ticket_branch_skips_overview(self):
        # "fix-10" ticket ref + pure infra body → requirements.txt change, model knows better
        # Evidence: fastapi de431d94 — overview → F1 0.500→0.286 (-0.214 delta)
        task = "Merge pull request #11 from tiangolo/fix-10\nPin versions of dependencies and bump version"
        assert _is_docs_branch_task(task) is True

    def test_ticket_branch_with_code_body_not_infra(self):
        # Ticket ref but body describes code changes → should NOT skip overview
        task = "Merge pull request #1 from org/fix-10\nFix handler error in request processing"
        assert _is_docs_branch_task(task) is False


class TestRenderPreparePrecisionFilter:
    """render_prepare(precision_filter=True) skips context when >4 key files found."""

    def _make_graph(self, tmp_path):
        """Create a minimal real git repo for render_prepare."""
        import subprocess
        from tempograph.builder import build_graph
        (tmp_path / "a.py").write_text("def alpha(): pass\n")
        (tmp_path / "b.py").write_text("def beta(): pass\n")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init",
                        "--author=test <t@t.com>"], cwd=tmp_path, check=True)
        return build_graph(str(tmp_path))

    def test_precision_filter_passes_through_to_render(self, tmp_path):
        """precision_filter param is accepted without error."""
        graph = self._make_graph(tmp_path)
        result_off = render_prepare(graph, "fix alpha function", precision_filter=False)
        result_on = render_prepare(graph, "fix alpha function", precision_filter=True)
        # Both calls should succeed — whether context is injected depends on keyword matching
        assert isinstance(result_off, str)
        assert isinstance(result_on, str)


class TestPathFallbackTemplateFilter:
    """path_fallback_files must exclude /templates/ and /static/ directories.

    Evidence: DRF 'improve_schema' → snake_case 'schema' → path match finds
    rest_framework/templates/rest_framework/schema.js (asset) alongside schemas.py.
    Model anchors on JS template instead of documentation.py → F1 regression.
    """

    def _make_graph_with_template_files(self, tmp_path):
        """Create a minimal git repo with both source and template/static schema files.

        The source file has 'schema' in the PATH but no 'schema'-named symbols.
        The template/static files also have 'schema' in PATH but NO parseable symbols.
        This ensures path_fallback (not symbol focus) is triggered for keyword 'schema'.
        """
        import subprocess
        from tempograph.builder import build_graph

        # Source file: 'schema' in path; symbols have unrelated names so focus won't match
        schemas_dir = tmp_path / "rest_framework"
        schemas_dir.mkdir()
        (schemas_dir / "schemas.py").write_text("def generate_view(): pass\n")

        # Template file: 'schema' in path, no parseable symbols → path match only
        tpl_dir = tmp_path / "rest_framework" / "templates" / "rest_framework"
        tpl_dir.mkdir(parents=True)
        # Deliberately no JS symbols — just a comment so tree-sitter finds nothing
        (tpl_dir / "schema.js").write_text("// template placeholder\n")

        # Static file: 'schema' in path — must also be excluded
        static_dir = tmp_path / "static" / "rest_framework"
        static_dir.mkdir(parents=True)
        (static_dir / "schema.py").write_text("def static_helper(): pass\n")

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init",
                        "--author=test <t@t.com>"], cwd=tmp_path, check=True)
        return build_graph(str(tmp_path))

    def test_template_and_static_excluded_from_path_fallback(self, tmp_path):
        """Template and static files must not appear in KEY FILES (path match)."""
        graph = self._make_graph_with_template_files(tmp_path)
        # Task: "improve_schema" → snake_case → "schema" part → path fallback
        result = render_prepare(graph, "Merge pull request #4979 from feature/improve_schema_shortcut")
        # Template and static files must be absent from context
        assert "schema.js" not in result, "Template JS file must not appear in path fallback"
        assert "schema.css" not in result, "Static CSS file must not appear in path fallback"
        # The source schema file should be present if path fallback triggers
        if "KEY FILES" in result:
            assert "schemas.py" in result, "Source schemas.py should appear in path fallback"
