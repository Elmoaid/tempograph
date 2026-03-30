"""Tests for S1042: Indirect reachability signal in render_focused.

S1042 fires when a focus seed has 0 non-test direct callers but its PARENT symbol
(class or React hook) has ≥1 non-test external callers — indicating the seed is
likely reachable via instance dispatch or hook destructuring rather than direct call.

Two patterns addressed:
1. Python class methods: `obj.method()` calls aren't captured as CALLS edges to
   ClassName.method because tree-sitter can't resolve variable-typed receivers.
2. TypeScript hook-returned functions: `const {fn} = useHook()` — the returned
   function is never a direct CALLS target, just destructured from the hook's return.

Output forms:
- SINGLETON: seed + <5 sibling methods also match →
    "↳ instance method: ClassName has N callers — method may be called via `instance.method()`..."
- FAMILY: seed + ≥5 siblings with 0 non-test callers →
    "↳ instance method family: ClassName has N callers — method and M siblings have 0 direct callers..."
- HOOK SINGLETON: seed in hook, <5 siblings →
    "↳ hook-returned: fn is inside useHook (N caller) — invoked via destructuring..."
- HOOK FAMILY: seed in hook, ≥5 siblings →
    "↳ hook-returned family: useHook has N caller — fn and M siblings returned via destructuring..."

Distinct from:
- S67 (dead candidate): uses confidence scoring; S1042 is purely structural (parent liveness)
- S1039 (hub callee warning): S1039 is about CALLEES of the seed; S1042 is about the seed's PARENT
- S1037 (subclass exposure): S1037 is about inheritance; S1042 is about instance dispatch gaps
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
    complexity: int = 10,
    exported: bool = True,
    parent_id: str | None = None,
    language: str = "python",
):
    from tempograph.types import Symbol, SymbolKind, Language

    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
        "hook": SymbolKind.HOOK,
        "property": SymbolKind.PROPERTY,
    }
    lmap = {
        "python": Language.PYTHON,
        "typescript": Language.TYPESCRIPT,
        "tsx": Language.TSX,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=lmap.get(language, Language.PYTHON),
        file_path=file_path,
        line_start=1,
        line_end=20,
        exported=exported,
        complexity=complexity,
        parent_id=parent_id,
    )


def _make_graph(
    seed,
    parent=None,
    parent_ext_callers=None,
    seed_callers=None,
    siblings=None,
):
    """Build a minimal mock graph for S1042 unit tests.

    Args:
        seed: the symbol being focused on
        parent: parent symbol (class or hook) for the seed
        parent_ext_callers: list of symbols that call the PARENT from outside its file
        seed_callers: list of symbols that call the SEED directly
        siblings: list of extra sibling symbols under the same parent (all with 0 callers)
    """
    g = types.SimpleNamespace()
    g.symbols = {seed.id: seed}
    g._callers = {}
    g.hot_files = set()
    g.root = None

    if parent:
        g.symbols[parent.id] = parent
        seed.parent_id = parent.id  # wire parent relationship

    if parent_ext_callers:
        for c in parent_ext_callers:
            g.symbols[c.id] = c
            g._callers.setdefault(parent.id, []).append(c.id)

    if seed_callers:
        for c in seed_callers:
            g.symbols[c.id] = c
            g._callers.setdefault(seed.id, []).append(c.id)

    if siblings:
        for s in siblings:
            g.symbols[s.id] = s
            # siblings have 0 callers by default (not added to _callers)

    def _callers_of(sid):
        ids = g._callers.get(sid, [])
        return [g.symbols[i] for i in ids if i in g.symbols]

    g.callers_of = _callers_of
    g.callees_of = lambda sid: []
    g.children_of = lambda sid: []
    g.renderers_of = lambda sid: []
    g.subtypes_of = lambda name: []
    g.importers_of = lambda fp: ["src/other.py"]  # has importers (not entry-point)
    g.find_symbol = lambda name: [s for s in g.symbols.values() if s.name == name]

    return g


# ---------------------------------------------------------------------------
# Unit tests: _compute_indirect_reachability
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_indirect_reachability


class TestIndirectReachabilityUnit:

    # ---- FIRES: Python class instance method (singleton) ----

    def test_fires_singleton_class_method(self):
        """Python method in a class with 1 external caller — singleton form."""
        parent = _make_sym("MyService", kind="class", file_path="src/service.py", complexity=0, parent_id=None)
        seed = _make_sym("process", kind="method", file_path="src/service.py", complexity=8,
                         parent_id=parent.id)
        ext_caller = _make_sym("main", file_path="src/main.py", complexity=3)
        g = _make_graph(seed, parent=parent, parent_ext_callers=[ext_caller])
        result = _compute_indirect_reachability(g, [seed])
        assert "↳ instance method:" in result
        assert "MyService" in result
        assert "process" in result
        assert "instance" in result

    def test_fires_singleton_shows_parent_caller_count(self):
        """Singleton form shows the exact parent caller count."""
        parent = _make_sym("Repo", kind="class", file_path="src/repo.py", complexity=0)
        seed = _make_sym("find_all", kind="method", file_path="src/repo.py", complexity=7,
                         parent_id=parent.id)
        callers = [
            _make_sym("svc_a", file_path="src/svc_a.py"),
            _make_sym("svc_b", file_path="src/svc_b.py"),
            _make_sym("svc_c", file_path="src/svc_c.py"),
        ]
        g = _make_graph(seed, parent=parent, parent_ext_callers=callers)
        result = _compute_indirect_reachability(g, [seed])
        assert "3" in result
        assert "callers" in result

    def test_fires_family_class_method(self):
        """Class with ≥5 methods having 0 callers — family form fires."""
        parent = _make_sym("BigService", kind="class", file_path="src/svc.py", complexity=0)
        seed = _make_sym("main_op", kind="method", file_path="src/svc.py", complexity=10,
                         parent_id=parent.id)
        # 5 siblings under same parent with 0 callers
        siblings = [
            _make_sym(f"op_{i}", kind="method", file_path="src/svc.py", complexity=5,
                      parent_id=parent.id)
            for i in range(5)
        ]
        ext_caller = _make_sym("controller", file_path="src/ctrl.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[ext_caller], siblings=siblings)
        result = _compute_indirect_reachability(g, [seed])
        assert "↳ instance method family:" in result
        assert "BigService" in result
        assert "5 sibling methods" in result

    def test_fires_family_threshold_exactly_5_siblings(self):
        """Exactly 5 siblings (total=6 ≥ threshold 5) — family form."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("do_thing", kind="method", file_path="src/cls.py", complexity=8,
                         parent_id=parent.id)
        siblings = [
            _make_sym(f"sibling_{i}", kind="method", file_path="src/cls.py",
                      complexity=5, parent_id=parent.id)
            for i in range(5)
        ]
        ext_caller = _make_sym("client", file_path="src/client.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[ext_caller], siblings=siblings)
        result = _compute_indirect_reachability(g, [seed])
        assert "family" in result

    def test_fires_singleton_below_family_threshold(self):
        """Only 3 siblings (total=4 < threshold 5) — singleton form."""
        parent = _make_sym("SmallCls", kind="class", file_path="src/small.py", complexity=0)
        seed = _make_sym("work", kind="method", file_path="src/small.py", complexity=8,
                         parent_id=parent.id)
        siblings = [
            _make_sym(f"s_{i}", kind="method", file_path="src/small.py",
                      complexity=5, parent_id=parent.id)
            for i in range(3)
        ]
        ext_caller = _make_sym("user", file_path="src/user.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[ext_caller], siblings=siblings)
        result = _compute_indirect_reachability(g, [seed])
        assert "family" not in result
        assert "↳ instance method:" in result

    # ---- FIRES: TypeScript hook-returned function ----

    def test_fires_hook_returned_singleton(self):
        """Function inside a hook with 1 external caller — hook singleton form."""
        parent = _make_sym("useCounter", kind="hook",
                           file_path="src/useCounter.ts", complexity=5,
                           language="typescript")
        seed = _make_sym("increment", kind="function",
                         file_path="src/useCounter.ts", complexity=0,
                         parent_id=parent.id, language="typescript")
        caller = _make_sym("CounterWidget", file_path="src/CounterWidget.tsx",
                           language="tsx")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert "↳ hook-returned:" in result
        assert "useCounter" in result
        assert "increment" in result
        assert "destructuring" in result

    def test_fires_hook_returned_family(self):
        """Hook with ≥5 returned functions having 0 callers — hook family form."""
        parent = _make_sym("useEditor", kind="hook",
                           file_path="src/useEditor.ts", complexity=15,
                           language="typescript")
        seed = _make_sym("undo", kind="function",
                         file_path="src/useEditor.ts", complexity=0,
                         parent_id=parent.id, language="typescript")
        siblings = [
            _make_sym(f"action_{i}", kind="function",
                      file_path="src/useEditor.ts", complexity=0,
                      parent_id=parent.id, language="typescript")
            for i in range(5)
        ]
        caller = _make_sym("EditorPanel", file_path="src/EditorPanel.tsx", language="tsx")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller], siblings=siblings)
        result = _compute_indirect_reachability(g, [seed])
        assert "↳ hook-returned family:" in result
        assert "useEditor" in result
        assert "siblings returned via destructuring" in result

    def test_fires_hook_cx_zero_no_floor(self):
        """Hook-returned function with cx=0 still fires (no complexity floor for hooks)."""
        parent = _make_sym("useToggle", kind="hook",
                           file_path="src/useToggle.ts", complexity=3,
                           language="typescript")
        seed = _make_sym("toggle", kind="function",
                         file_path="src/useToggle.ts", complexity=0,
                         parent_id=parent.id, language="typescript")
        caller = _make_sym("ToggleBtn", file_path="src/ToggleBtn.tsx", language="tsx")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert "hook-returned" in result

    # ---- SILENT: seed has non-test direct callers ----

    def test_silent_seed_has_direct_callers(self):
        """Seed has non-test direct callers — signal should not fire."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("fn", kind="method", file_path="src/cls.py", complexity=8,
                         parent_id=parent.id)
        direct_caller = _make_sym("user", file_path="src/user.py")
        ext_caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[ext_caller],
                        seed_callers=[direct_caller])
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    def test_silent_seed_has_test_callers_only(self):
        """Seed called only from test files — treated same as 0 non-test callers → fires.

        The test-only caller situation is exactly the false-positive case S1042 addresses:
        the method has non-zero callers but only from tests. Should still fire.
        """
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("fn", kind="method", file_path="src/cls.py", complexity=8,
                         parent_id=parent.id)
        test_caller = _make_sym("test_fn", file_path="tests/test_cls.py")
        ext_caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[ext_caller],
                        seed_callers=[test_caller])
        result = _compute_indirect_reachability(g, [seed])
        # test-only callers don't suppress the signal (seed is still 0 non-test callers)
        assert "instance method" in result

    # ---- SILENT: parent has no external callers ----

    def test_silent_parent_no_external_callers(self):
        """Parent class has no callers from outside its own file — parent also dead."""
        parent = _make_sym("DeadCls", kind="class", file_path="src/dead.py", complexity=0)
        seed = _make_sym("do_work", kind="method", file_path="src/dead.py", complexity=8,
                         parent_id=parent.id)
        # No parent_ext_callers
        g = _make_graph(seed, parent=parent)
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    def test_silent_parent_only_same_file_callers(self):
        """Parent callers are all in the same file — external caller check fails."""
        parent = _make_sym("InternalCls", kind="class", file_path="src/module.py", complexity=0)
        seed = _make_sym("method", kind="method", file_path="src/module.py", complexity=8,
                         parent_id=parent.id)
        same_file_caller = _make_sym("factory", file_path="src/module.py")  # SAME FILE
        g = _make_graph(seed, parent=parent, parent_ext_callers=[same_file_caller])
        # same-file caller is filtered out by external check
        # Manually build graph with same-file parent caller
        g._callers[parent.id] = [same_file_caller.id]
        g.symbols[same_file_caller.id] = same_file_caller
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    def test_silent_parent_only_test_callers(self):
        """Parent's only callers are test files — doesn't count as external."""
        parent = _make_sym("TestOnlyCls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("method", kind="method", file_path="src/cls.py", complexity=8,
                         parent_id=parent.id)
        test_caller = _make_sym("test_cls", file_path="tests/test_cls.py")
        g = _make_graph(seed, parent=parent)
        g._callers[parent.id] = [test_caller.id]
        g.symbols[test_caller.id] = test_caller
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    # ---- SILENT: complexity floor for class methods ----

    def test_silent_class_method_low_cx(self):
        """Class method with cx=4 — below floor (5) for class methods."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("getter", kind="method", file_path="src/cls.py", complexity=4,
                         parent_id=parent.id)
        caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    def test_fires_class_method_cx_exactly_5(self):
        """Class method with cx=5 — at floor exactly, should fire."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("do_work", kind="method", file_path="src/cls.py", complexity=5,
                         parent_id=parent.id)
        caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert "instance method" in result

    # ---- SILENT: wrong parent kind ----

    def test_silent_parent_is_function_not_class_or_hook(self):
        """Parent is a plain function (not class or hook) — does not fire."""
        parent = _make_sym("make_fn", kind="function", file_path="src/factory.py", complexity=5)
        seed = _make_sym("inner", kind="function", file_path="src/factory.py", complexity=8,
                         parent_id=parent.id)
        caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    def test_silent_no_parent_id(self):
        """Seed has no parent_id (module-level function) — does not fire."""
        seed = _make_sym("top_level_fn", kind="function", file_path="src/utils.py", complexity=8)
        ext_caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed)
        # No parent set
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    # ---- SILENT: test-file seed ----

    def test_silent_seed_in_test_file(self):
        """Seed is in a test file — does not fire."""
        parent = _make_sym("TestCls", kind="class", file_path="tests/test_service.py", complexity=0)
        seed = _make_sym("test_method", kind="method", file_path="tests/test_service.py",
                         complexity=8, parent_id=parent.id)
        caller = _make_sym("conftest", file_path="tests/conftest.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    # ---- SILENT: wrong seed kind ----

    def test_silent_class_seed_kind(self):
        """Seed kind=class — does not fire (signal only for methods/functions)."""
        parent = _make_sym("Module", kind="class", file_path="src/mod.py", complexity=0)
        seed = _make_sym("SubCls", kind="class", file_path="src/mod.py", complexity=8,
                         parent_id=parent.id)
        caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert result == ""

    # ---- Edge cases ----

    def test_empty_seeds_returns_empty(self):
        """Empty seed list — does not fire."""
        g = _make_graph(_make_sym("dummy"))
        result = _compute_indirect_reachability(g, [])
        assert result == ""

    def test_first_qualifying_seed_used_in_multi_seed(self):
        """Multi-seed focus: signal fires for first qualifying seed, stops there."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed1 = _make_sym("method_a", kind="method", file_path="src/cls.py", complexity=8,
                          parent_id=parent.id)
        seed2 = _make_sym("method_b", kind="method", file_path="src/cls.py", complexity=8,
                          parent_id=parent.id)
        caller = _make_sym("ext", file_path="src/other.py")
        g = _make_graph(seed1, parent=parent, parent_ext_callers=[caller])
        g.symbols[seed2.id] = seed2
        result = _compute_indirect_reachability(g, [seed1, seed2])
        # Should fire for seed1 (first qualifying) — output mentions method_a
        assert "method_a" in result
        # Should NOT fire twice
        assert result.count("↳") == 1

    def test_singular_caller_word(self):
        """Exactly 1 external parent caller → uses 'caller' (singular)."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("run", kind="method", file_path="src/cls.py", complexity=8,
                         parent_id=parent.id)
        caller = _make_sym("only_one", file_path="src/main.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert "1 external caller" in result
        assert "callers" not in result.split("external")[1][:10]  # not plural after "external"

    def test_plural_caller_word(self):
        """More than 1 parent caller → uses 'callers' (plural)."""
        parent = _make_sym("Cls", kind="class", file_path="src/cls.py", complexity=0)
        seed = _make_sym("run", kind="method", file_path="src/cls.py", complexity=8,
                         parent_id=parent.id)
        callers = [_make_sym(f"c{i}", file_path=f"src/m{i}.py") for i in range(3)]
        g = _make_graph(seed, parent=parent, parent_ext_callers=callers)
        result = _compute_indirect_reachability(g, [seed])
        assert "3 external callers" in result

    def test_arrow_prefix(self):
        """Signal output starts with ↳ prefix."""
        parent = _make_sym("Service", kind="class", file_path="src/svc.py", complexity=0)
        seed = _make_sym("execute", kind="method", file_path="src/svc.py", complexity=8,
                         parent_id=parent.id)
        caller = _make_sym("cli", file_path="src/cli.py")
        g = _make_graph(seed, parent=parent, parent_ext_callers=[caller])
        result = _compute_indirect_reachability(g, [seed])
        assert result.startswith("↳")


# ---------------------------------------------------------------------------
# Integration tests: real tempograph codebase
# ---------------------------------------------------------------------------

class TestIndirectReachabilityIntegration:
    """Integration tests on the real tempograph/types.py and storage.py codebase."""

    @staticmethod
    def _focus(query: str) -> str:
        from tempograph import build_graph
        from tempograph.render.focused import render_focused
        g = build_graph(".")
        return render_focused(g, query)

    def test_fires_for_build_indexes(self):
        """build_indexes (Tempo class, 0 direct callers) — confirmed FP from cycle-254."""
        result = self._focus("build_indexes")
        # Signal fires: Tempo has external callers
        assert "instance method" in result
        assert "Tempo" in result

    def test_fires_for_watch_loop(self):
        """_watch_loop (GraphWatcher class, 0 direct callers) — confirmed FP from cycle-254."""
        result = self._focus("_watch_loop")
        assert "instance method" in result
        assert "GraphWatcher" in result

    def test_fires_for_switchMode(self):
        """switchMode (useModeRunner hook) — confirmed FP: returned via destructuring."""
        result = self._focus("switchMode")
        # hook-returned signal fires for the useModeRunner version
        assert "hook-returned" in result
        assert "useModeRunner" in result

    def test_silent_for_build_graph(self):
        """build_graph has many direct callers — S1042 must NOT fire."""
        result = self._focus("build_graph")
        assert "instance method" not in result
        assert "hook-returned" not in result

    def test_silent_for_render_focused(self):
        """render_focused is a top-level function, no parent — S1042 must NOT fire."""
        result = self._focus("render_focused")
        assert "instance method" not in result
        assert "hook-returned" not in result
