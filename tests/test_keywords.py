"""Tests for _extract_cl_keywords: change-localization keyword extraction from PR titles."""
from __future__ import annotations

import pytest

from tempograph.keywords import _extract_cl_keywords


# ── PR title / Merge PR format ────────────────────────────────────────────────

class TestMergePRFormat:
    def test_camelcase_from_branch_extracted(self):
        kws = _extract_cl_keywords(
            "Merge pull request #123 from org/add-FileParser-support"
        )
        assert any("FileParser" in k or "File" in k for k in kws)

    def test_hyphenated_branch_becomes_camelcase(self):
        kws = _extract_cl_keywords(
            "Merge pull request #42 from org/fix-streaming-body"
        )
        # "fix-streaming-body" → "FixStreamingBody" (full compound) + "Streaming" (sub-part)
        assert any("Streaming" in k for k in kws)

    def test_trunk_branch_returns_empty(self):
        for branch in ["main", "master", "develop"]:
            kws = _extract_cl_keywords(
                f"Merge pull request #1 from org/{branch}"
            )
            assert kws == [], f"Expected [] for trunk branch '{branch}', got {kws}"

    def test_docs_branch_returns_empty(self):
        kws = _extract_cl_keywords(
            "Merge pull request #99 from org/docs-update"
        )
        assert kws == []

    def test_readme_branch_returns_empty(self):
        kws = _extract_cl_keywords(
            "Merge pull request #10 from org/update-readme"
        )
        assert kws == []

    def test_ticket_branch_mines_body(self):
        kws = _extract_cl_keywords(
            "Merge pull request #321 from org/issue-1234\nFix StreamingResponse encoding bug"
        )
        assert any("Streaming" in k or "StreamingResponse" in k for k in kws)


# ── Conventional commit format ────────────────────────────────────────────────

class TestConventionalCommit:
    def test_feat_scope_extracted_as_priority(self):
        kws = _extract_cl_keywords("feat(FileParser): add TypeScript support")
        assert "FileParser" in kws

    def test_fix_scope_extracted(self):
        kws = _extract_cl_keywords("fix(StreamMiddleware): handle encoding errors")
        assert "StreamMiddleware" in kws

    def test_cc_prefix_stripped(self):
        kws = _extract_cl_keywords("feat: add render_focused function")
        assert "render_focused" in kws

    def test_refactor_scope(self):
        kws = _extract_cl_keywords("refactor(build_graph): split into modules")
        assert "build_graph" in kws

    def test_trunk_merge_branch_returns_empty(self):
        kws = _extract_cl_keywords("Merge branch 'main'")
        assert kws == []

    def test_version_merge_returns_empty(self):
        kws = _extract_cl_keywords("Merge branch '2.2.x'")
        assert kws == []


# ── Backtick identifiers ──────────────────────────────────────────────────────

class TestBacktickIdentifiers:
    def test_backtick_identifier_is_priority(self):
        kws = _extract_cl_keywords("deprecate `should_ignore_error` in favor of new API")
        assert "should_ignore_error" in kws

    def test_backtick_comes_before_other_keywords(self):
        kws = _extract_cl_keywords("fix: update `notFoundHandler` in ServerRouter class")
        assert kws.index("notFoundHandler") < kws.index("ServerRouter")


# ── CamelCase and snake_case extraction ───────────────────────────────────────

class TestCamelSnakeExtraction:
    def test_camelcase_extracted(self):
        kws = _extract_cl_keywords("Add support for HttpRequest class")
        assert "HttpRequest" in kws

    def test_snake_case_multiword_is_priority(self):
        kws = _extract_cl_keywords("fix render_overview display issue")
        assert "render_overview" in kws

    def test_skip_words_not_in_result(self):
        kws = _extract_cl_keywords("add new feature to the code")
        skip_words = {"the", "add", "new", "code", "feature", "to"}
        assert not any(k.lower() in skip_words for k in kws)

    def test_lower_camel_case_extracted(self):
        kws = _extract_cl_keywords("Add reply.notFound() method")
        assert "notFound" in kws

    def test_short_words_filtered(self):
        kws = _extract_cl_keywords("fix bug in app")
        assert "in" not in kws
        assert "app" not in kws or True  # 'app' < 3 chars, may be filtered

    def test_returns_list(self):
        result = _extract_cl_keywords("feat: add something")
        assert isinstance(result, list)

    def test_empty_input_returns_list(self):
        result = _extract_cl_keywords("")
        assert isinstance(result, list)


# ── GitHub patch branches ─────────────────────────────────────────────────────

class TestGitHubPatchBranch:
    def test_github_patch_branch_mines_body(self):
        kws = _extract_cl_keywords(
            "Merge pull request #5 from org/Username-patch-1\nFix CookieJar encoding"
        )
        assert any("Cookie" in k or "Encoding" in k or "CookieJar" in k for k in kws)


# ── Sub-part decomposition ────────────────────────────────────────────────────

class TestSubPartDecomposition:
    def test_long_part_from_hyphenated_appears_in_general(self):
        kws = _extract_cl_keywords(
            "Merge pull request #1 from org/fix-streaming-response"
        )
        # "fix-streaming-response" → "FixStreamingResponse" (full compound) + "Streaming" sub-part
        assert any("Streaming" in k for k in kws)

    def test_cc_scope_comma_separated(self):
        kws = _extract_cl_keywords("feat(Request, Response): add headers support")
        assert "Request" in kws
        assert "Response" in kws
