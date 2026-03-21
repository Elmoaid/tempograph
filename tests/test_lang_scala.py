"""Tests for Scala language handler (ScalaHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


def _parse(code: str, filename: str = "src/main/Main.scala"):
    p = FileParser(filename, Language.SCALA, code.encode())
    return p.parse()


# ── Classes ──────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("class Dog(name: String)")
        dog = next(s for s in syms if s.name == "Dog")
        assert dog.kind == SymbolKind.CLASS

    def test_case_class_extracted(self):
        syms, _, _ = _parse("case class Point(x: Int, y: Int)")
        assert any(s.name == "Point" for s in syms)

    def test_class_exported(self):
        syms, _, _ = _parse("class Dog")
        dog = next(s for s in syms if s.name == "Dog")
        assert dog.exported is True

    def test_class_methods(self):
        syms, _, _ = _parse("""
class Dog(name: String) {
  def bark(): String = "Woof"
  private def breathe(): Unit = ()
}
""")
        names = {s.name for s in syms}
        assert "bark" in names
        assert "breathe" in names

    def test_private_method_not_exported(self):
        syms, _, _ = _parse("""
class Dog {
  private def breathe(): Unit = ()
}
""")
        fn = next(s for s in syms if s.name == "breathe")
        assert fn.exported is False

    def test_class_contains_method(self):
        _, edges, _ = _parse("""
class Dog {
  def bark(): String = "Woof"
}
""")
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Dog" in e.source_id and "bark" in e.target_id for e in contains)

    def test_class_inheritance(self):
        _, edges, _ = _parse("class Dog extends Animal")
        inherits = [e for e in edges if e.kind == EdgeKind.INHERITS]
        assert any("Dog" in e.source_id and "Animal" in e.target_id for e in inherits)


# ── Traits ───────────────────────────────────────────────────────────────────

class TestTrait:
    def test_trait_extracted(self):
        syms, _, _ = _parse("""
trait Repository[T] {
  def findById(id: String): Option[T]
}
""")
        trait = next(s for s in syms if s.name == "Repository")
        assert trait.kind == SymbolKind.INTERFACE

    def test_trait_abstract_methods(self):
        syms, _, _ = _parse("""
trait Repo[T] {
  def findById(id: String): Option[T]
  def save(entity: T): T
}
""")
        names = {s.name for s in syms}
        assert "findById" in names
        assert "save" in names


# ── Objects ──────────────────────────────────────────────────────────────────

class TestObject:
    def test_object_extracted(self):
        syms, _, _ = _parse("""
object UserService {
  def find(id: String): Option[User] = None
}
""")
        obj = next(s for s in syms if s.name == "UserService")
        assert obj.kind == SymbolKind.CLASS

    def test_object_members(self):
        syms, _, _ = _parse("""
object Utils {
  def format(d: Any): String = d.toString
}
""")
        assert any(s.name == "format" for s in syms)


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("enum Color { case Red, Green, Blue }")
        color = next(s for s in syms if s.name == "Color")
        assert color.kind == SymbolKind.ENUM


# ── Imports ──────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_extracted(self):
        _, _, imports = _parse("import scala.concurrent.Future")
        assert "scala.concurrent.Future" in imports

    def test_multiple_imports(self):
        _, _, imports = _parse("""
import com.example.db.Repo
import scala.concurrent.Future
import scala.util.Try
""")
        assert len(imports) == 3


# ── Integration ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_full_file(self):
        code = """
import com.example.db.Repo
import scala.concurrent.Future

object UserService {
  def find(id: String): Future[Option[User]] = Repo.findById(id)
  private def validate(email: String): Boolean = email.contains("@")
}

trait Repository[T] {
  def findById(id: String): Future[Option[T]]
}

class UserRepositoryImpl extends Repository[User] {
  override def findById(id: String): Future[Option[User]] = ???
}

enum Status { case Active, Inactive }
"""
        syms, edges, imports = _parse(code)
        names = {s.name for s in syms}
        assert "UserService" in names
        assert "find" in names
        assert "validate" in names
        assert "Repository" in names
        assert "UserRepositoryImpl" in names
        assert "Status" in names
        assert len(imports) == 2

        validate_fn = next(s for s in syms if s.name == "validate")
        assert validate_fn.exported is False

        inherits = [e for e in edges if e.kind == EdgeKind.INHERITS]
        assert any("UserRepositoryImpl" in e.source_id for e in inherits)
