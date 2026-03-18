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
