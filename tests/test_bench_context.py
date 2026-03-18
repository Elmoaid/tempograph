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

    def test_snake_case_branch_typo_mines_body(self):
        """Branch with typo (forwardred vs ForwardRef) mines body to recover correct identifier."""
        kws = _kw(
            "Merge pull request #706 from koxudaxi/support_forwardred_in_python36\n"
            "support ForwardRef in Python 3.6"
        )
        # Body contains the correct identifier "ForwardRef" (strict_camel has internal capital F→R)
        assert "ForwardRef" in kws
        # And ForwardRef should be near the front (it's a priority/CamelCase token from body)
        assert kws.index("ForwardRef") < 4

    def test_pure_snake_case_branch_mines_body(self):
        """Pure snake_case branch with no CamelCase also mines body for symbol names."""
        kws = _kw(
            "Merge pull request #100 from org/some_feature_branch\n"
            "Fix SessionCookiePartitioned flag handling"
        )
        assert "SessionCookiePartitioned" in kws

    def test_fork_master_mines_body(self):
        """'Username-master' branch is a fork PR from master; body has the real task signal."""
        kws = _kw(
            "Merge pull request #650 from kgriffs/CygnusNetworks-master\n"
            "Add support for Content-Range units"
        )
        # Body keywords should be present ('Content' from 'Content-Range')
        assert "Content" in kws or "content" in kws or "range" in kws.lower() or any(k for k in kws if "range" in k.lower())

    def test_fork_master_body_first_ordering(self):
        """For 'Username-master' branches, body keywords appear before branch keywords."""
        kws = _kw(
            "Merge pull request #636 from kgriffs/hooblei-master\n"
            "Fix context_type fallback in Request"
        )
        # Body keyword 'context_type' should appear in results (body mined)
        assert "context_type" in kws or "ContextType" in kws

    def test_informative_branch_not_treated_as_fork_master(self):
        """'no-logger-by-default' ends in '-default', not '-master'; not fork-master."""
        kws = _kw("Merge pull request #347 from fastify/no-logger-by-default")
        assert "NoLoggerByDefault" in kws or "logger" in kws
        assert "NoLoggerByDefault" in kws  # branch CamelCase preserved


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


class TestBacktickExtraction:
    def test_backtick_name_extracted_first(self):
        """Backtick-quoted names are highest-priority keywords."""
        kws = _kw("deprecate `should_ignore_error` (#5899)")
        assert kws[0] == "should_ignore_error"

    def test_backtick_snake_case_extracted(self):
        """snake_case names in backticks are extracted."""
        kws = _kw("fix `url_for` to handle query params")
        assert "url_for" in kws
        assert kws.index("url_for") < kws.index("query") if "query" in kws else True

    def test_backtick_priority_over_prose(self):
        """Backtick names come before generic prose words."""
        kws = _kw("add `session_interface` to handle sessions")
        assert "session_interface" in kws
        assert kws.index("session_interface") == 0

    def test_backtick_too_short_ignored(self):
        """Single/double-char backtick names (like `x`, `id`) are not extracted."""
        kws = _kw("fix `x` and `ab` handling")
        assert "x" not in kws
        assert "ab" not in kws

    def test_backtick_in_merge_pr_body(self):
        """Backtick names extracted from PR body for ticket branches."""
        title = "Merge pull request #5899 from pallets/issue5899-deprecate\ndeprecate `should_ignore_error` method"
        kws = _kw(title)
        assert "should_ignore_error" in kws


class TestSmallWordSkipList:
    def test_are_filtered(self):
        """'are' should not appear as a keyword (English article, not a symbol)."""
        kws = _kw("all teardown callbacks are called despite errors")
        assert "are" not in kws

    def test_via_filtered(self):
        """'via' is filtered."""
        kws = _kw("connect to database via connection pool")
        assert "via" not in kws

    def test_pre_filtered_as_standalone(self):
        """Standalone 'pre' is filtered (but 'pre-commit' → 'PreCommit' still works)."""
        kws = _kw("run pre and post hooks")
        assert "pre" not in kws

    def test_non_filtered(self):
        """Standalone 'non' is filtered."""
        kws = _kw("handle non blocking operations")
        assert "non" not in kws


