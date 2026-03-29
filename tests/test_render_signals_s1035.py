"""Tests for S1035: Orchestrator advisory signal in render_focused.

S1035 fires when the focus seed has many cross-file callees (≥6) but few non-test callers
(1–4). This characterizes an orchestrator: a coordination function that calls many things
but is not itself widely called. The signal advises agents that BFS expands mostly
downstream and to focus on direct callees.

Distinct from:
- S65 (change_exposure): quantifies risk, not topology role
- S198 (leaf function): opposite topology — many callers, 0 callees
- apex (S45): fires for entry points with 0 callers (handled inline per-symbol)
- S180 (complex hub): fires for high-cx functions with many callers AND callees
"""
from __future__ import annotations
import types
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sym(
    name: str,
    file_path: str = "src/core.py",
    kind: str = "function",
    exported: bool = False,
):
    from tempograph.types import Symbol, SymbolKind, Language
    kmap = {"function": SymbolKind.FUNCTION, "method": SymbolKind.METHOD,
            "class": SymbolKind.CLASS, "variable": SymbolKind.VARIABLE}
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=20,
        exported=exported,
    )


def _make_graph(seed_sym, *, callee_fps: list[str], caller_fps: list[str]):
    """Build a minimal fake Tempo-like graph for unit tests.

    callee_fps: file paths for cross-file callees (non-test)
    caller_fps: file paths for callers (non-test)
    """
    syms = {seed_sym.id: seed_sym}

    callees = []
    for i, fp in enumerate(callee_fps):
        s = _make_sym(f"callee_{i}", file_path=fp)
        syms[s.id] = s
        callees.append(s)

    callers = []
    for i, fp in enumerate(caller_fps):
        s = _make_sym(f"caller_{i}", file_path=fp)
        syms[s.id] = s
        callers.append(s)

    g = types.SimpleNamespace(
        symbols=syms,
        hot_files=set(),
        root="/repo",
    )
    g.callees_of = lambda sid: callees if sid == seed_sym.id else []
    g.callers_of = lambda sid: callers if sid == seed_sym.id else []
    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_orchestrator_advisory
# ---------------------------------------------------------------------------

