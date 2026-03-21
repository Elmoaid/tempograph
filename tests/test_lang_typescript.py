"""Tests for TypeScript-specific features: interface members, namespaces, type aliases, enums."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


def _parse(code: str, filename: str = "api.ts"):
    p = FileParser(filename, Language.TYPESCRIPT, code.encode())
    return p.parse()


# ── Interface members ────────────────────────────────────────────────────────

class TestInterfaceMembers:
    def test_method_signature_extracted(self):
        syms, _, _ = _parse("""
interface UserRepo {
  findById(id: string): User;
}
""")
        names = {s.name for s in syms}
        assert "UserRepo" in names
        assert "findById" in names

    def test_property_signature_extracted(self):
        syms, _, _ = _parse("""
interface Config {
  host: string;
  port: number;
}
""")
        names = {s.name for s in syms}
        assert "host" in names
        assert "port" in names

    def test_method_has_correct_kind(self):
        syms, _, _ = _parse("""
interface Service {
  process(data: unknown): void;
}
""")
        method = next(s for s in syms if s.name == "process")
        assert method.kind == SymbolKind.METHOD

    def test_property_has_correct_kind(self):
        syms, _, _ = _parse("""
interface Config {
  debug: boolean;
}
""")
        prop = next(s for s in syms if s.name == "debug")
        assert prop.kind == SymbolKind.PROPERTY

    def test_interface_contains_members(self):
        _, edges, _ = _parse("""
interface Repo {
  save(item: Item): void;
  name: string;
}
""")
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Repo" in e.source_id and "save" in e.target_id for e in contains)
        assert any("Repo" in e.source_id and "name" in e.target_id for e in contains)

    def test_member_parent_id_set(self):
        syms, _, _ = _parse("""
interface Repo {
  getAll(): Item[];
}
""")
        member = next(s for s in syms if s.name == "getAll")
        assert member.parent_id is not None
        assert "Repo" in member.parent_id

    def test_exported_interface_members_are_exported(self):
        syms, _, _ = _parse("""
export interface PublicService {
  doWork(): void;
}
""")
        member = next(s for s in syms if s.name == "doWork")
        assert member.exported is True


# ── TypeScript namespaces ────────────────────────────────────────────────────

class TestNamespace:
    def test_namespace_extracted(self):
        syms, _, _ = _parse("""
export namespace Auth {
  export function validate(token: string): boolean { return true; }
}
""")
        ns = next(s for s in syms if s.name == "Auth")
        assert ns.kind == SymbolKind.MODULE

    def test_namespace_members_extracted(self):
        syms, _, _ = _parse("""
namespace Utils {
  export function formatDate(d: Date): string { return d.toISOString(); }
  export const VERSION = '1.0';
}
""")
        names = {s.name for s in syms}
        assert "Utils" in names
        assert "formatDate" in names
        assert "VERSION" in names

    def test_namespace_member_parent(self):
        syms, _, _ = _parse("""
namespace Http {
  export function get(url: string): Promise<Response> { return fetch(url); }
}
""")
        fn = next(s for s in syms if s.name == "get")
        assert fn.parent_id is not None
        assert "Http" in fn.parent_id

    def test_exported_namespace(self):
        syms, _, _ = _parse("""
export namespace Config {
  export const DEBUG = false;
}
""")
        ns = next(s for s in syms if s.name == "Config")
        assert ns.exported is True

    def test_non_exported_namespace(self):
        syms, _, _ = _parse("""
namespace Internal {
  function helper(): void {}
}
""")
        ns = next(s for s in syms if s.name == "Internal")
        assert ns.exported is False


# ── Type Aliases ─────────────────────────────────────────────────────────────

class TestTypeAlias:
    def test_type_alias_extracted(self):
        syms, _, _ = _parse("export type UserId = string;\n")
        assert any(s.name == "UserId" for s in syms)

    def test_type_alias_kind(self):
        syms, _, _ = _parse("export type Callback = (err: Error | null) => void;\n")
        alias = next(s for s in syms if s.name == "Callback")
        assert alias.kind == SymbolKind.TYPE_ALIAS

    def test_type_alias_exported(self):
        syms, _, _ = _parse("export type Status = 'active' | 'inactive';\n")
        alias = next(s for s in syms if s.name == "Status")
        assert alias.exported is True

    def test_non_exported_type_alias(self):
        syms, _, _ = _parse("type Internal = { id: number };\n")
        alias = next(s for s in syms if s.name == "Internal")
        assert alias.exported is False


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("export enum Color { Red, Green, Blue }\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("enum Direction { North, South, East, West }\n")
        e = next(s for s in syms if s.name == "Direction")
        assert e.kind == SymbolKind.ENUM

    def test_exported_enum(self):
        syms, _, _ = _parse("export enum Status { Active = 1, Inactive = 0 }\n")
        e = next(s for s in syms if s.name == "Status")
        assert e.exported is True


# ── Classes with TS-specific features ────────────────────────────────────────

class TestTSClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("class Service {}\n")
        assert any(s.name == "Service" for s in syms)

    def test_class_inherits_edge(self):
        _, edges, _ = _parse("class Poodle extends Dog { bark() {} }\n")
        assert any(e.kind == EdgeKind.INHERITS for e in edges)

    def test_class_implements_edge(self):
        _, edges, _ = _parse("class Dog implements Animal { bark() {} }\n")
        assert any(e.kind == EdgeKind.IMPLEMENTS for e in edges)

    def test_exported_class(self):
        syms, _, _ = _parse("export class UserService {}\n")
        cls = next(s for s in syms if s.name == "UserService")
        assert cls.exported is True

    def test_class_method_extracted(self):
        syms, _, _ = _parse("class Api {\n  fetch(url: string): Promise<Response> { return fetch(url); }\n}\n")
        assert any(s.name == "fetch" for s in syms)

    def test_class_with_decorator(self):
        code = "@Injectable()\nexport class AuthService { validate() {} }\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "AuthService" for s in syms)


# ── Functions ─────────────────────────────────────────────────────────────────

class TestTSFunction:
    def test_typed_function_extracted(self):
        syms, _, _ = _parse("function parse(input: string): number { return 0; }\n")
        assert any(s.name == "parse" for s in syms)

    def test_exported_typed_function(self):
        syms, _, _ = _parse("export function greet(name: string): string { return name; }\n")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.exported is True
        assert fn.kind == SymbolKind.FUNCTION

    def test_generic_function_extracted(self):
        syms, _, _ = _parse("export function identity<T>(x: T): T { return x; }\n")
        assert any(s.name == "identity" for s in syms)