class TestParseKeyFilesFromContext:
    """Tests for _parse_key_files_from_context in run.py."""

    def _parse(self, ctx):
        from bench.changelocal.run import _parse_key_files_from_context
        return _parse_key_files_from_context(ctx)

    def test_key_files_referenced_above(self):
        ctx = "some graph output\n\nKEY FILES REFERENCED ABOVE:\n  lib/reply.js\n  docs/Reply.md\n"
        assert self._parse(ctx) == ["lib/reply.js", "docs/Reply.md"]

    def test_key_files_path_match(self):
        ctx = "KEY FILES (path match):\n  tornado/demos/file1.py\n  tornado/demos/file2.py\n"
        assert self._parse(ctx) == ["tornado/demos/file1.py", "tornado/demos/file2.py"]

    def test_empty_context(self):
        assert self._parse("") == []

    def test_no_key_files_section(self):
        ctx = "GRAPH OVERVIEW\n  src/main.py imports src/util.py\n"
        assert self._parse(ctx) == []

    def test_single_file(self):
        ctx = "KEY FILES REFERENCED ABOVE:\n  httpx/interfaces.py\n"
        assert self._parse(ctx) == ["httpx/interfaces.py"]


# ---------------------------------------------------------------------------
# Parity tests: context.py _extract_keywords == render.py _extract_cl_keywords
# These serve as a contract: both implementations must produce identical output.
# If they diverge, one of them has a bug or missed a feature.
# ---------------------------------------------------------------------------

class TestExtractKeywordsParity:
    """Verify context.py::_extract_keywords ≡ render.py::_extract_cl_keywords."""

    CASES = [
        "Merge pull request #595 from encode/reply-not-found",
        "Merge pull request #241 from encode/protocol-support",
        "Merge pull request #1 from org/main",
        "Merge pull request #1 from org/master",
        "Merge pull request #1 from org/docs-update",
        "Merge pull request #675 from PlasmaPower/flush-headers",
        "Merge pull request #255 from encode/refactor/streaming-improvements",
        "Merge pull request #595 from encode/24937-ranging-to-victory",
        "Fix teardown callbacks (#5928)",
        "fix: prevent null pointer in handler",
        "Drop Protocol str enum class in favor of plain old string",
        "fix use of `importlib.util.find_spec` (#5161)",
        "Merge branch 'stable'",
        "Merge branch '3.0.x'",
        "Merge branch 'master' into master",
        "Merge branch 'main' into patch-1",
        "Merge pull request pydantic/pydantic-core#7 from samuelcolvin/pass-data",
        "deprecate `should_ignore_error` (#5899)",
        "all teardown callbacks are called despite errors",
        "connect to database via connection pool",
        "Merge pull request #347 from fastify/no-logger-by-default",
        "Merge pull request #4978 from sayzlim/master\nFix double image request",
        "Merge pull request #5208 from psf/partII\nPart II: The Principles",
    ]

    def test_all_cases_match(self):
        from bench.changelocal.context import _extract_keywords
        from tempograph.render import _extract_cl_keywords
        for task in self.CASES:
            ctx_result = _extract_keywords(task)
            rnd_result = _extract_cl_keywords(task)
            assert ctx_result == rnd_result, (
                f"Divergence for {task!r}:\n"
                f"  context.py: {ctx_result}\n"
                f"  render.py:  {rnd_result}"
            )


# ---------------------------------------------------------------------------
# Parity tests: context.py _extract_file_paths == render.py _extract_focus_files
# ---------------------------------------------------------------------------

