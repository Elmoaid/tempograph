"""Tests for S1046: Broker warning signal in render_focused.

S1046 fires when a focus seed has BOTH many cross-file callers AND many
cross-file callees — a bidirectional hub. This is distinct from orchestrators
(few callers + many callees), utility leaves (many callers + few callees), and
hub-callee warnings (seed's callees are hubs, not the seed itself).

Conditions to fire:
- kind in {function, method}
- not a test file
- ≥5 cross-file non-test callers
- ≥5 cross-file non-test callees

Distinct from:
- S1035 (orchestrator): requires 1–4 callers; broker requires ≥5
- S65 (change_exposure): risk aggregation, not topology
- S1039 (hub callee): callee of seed is a hub; broker = seed itself is the hub
"""
from __future__ import annotations

import types

import pytest

from tempograph.types import Language, Symbol, SymbolKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sym(
    name: str,
    file_path: str = "src/core.py",
    kind: str = "function",
    language: str = "python",
) -> Symbol:
    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=Language.PYTHON if language == "python" else Language.TYPESCRIPT,
        file_path=file_path,
        line_start=1,
        line_end=40,
        signature=f"def {name}():",
        complexity=8,
    )


def _make_graph(
    symbols: list[Symbol],
    callers: dict[str, list[str]] | None = None,
    callees: dict[str, list[str]] | None = None,
) -> types.SimpleNamespace:
    """Minimal mock Tempo graph with callers_of and callees_of support."""
    sym_map = {s.id: s for s in symbols}
    g = types.SimpleNamespace()
    g.symbols = sym_map
    g.root = "/tmp/test_repo"
    g.hot_files = set()
    g.edges = []
    _callers = callers or {}
    _callees = callees or {}

    def callers_of(sym_id: str) -> list[Symbol]:
        return [sym_map[cid] for cid in _callers.get(sym_id, []) if cid in sym_map]

    def callees_of(sym_id: str) -> list[Symbol]:
        return [sym_map[cid] for cid in _callees.get(sym_id, []) if cid in sym_map]

    g.callers_of = callers_of
    g.callees_of = callees_of
    return g


from tempograph.render.focused import _compute_broker_warning


# ---------------------------------------------------------------------------
# Unit tests — FIRES
# ---------------------------------------------------------------------------


