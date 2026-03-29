"""Tests for S1039: Hub callee warning signal in render_focused.

S1039 fires when a direct (depth-1) callee of the focus seed is itself a hub —
i.e., called from >= 10 unique non-test caller files.

Purpose: when editing a function, agents may decide to also modify one of its
callees to support the change. If that callee is widely-shared infrastructure
(many callers), modifying it carries a much larger blast radius than modifying
the seed alone. The signal makes this visible BEFORE the agent decides to touch
the callee.

Distinct from:
- S65 (change_exposure): the SEED's own blast radius (its callers)
- S66 (hub BFS scope): SEED is a hub and BFS is truncated
- S1035 (orchestrator advisory): SEED has many callees, few callers
- S1036 (relay point): one callee dominates the DOWNSTREAM reach
"""
from __future__ import annotations
import types
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sym(
    name: str,
    file_path: str = "src/utils.py",
    kind: str = "function",
    exported: bool = True,
    qualified_name: str | None = None,
):
    from tempograph.types import Symbol, SymbolKind, Language
    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
        "variable": SymbolKind.VARIABLE,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=qualified_name if qualified_name is not None else name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=30,
        exported=exported,
    )


def _make_graph(
    seed_sym,
    *,
    callees=None,          # list of (sym, caller_files_count, test_callers_count=0)
    same_file_callees=None,  # list of sym that are in seed's same file (excluded)
):
    """Build a minimal fake Tempo for hub callee warning unit tests.

    callees: list of (Symbol, n_caller_files, n_test_callers) tuples.
    Each callee is given n_caller_files unique non-test callers from different files,
    and n_test_callers callers from test files. This lets us test threshold boundaries.
    """
    callees = callees or []
    same_file_callees = same_file_callees or []

    g = types.SimpleNamespace()
    g.symbols = {seed_sym.id: seed_sym}
    g.edges = []
    g.files = {}
    g.hot_files = set()
    g._callers = {}
    g._callees = {seed_sym.id: []}
    g._children = {}
    g._importers = {}
    g._renderers = {}
    g._subtypes = {}

    # Wire cross-file callees (from different files)
    for item in callees:
        if len(item) == 2:
            callee_sym, n_caller_files = item
            n_test_callers = 0
        else:
            callee_sym, n_caller_files, n_test_callers = item

        assert callee_sym.file_path != seed_sym.file_path, (
            "Use same_file_callees for same-file entries"
        )
        g.symbols[callee_sym.id] = callee_sym
        g._callees[seed_sym.id].append(callee_sym.id)

        callee_caller_ids = []

        # Add non-test callers from distinct files
        for i in range(n_caller_files):
            caller_file = f"src/module_{i:03d}.py"
            caller_sym = _make_sym(f"caller_{i}", file_path=caller_file)
            g.symbols[caller_sym.id] = caller_sym
            callee_caller_ids.append(caller_sym.id)

        # Add test-file callers (should be excluded from hub score)
        for j in range(n_test_callers):
            test_caller = _make_sym(f"test_fn_{j}", file_path=f"tests/test_module_{j}.py")
            g.symbols[test_caller.id] = test_caller
            callee_caller_ids.append(test_caller.id)

        g._callers[callee_sym.id] = callee_caller_ids

    # Wire same-file callees (should be excluded from d1_callees)
    for callee_sym in same_file_callees:
        assert callee_sym.file_path == seed_sym.file_path
        g.symbols[callee_sym.id] = callee_sym
        g._callees[seed_sym.id].append(callee_sym.id)

    def _callers_of(sym_id):
        return [g.symbols[s] for s in g._callers.get(sym_id, []) if s in g.symbols]

    def _callees_of(sym_id):
        return [g.symbols[s] for s in g._callees.get(sym_id, []) if s in g.symbols]

    def _renderers_of(sym_id):
        return []

    def _subtypes_of(name):
        return []

    def _importers_of(fp):
        return []

    def _find_symbol(name):
        return [s for s in g.symbols.values() if s.name == name]

    g.callers_of = _callers_of
    g.callees_of = _callees_of
    g.renderers_of = _renderers_of
    g.subtypes_of = _subtypes_of
    g.importers_of = _importers_of
    g.find_symbol = _find_symbol

    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_hub_callee_warning
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_hub_callee_warning