class TestComputeOrchestratorAdvisory:
    """Unit tests for _compute_orchestrator_advisory."""

    def _call(self, seeds, graph):
        from tempograph.render.focused import _compute_orchestrator_advisory
        return _compute_orchestrator_advisory(seeds, graph)

    def test_fires_for_orchestrator(self):
        """Fires when 6+ cross-file callees and 1–4 non-test callers."""
        seed = _make_sym("orchestrate_pipeline", "src/pipeline.py")
        callee_fps = [f"src/step_{i}.py" for i in range(7)]
        caller_fps = ["src/main.py", "src/cli.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result != ""
        assert "orchestrator" in result

    def test_callee_count_in_output(self):
        """Output includes the cross-file callee count."""
        seed = _make_sym("run_steps", "src/runner.py")
        callee_fps = [f"src/m{i}.py" for i in range(8)]
        caller_fps = ["src/app.py", "src/cli.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert "8 cross-file callees" in result

    def test_caller_count_in_output(self):
        """Output includes the non-test caller count."""
        seed = _make_sym("coordinate", "src/coord.py")
        callee_fps = [f"src/dep_{i}.py" for i in range(6)]
        caller_fps = ["src/main.py", "src/api.py", "src/script.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert "3 callers" in result

    def test_singular_caller_grammar(self):
        """Uses 'caller' (not 'callers') for exactly 1 caller."""
        seed = _make_sym("do_init", "src/init.py")
        callee_fps = [f"src/sub_{i}.py" for i in range(6)]
        caller_fps = ["src/bootstrap.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert "1 caller" in result
        assert "1 callers" not in result

    def test_fires_at_boundary_4_callers(self):
        """Still fires at exactly 4 callers (upper boundary)."""
        seed = _make_sym("process_data", "src/processor.py")
        callee_fps = [f"src/out_{i}.py" for i in range(6)]
        caller_fps = [f"src/entry_{i}.py" for i in range(4)]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result != ""

    def test_silent_when_callers_exceed_4(self):
        """Silent when 5+ callers — this is a hub, not an orchestrator."""
        seed = _make_sym("utility", "src/util.py")
        callee_fps = [f"src/dep_{i}.py" for i in range(6)]
        caller_fps = [f"src/user_{i}.py" for i in range(5)]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result == ""

    def test_silent_when_callees_below_6(self):
        """Silent when fewer than 6 cross-file callees."""
        seed = _make_sym("small_orchestrator", "src/small.py")
        callee_fps = [f"src/dep_{i}.py" for i in range(5)]  # only 5
        caller_fps = ["src/main.py", "src/cli.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result == ""

    def test_silent_when_entry_point_zero_callers(self):
        """Silent when 0 non-test callers — entry point, apex signal handles this."""
        seed = _make_sym("main", "src/main.py")
        callee_fps = [f"src/m{i}.py" for i in range(10)]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=[])
        result = self._call([seed], g)
        assert result == ""

    def test_silent_for_test_file_seed(self):
        """Silent when seed is in a test file."""
        seed = _make_sym("setup_pipeline", "tests/test_pipeline.py")
        callee_fps = [f"src/m{i}.py" for i in range(7)]
        caller_fps = ["tests/conftest.py", "tests/test_other.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result == ""

    def test_silent_for_class_seed(self):
        """Silent for class seeds — applies to functions/methods only."""
        seed = _make_sym("PipelineManager", "src/manager.py", kind="class")
        callee_fps = [f"src/m{i}.py" for i in range(7)]
        caller_fps = ["src/main.py", "src/api.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result == ""

    def test_fires_for_method_seed(self):
        """Fires for method seeds, not just standalone functions."""
        seed = _make_sym("build_context", "src/builder.py", kind="method")
        callee_fps = [f"src/ctx_{i}.py" for i in range(6)]
        caller_fps = ["src/pipeline.py", "src/server.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result != ""

    def test_output_mentions_downstream(self):
        """Output advises agent to focus on direct callees."""
        seed = _make_sym("run_workflow", "src/workflow.py")
        callee_fps = [f"src/stage_{i}.py" for i in range(6)]
        caller_fps = ["src/main.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert "downstream" in result
        assert "direct callees" in result

    def test_same_file_callees_excluded_from_count(self):
        """Callees in the same file as seed don't count toward cross-file threshold."""
        seed = _make_sym("process", "src/core.py")
        # All callees in the same file — should NOT count
        callee_fps = ["src/core.py"] * 8
        caller_fps = ["src/main.py", "src/api.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result == ""

    def test_test_callers_excluded(self):
        """Test file callers don't count toward the caller threshold."""
        seed = _make_sym("bootstrap", "src/bootstrap.py")
        callee_fps = [f"src/m{i}.py" for i in range(7)]
        # Only test callers — counts as 0 non-test callers → SILENT
        caller_fps = ["tests/test_bootstrap.py", "tests/test_integration.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result == ""

    def test_output_starts_with_arrow(self):
        """Output uses the ↳ prefix format consistent with other header signals."""
        seed = _make_sym("orchestrate", "src/orch.py")
        callee_fps = [f"src/m{i}.py" for i in range(6)]
        caller_fps = ["src/main.py"]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        result = self._call([seed], g)
        assert result.startswith("↳ orchestrator:")

    def test_empty_seeds_list(self):
        """Returns empty string for empty seeds list."""
        from tempograph.render.focused import _compute_orchestrator_advisory
        g = types.SimpleNamespace()
        assert _compute_orchestrator_advisory([], g) == ""


# ---------------------------------------------------------------------------
# Integration test: render_focused on the real codebase
# ---------------------------------------------------------------------------

class TestOrchestratorAdvisoryIntegration:
    """Integration tests using the real tempograph codebase."""

    @pytest.fixture(scope="class")
    def graph(self):
        from tempograph import build_graph
        import os
        repo = os.path.dirname(os.path.dirname(__file__))
        return build_graph(repo)

    def test_render_prepare_silent_after_decomp(self, graph):
        """render_prepare (6 callees, 5 same-file after decomp) should NOT fire orchestrator."""
        from tempograph.render.focused import render_focused
        output = render_focused(graph, "render_prepare")
        # After decomposition, render_prepare has only 1 cross-file callee
        # (TaskMemory). The 5 same-file helpers don't count for orchestrator.
        assert "↳ orchestrator:" not in output

    def test_render_focused_silent(self, graph):
        """render_focused (hub: many callers AND callees) should not fire."""
        from tempograph.render.focused import render_focused
        output = render_focused(graph, "render_focused")
        assert "↳ orchestrator:" not in output

    def test_hub_with_many_callers_silent(self):
        """A hub with 5+ non-test callers should not fire (callers > 4 = not orchestrator)."""
        seed = _make_sym("dispatch_requests", file_path="src/router.py")
        callee_fps = [f"src/handler_{i}.py" for i in range(8)]
        caller_fps = [f"src/caller_{i}.py" for i in range(5)]
        g = _make_graph(seed, callee_fps=callee_fps, caller_fps=caller_fps)
        from tempograph.render.focused import _compute_orchestrator_advisory
        result = _compute_orchestrator_advisory([seed], g)
        assert result == ""
