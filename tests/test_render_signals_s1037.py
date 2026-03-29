"""Tests for S1037: Subclass exposure signal in render_focused.

S1037 fires when the focus seed is a CLASS with ≥ 1 cross-file subclass.
BFS follows CALLS/CONTAINS edges but NOT INHERITS/IMPLEMENTS edges.
Subclasses are invisible when focusing on a base class — S1037 makes them visible.

Distinct from:
- S65 (change_exposure): fires on caller-file blast risk — not inheritance
- S1034 (cross-file siblings): naming-family in same dir — not inheritance
- S1035 (orchestrator): callee/caller topology — not inheritance
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
    kind: str = "class",
    exported: bool = True,
):
    from tempograph.types import Symbol, SymbolKind, Language
    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
        "variable": SymbolKind.VARIABLE,
        "interface": SymbolKind.INTERFACE,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.CLASS),
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=30,
        exported=exported,
    )


def _make_graph(base_sym, *, subclasses=None, same_file_subs=None):
    """Build a minimal fake Tempo for subclass exposure unit tests.

    base_sym: the class being focused on (the base)
    subclasses: list of (name, file_path) tuples for cross-file subclasses
    same_file_subs: list of (name,) tuples for same-file subclasses (should be silent)
    """
    subclasses = subclasses or []
    same_file_subs = same_file_subs or []

    from tempograph.types import EdgeKind, Tempo

    g = types.SimpleNamespace()
    g.symbols = {base_sym.id: base_sym}
    g.edges = []
    g.files = {}
    g.hot_files = set()
    g._callers = {}
    g._callees = {}
    g._children = {}
    g._importers = {base_sym.file_path: ["src/app.py"]}  # base file IS imported
    g._renderers = {}
    g._subtypes = {}

    # Add cross-file subclasses
    sub_syms = []
    for sub_name, sub_file in subclasses:
        sub = _make_sym(sub_name, file_path=sub_file, kind="class")
        g.symbols[sub.id] = sub
        # INHERITS edge: sub → base
        g.edges.append(types.SimpleNamespace(
            kind=EdgeKind.INHERITS,
            source_id=sub.id,
            target_id=base_sym.id,
            line=1,
        ))
        # Also register bare-name target (unresolved)
        g.edges.append(types.SimpleNamespace(
            kind=EdgeKind.INHERITS,
            source_id=sub.id,
            target_id=base_sym.name,
            line=1,
        ))
        g._subtypes.setdefault(base_sym.id, []).append(sub.id)
        g._subtypes.setdefault(base_sym.name, []).append(sub.id)
        sub_syms.append(sub)

    # Add same-file subclasses
    for (sub_name,) in same_file_subs:
        sub = _make_sym(sub_name, file_path=base_sym.file_path, kind="class")
        g.symbols[sub.id] = sub
        g.edges.append(types.SimpleNamespace(
            kind=EdgeKind.INHERITS,
            source_id=sub.id,
            target_id=base_sym.id,
            line=1,
        ))
        g._subtypes.setdefault(base_sym.id, []).append(sub.id)

    def _callers_of(sym_id):
        return [g.symbols[s] for s in g._callers.get(sym_id, []) if s in g.symbols]

    def _callees_of(sym_id):
        return [g.symbols[s] for s in g._callees.get(sym_id, []) if s in g.symbols]

    def _children_of(sym_id):
        return [g.symbols[s] for s in g._children.get(sym_id, []) if s in g.symbols]

    def _importers_of(fp):
        return g._importers.get(fp, [])

    def _subtypes_of(name):
        """Simplified subtypes_of for tests — checks name and ID."""
        seen = set()
        out = []
        for key in [name]:
            for sid in g._subtypes.get(key, []):
                if sid not in seen and sid in g.symbols:
                    seen.add(sid)
                    out.append(g.symbols[sid])
        # Also check if name matches a symbol ID
        for sym_id_key in [sid for sid in g._subtypes if "::" in sid and sid.endswith(f"::{name}")]:
            for sid in g._subtypes.get(sym_id_key, []):
                if sid not in seen and sid in g.symbols:
                    seen.add(sid)
                    out.append(g.symbols[sid])
        return out

    def _find_symbol(name):
        return [s for s in g.symbols.values() if s.name == name]

    g.callers_of = _callers_of
    g.callees_of = _callees_of
    g.children_of = _children_of
    g.importers_of = _importers_of
    g.subtypes_of = _subtypes_of
    g.find_symbol = _find_symbol

    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_subclass_exposure
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_subclass_exposure


class TestSubclassExposureUnit:
    def test_fires_single_cross_file_subclass(self):
        base = _make_sym("BaseLoader", file_path="src/loader.py", kind="class")
        g = _make_graph(base, subclasses=[("LocalLoader", "src/local.py")])
        result = _compute_subclass_exposure([base], g)
        assert "subclass:" in result
        assert "LocalLoader" in result
        assert "local.py" in result
        assert "propagate" in result

    def test_fires_two_cross_file_subclasses(self):
        base = _make_sym("BaseLoader", file_path="src/loader.py", kind="class")
        g = _make_graph(base, subclasses=[
            ("LocalLoader", "src/local.py"),
            ("RemoteLoader", "src/remote.py"),
        ])
        result = _compute_subclass_exposure([base], g)
        assert "2 subclasses" in result
        assert "LocalLoader" in result
        assert "RemoteLoader" in result
        assert "propagate to all" in result

    def test_fires_many_cross_file_subclasses_with_overflow(self):
        base = _make_sym("BasePlugin", file_path="src/plugin.py", kind="class")
        subs = [(f"Plugin{i}", f"src/plugin{i}.py") for i in range(5)]
        g = _make_graph(base, subclasses=subs)
        result = _compute_subclass_exposure([base], g)
        assert "5 subclasses" in result
        assert "+3 more" in result  # shows first 2, then "+3 more"
        assert "propagate to all" in result

    def test_single_subclass_no_n_prefix(self):
        """Single subclass omits count prefix — uses 'subclass:' not 'N subclasses:'."""
        base = _make_sym("AuthMixin", file_path="src/mixins.py", kind="class")
        g = _make_graph(base, subclasses=[("UserModel", "src/models.py")])
        result = _compute_subclass_exposure([base], g)
        assert result.startswith("↳ subclass:")
        assert "1 subclass" not in result  # no count prefix for singular

    def test_silent_for_function_seed(self):
        fn = _make_sym("process_data", file_path="src/core.py", kind="function")
        g = _make_graph(fn, subclasses=[("SubProcessor", "src/sub.py")])
        result = _compute_subclass_exposure([fn], g)
        assert result == ""

    def test_silent_for_method_seed(self):
        m = _make_sym("handle", file_path="src/handler.py", kind="method")
        g = _make_graph(m, subclasses=[("ChildHandler", "src/child.py")])
        result = _compute_subclass_exposure([m], g)
        assert result == ""

    def test_silent_no_subclasses(self):
        base = _make_sym("StandaloneClass", file_path="src/standalone.py", kind="class")
        g = _make_graph(base, subclasses=[])
        result = _compute_subclass_exposure([base], g)
        assert result == ""

    def test_silent_same_file_subclasses_only(self):
        """Same-file subclasses are visible in context — signal is silent."""
        base = _make_sym("BaseStrategy", file_path="src/strategy.py", kind="class")
        g = _make_graph(base, same_file_subs=[("StrategyA",), ("StrategyB",)])
        result = _compute_subclass_exposure([base], g)
        assert result == ""

    def test_silent_test_file_seed(self):
        base = _make_sym("MockBase", file_path="tests/test_helpers.py", kind="class")
        g = _make_graph(base, subclasses=[("MockChild", "tests/test_child.py")])
        result = _compute_subclass_exposure([base], g)
        assert result == ""

    def test_silent_empty_seeds(self):
        g = _make_graph(_make_sym("Anything"))
        result = _compute_subclass_exposure([], g)
        assert result == ""

    def test_mixed_same_and_cross_file_fires_cross_only(self):
        """Same-file subclass is excluded, cross-file subclass fires signal."""
        base = _make_sym("BaseConfig", file_path="src/config.py", kind="class")
        g = _make_graph(
            base,
            subclasses=[("RemoteConfig", "src/remote_config.py")],
            same_file_subs=[("LocalConfig",)],
        )
        result = _compute_subclass_exposure([base], g)
        assert "subclass:" in result
        assert "RemoteConfig" in result
        assert "LocalConfig" not in result

    def test_interface_kind_fires(self):
        """Interface kind (not just class) should also trigger the signal."""
        iface = _make_sym("Serializable", file_path="src/interfaces.py", kind="interface")
        g = _make_graph(iface, subclasses=[("JsonSerializer", "src/json_serial.py")])
        result = _compute_subclass_exposure([iface], g)
        assert "subclass:" in result
        assert "JsonSerializer" in result

    def test_test_file_subclasses_excluded(self):
        """Subclasses in test files are not counted (test doubles, not real subclasses)."""
        base = _make_sym("BaseParser", file_path="src/parser.py", kind="class")
        # One real subclass, one test mock
        real_sub = ("AdvancedParser", "src/advanced.py")
        test_sub = ("FakeParser", "tests/test_parser.py")

        from tempograph.types import EdgeKind
        g = _make_graph(base)

        # Manually add real subclass
        real = _make_sym("AdvancedParser", file_path="src/advanced.py", kind="class")
        fake = _make_sym("FakeParser", file_path="tests/test_parser.py", kind="class")
        g.symbols[real.id] = real
        g.symbols[fake.id] = fake
        g._subtypes[base.name] = [real.id, fake.id]

        result = _compute_subclass_exposure([base], g)
        assert "AdvancedParser" in result
        assert "FakeParser" not in result


# ---------------------------------------------------------------------------
# Integration tests against the real tempograph codebase
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_graph():
    from tempograph import build_graph
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return build_graph(repo)


class TestSubclassExposureIntegration:
    def test_fires_for_python_handler_mixin(self, real_graph):
        """PythonHandlerMixin is inherited by FileParser (cross-file) — signal fires."""
        syms = [s for s in real_graph.symbols.values() if s.name == "PythonHandlerMixin"]
        if not syms:
            pytest.skip("PythonHandlerMixin not found in graph")
        result = _compute_subclass_exposure([syms[0]], real_graph)
        assert result != "", "Expected signal to fire for PythonHandlerMixin"
        assert "FileParser" in result

    def test_fires_for_js_handler_mixin(self, real_graph):
        """JSHandlerMixin is inherited by FileParser — same cross-file pattern."""
        syms = [s for s in real_graph.symbols.values() if s.name == "JSHandlerMixin"]
        if not syms:
            pytest.skip("JSHandlerMixin not found in graph")
        result = _compute_subclass_exposure([syms[0]], real_graph)
        assert result != "", "Expected signal to fire for JSHandlerMixin"
        assert "FileParser" in result

    def test_silent_for_file_parser(self, real_graph):
        """FileParser is the subclass (not a base) — signal is silent."""
        syms = [s for s in real_graph.symbols.values() if s.name == "FileParser"]
        if not syms:
            pytest.skip("FileParser not found in graph")
        result = _compute_subclass_exposure([syms[0]], real_graph)
        assert result == "", f"Expected SILENT for FileParser but got: {result!r}"

    def test_silent_for_tempo_class(self, real_graph):
        """Tempo dataclass has no subclasses — signal is silent."""
        syms = [s for s in real_graph.symbols.values() if s.name == "Tempo" and s.kind.value == "class"]
        if not syms:
            pytest.skip("Tempo class not found in graph")
        result = _compute_subclass_exposure([syms[0]], real_graph)
        assert result == "", f"Expected SILENT for Tempo but got: {result!r}"