class TestHubCalleeWarningUnit:
    # ---- Fires correctly ----

    def test_fires_single_hub_callee(self):
        """Single cross-file callee with 10+ caller files → fires."""
        seed = _make_sym("process_request", file_path="src/handler.py")
        hub = _make_sym("build_result", file_path="src/builder.py")
        g = _make_graph(seed, callees=[(hub, 12)])
        result = _compute_hub_callee_warning([seed], g)
        assert "hub callee:" in result
        assert "build_result" in result
        assert "12 caller files" in result
        assert "shared infrastructure" in result

    def test_fires_multiple_hub_callees_shows_top_two(self):
        """Multiple hub callees → shows top 2 by caller file count."""
        seed = _make_sym("orchestrate", file_path="src/core.py")
        hub_a = _make_sym("config_get", file_path="src/config.py")
        hub_b = _make_sym("serialize", file_path="src/serial.py")
        g = _make_graph(seed, callees=[(hub_a, 15), (hub_b, 11)])
        result = _compute_hub_callee_warning([seed], g)
        assert "hub callees:" in result
        assert "config_get" in result
        assert "serialize" in result
        assert "15 files" in result
        assert "11 files" in result

    def test_orders_by_caller_count_descending(self):
        """Hub callees sorted highest caller count first."""
        seed = _make_sym("run", file_path="src/runner.py")
        small_hub = _make_sym("minor_util", file_path="src/minor.py")
        big_hub = _make_sym("major_shared", file_path="src/major.py")
        g = _make_graph(seed, callees=[(small_hub, 10), (big_hub, 25)])
        result = _compute_hub_callee_warning([seed], g)
        # big_hub (25) should appear before small_hub (10)
        pos_big = result.index("major_shared")
        pos_small = result.index("minor_util")
        assert pos_big < pos_small

    def test_overflow_for_more_than_two_hub_callees(self):
        """Three hub callees → shows top 2 + overflow count."""
        seed = _make_sym("do_all", file_path="src/main.py")
        hubs = [
            _make_sym(f"hub_{i}", file_path=f"src/hub_{i}.py")
            for i in range(3)
        ]
        g = _make_graph(seed, callees=[(hubs[0], 20), (hubs[1], 15), (hubs[2], 10)])
        result = _compute_hub_callee_warning([seed], g)
        assert "+1 more" in result

    def test_uses_qualified_name_for_methods(self):
        """For method callees, qualified_name (e.g. Config.get) is used in output."""
        seed = _make_sym("process", file_path="src/worker.py")
        hub = _make_sym("get", file_path="src/config.py", kind="method",
                         qualified_name="Config.get")
        g = _make_graph(seed, callees=[(hub, 14)])
        result = _compute_hub_callee_warning([seed], g)
        assert "Config.get" in result

    # ---- Threshold boundary conditions ----

    def test_fires_at_exactly_10_caller_files(self):
        """Exactly 10 unique non-test caller files = at threshold → fires."""
        seed = _make_sym("fetch", file_path="src/fetcher.py")
        hub = _make_sym("parse", file_path="src/parser.py")
        g = _make_graph(seed, callees=[(hub, 10)])
        result = _compute_hub_callee_warning([seed], g)
        assert "hub callee:" in result
        assert "10 caller files" in result

    def test_silent_at_9_caller_files(self):
        """9 caller files = just below threshold → silent."""
        seed = _make_sym("fetch", file_path="src/fetcher.py")
        almost_hub = _make_sym("parse", file_path="src/parser.py")
        g = _make_graph(seed, callees=[(almost_hub, 9)])
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    # ---- Test-file caller exclusion ----

    def test_test_callers_excluded_from_hub_score(self):
        """Test-file callers don't count toward the hub threshold."""
        seed = _make_sym("run_pipeline", file_path="src/pipeline.py")
        callee = _make_sym("setup", file_path="src/setup.py")
        # 5 real callers + 20 test callers = 5 real (below threshold)
        g = _make_graph(seed, callees=[(callee, 5, 20)])
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    def test_test_callers_combined_with_real_hits_threshold(self):
        """10 real callers + 10 test callers → fires (only real count)."""
        seed = _make_sym("process", file_path="src/proc.py")
        hub = _make_sym("validate", file_path="src/validate.py")
        g = _make_graph(seed, callees=[(hub, 10, 10)])
        result = _compute_hub_callee_warning([seed], g)
        assert "hub callee:" in result
        assert "10 caller files" in result

    # ---- Silent conditions ----

    def test_silent_no_callees(self):
        """Seed with no callees → silent."""
        seed = _make_sym("leaf_fn", file_path="src/leaf.py")
        g = _make_graph(seed)
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    def test_silent_all_callees_below_threshold(self):
        """Callees with < 10 caller files → silent."""
        seed = _make_sym("do_work", file_path="src/worker.py")
        small = _make_sym("helper", file_path="src/helpers.py")
        g = _make_graph(seed, callees=[(small, 5)])
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    def test_silent_for_class_seed(self):
        """Class seeds → silent (only function/method seeds fire)."""
        seed = _make_sym("MyClass", file_path="src/myclass.py", kind="class")
        hub = _make_sym("shared_fn", file_path="src/shared.py")
        g = _make_graph(seed, callees=[(hub, 15)])
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    def test_silent_for_test_file_seed(self):
        """Test file seeds → silent."""
        seed = _make_sym("test_something", file_path="tests/test_core.py")
        hub = _make_sym("setup_db", file_path="src/db.py")
        g = _make_graph(seed, callees=[(hub, 20)])
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    def test_silent_for_same_file_callees(self):
        """Same-file callees are excluded (already visible in context)."""
        seed = _make_sym("public_api", file_path="src/api.py")
        same_file = _make_sym("_internal_helper", file_path="src/api.py")
        g = _make_graph(seed, same_file_callees=[same_file])
        # Manually add many callers to the same-file callee — they shouldn't trigger
        g._callers[same_file.id] = [
            _make_sym(f"caller_{i}", file_path=f"src/other_{i}.py").id
            for i in range(15)
        ]
        result = _compute_hub_callee_warning([seed], g)
        assert result == ""

    def test_silent_empty_seeds(self):
        """Empty seeds list → silent."""
        g = types.SimpleNamespace()
        result = _compute_hub_callee_warning([], g)
        assert result == ""

    def test_method_seed_fires(self):
        """Method seeds (not just functions) fire when callee is a hub."""
        seed = _make_sym("render", file_path="src/component.py", kind="method")
        hub = _make_sym("get_state", file_path="src/store.py")
        g = _make_graph(seed, callees=[(hub, 12)])
        result = _compute_hub_callee_warning([seed], g)
        assert "hub callee:" in result


