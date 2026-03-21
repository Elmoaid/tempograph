"""Tests for Elixir language handler (ElixirHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


def _parse(code: str, filename: str = "lib/users.ex"):
    p = FileParser(filename, Language.ELIXIR, code.encode())
    return p.parse()


# ── Modules ──────────────────────────────────────────────────────────────────

class TestModule:
    def test_defmodule_extracted(self):
        syms, _, _ = _parse("""
defmodule MyApp.Users do
end
""")
        mod = next(s for s in syms if s.name == "MyApp.Users")
        assert mod.kind == SymbolKind.CLASS

    def test_module_exported(self):
        syms, _, _ = _parse("defmodule MyApp.Users do\nend")
        mod = next(s for s in syms if s.name == "MyApp.Users")
        assert mod.exported is True

    def test_nested_module(self):
        syms, edges, _ = _parse("""
defmodule Outer do
  defmodule Inner do
  end
end
""")
        names = {s.name for s in syms}
        assert "Outer" in names
        assert "Inner" in names
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Outer" in e.source_id and "Inner" in e.target_id for e in contains)


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_def_extracted(self):
        syms, _, _ = _parse("""
defmodule M do
  def greet(name), do: "Hello " <> name
end
""")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.kind == SymbolKind.METHOD

    def test_def_exported(self):
        syms, _, _ = _parse("""
defmodule M do
  def greet(name), do: name
end
""")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.exported is True

    def test_defp_not_exported(self):
        syms, _, _ = _parse("""
defmodule M do
  defp validate(email), do: email
end
""")
        fn = next(s for s in syms if s.name == "validate")
        assert fn.exported is False

    def test_defmacro_extracted(self):
        syms, _, _ = _parse("""
defmodule M do
  defmacro with_conn(do: block) do
    quote do: unquote(block)
  end
end
""")
        assert any(s.name == "with_conn" for s in syms)

    def test_function_parent_is_module(self):
        syms, _, _ = _parse("""
defmodule M do
  def find(id), do: id
end
""")
        fn = next(s for s in syms if s.name == "find")
        assert fn.parent_id is not None
        assert "M" in fn.parent_id

    def test_module_contains_function(self):
        _, edges, _ = _parse("""
defmodule M do
  def find(id), do: id
end
""")
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("M" in e.source_id and "find" in e.target_id for e in contains)


# ── Imports ──────────────────────────────────────────────────────────────────

class TestImports:
    def test_alias_extracted(self):
        _, _, imports = _parse("alias MyApp.Repo")
        assert "MyApp.Repo" in imports

    def test_use_extracted(self):
        _, _, imports = _parse("use Phoenix.Controller")
        assert "Phoenix.Controller" in imports

    def test_import_extracted(self):
        _, _, imports = _parse("import Ecto.Query")
        assert "Ecto.Query" in imports

    def test_multiple_imports(self):
        _, _, imports = _parse("""
alias MyApp.Repo
use Phoenix.Controller
import Ecto.Query
""")
        assert len(imports) == 3


# ── Integration ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_full_module(self):
        code = """
alias MyApp.Repo

defmodule MyApp.Users do
  def find(id), do: Repo.get(User, id)
  def all(), do: Repo.all(User)
  defp validate(email), do: String.contains?(email, "@")

  defmodule Helper do
    def format(user), do: user.name
  end
end
"""
        syms, edges, imports = _parse(code)
        names = {s.name for s in syms}
        assert "MyApp.Users" in names
        assert "find" in names
        assert "all" in names
        assert "validate" in names
        assert "Helper" in names
        assert "format" in names
        assert "MyApp.Repo" in imports

        validate_fn = next(s for s in syms if s.name == "validate")
        assert validate_fn.exported is False

        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("MyApp.Users" in e.source_id and "find" in e.target_id for e in contains)
        assert any("MyApp.Users" in e.source_id and "Helper" in e.target_id for e in contains)
