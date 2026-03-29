"""Tests for S1036: Relay point signal in render_focused.

S1036 fires when one depth-1 cross-file callee of the seed dominates ≥65% of the combined
cross-file downstream reach of all depth-1 callees. The idea: BFS presents all depth-1
callees as peers, but one of them secretly gates most of the downstream call surface.
Changing that intermediate cascades through the majority of the chain.

Distinct from:
- S1035 (orchestrator advisory): fires when the SEED has many callees and few callers
- S65 (change_exposure): quantifies risk via caller files and hot callees
- S122 (blast radius): counts files affected, not downstream reach concentration
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


def _make_graph(
    seed_sym,
    *,
    relay_sym=None,
    relay_downstream_count: int = 0,
    other_callees: list = None,
    other_downstream_per_callee: list = None,
):
    """Build a minimal fake Tempo for relay point unit tests.

    relay_sym: the potential relay — a cross-file callee of seed
    relay_downstream_count: how many cross-file callees relay_sym has
    other_callees: list of other cross-file callee symbols
    other_downstream_per_callee: downstream reach for each other callee
    """
    other_callees = other_callees or []
    other_downstream_per_callee = other_downstream_per_callee or [0] * len(other_callees)

    syms = {seed_sym.id: seed_sym}
    seed_callee_ids = []

    # Add relay callee and its downstream
    relay_downstream_syms = []
    if relay_sym:
        syms[relay_sym.id] = relay_sym
        seed_callee_ids.append(relay_sym.id)
        for i in range(relay_downstream_count):
            ds = _make_sym(f"relay_ds_{i}", file_path="src/downstream.py")
            syms[ds.id] = ds
            relay_downstream_syms.append(ds)

    # Add other callees and their downstream
    other_callee_downstream: dict[str, list] = {}
    for i, (callee, n_ds) in enumerate(zip(other_callees, other_downstream_per_callee)):
        syms[callee.id] = callee
        seed_callee_ids.append(callee.id)
        ds_syms = []
        for j in range(n_ds):
            ds = _make_sym(f"other_ds_{i}_{j}", file_path="src/other_downstream.py")
            syms[ds.id] = ds
            ds_syms.append(ds)
        other_callee_downstream[callee.id] = ds_syms

    # Build _callees index
    callees_index: dict[str, list[str]] = {seed_sym.id: seed_callee_ids}
    if relay_sym:
        callees_index[relay_sym.id] = [ds.id for ds in relay_downstream_syms]
    for callee_id, ds_syms in other_callee_downstream.items():
        callees_index[callee_id] = [ds.id for ds in ds_syms]

    g = types.SimpleNamespace(
        symbols=syms,
        hot_files=set(),
        root="/repo",
        _callees=callees_index,
    )
    return g


def _ordered_stub(seed_sym, d1_syms: list):
    """Build a minimal ordered BFS stub: seed at depth=0, d1_syms at depth=1."""
    result = [(seed_sym, 0)]
    for s in d1_syms:
        result.append((s, 1))
    return result


# ---------------------------------------------------------------------------
# Unit tests for _compute_relay_point
# ---------------------------------------------------------------------------

class TestComputeRelayPoint:
    """Unit tests for _compute_relay_point."""

    def _call(self, seeds, graph, ordered):
        from tempograph.render.focused import _compute_relay_point
        return _compute_relay_point(seeds, graph, ordered)

    def _relay_sym(self, name="relay_fn", file_path="src/relay.py"):
        return _make_sym(name, file_path=file_path)

    def _other_callee(self, i=0):
        return _make_sym(f"other_callee_{i}", file_path=f"src/other_{i}.py")

    # --- FIRES: dominant relay cases ---

    def test_fires_100pct_dominant(self):
        """Relay owns 100% of downstream: all reach goes through one function."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        other = self._other_callee()
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=10,
            other_callees=[other],
            other_downstream_per_callee=[0],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert "relay point" in result
        assert relay.name in result
        assert "10/10" in result
        assert "100%" in result

    def test_fires_exactly_65pct_threshold(self):
        """Relay owns exactly 65% of total downstream — just at the threshold."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        other = self._other_callee()
        # 13 relay + 7 other = 20 total; 13/20 = 65%
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=13,
            other_callees=[other],
            other_downstream_per_callee=[7],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert "relay point" in result
        assert relay.name in result

    def test_fires_80pct_dominant_with_multiple_others(self):
        """Relay owns 80% even when there are 3 other callees."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        others = [self._other_callee(i) for i in range(3)]
        # 8 relay + 3*0.67 = 8+2 = 10 total; 8/10 = 80%
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=8,
            other_callees=others,
            other_downstream_per_callee=[1, 1, 0],
        )
        ordered = _ordered_stub(seed, [relay] + others)
        result = self._call([seed], graph, ordered)
        assert "relay point" in result
        assert relay.name in result

    def test_output_format(self):
        """Check output includes expected tokens: relay name, counts, treat-as-load-bearing."""
        seed = _make_sym("entry_fn")
        relay = self._relay_sym("dispatch_fn")
        other = self._other_callee()
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=9,
            other_callees=[other],
            other_downstream_per_callee=[1],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result.startswith("↳ relay point:")
        assert "dispatch_fn" in result
        assert "load-bearing" in result

    # --- SILENT: below threshold cases ---

    def test_silent_below_65pct_threshold(self):
        """Relay owns only 60% — just below threshold; signal stays silent."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        other = self._other_callee()
        # 6 relay + 4 other = 10 total; 6/10 = 60% (< 65%)
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=6,
            other_callees=[other],
            other_downstream_per_callee=[4],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_silent_total_reach_below_8(self):
        """Total reach is only 7 — not meaningful enough for relay signal."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        other = self._other_callee()
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=6,
            other_callees=[other],
            other_downstream_per_callee=[1],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_silent_relay_owns_fewer_than_5(self):
        """Even with high ratio, relay must own ≥5 callees in absolute count."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        other = self._other_callee()
        # 4 relay + 1 other = 5 total (below 8, also relay < 5 absolute)
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=4,
            other_callees=[other],
            other_downstream_per_callee=[1],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_silent_only_one_cross_file_callee(self):
        """Only 1 cross-file callee — no comparison possible, signal silent."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        graph = _make_graph(seed, relay_sym=relay, relay_downstream_count=10)
        ordered = _ordered_stub(seed, [relay])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_silent_seed_is_not_function(self):
        """Class seeds don't get relay signal."""
        seed = _make_sym("MyClass", kind="class")
        relay = self._relay_sym()
        other = self._other_callee()
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=10,
            other_callees=[other],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_silent_seed_in_test_file(self):
        """Test file seeds don't get relay signal."""
        seed = _make_sym("test_something", file_path="tests/test_core.py")
        relay = self._relay_sym()
        other = self._other_callee()
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=10,
            other_callees=[other],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_silent_empty_seeds(self):
        """Empty seeds list returns empty."""
        from tempograph.render.focused import _compute_relay_point
        graph = types.SimpleNamespace(symbols={}, hot_files=set(), _callees={})
        result = _compute_relay_point([], graph, [])
        assert result == ""

    def test_silent_balanced_callees(self):
        """Balanced downstream (40/60 split) — no dominant relay."""
        seed = _make_sym("seed_fn")
        relay = self._relay_sym()
        other = self._other_callee()
        # 8 relay + 7 other = 15 total; 8/15 = 53% (< 65%)
        graph = _make_graph(
            seed,
            relay_sym=relay,
            relay_downstream_count=8,
            other_callees=[other],
            other_downstream_per_callee=[7],
        )
        ordered = _ordered_stub(seed, [relay, other])
        result = self._call([seed], graph, ordered)
        assert result == ""

    def test_co_seed_excluded_from_relay_candidates(self):
        """When a callee is also in seeds (co-matched), it's excluded from relay consideration."""
        seed = _make_sym("seed_fn")
        co_seed = self._relay_sym("co_seed_fn")  # this would be the relay, but it's also a seed
        other = self._other_callee()
        graph = _make_graph(
            seed,
            relay_sym=co_seed,
            relay_downstream_count=15,
            other_callees=[other],
            other_downstream_per_callee=[1],
        )
        ordered = _ordered_stub(seed, [co_seed, other])
        # Pass both as seeds — co_seed should be excluded from relay candidates
        result = self._call([seed, co_seed], graph, ordered)
        assert result == ""  # co_seed excluded, only other (reach=1) remains -> total<8


