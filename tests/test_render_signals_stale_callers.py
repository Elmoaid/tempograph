"""Tests for stale caller detection signal."""
import pytest
from tempograph.types import Symbol, SymbolKind, Language


def _sym(id, name, file_path):
    return Symbol(
        id=id, name=name, qualified_name=name,
        kind=SymbolKind.FUNCTION, language=Language.PYTHON,
        file_path=file_path, line_start=1, line_end=5,
    )


class _Graph:
    def __init__(self, root="/tmp", caller_map=None):
        self.root = root
        self._caller_map = caller_map or {}

    def callers_of(self, sid):
        return self._caller_map.get(sid, [])


class TestComputeStaleCallers:
    def test_returns_empty_when_no_root(self):
        from tempograph.render.focused import _compute_stale_callers
        s = _sym("a.py::f", "f", "a.py")
        g = _Graph(root=None)
        assert _compute_stale_callers([s], g) == ""

    def test_returns_empty_when_empty_seeds(self):
        from tempograph.render.focused import _compute_stale_callers
        assert _compute_stale_callers([], _Graph()) == ""

    def test_fires_when_caller_is_stale(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("target.py::process", "process", "target.py")
        caller = _sym("old.py::old_fn", "old_fn", "old.py")
        g = _Graph(caller_map={"target.py::process": [caller]})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"target.py": 5, "old.py": 180},
        )
        assert "stale" in result.lower()
        assert "old_fn" in result
        assert "180d" in result

    def test_no_fire_when_seed_is_old(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("old.py::f", "f", "old.py")
        g = _Graph(caller_map={"old.py::f": []})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"old.py": 60},
        )
        assert result == ""

    def test_no_fire_when_caller_is_same_file(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("lib.py::fn", "fn", "lib.py")
        caller = _sym("lib.py::other", "other", "lib.py")
        g = _Graph(caller_map={"lib.py::fn": [caller]})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"lib.py": 2},
        )
        assert result == ""

    def test_no_fire_when_caller_is_recent(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("a.py::fn", "fn", "a.py")
        caller = _sym("b.py::bar", "bar", "b.py")
        g = _Graph(caller_map={"a.py::fn": [caller]})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"a.py": 5, "b.py": 30},
        )
        assert result == ""

    def test_sorts_by_stalest_first(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("a.py::fn", "fn", "a.py")
        c1 = _sym("b.py::b_fn", "b_fn", "b.py")
        c2 = _sym("c.py::c_fn", "c_fn", "c.py")
        g = _Graph(caller_map={"a.py::fn": [c1, c2]})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"a.py": 1, "b.py": 100, "c.py": 200},
        )
        # c_fn (200d) should appear before b_fn (100d)
        assert result.index("c_fn") < result.index("b_fn")

    def test_overflow_indicator(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("a.py::fn", "fn", "a.py")
        callers = [_sym(f"f{i}.py::fn{i}", f"fn{i}", f"f{i}.py") for i in range(5)]
        g = _Graph(caller_map={"a.py::fn": callers})
        ages = {"a.py": 1}
        for i in range(5):
            ages[f"f{i}.py"] = 100 + i * 10
        result = _compute_stale_callers([seed], g, _file_ages=ages)
        assert "+2 more" in result

    def test_seed_at_boundary_30_days_excluded(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("a.py::fn", "fn", "a.py")
        caller = _sym("b.py::bar", "bar", "b.py")
        g = _Graph(caller_map={"a.py::fn": [caller]})
        # seed_age=30 is NOT < 30, so > 30 check excludes it
        result = _compute_stale_callers(
            [seed], g, _file_ages={"a.py": 30, "b.py": 200},
        )
        assert result == ""

    def test_caller_at_boundary_89_days_not_stale(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("a.py::fn", "fn", "a.py")
        caller = _sym("b.py::bar", "bar", "b.py")
        g = _Graph(caller_map={"a.py::fn": [caller]})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"a.py": 5, "b.py": 89},
        )
        assert result == ""

    def test_caller_at_boundary_90_days_is_stale(self):
        from tempograph.render.focused import _compute_stale_callers
        seed = _sym("a.py::fn", "fn", "a.py")
        caller = _sym("b.py::bar", "bar", "b.py")
        g = _Graph(caller_map={"a.py::fn": [caller]})
        result = _compute_stale_callers(
            [seed], g, _file_ages={"a.py": 5, "b.py": 90},
        )
        assert "stale" in result.lower()
        assert "bar" in result
