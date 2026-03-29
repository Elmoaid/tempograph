"""Tests for hidden coupling detection signal."""
import pytest


class TestComputeHiddenCoupling:
    def test_returns_empty_when_no_root(self):
        """No git root -> empty string."""
        from tempograph.render.focused import _compute_hidden_coupling
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="a.py::foo", name="foo", qualified_name="foo",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="a.py", line_start=1, line_end=2,
        )

        class MockGraph:
            root = None

        assert _compute_hidden_coupling([seed], MockGraph()) == ""

    def test_returns_empty_for_empty_seeds(self):
        """No seeds -> empty string."""
        from tempograph.render.focused import _compute_hidden_coupling

        class MockGraph:
            root = "/tmp"

        assert _compute_hidden_coupling([], MockGraph()) == ""
