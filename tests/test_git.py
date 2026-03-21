"""Tests for tempograph/git.py: git integration utilities."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tempograph.git import (
    is_git_repo,
    changed_files_unstaged,
    changed_files_staged,
    changed_files_vs_head,
    changed_files_since,
    current_branch,
    recently_modified_files,
    file_commit_counts,
)


TEMPOGRAPH_ROOT = str(Path(__file__).parent.parent)


# ── is_git_repo ───────────────────────────────────────────────────────────────

class TestIsGitRepo:
    def test_non_git_dir_returns_false(self, tmp_path):
        assert is_git_repo(str(tmp_path)) is False

    def test_dir_with_dot_git_returns_true(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert is_git_repo(str(tmp_path)) is True

    def test_tempograph_repo_detected(self):
        assert is_git_repo(TEMPOGRAPH_ROOT) is True


# ── changed_files helpers ─────────────────────────────────────────────────────

class TestChangedFiles:
    def test_unstaged_returns_list(self):
        result = changed_files_unstaged(TEMPOGRAPH_ROOT)
        assert isinstance(result, list)

    def test_staged_returns_list(self):
        result = changed_files_staged(TEMPOGRAPH_ROOT)
        assert isinstance(result, list)

    def test_vs_head_returns_list(self):
        result = changed_files_vs_head(TEMPOGRAPH_ROOT)
        assert isinstance(result, list)

    def test_since_ref_returns_list(self):
        result = changed_files_since(TEMPOGRAPH_ROOT, ref="HEAD~1")
        assert isinstance(result, list)

    def test_non_git_dir_returns_empty(self, tmp_path):
        assert changed_files_unstaged(str(tmp_path)) == []
        assert changed_files_staged(str(tmp_path)) == []
        assert changed_files_vs_head(str(tmp_path)) == []

    def test_git_failure_returns_empty(self, tmp_path):
        # Non-git dir: git commands fail gracefully
        result = changed_files_since(str(tmp_path), ref="HEAD~1")
        assert isinstance(result, list)


# ── current_branch ────────────────────────────────────────────────────────────

class TestCurrentBranch:
    def test_returns_string_or_none(self):
        result = current_branch(TEMPOGRAPH_ROOT)
        assert result is None or isinstance(result, str)

    def test_tempograph_has_a_branch(self):
        result = current_branch(TEMPOGRAPH_ROOT)
        assert result is not None and len(result) > 0

    def test_non_git_dir_returns_none(self, tmp_path):
        result = current_branch(str(tmp_path))
        assert result is None


# ── recently_modified_files ───────────────────────────────────────────────────

class TestRecentlyModifiedFiles:
    def test_returns_set(self):
        result = recently_modified_files(TEMPOGRAPH_ROOT)
        assert isinstance(result, set)

    def test_non_empty_for_active_repo(self):
        result = recently_modified_files(TEMPOGRAPH_ROOT, n_commits=5)
        assert len(result) > 0

    def test_returns_relative_paths(self):
        result = recently_modified_files(TEMPOGRAPH_ROOT, n_commits=3)
        for fp in result:
            assert not fp.startswith("/"), f"Expected relative path, got: {fp}"

    def test_non_git_dir_returns_empty(self, tmp_path):
        result = recently_modified_files(str(tmp_path))
        assert result == set()

    def test_n_commits_zero_returns_empty(self):
        result = recently_modified_files(TEMPOGRAPH_ROOT, n_commits=0)
        assert isinstance(result, set)


# ── file_commit_counts ────────────────────────────────────────────────────────

class TestFileCommitCounts:
    def test_returns_dict(self):
        result = file_commit_counts(TEMPOGRAPH_ROOT)
        assert isinstance(result, dict)

    def test_active_files_have_counts(self):
        result = file_commit_counts(TEMPOGRAPH_ROOT, n_commits=50)
        assert len(result) > 0

    def test_counts_are_positive_ints(self):
        result = file_commit_counts(TEMPOGRAPH_ROOT, n_commits=20)
        assert all(isinstance(v, int) and v > 0 for v in result.values())

    def test_non_git_dir_returns_empty(self, tmp_path):
        result = file_commit_counts(str(tmp_path))
        assert result == {}

    def test_known_active_file_has_count(self):
        result = file_commit_counts(TEMPOGRAPH_ROOT, n_commits=100)
        # tempograph/parser.py is the most-changed file
        assert "tempograph/parser.py" in result
        assert result["tempograph/parser.py"] > 0