# ---------------------------------------------------------------------------
# Integration test: real codebase
# ---------------------------------------------------------------------------

class TestRelayPointIntegration:
    """Integration tests using the actual tempograph codebase."""

    def _focus(self, query):
        from tempograph import build_graph
        from tempograph.render.focused import render_focused
        graph = build_graph(".")
        return render_focused(graph, query)

    def test_get_or_build_graph_fires(self):
        """_get_or_build_graph → build_graph is a classic relay (100% downstream)."""
        result = self._focus("_get_or_build_graph")
        assert "relay point" in result
        assert "build_graph" in result

    def test_prepare_context_fires(self):
        """prepare_context → render_prepare is a relay (100% downstream)."""
        result = self._focus("prepare_context")
        assert "↳ relay point:" in result
        assert "render_prepare" in result

    def test_build_graph_silent(self):
        """build_graph itself is a complex hub — no single relay in its callees."""
        result = self._focus("build_graph")
        assert "↳ relay point:" not in result

    def test_render_focused_silent(self):
        """render_focused has balanced callee reach — no dominant relay.
        Note: 'relay point' may appear in git commit messages shown in BFS output
        (e.g. 'creative: S1036 — relay point...') — assert on the signal prefix instead."""
        result = self._focus("render_focused")
        assert "↳ relay point:" not in result