class TestExtractFilePathsParity:
    """Verify context.py::_extract_file_paths ≡ render.py::_extract_focus_files."""

    # Realistic snippets of tempograph focus output (file paths appear multiple times)
    CASES = [
        # Basic source-file extraction
        "  src/flask/app.py (callers)\n  src/flask/ctx.py (callers)\n  tests/test_app.py\n",
        # Hub detection: fastify.js dominates
        ("  fastify.js\n  fastify.js\n  fastify.js\n  fastify.js\n  fastify.js\n"
         "  lib/reply.js\n  lib/route.js\n  lib/schemas.js\n  lib/hooks.js\n  lib/errors.js\n"
         "  lib/validation.js\n  lib/middleware.js\n"),
        # Root-level + nested files
        "app.py\nlib/router.js\nsrc/server.ts\n",
        # Test files ranked after source
        "tests/test_router.py\nsrc/router.py\nsrc/auth.py\n",
        # Example files ranked last
        "examples/demo.py\nsrc/core.py\nsrc/utils.py\n",
        # Multi-language
        "internal/main.go\nsrc/lib.rs\nlib/util.js\n",
    ]

    def test_all_cases_match(self):
        from bench.changelocal.context import _extract_file_paths
        from tempograph.render import _extract_focus_files
        for text in self.CASES:
            ctx_result = _extract_file_paths(text)
            rnd_result = _extract_focus_files(text)
            assert ctx_result == rnd_result, (
                f"Divergence for focus text snippet:\n"
                f"  context.py: {ctx_result}\n"
                f"  render.py:  {rnd_result}"
            )

    def test_keyword_boost_matches(self):
        """Keyword-boosted ranking must agree between implementations."""
        from bench.changelocal.context import _extract_file_paths
        from tempograph.render import _extract_focus_files
        text = "src/router.py\nsrc/auth.py\nsrc/auth.py\n"
        ctx = _extract_file_paths(text, task_keywords=["auth"])
        rnd = _extract_focus_files(text, task_keywords=["auth"])
        assert ctx == rnd, f"Keyword-boost divergence: context={ctx}, render={rnd}"

    def test_hub_with_keyword_exempt_matches(self):
        """Hub-exemption for keyword-matched files must agree."""
        from bench.changelocal.context import _extract_file_paths
        from tempograph.render import _extract_focus_files
        # fastify.js appears 5/13 times (38% → hub), but keyword 'fastify' exempts it
        text = ("  fastify.js\n" * 5 +
                "  lib/reply.js\n  lib/route.js\n  lib/schemas.js\n"
                "  lib/hooks.js\n  lib/errors.js\n  lib/validation.js\n"
                "  lib/middleware.js\n  lib/lifecycles.js\n")
        ctx = _extract_file_paths(text, task_keywords=["fastify"])
        rnd = _extract_focus_files(text, task_keywords=["fastify"])
        assert ctx == rnd, f"Hub-exempt divergence: context={ctx}, render={rnd}"


# ---------------------------------------------------------------------------
# Hub penalty unit tests
# ---------------------------------------------------------------------------

