"""Tests for bench/changelocal/context.py keyword extraction."""
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))


def _kw(text: str) -> list[str]:
    from bench.changelocal.context import _extract_keywords
    return _extract_keywords(text)


class TestExtractKeywords:
    def test_branch_name_extracted_from_merge_pr(self):
        """Branch name is the primary task signal in 'Merge pull request' titles."""
        kws = _kw("Merge pull request #588 from nwoltman/reply-not-found")
        assert "ReplyNotFound" in kws or "reply" in kws
        assert "nwoltman" not in kws

    def test_hyphenated_branch_converted_to_camel(self):
        kws = _kw("Merge pull request #675 from PlasmaPower/flush-headers")
        assert "FlushHeaders" in kws

    def test_trunk_branch_returns_empty(self):
        """PRs from master/main/develop provide no task context."""
        assert _kw("Merge pull request #2 from requests/master") == []
        assert _kw("Merge pull request #10 from org/main") == []
        assert _kw("Merge pull request #5 from org/develop") == []

    def test_nested_branch_path_extracted(self):
        """feature/branch-name → use the leaf name as task."""
        kws = _kw("Merge pull request #591 from StarpTech/feature/stream-interface")
        # Should extract something from the branch path
        assert len(kws) > 0
        assert "StreamInterface" in kws or "stream" in kws

    def test_direct_title_still_works(self):
        """Non-merge PR titles with CamelCase and snake_case still work."""
        kws = _kw("Add OAuth2 support to AuthProvider and UserStore")
        assert "OAuth2" in kws
        assert "AuthProvider" in kws
        assert "UserStore" in kws

    def test_screaming_snake_case_extracted(self):
        kws = _kw("fix: SESSION_COOKIE_PARTITIONED flag not respected")
        assert "SESSION_COOKIE_PARTITIONED" in kws

    def test_merge_branch_stable_returns_empty(self):
        """Version branch merges have no task context."""
        assert _kw("Merge branch 'stable'") == []
        assert _kw("Merge branch '3.0.x'") == []

    def test_generic_words_filtered(self):
        """Common English words should not appear as keywords."""
        kws = _kw("fix: handle the request properly")
        assert "the" not in kws
        assert "fix" not in kws
        assert "handle" not in kws

    def test_generic_identifiers_filtered(self):
        """error/option/log/ticket/docs/readme should not appear as keywords (universal noise)."""
        kws = _kw("Merge pull request #558 from fastify/http-errors")
        assert "errors" not in kws
        assert "error" not in kws

        kws = _kw("Merge pull request #674 from fastify/no-ajv-option")
        assert "option" not in kws
        assert "options" not in kws

        kws = _kw("Merge pull request #436 from allevo/logger-to-log")
        assert "log" not in kws
        assert "logger" not in kws

        kws = _kw("Merge pull request #6612 from miketheman/update-docs")
        assert "docs" not in kws

        kws = _kw("Merge pull request #101 from user/update-readme")
        assert "readme" not in kws

    def test_ticket_branch_mines_body(self):
        """Ticket-reference branches (issue1234, ticket-20550) include body keywords."""
        kws = _kw(
            "Merge pull request #2764 from gchp/ticket-20550\n"
            "Fixed #20550 -- Added keepdb argument to destroy_test_db"
        )
        # Body-derived snake_case identifiers should be included
        assert "keepdb" in kws or "destroy_test_db" in kws

    def test_numeric_branch_mines_body(self):
        """Branches starting with numbers (24937-word) include body for better context."""
        kws = _kw(
            "Merge pull request #4818 from dracos/24937-ranging-to-victory\n"
            "Fixed #24937 -- fix serialization of DateTimeRangeField."
        )
        assert "DateTimeRangeField" in kws or "RangeField" in kws

    def test_good_branch_does_not_mine_body(self):
        """Informative branches (reply-not-found) do NOT include body to avoid noise."""
        kws = _kw(
            "Merge pull request #588 from nwoltman/reply-not-found\n"
            "ShouldNotAppear IrrelevantClass in body text"
        )
        assert "IrrelevantClass" not in kws
        assert "ShouldNotAppear" not in kws
        assert "ReplyNotFound" in kws or "reply" in kws


class TestSelectiveOverviewCondition:
    """Validate which tasks produce empty keywords (→ overview) vs non-empty (→ no overview)."""

    def test_master_merge_returns_empty(self):
        """Merge branch master/main → empty keywords → overview eligible."""
        assert _kw("Merge branch 'master' into master") == []
        assert _kw("Merge branch 'main' into patch-1") == []
        assert _kw("Merge pull request #2 from requests/master\nSyncing fork") == []

    def test_trunk_branch_with_body_still_empty(self):
        """Contributor using fork master even with a real body → [] to preserve overview fallback.

        Returning [] triggers selective-overview, which benefits low-baseline repos (requests +131%).
        Extracting keywords from body would suppress overview and hurt these cases.
        """
        assert _kw("Merge pull request #4978 from sayzlim/master\nFix double image request") == []
        assert _kw("Merge pull request #456 from user/master") == []

    def test_logger_branch_returns_nonempty(self):
        """no-logger-by-default → ['NoLoggerByDefault'] (non-empty → no overview fallback).

        This is the key: a well-named branch like 'no-logger-by-default' extracts a keyword
        even though 'logger' and 'default' are in the skip list. 'no' (2 chars) is filtered,
        but the compound CamelCase 'NoLoggerByDefault' passes. Non-empty → selective overview
        will NOT inject overview for high-baseline repos where focus search fails.
        """
        kws = _kw("Merge pull request #347 from fastify/no-logger-by-default")
        assert len(kws) > 0  # non-empty → no overview fallback under selective strategy

    def test_descriptive_branch_returns_nonempty(self):
        """reply-not-found → non-empty → no overview fallback."""
        kws = _kw("Merge pull request #588 from nwoltman/reply-not-found")
        assert len(kws) > 0

    def test_part_ii_title_returns_empty(self):
        """'Part II: The Principles...' from master → empty keywords → overview eligible."""
        assert _kw("Merge pull request #5208 from psf/partII\nPart II: The Principles") == []


