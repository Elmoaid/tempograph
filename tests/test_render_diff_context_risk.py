"""S39: Change risk score in diff mode output."""

from unittest.mock import patch

from tempograph.builder import build_graph
from tempograph.render import render_diff_context


class TestDiffChangeRiskScore:
    """render_diff_context should annotate files with MEDIUM/HIGH change risk."""

    def _build(self, tmp_path, files: dict):
        for name, content in files.items():
            (tmp_path / name).write_text(content)
        return build_graph(str(tmp_path), use_cache=False)

    def test_high_risk_annotated(self, tmp_path):
        """File with many cross-file callers gets HIGH risk annotation."""
        files = {
            "core.py": "def a():\n    pass\ndef b():\n    pass\ndef c():\n    pass\n",
        }
        # Add many caller files to push callers_count >= 12
        for i in range(12):
            files[f"user_{i}.py"] = (
                "from core import a, b, c\n"
                f"def use_{i}():\n    a(); b(); c()\n"
            )
        g = self._build(tmp_path, files)
        # No git repo → churn=0, but callers should be enough for HIGH
        out = render_diff_context(g, ["core.py"])
        assert "change risk: HIGH" in out, f"Expected HIGH risk; got:\n{out}"
        assert "callers:" in out
        assert "churn: 0" in out

    def test_medium_risk_annotated(self, tmp_path):
        """File with moderate cross-file callers gets MEDIUM risk annotation."""
        files = {
            "core.py": "def helper():\n    pass\n",
        }
        # 6 callers → risk=6 → MEDIUM (no churn)
        for i in range(6):
            files[f"user_{i}.py"] = (
                "from core import helper\n"
                f"def use_{i}():\n    helper()\n"
            )
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["core.py"])
        assert "change risk: MEDIUM" in out, f"Expected MEDIUM risk; got:\n{out}"

    def test_low_risk_not_annotated(self, tmp_path):
        """File with few callers should NOT show a risk annotation."""
        g = self._build(tmp_path, {
            "core.py": "def fn():\n    pass\n",
            "user.py": "from core import fn\ndef use():\n    fn()\n",
        })
        out = render_diff_context(g, ["core.py"])
        assert "change risk:" not in out, f"LOW risk must not be shown; got:\n{out}"

    def test_risk_line_format(self, tmp_path):
        """Risk line must match exact format: '  change risk: LEVEL (callers: N, churn: M)'."""
        import re
        files = {
            "core.py": "def a():\n    pass\n",
        }
        for i in range(8):
            files[f"u{i}.py"] = f"from core import a\ndef f{i}():\n    a()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["core.py"])
        pattern = r"  change risk: (HIGH|MEDIUM) \(callers: \d+, churn: \d+\)"
        match = re.search(pattern, out)
        assert match, f"Risk line format mismatch; got:\n{out}"

    def test_churn_contributes_to_risk(self, tmp_path):
        """Churn from git history should contribute to the risk score."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "core.py").write_text("def fn():\n    pass\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

        g = build_graph(str(tmp_path), use_cache=False)
        # Patch git.file_commit_counts to return churn=4 → risk = 0 + 4*2 = 8 → MEDIUM
        from tempograph.git import file_commit_counts
        file_commit_counts.cache_clear()
        with patch("tempograph.git.file_commit_counts", return_value={"core.py": 4}):
            out = render_diff_context(g, ["core.py"])
        file_commit_counts.cache_clear()

        assert "change risk: MEDIUM" in out, f"Expected MEDIUM from churn; got:\n{out}"


class TestDiffUntestedChanges:
    """S212: render_diff_context should list changed callables with zero test coverage."""

    def _build(self, tmp_path, files: dict):
        for name, content in files.items():
            (tmp_path / name).write_text(content)
        return build_graph(str(tmp_path), use_cache=False)

    def test_untested_fn_appears(self, tmp_path):
        """Function in changed file with no test caller is listed as untested change."""
        g = self._build(tmp_path, {
            "core.py": "def tested_fn():\n    pass\n\ndef untested_fn():\n    pass\n",
            "test_core.py": "from core import tested_fn\ndef test_x():\n    tested_fn()\n",
        })
        out = render_diff_context(g, ["core.py"])
        assert "untested changes" in out, f"Expected 'untested changes' section; got:\n{out}"
        assert "untested_fn" in out, f"Expected untested_fn listed; got:\n{out}"
        # Count must be 1 — only untested_fn is uncovered, not tested_fn
        assert "untested changes (1)" in out, f"Expected count=1; got:\n{out}"

    def test_all_tested_no_section(self, tmp_path):
        """When all changed callables have test callers, the untested section is absent."""
        g = self._build(tmp_path, {
            "core.py": "def fn():\n    pass\n",
            "test_core.py": "from core import fn\ndef test_fn():\n    fn()\n",
        })
        out = render_diff_context(g, ["core.py"])
        assert "untested changes" not in out, f"Unexpected 'untested changes'; got:\n{out}"

    def test_overflow_count_shown(self, tmp_path):
        """When > 6 untested callables, first 6 shown with +N more."""
        fns = "\n".join(f"def fn{i}():\n    pass" for i in range(8))
        g = self._build(tmp_path, {
            "core.py": fns,
        })
        out = render_diff_context(g, ["core.py"])
        assert "untested changes" in out, f"Expected untested changes; got:\n{out}"
        assert "+2 more" in out, f"Expected '+2 more' overflow; got:\n{out}"