class TestHubPenalty:
    """Tests for is_hub() / hub_penalty in _extract_file_paths."""

    def _efp(self, text, keywords=None):
        from bench.changelocal.context import _extract_file_paths
        return _extract_file_paths(text, task_keywords=keywords or [])

    def test_hub_file_demoted_to_end(self):
        """A file consuming >30% of mentions without keyword match is demoted."""
        # fastify.js: 5/13 = 38% — should NOT be first in output
        text = (
            "  fastify.js\n" * 5 +
            "  lib/reply.js\n  lib/route.js\n  lib/schemas.js\n"
            "  lib/hooks.js\n  lib/errors.js\n  lib/validation.js\n"
            "  lib/middleware.js\n  lib/lifecycles.js\n"
        )
        result = self._efp(text)
        assert result[0] != "fastify.js", "Hub file should not be ranked first"

    def test_hub_file_exempt_when_keyword_matches(self):
        """Hub file with keyword match is NOT demoted."""
        text = (
            "  fastify.js\n" * 5 +
            "  lib/reply.js\n  lib/route.js\n  lib/schemas.js\n"
            "  lib/hooks.js\n  lib/errors.js\n  lib/validation.js\n"
            "  lib/middleware.js\n  lib/lifecycles.js\n"
        )
        result = self._efp(text, keywords=["fastify"])
        # With keyword match, fastify.js is exempt → should rank high (frequency wins)
        assert result[0] == "fastify.js", "Keyword-exempt hub should rank first"

    def test_hub_threshold_not_triggered_on_small_total(self):
        """Hub penalty skipped when total_mentions <= 6 (too small to be meaningful)."""
        # Only 4 mentions total — hub guard inactive
        text = "  fastify.js\n  fastify.js\n  lib/reply.js\n  lib/route.js\n"
        result = self._efp(text)
        # fastify.js (2/4 = 50%) but total<=6 → no penalty → appears first by frequency
        assert result[0] == "fastify.js"

    def test_hub_threshold_exactly_at_boundary(self):
        """A file at exactly 30% mention share is NOT penalised (strict >)."""
        # 3 out of 10 total = 30.0% — exactly at boundary, not >30%, no penalty
        text = (
            "  fastify.js\n" * 3 +
            "  lib/a.js\n  lib/b.js\n  lib/c.js\n  lib/d.js\n"
            "  lib/e.js\n  lib/f.js\n  lib/g.js\n"
        )
        result = self._efp(text)
        # 30% is not > 30%, so no hub penalty → fastify.js ranks first by freq
        assert result[0] == "fastify.js"

    def test_hub_triggered_just_above_boundary(self):
        """A file at 31%+ mention share IS penalised."""
        # 4 out of 11 total = 36% — above 30%, hub penalty applies
        text = (
            "  fastify.js\n" * 4 +
            "  lib/a.js\n  lib/b.js\n  lib/c.js\n  lib/d.js\n"
            "  lib/e.js\n  lib/f.js\n  lib/g.js\n"
        )
        result = self._efp(text)
        assert result[0] != "fastify.js", "Hub (36%) should be demoted"


# ---------------------------------------------------------------------------
# _is_docs_task unit tests
# ---------------------------------------------------------------------------

class TestIsDocsTask:
    """Tests for _is_docs_task() — overview suppression for docs branches."""

    def _docs(self, task):
        from bench.changelocal.context import _is_docs_task
        return _is_docs_task(task)

    def test_docs_prefix_branch(self):
        """'docs-javascript' branch triggers docs detection."""
        assert self._docs("Merge pull request #4636 from pallets/docs-javascript") is True

    def test_docs_subdir_branch(self):
        """'docs/#...' path branch triggers docs detection."""
        assert self._docs("Merge pull request #4579 from lecovi/docs/#4574-test-typing") is True

    def test_readme_prefix(self):
        assert self._docs("Merge pull request #1 from org/readme-fix") is True

    def test_changelog_prefix(self):
        assert self._docs("Merge pull request #1 from org/changelog-update") is True

    def test_doc_suffix(self):
        assert self._docs("Merge pull request #1 from org/api-docs") is True

    def test_non_docs_branch(self):
        """Regular code branches are NOT docs tasks."""
        assert self._docs("Merge pull request #4337 from pallets/remove-deprecated-code") is False
        assert self._docs("Merge pull request #588 from nwoltman/reply-not-found") is False

    def test_trunk_branch_not_docs(self):
        """Trunk branch merges are not docs tasks (handled separately)."""
        assert self._docs("Merge branch 'master'") is False
        assert self._docs("Merge branch 'stable'") is False

    def test_non_merge_pr_not_docs(self):
        """Direct commit messages are not docs tasks."""
        assert self._docs("fix: prevent null pointer in handler") is False
        assert self._docs("add SESSION_COOKIE_PARTITIONED config (#5499)") is False