class TestBrokerWarningFires:

    def test_fires_at_minimum_threshold(self):
        """Exactly 5 callers and 5 callees — fires at the threshold."""
        seed = _make_sym("dispatch_event", "src/events.py")
        callers = [_make_sym(f"caller_{i}", f"src/handler_{i}.py") for i in range(5)]
        callees = [_make_sym(f"callee_{i}", f"src/util_{i}.py") for i in range(5)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert "broker" in result
        assert "5 callers" in result
        assert "5 callees" in result

    def test_fires_with_elevated_both(self):
        """8 callers and 9 callees — fires with exact counts in output."""
        seed = _make_sym("process_request", "src/server.py")
        callers = [_make_sym(f"c{i}", f"src/client_{i}.py") for i in range(8)]
        callees = [_make_sym(f"e{i}", f"src/backend_{i}.py") for i in range(9)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert "broker" in result
        assert "8 callers" in result
        assert "9 callees" in result
        assert "↔" in result

    def test_fires_for_method_kind(self):
        """Method with ≥5 callers and ≥5 callees → fires."""
        seed = _make_sym("handle", "src/processor.py", kind="method")
        callers = [_make_sym(f"caller_{i}", f"src/mod_{i}.py") for i in range(6)]
        callees = [_make_sym(f"dep_{i}", f"src/dep_{i}.py") for i in range(7)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert "broker" in result

    def test_fires_message_format(self):
        """Output format: '↳ broker: N callers ↔ M callees — ...'"""
        seed = _make_sym("render_page", "src/renderer.py")
        callers = [_make_sym(f"page_{i}", f"src/page_{i}.py") for i in range(5)]
        callees = [_make_sym(f"widget_{i}", f"src/widget_{i}.py") for i in range(6)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result.startswith("↳ broker:")
        assert "bidirectional hub" in result
        assert "upstream and downstream" in result

    def test_fires_ignores_same_file_callers_in_threshold(self):
        """Same-file callers don't count toward the ≥5 threshold — cross-file only."""
        seed = _make_sym("transform", "src/core.py")
        # 3 same-file callers (don't count) + 5 cross-file callers (do count)
        same_callers = [_make_sym(f"local_{i}", "src/core.py") for i in range(3)]
        cross_callers = [_make_sym(f"remote_{i}", f"src/module_{i}.py") for i in range(5)]
        callees = [_make_sym(f"dep_{i}", f"src/dep_{i}.py") for i in range(5)]
        all_syms = [seed] + same_callers + cross_callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in same_callers + cross_callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        # Cross-file callers: 5 → fires; same-file 3 don't count
        assert "broker" in result
        assert "5 callers" in result


# ---------------------------------------------------------------------------
# Unit tests — SILENT
# ---------------------------------------------------------------------------


class TestBrokerWarningSilent:

    def test_silent_many_callers_few_callees(self):
        """Utility leaf: 12 callers but only 3 callees → silent (not a broker)."""
        seed = _make_sym("is_valid", "src/validators.py")
        callers = [_make_sym(f"c{i}", f"src/mod_{i}.py") for i in range(12)]
        callees = [_make_sym(f"e{i}", f"src/dep_{i}.py") for i in range(3)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_many_callees_few_callers(self):
        """Orchestrator territory: 2 callers + 10 callees → silent (not broker, that's S1035)."""
        seed = _make_sym("orchestrate", "src/main.py")
        callers = [_make_sym(f"c{i}", f"src/entry_{i}.py") for i in range(2)]
        callees = [_make_sym(f"e{i}", f"src/service_{i}.py") for i in range(10)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_below_caller_threshold(self):
        """4 callers + 8 callees — one short of the 5-caller minimum → silent."""
        seed = _make_sym("route", "src/router.py")
        callers = [_make_sym(f"c{i}", f"src/handler_{i}.py") for i in range(4)]
        callees = [_make_sym(f"e{i}", f"src/backend_{i}.py") for i in range(8)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_below_callee_threshold(self):
        """8 callers + 4 callees — one short of the 5-callee minimum → silent."""
        seed = _make_sym("lookup", "src/cache.py")
        callers = [_make_sym(f"c{i}", f"src/module_{i}.py") for i in range(8)]
        callees = [_make_sym(f"e{i}", f"src/store_{i}.py") for i in range(4)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_test_file_seed(self):
        """Seed in a test file → silent regardless of callers/callees."""
        seed = _make_sym("test_broker", "tests/test_core.py")
        callers = [_make_sym(f"c{i}", f"tests/helper_{i}.py") for i in range(6)]
        callees = [_make_sym(f"e{i}", f"src/dep_{i}.py") for i in range(6)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_class_kind(self):
        """Class kind (not function/method) → silent."""
        seed = _make_sym("GraphBuilder", "src/builder.py", kind="class")
        callers = [_make_sym(f"c{i}", f"src/mod_{i}.py") for i in range(6)]
        callees = [_make_sym(f"e{i}", f"src/dep_{i}.py") for i in range(6)]
        all_syms = [seed] + callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_test_file_callers_excluded(self):
        """Callers in test files don't count — 6 callers all in tests/ → silent."""
        seed = _make_sym("parse_input", "src/parser.py")
        # 6 callers but all in test files
        test_callers = [_make_sym(f"test_{i}", f"tests/test_mod_{i}.py") for i in range(6)]
        callees = [_make_sym(f"e{i}", f"src/dep_{i}.py") for i in range(6)]
        all_syms = [seed] + test_callers + callees
        g = _make_graph(
            all_syms,
            callers={seed.id: [c.id for c in test_callers]},
            callees={seed.id: [e.id for e in callees]},
        )
        result = _compute_broker_warning([seed], g)
        assert result == ""

    def test_silent_empty_seeds(self):
        """No seeds → silent."""
        g = _make_graph([], {}, {})
        result = _compute_broker_warning([], g)
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests: real codebase
# ---------------------------------------------------------------------------


class TestBrokerWarningIntegration:

    def test_fires_for_render_focused(self):
        """render_focused in focused.py is a bidirectional hub — broker fires."""
        from tempograph import build_graph
        from tempograph.render.focused import _compute_broker_warning

        graph = build_graph(".")
        sym = None
        for s in graph.symbols.values():
            if s.name == "render_focused" and "focused.py" in s.file_path:
                sym = s
                break
        assert sym is not None, "render_focused not found in graph"

        result = _compute_broker_warning([sym], graph)
        assert "broker" in result, f"Expected broker to fire for render_focused, got: {result!r}"
        assert "callers" in result
        assert "callees" in result

    def test_silent_for_scan_calls(self):
        """_scan_calls has many callers but few callees — not a broker → silent."""
        from tempograph import build_graph
        from tempograph.render.focused import _compute_broker_warning

        graph = build_graph(".")
        sym = None
        for s in graph.symbols.values():
            if s.name == "_scan_calls" and "parser.py" in s.file_path:
                sym = s
                break
        assert sym is not None, "_scan_calls not found in graph"

        result = _compute_broker_warning([sym], graph)
        assert result == "", f"_scan_calls should be silent (leaf caller pattern), got: {result!r}"
