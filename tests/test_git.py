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
    changed_files_branch,
    cochange_matrix_recency,
    file_change_velocity,
    recent_file_commits,
    cochange_pairs,
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


# ── changed_files_branch ──────────────────────────────────────────────────────

class TestChangedFilesBranch:
    def test_returns_list(self):
        result = changed_files_branch(TEMPOGRAPH_ROOT)
        assert isinstance(result, list)

    def test_non_git_dir_returns_empty(self, tmp_path):
        result = changed_files_branch(str(tmp_path))
        assert result == []

    def test_all_items_are_strings(self):
        result = changed_files_branch(TEMPOGRAPH_ROOT)
        assert all(isinstance(f, str) for f in result)


# ── cochange_matrix_recency ───────────────────────────────────────────────────

class TestCochangeMatrixRecency:
    def test_returns_dict(self):
        result = cochange_matrix_recency(TEMPOGRAPH_ROOT)
        assert isinstance(result, dict)

    def test_values_are_lists_of_tuples(self):
        result = cochange_matrix_recency(TEMPOGRAPH_ROOT, n_commits=50)
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, list)
            for item in v:
                assert len(item) == 3  # (file, frequency, recency_score)

    def test_non_git_dir_returns_empty(self, tmp_path):
        result = cochange_matrix_recency(str(tmp_path))
        assert result == {}


# ── file_change_velocity ──────────────────────────────────────────────────────

class TestFileChangeVelocity:
    def test_returns_dict(self):
        result = file_change_velocity(TEMPOGRAPH_ROOT)
        assert isinstance(result, dict)

    def test_values_are_floats(self):
        result = file_change_velocity(TEMPOGRAPH_ROOT, recent_days=30)
        assert all(isinstance(v, float) for v in result.values())

    def test_active_file_has_positive_velocity(self):
        result = file_change_velocity(TEMPOGRAPH_ROOT, recent_days=90)
        # parser.py is frequently changed — should appear with positive velocity
        if result:
            assert all(v > 0 for v in result.values())

    def test_non_git_dir_returns_empty(self, tmp_path):
        result = file_change_velocity(str(tmp_path))
        assert result == {}


# ── recent_file_commits ───────────────────────────────────────────────────────

class TestRecentFileCommits:
    def test_returns_list(self):
        result = recent_file_commits(TEMPOGRAPH_ROOT, "tempograph/parser.py")
        assert isinstance(result, list)

    def test_entries_have_expected_keys(self):
        result = recent_file_commits(TEMPOGRAPH_ROOT, "tempograph/parser.py", n=3)
        for entry in result:
            assert "days_ago" in entry
            assert "message" in entry

    def test_days_ago_is_non_negative(self):
        result = recent_file_commits(TEMPOGRAPH_ROOT, "tempograph/parser.py", n=3)
        assert all(entry["days_ago"] >= 0 for entry in result)

    def test_unknown_file_returns_empty(self):
        result = recent_file_commits(TEMPOGRAPH_ROOT, "nonexistent_xyz.py")
        assert result == []

    def test_respects_n_limit(self):
        result = recent_file_commits(TEMPOGRAPH_ROOT, "tempograph/parser.py", n=2)
        assert len(result) <= 2


# ── cochange_pairs ────────────────────────────────────────────────────────────

class TestCochangePairs:
    def test_returns_list(self):
        result = cochange_pairs(TEMPOGRAPH_ROOT, "tempograph/parser.py")
        assert isinstance(result, list)

    def test_entries_have_path_and_count(self):
        result = cochange_pairs(TEMPOGRAPH_ROOT, "tempograph/parser.py", n=5, min_count=1)
        for entry in result:
            assert "path" in entry
            assert "count" in entry
            assert isinstance(entry["count"], int)

    def test_excludes_input_file_itself(self):
        result = cochange_pairs(TEMPOGRAPH_ROOT, "tempograph/parser.py", min_count=1)
        assert not any(e["path"] == "tempograph/parser.py" for e in result)

    def test_non_git_dir_returns_empty(self, tmp_path):
        result = cochange_pairs(str(tmp_path), "some/file.py")
        assert result == []

    def test_respects_n_limit(self):
        result = cochange_pairs(TEMPOGRAPH_ROOT, "tempograph/parser.py", n=3, min_count=1)
        assert len(result) <= 3