class TestCrossRepoPRFormat:
    """Cross-repo PR format: 'Merge pull request org/repo#N from org/branch'."""

    def test_cross_repo_extracts_branch(self):
        """pydantic-core cross-repo PRs extract branch name despite non-standard format."""
        kws = _kw("Merge pull request pydantic/pydantic-core#7 from samuelcolvin/pass-data\nPass data")
        assert "PassData" in kws

    def test_cross_repo_strips_url_trailers(self):
        """Original-commit-link URL trailers don't pollute keyword extraction."""
        kws = _kw(
            "Merge pull request pydantic/pydantic-core#7 from samuelcolvin/pass-data\n"
            "Pass data\n\n"
            "Original-commit-link: https://github.com/pydantic/pydantic-core/commit/e5d576f31292c77e164089a36da79ab874eb7b0f"
        )
        # Should extract branch-based keywords, NOT URL garbage
        assert "CommitLink" not in kws
        assert "PydanticCore" not in kws
        assert "https" not in kws
        assert "PassData" in kws


def _fp(text: str, keywords: list[str] | None = None) -> list[str]:
    from bench.changelocal.context import _extract_file_paths
    return _extract_file_paths(text, task_keywords=keywords)


class TestExtractFilePaths:
    def test_extracts_py_files(self):
        ctx = "src/flask/app.py mentioned here and again src/flask/app.py, also src/flask/ctx.py"
        files = _fp(ctx)
        assert "src/flask/app.py" in files
        assert "src/flask/ctx.py" in files

    def test_root_level_files_matched(self):
        """Root-level files like fastify.js (no dir prefix) must be captured."""
        ctx = "fastify.js is the main file; also lib/router.js"
        files = _fp(ctx)
        assert "fastify.js" in files
        assert "lib/router.js" in files

    def test_source_ranked_above_tests(self):
        """Source files should appear before test/spec files."""
        ctx = "src/app.py src/app.py tests/test_app.py src/app.py"
        files = _fp(ctx)
        assert files.index("src/app.py") < files.index("tests/test_app.py")

    def test_source_ranked_above_examples(self):
        """Example/demo files should be ranked last."""
        ctx = "src/core.py examples/demo.py src/core.py"
        files = _fp(ctx)
        assert files.index("src/core.py") < files.index("examples/demo.py")

    def test_frequency_breaks_ties(self):
        """Higher-frequency files rank first within same tier."""
        ctx = "src/a.py src/b.py src/a.py src/b.py src/a.py"
        files = _fp(ctx)
        assert files.index("src/a.py") < files.index("src/b.py")

    def test_keyword_match_boosts_file(self):
        """File whose name contains a task keyword is boosted over same-tier files."""
        ctx = "src/router.py src/auth.py src/auth.py src/router.py src/router.py"
        # Without keyword: router wins by frequency
        files_no_kw = _fp(ctx)
        assert files_no_kw[0] == "src/router.py"
        # With keyword auth: auth is boosted despite lower frequency
        files_with_kw = _fp(ctx, keywords=["auth"])
        assert files_with_kw[0] == "src/auth.py"

    def test_capped_at_15(self):
        """Result is capped at 15 files even if more are present."""
        files_text = " ".join(f"src/module{i}.py" for i in range(20))
        files = _fp(files_text)
        assert len(files) <= 15

    def test_deduplicates_paths(self):
        """Same path appearing multiple times returns only one entry."""
        ctx = "src/app.py src/app.py src/app.py"
        files = _fp(ctx)
        assert files.count("src/app.py") == 1

    def test_ignores_non_code_extensions(self):
        """Non-code files (rst, md, txt) are not extracted."""
        ctx = "docs/config.rst CHANGES.rst README.md src/app.py"
        files = _fp(ctx)
        assert not any(f.endswith(".rst") or f.endswith(".md") for f in files)
        assert "src/app.py" in files

    def test_multi_language_support(self):
        """JS, TS, Go, Rust files are all captured."""
        ctx = "lib/router.js src/server.ts internal/main.go src/lib.rs"
        files = _fp(ctx)
        assert "lib/router.js" in files
        assert "src/server.ts" in files
        assert "internal/main.go" in files
        assert "src/lib.rs" in files
