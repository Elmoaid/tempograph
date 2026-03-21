"""Tests for context coverage signal in prepare_context output (S24)."""
import pytest
from tempograph.prepare import _coverage_line, render_prepare
from tempograph.types import Symbol, SymbolKind, Language, Tempo


def _make_symbol(name: str, file_path: str = "src/mod.py") -> Symbol:
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=5,
    )


def _make_graph(symbols: list[Symbol], root: str = "/tmp/repo") -> Tempo:
    graph = Tempo(root=root)
    for sym in symbols:
        graph.symbols[sym.id] = sym
    return graph


class TestCoverageLine:
    def test_all_resolved_high_confidence(self):
        syms = [_make_symbol("alpha"), _make_symbol("beta")]
        graph = _make_graph(syms)
        result = _coverage_line(["alpha", "beta"], graph, ["src/mod.py"])
        assert "2 of 2" in result
        assert "100%" in result
        assert "confidence: high" in result
        assert "1 key files" in result

    def test_partial_resolved_medium_confidence(self):
        syms = [_make_symbol("alpha")]
        graph = _make_graph(syms)
        result = _coverage_line(["alpha", "gamma"], graph, ["a.py", "b.py"])
        assert "1 of 2" in result
        assert "50%" in result
        assert "confidence: medium" in result
        assert "2 key files" in result

    def test_none_resolved_low_confidence(self):
        graph = _make_graph([_make_symbol("unrelated")])
        result = _coverage_line(["alpha", "beta"], graph, [])
        assert "0 of 2" in result
        assert "confidence: low" in result
        assert "0 key files" in result

    def test_empty_query_returns_empty(self):
        graph = _make_graph([_make_symbol("alpha")])
        assert _coverage_line([], graph, ["a.py"]) == ""

    def test_case_insensitive_match(self):
        syms = [_make_symbol("Alpha")]
        graph = _make_graph(syms)
        result = _coverage_line(["alpha"], graph, ["a.py"])
        assert "1 of 1" in result
        assert "confidence: high" in result

    def test_boundary_75_percent(self):
        syms = [_make_symbol("a"), _make_symbol("b"), _make_symbol("c")]
        graph = _make_graph(syms)
        # 3 of 4 = 75% — boundary, should be medium (>75 required for high)
        result = _coverage_line(["a", "b", "c", "d"], graph, [])
        assert "3 of 4" in result
        assert "confidence: medium" in result


class TestRenderPrepareCoverage:
    def _make_git_graph(self, tmp_path):
        import subprocess
        from tempograph.builder import build_graph
        (tmp_path / "app.py").write_text("def serve(): pass\ndef handle(): pass\n")
        (tmp_path / "utils.py").write_text("def parse(): pass\n")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=tmp_path, check=True,
                        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"})
        return build_graph(str(tmp_path))

    def test_coverage_appears_in_general_task(self, tmp_path):
        graph = self._make_git_graph(tmp_path)
        result = render_prepare(graph, "fix serve function")
        assert "Context coverage:" in result
        assert "confidence:" in result

    def test_coverage_appears_before_feedback(self, tmp_path):
        graph = self._make_git_graph(tmp_path)
        result = render_prepare(graph, "fix serve function")
        lines = result.split("\n")
        coverage_idx = None
        feedback_idx = None
        for i, line in enumerate(lines):
            if "Context coverage:" in line:
                coverage_idx = i
            if "report_feedback" in line:
                feedback_idx = i
        if coverage_idx is not None and feedback_idx is not None:
            assert coverage_idx < feedback_idx