# ---------------------------------------------------------------------------
# _prioritize_files unit tests
# ---------------------------------------------------------------------------

class TestPrioritizeFiles:
    """Tests for _prioritize_files() in run.py."""

    def _pf(self, files, max_files=200):
        from bench.changelocal.run import _prioritize_files
        return _prioritize_files(files, max_files=max_files)

    def test_source_files_before_docs(self):
        """Python/JS source files appear before markdown docs."""
        files = [
            "docs/guide/intro.md",
            "docs/api.md",
            "sanic/app.py",
            "sanic/handlers.py",
        ]
        result = self._pf(files)
        # .py files should appear before .md files
        py_indices = [i for i, f in enumerate(result) if f.endswith(".py")]
        md_indices = [i for i, f in enumerate(result) if f.endswith(".md")]
        assert max(py_indices) < min(md_indices), "Source files must precede markdown docs"

    def test_source_files_before_config(self):
        """Source files appear before config files, which appear before docs."""
        files = [
            "setup.cfg",
            "pyproject.toml",
            "README.md",
            "falcon/app.py",
            "falcon/errors.py",
        ]
        result = self._pf(files)
        py_positions = [result.index(f) for f in files if f.endswith(".py")]
        toml_positions = [result.index(f) for f in files if f.endswith(".toml") or f.endswith(".cfg")]
        md_positions = [result.index(f) for f in files if f.endswith(".md")]
        assert max(py_positions) < min(toml_positions), "Source before config"
        assert max(toml_positions) < min(md_positions), "Config before docs"

    def test_max_files_cap_respected(self):
        """max_files parameter limits output length."""
        files = [f"module/file{i}.py" for i in range(50)]
        result = self._pf(files, max_files=10)
        assert len(result) == 10

    def test_all_source_files_fit_before_cap(self):
        """When source files < max_files, all source files appear in output."""
        files = (
            ["guides/chapter{i}.md".format(i=i) for i in range(300)] +
            ["sanic/core.py", "sanic/router.py"]
        )
        result = self._pf(files, max_files=200)
        assert "sanic/core.py" in result
        assert "sanic/router.py" in result

    def test_makefile_treated_as_config(self):
        """Makefile is config-tier (not docs)."""
        files = ["Makefile", "README.md", "src/main.py"]
        result = self._pf(files)
        assert result.index("src/main.py") < result.index("Makefile")
        assert result.index("Makefile") < result.index("README.md")

    def test_all_languages_recognized(self):
        """All supported source extensions get tier 0."""
        files = [
            "mod.go", "lib.rs", "App.java", "Module.cs",
            "script.rb", "Component.tsx", "util.jsx",
            "README.md",
        ]
        result = self._pf(files)
        md_pos = result.index("README.md")
        for f in files[:-1]:
            assert result.index(f) < md_pos, f"{f} should be before README.md"

    def test_within_tier_alphabetical_order(self):
        """Files in the same tier are sorted alphabetically."""
        files = ["src/z.py", "src/a.py", "src/m.py"]
        result = self._pf(files)
        assert result == ["src/a.py", "src/m.py", "src/z.py"]


# ---------------------------------------------------------------------------
# No-match path fallback (new: triggers on 0 symbol matches, not just >10)
# ---------------------------------------------------------------------------

