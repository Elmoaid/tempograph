"""Tests for change-localization helpers in render.py."""
import pytest
from tempograph.render import _extract_cl_keywords, _is_change_localization


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