# ---------------------------------------------------------------------------
# Integration tests using render_focused
# ---------------------------------------------------------------------------

class TestHubCalleeWarningIntegration:
    """Integration tests: render_focused produces hub callee output on real codebase."""

    @staticmethod
    def _focus(query: str) -> str:
        from tempograph import build_graph
        from tempograph.render.focused import render_focused
        g = build_graph(".")
        return render_focused(g, query)

    def test_get_or_build_graph_has_hub_callee(self):
        """_get_or_build_graph calls Config.get (43 files) — should fire."""
        result = self._focus("_get_or_build_graph")
        assert "hub callee:" in result or "hub callees:" in result
        assert "Config" in result

    def test_render_prepare_has_hub_callees(self):
        """render_prepare calls Config.get + count_tokens — both are hubs."""
        result = self._focus("render_prepare")
        assert "hub callee" in result
        # At least one of the known hubs should appear
        assert "Config" in result or "count_tokens" in result

    def test_is_test_file_is_silent(self):
        """_is_test_file is a simple utility — its callees are not hubs."""
        result = self._focus("_is_test_file")
        assert "hub callee:" not in result
        assert "hub callees:" not in result

    def test_file_parser_class_is_silent(self):
        """FileParser is a class seed — hub callee should not fire."""
        result = self._focus("FileParser")
        assert "hub callee:" not in result
        assert "hub callees:" not in result