class TestNoMatchPathFallback:
    """Path fallback should trigger when focus returns 'No symbols matching', not only >10 files."""

    def _make_mock_context(self, task, path_hits):
        """Run get_tempograph_context with mocked graph and render_focused returning no-match."""
        from unittest.mock import MagicMock, patch
        from bench.changelocal.context import get_tempograph_context

        sym = MagicMock()
        sym.file_path = path_hits[0] if path_hits else "no_match.py"

        graph = MagicMock()
        graph.symbols = {f"sym_{i}": MagicMock(file_path=p) for i, p in enumerate(path_hits)}

        with patch("bench.changelocal.context.build_graph", return_value=graph), \
             patch("bench.changelocal.context.render_focused", return_value="No symbols matching 'config_from_object'"), \
             patch("bench.changelocal.context.render_overview", return_value=""), \
             patch("bench.changelocal.context.render_blast_radius", return_value=""):
            return get_tempograph_context(MagicMock(), task)

    def test_no_match_triggers_path_fallback(self):
        """When focus returns 'No symbols matching', snake_case split produces KEY FILES output.

        'config_from_object' fails full-string path match, then splits: 'config' matches
        'sanic/config.py' and 'sanic/config_ext.py' (≤5 paths → used as fallback).
        """
        task = "Merge pull request #1436 from jotagesales/config_from_object"
        result = self._make_mock_context(task, ["sanic/config.py", "sanic/config_ext.py", "sanic/app.py"])
        assert "KEY FILES (path match):" in result
        # 'config' part matches sanic/config.py and sanic/config_ext.py (≤5 → valid fallback)
        assert "sanic/config.py" in result

    def test_no_match_with_no_path_hit_yields_empty(self):
        """When focus is no-match AND no file has keyword in path, context is empty."""
        task = "Merge pull request #1612 from c-goosen/bandit_security_static"
        result = self._make_mock_context(task, ["sanic/app.py", "sanic/server.py"])
        # 'bandit' and 'security' and 'static' are not in ['sanic/app.py', 'sanic/server.py']
        assert result == ""

    def test_camelcase_path_fallback(self):
        """CamelCase keyword split: 'RequestStreamingSupport' → 'streaming' matches path."""
        from unittest.mock import MagicMock, patch
        from bench.changelocal.context import get_tempograph_context

        paths = ["sanic/streaming.py", "sanic/app.py", "sanic/server.py"]
        graph = MagicMock()
        graph.symbols = {f"sym_{i}": MagicMock(file_path=p) for i, p in enumerate(paths)}

        with patch("bench.changelocal.context.build_graph", return_value=graph), \
             patch("bench.changelocal.context.render_focused", return_value="No symbols matching 'RequestStreamingSupport'"), \
             patch("bench.changelocal.context.render_overview", return_value=""), \
             patch("bench.changelocal.context.render_blast_radius", return_value=""):
            result = get_tempograph_context(
                MagicMock(),
                "Merge pull request #1423 from yunstanford/request-streaming-support",
            )
        # 'Streaming' component (len≥4) matches 'sanic/streaming.py' → path fallback
        assert "KEY FILES (path match):" in result
        assert "sanic/streaming.py" in result


