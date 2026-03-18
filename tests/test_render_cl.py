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
        # Function only matches "Merge pull request ... from org/branch" format
        assert _is_docs_branch_task("fix: update documentation for API") is False

    def test_documentation_prefix_branch(self):
        assert _is_docs_branch_task("Merge pull request #8 from org/documentation-update") is True


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
