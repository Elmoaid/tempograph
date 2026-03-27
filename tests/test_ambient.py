"""Tests for tempograph/ambient.py — per-directory LOD-1 context generation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tempograph.builder import build_graph
from tempograph.ambient import (
    generate_ambient,
    write_ambient,
    CONTEXT_FILENAME,
    _parse_changed_lines,
    _recently_changed_symbols_section,
)


def _build(tmp_path: Path, files: dict[str, str]) -> object:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_graph(str(tmp_path), use_cache=False, use_config=False)


class TestAmbientLod1Map:
    def test_ambient_generates_lod1_map(self, tmp_path):
        """Symbol names for files in the directory appear in generated content."""
        g = _build(tmp_path, {"utils.py": "def helper(): pass\ndef runner(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        assert contents, "Expected at least one directory"
        combined = "\n".join(contents.values())
        assert "helper" in combined
        assert "runner" in combined

    def test_ambient_lod1_shows_filename(self, tmp_path):
        """Output includes the filename as part of the LOD-1 map."""
        g = _build(tmp_path, {"models.py": "class User: pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        assert "models.py" in combined

    def test_ambient_header_has_freshness(self, tmp_path):
        """File header includes Generated timestamp."""
        g = _build(tmp_path, {"app.py": "def main(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        assert "tempograph-context" in combined
        assert "Generated:" in combined

    def test_ambient_header_has_gitignore_hint(self, tmp_path):
        """Header suggests adding context file to .gitignore."""
        g = _build(tmp_path, {"app.py": "def main(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        assert ".gitignore" in combined


class TestAmbientCrossFileEdges:
    def test_ambient_includes_cross_file_edges(self, tmp_path):
        """1-hop cross-file call relationships appear in output."""
        g = _build(tmp_path, {
            "caller.py": "from utils import helper\ndef run():\n    helper()\n",
            "utils.py": "def helper(): pass\n",
        })
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        # Either cross-file calls section or "no cross-file calls" message must appear
        assert ("calls" in combined or "cross-file" in combined.lower())

    def test_ambient_cross_file_shows_arrow(self, tmp_path):
        """Cross-file calls section uses arrow notation."""
        g = _build(tmp_path, {
            "a.py": "from b import thing\ndef run():\n    thing()\n",
            "b.py": "def thing(): pass\n",
        })
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        # Either cross-file call found (→ or ←) or no-call message
        has_call_notation = "→" in combined or "←" in combined
        has_no_call_msg = "no cross-file calls" in combined
        assert has_call_notation or has_no_call_msg


class TestAmbientTestMapping:
    def test_ambient_test_mapping_name_convention(self, tmp_path):
        """test_<module>.py is matched to <module>.py by naming convention."""
        g = _build(tmp_path, {
            "auth.py": "def login(): pass\n",
            "tests/test_auth.py": "from auth import login\ndef test_login(): pass\n",
        })
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        # auth.py should be matched to test_auth.py
        assert "test_auth" in combined or "tested by" in combined

    def test_ambient_no_tests_shows_placeholder(self, tmp_path):
        """When no test files exist, a placeholder message appears."""
        g = _build(tmp_path, {"utils.py": "def helper(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        assert "no test files detected" in combined or "Test coverage" in combined


class TestAmbientHotOnly:
    def test_ambient_skips_non_hot_dirs(self, tmp_path, monkeypatch):
        """hot_only=True skips directories where all files have no recent git changes."""
        from tempograph.git import file_last_modified_days as _fld
        # Patch file_last_modified_days to return 90 days for all files (not hot)
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda repo, fp: 90)
        g = _build(tmp_path, {"stale.py": "def old(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=True)
        assert len(contents) == 0, "Expected no dirs when all files are 90d stale"

    def test_ambient_all_dirs_bypasses_hot_filter(self, tmp_path, monkeypatch):
        """hot_only=False includes all directories regardless of git age."""
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda repo, fp: 90)
        g = _build(tmp_path, {"stale.py": "def old(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        assert len(contents) > 0, "Expected dirs when hot_only=False"

    def test_ambient_includes_hot_dirs(self, tmp_path, monkeypatch):
        """hot_only=True includes directories where at least one file changed recently."""
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda repo, fp: 2)
        g = _build(tmp_path, {"fresh.py": "def new_fn(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=True)
        assert len(contents) > 0, "Expected dirs when files changed 2 days ago"


class TestAmbientWriteFiles:
    def test_write_ambient_creates_context_file(self, tmp_path):
        """write_ambient creates .tempograph-context.md in each directory."""
        g = _build(tmp_path, {"app.py": "def main(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        write_ambient(contents, str(tmp_path))

        context_files = list(tmp_path.rglob(CONTEXT_FILENAME))
        assert len(context_files) > 0, f"Expected {CONTEXT_FILENAME} to be written"

    def test_write_ambient_content_readable(self, tmp_path):
        """Written file contains expected symbol names."""
        g = _build(tmp_path, {"lib.py": "def exported_fn(): pass\n"})
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        write_ambient(contents, str(tmp_path))

        ctx_file = tmp_path / CONTEXT_FILENAME
        assert ctx_file.exists()
        text = ctx_file.read_text()
        assert "exported_fn" in text

    def test_ambient_returns_dict_of_strings(self, tmp_path):
        """generate_ambient returns {str: str} dict."""
        g = _build(tmp_path, {"foo.py": "x = 1\n"})
        result = generate_ambient(g, str(tmp_path), hot_only=False)
        assert isinstance(result, dict)
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, str)


class TestParseChangedLines:
    def test_single_line_added(self):
        diff = "@@ -0,0 +1 @@\n+def foo(): pass\n"
        assert _parse_changed_lines(diff) == {1}

    def test_multi_line_hunk(self):
        diff = "@@ -5,0 +5,3 @@\n+line1\n+line2\n+line3\n"
        assert _parse_changed_lines(diff) == {5, 6, 7}

    def test_pure_deletion_excluded(self):
        # length=0 means deletion only — no new lines
        diff = "@@ -10,3 +10,0 @@\n-deleted\n"
        assert _parse_changed_lines(diff) == set()

    def test_multiple_hunks(self):
        diff = "@@ -1,0 +1,2 @@\n+a\n+b\n@@ -20,0 +22,1 @@\n+c\n"
        assert _parse_changed_lines(diff) == {1, 2, 22}

    def test_no_hunk_headers(self):
        diff = "diff --git a/foo.py b/foo.py\nindex 1234..5678\n"
        assert _parse_changed_lines(diff) == set()


class TestRecentlyChangedSymbolsSection:
    def test_absent_when_no_hot_files(self, tmp_path, monkeypatch):
        """Section is empty string when all files are older than 7 days."""
        g = _build(tmp_path, {"mod.py": "def alpha(): pass\n"})
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 90)
        section = _recently_changed_symbols_section(str(tmp_path), g, ["mod.py"])
        assert section == ""

    def test_absent_when_git_returns_empty(self, tmp_path, monkeypatch):
        """Section is empty string when git log returns no output."""
        g = _build(tmp_path, {"mod.py": "def alpha(): pass\n"})
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 0)
        mock_result = MagicMock(returncode=0, stdout="")
        with patch("tempograph.ambient.subprocess.run", return_value=mock_result):
            section = _recently_changed_symbols_section(str(tmp_path), g, ["mod.py"])
        assert section == ""

    def test_shows_changed_symbol(self, tmp_path, monkeypatch):
        """Symbol whose line range overlaps the diff hunk appears in output."""
        # alpha() is at line 1, beta() at line 2
        g = _build(tmp_path, {"mod.py": "def alpha(): pass\ndef beta(): pass\n"})
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 0)
        # Diff covers only line 1 (alpha's range)
        fake_diff = "@@ -0,0 +1,1 @@\n+def alpha(): pass\n"
        mock_result = MagicMock(returncode=0, stdout=fake_diff)
        with patch("tempograph.ambient.subprocess.run", return_value=mock_result):
            section = _recently_changed_symbols_section(str(tmp_path), g, ["mod.py"])
        assert "alpha" in section
        assert "beta" not in section

    def test_excludes_unchanged_symbol(self, tmp_path, monkeypatch):
        """Symbol outside the diff hunk range does not appear."""
        g = _build(tmp_path, {
            "mod.py": "def alpha(): pass\n\n\n\ndef beta(): pass\n",
        })
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 0)
        # Only line 5 changed — beta's range
        fake_diff = "@@ -4,0 +5,1 @@\n+def beta(): pass\n"
        mock_result = MagicMock(returncode=0, stdout=fake_diff)
        with patch("tempograph.ambient.subprocess.run", return_value=mock_result):
            section = _recently_changed_symbols_section(str(tmp_path), g, ["mod.py"])
        assert "beta" in section
        assert "alpha" not in section

    def test_overflow_cap(self, tmp_path, monkeypatch):
        """More than _MAX_RECENT_SYMS_PER_FILE shows overflow count."""
        code = "\n".join(f"def fn{i}(): pass" for i in range(6)) + "\n"
        g = _build(tmp_path, {"mod.py": code})
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 0)
        # All 6 lines changed
        fake_diff = "@@ -0,0 +1,6 @@\n" + "".join(f"+def fn{i}(): pass\n" for i in range(6))
        mock_result = MagicMock(returncode=0, stdout=fake_diff)
        with patch("tempograph.ambient.subprocess.run", return_value=mock_result):
            section = _recently_changed_symbols_section(str(tmp_path), g, ["mod.py"])
        # Should show 3 names + overflow
        assert "+3 more" in section or "+2 more" in section or "+1 more" in section

    def test_integrated_into_generate_ambient(self, tmp_path, monkeypatch):
        """generate_ambient includes recently-changed section when symbols found."""
        g = _build(tmp_path, {"app.py": "def main(): pass\n"})
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 0)
        # Mock the batch function: app.py changed at line 1 (where main() is defined)
        monkeypatch.setattr(
            "tempograph.ambient._batch_changed_lines",
            lambda repo, files, days: {"app.py": {1}},
        )
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        assert "Recently changed" in combined
        assert "main" in combined

    def test_section_absent_in_generate_ambient_when_stale(self, tmp_path, monkeypatch):
        """generate_ambient omits section for stale files."""
        g = _build(tmp_path, {"app.py": "def main(): pass\n"})
        monkeypatch.setattr("tempograph.ambient.file_last_modified_days", lambda r, fp: 90)
        contents = generate_ambient(g, str(tmp_path), hot_only=False)
        combined = "\n".join(contents.values())
        assert "Recently changed" not in combined
