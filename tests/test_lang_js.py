"""Tests for lang/js_handler.py: TypeScript/JavaScript symbol extraction via FileParser."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind, EdgeKind


def _parse(code: str, filename: str = "mod.ts", lang: Language = Language.TYPESCRIPT) -> FileParser:
    p = FileParser(filename, lang, code.encode())
    p.parse()
    return p


def _names(p: FileParser) -> set[str]:
    return {s.name for s in p.symbols}


# ── function extraction ───────────────────────────────────────────────────────

class TestJSFunctionExtraction:
    def test_exported_function_extracted(self):
        p = _parse("export function greet(name: string): string { return name; }")
        assert "greet" in _names(p)

    def test_exported_function_is_exported(self):
        p = _parse("export function greet() {}")
        sym = next(s for s in p.symbols if s.name == "greet")
        assert sym.exported

    def test_non_exported_function_not_exported(self):
        p = _parse("function helper() {}")
        sym = next((s for s in p.symbols if s.name == "helper"), None)
        if sym:
            assert not sym.exported

    def test_arrow_function_export_extracted(self):
        p = _parse("export const process = (x: number) => x * 2;")
        assert "process" in _names(p)

    def test_async_function_extracted(self):
        p = _parse("export async function fetchData(): Promise<void> {}")
        assert "fetchData" in _names(p)

    def test_function_kind_is_function(self):
        p = _parse("export function fn() {}")
        sym = next(s for s in p.symbols if s.name == "fn")
        assert sym.kind == SymbolKind.FUNCTION


# ── class extraction ──────────────────────────────────────────────────────────

class TestJSClassExtraction:
    def test_exported_class_extracted(self):
        p = _parse("export class MyClass { constructor() {} }")
        assert "MyClass" in _names(p)

    def test_class_kind_is_class(self):
        p = _parse("export class Foo {}")
        sym = next(s for s in p.symbols if s.name == "Foo")
        assert sym.kind == SymbolKind.CLASS

    def test_class_method_extracted(self):
        p = _parse("export class Foo { bar(): void {} }")
        assert "bar" in _names(p)

    def test_method_kind_is_method(self):
        p = _parse("export class Foo { bar(): void {} }")
        sym = next(s for s in p.symbols if s.name == "bar")
        assert sym.kind == SymbolKind.METHOD

    def test_method_parent_is_class(self):
        p = _parse("export class Foo { bar(): void {} }")
        bar = next(s for s in p.symbols if s.name == "bar")
        foo = next(s for s in p.symbols if s.name == "Foo")
        assert bar.parent_id == foo.id


# ── interface extraction ──────────────────────────────────────────────────────

class TestJSInterfaceExtraction:
    def test_interface_extracted(self):
        p = _parse("export interface User { name: string; age: number; }")
        assert "User" in _names(p)

    def test_interface_kind(self):
        p = _parse("export interface Config { debug: boolean; }")
        sym = next(s for s in p.symbols if s.name == "Config")
        assert sym.kind == SymbolKind.INTERFACE


# ── type alias extraction ─────────────────────────────────────────────────────

class TestJSTypeAliasExtraction:
    def test_type_alias_extracted(self):
        p = _parse("export type Handler = (req: Request) => Response;")
        assert "Handler" in _names(p)

    def test_type_alias_kind(self):
        p = _parse("export type ID = string | number;")
        sym = next(s for s in p.symbols if s.name == "ID")
        assert sym.kind == SymbolKind.TYPE_ALIAS


# ── enum extraction ───────────────────────────────────────────────────────────

class TestJSEnumExtraction:
    def test_enum_extracted(self):
        p = _parse("export enum Status { Active, Inactive }")
        assert "Status" in _names(p)

    def test_enum_kind(self):
        p = _parse("export enum Color { Red, Green, Blue }")
        sym = next(s for s in p.symbols if s.name == "Color")
        assert sym.kind == SymbolKind.ENUM


# ── import reference extraction ───────────────────────────────────────────────
# JS imports are stored as raw strings in p.imports (resolved to edges by builder)

class TestJSImportRefs:
    def test_import_recorded(self):
        p = _parse("import { something } from './other';")
        assert any("other" in imp for imp in p.imports)

    def test_import_target_contains_module_name(self):
        p = _parse("import { fn } from './utils';")
        assert any("utils" in imp for imp in p.imports)

    def test_no_import_means_no_import_refs(self):
        p = _parse("export function standalone() {}")
        assert p.imports == []

    def test_type_only_import_skipped(self):
        # 'import type' has no runtime impact — should not be tracked
        p = _parse("import type { Foo } from './types';")
        assert not any("types" in imp for imp in p.imports)


# ── JSX/TSX component extraction ──────────────────────────────────────────────

class TestJSXComponentExtraction:
    def test_react_component_extracted(self):
        p = _parse(
            "export function MyComponent(): JSX.Element { return <div/>; }",
            filename="Comp.tsx",
            lang=Language.TSX,
        )
        assert "MyComponent" in _names(p)

    def test_arrow_component_extracted(self):
        p = _parse(
            "export const Button = (): JSX.Element => <button>click</button>;",
            filename="Button.tsx",
            lang=Language.TSX,
        )
        assert "Button" in _names(p)


# ── default export ────────────────────────────────────────────────────────────

class TestJSDefaultExport:
    def test_default_exported_class_extracted(self):
        p = _parse("class App { render() {} }\nexport default App;")
        assert "App" in _names(p)

    def test_default_export_function_extracted(self):
        p = _parse("export default function main() {}")
        assert "main" in _names(p)