class TestPrecisionFilter:
    """precision_filter=True skips context when >4 unique key files are found."""

    _BROAD_FOCUS = (
        "flask/app.py: Flask\n"
        "flask/ctx.py: AppContext\n"
        "flask/blueprints.py: Blueprint\n"
        "flask/testing.py: FlaskClient\n"
        "flask/globals.py: current_app\n"
        "flask/helpers.py: send_file\n"
    )  # 6 unique .py files → triggers precision gate

    _NARROW_FOCUS = (
        "flask/app.py: Flask\n"
        "flask/ctx.py: AppContext\n"
    )  # 2 unique files → passes through

    def _run(self, task, focused_return, precision_filter):
        from unittest.mock import MagicMock, patch
        from bench.changelocal.context import get_tempograph_context
        graph = MagicMock()
        graph.symbols = {}
        with patch("bench.changelocal.context.build_graph", return_value=graph), \
             patch("bench.changelocal.context.render_focused", return_value=focused_return), \
             patch("bench.changelocal.context.render_overview", return_value=""):
            return get_tempograph_context(MagicMock(), task, precision_filter=precision_filter)

    def test_precision_filter_skips_broad_context(self):
        """precision_filter=True + >4 key files → returns empty string."""
        result = self._run(
            "Merge pull request #500 from pallets/dispatch-context",
            self._BROAD_FOCUS,
            precision_filter=True,
        )
        assert result == ""

    def test_precision_filter_off_keeps_broad_context(self):
        """precision_filter=False (default) + >4 key files → context returned."""
        result = self._run(
            "Merge pull request #500 from pallets/dispatch-context",
            self._BROAD_FOCUS,
            precision_filter=False,
        )
        assert result != ""
        assert "KEY FILES" in result

    def test_precision_filter_keeps_narrow_context(self):
        """precision_filter=True + ≤4 key files → context returned normally."""
        result = self._run(
            "Merge pull request #501 from pallets/fix-appcontext",
            self._NARROW_FOCUS,
            precision_filter=True,
        )
        assert result != ""
        assert "KEY FILES" in result

    def test_precision_filter_skips_broad_path_fallback(self):
        """precision_filter=True + >4 path-fallback files → returns empty (no injection).

        Regression test for DRF authtoken-import: 'authtoken' keyword found 5 authtoken module
        files via path match. Without this gate, precision_filter=True still injected those 5 files
        and hurt the high-baseline model (F1 0.5→0). Fix: gate path_fallback_files the same way.
        """
        from unittest.mock import MagicMock, patch
        from bench.changelocal.context import get_tempograph_context

        # 5 path-matched files (>4 threshold) for a well-known module
        five_paths = [
            "rest_framework/authtoken/admin.py",
            "rest_framework/authtoken/migrations/0001_initial.py",
            "rest_framework/authtoken/models.py",
            "rest_framework/authtoken/serializers.py",
            "rest_framework/authtoken/views.py",
        ]
        graph = MagicMock()
        graph.symbols = {f"sym_{i}": MagicMock(file_path=p) for i, p in enumerate(five_paths)}
        with patch("bench.changelocal.context.build_graph", return_value=graph), \
             patch("bench.changelocal.context.render_focused",
                   return_value="No symbols matching AuthtokenImport"), \
             patch("bench.changelocal.context.render_overview", return_value=""):
            result = get_tempograph_context(
                MagicMock(),
                "Merge pull request #3785 from sheppard/authtoken-import\ndont import authtoken",
                precision_filter=True,
            )
        assert result == "", f"Expected empty (precision_filter gates broad path fallback), got: {result[:100]}"

    def test_template_and_static_dirs_excluded_from_path_fallback(self):
        """path_fallback_files must not include /templates/ or /static/ directory files.

        Regression test for DRF aafd0a64 (improve_schema): 'schema' keyword path match
        found rest_framework/templates/rest_framework/schema.js (a JS template asset)
        alongside schemas.py. Model anchored on the JS file instead of documentation.py.
        """
        from unittest.mock import MagicMock, patch
        from bench.changelocal.context import get_tempograph_context

        # Symbols in different locations — template and static must be filtered out
        paths = [
            "rest_framework/schemas.py",                           # source — should match
            "rest_framework/tests/test_schemas.py",               # test — should match
            "rest_framework/templates/rest_framework/schema.js",  # template — must exclude
            "static/rest_framework/schema.css",                   # static — must exclude
        ]
        graph = MagicMock()
        graph.symbols = {f"sym_{i}": MagicMock(file_path=p) for i, p in enumerate(paths)}
        with patch("bench.changelocal.context.build_graph", return_value=graph), \
             patch("bench.changelocal.context.render_focused",
                   return_value="No symbols matching ImproveSchemaShortcut"), \
             patch("bench.changelocal.context.render_overview", return_value=""):
            result = get_tempograph_context(
                MagicMock(),
                "Merge pull request #4979 from feature/improve_schema_shortcut",
                precision_filter=False,  # off so we see the path fallback output
            )
        # Template and static files must be absent
        assert "schema.js" not in result, "Template JS must not appear in path fallback"
        assert "schema.css" not in result, "Static CSS must not appear in path fallback"
        # Source schema files should appear
        if result:
            assert "schemas.py" in result or "test_schemas.py" in result, (
                "At least one source schema file should appear in path fallback"
            )
