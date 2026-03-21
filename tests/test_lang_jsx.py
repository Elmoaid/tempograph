"""Tests for JSX language handler (JSHandlerMixin with Language.JSX/TSX).

React components (PascalCase functions/consts in JSX/TSX files) are classified
as SymbolKind.COMPONENT. Hooks (use* functions) are SymbolKind.HOOK.
"""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse_jsx(code: str, filename: str = "src/Button.jsx"):
    p = FileParser(filename, Language.JSX, code.encode())
    return p.parse()


def _parse_tsx(code: str, filename: str = "src/Button.tsx"):
    p = FileParser(filename, Language.TSX, code.encode())
    return p.parse()


# ── React Components ──────────────────────────────────────────────────────────

class TestComponent:
    def test_jsx_component_function_extracted(self):
        code = "export default function Button({ label }) {\n    return <button>{label}</button>;\n}\n"
        syms, _, _ = _parse_jsx(code)
        assert any(s.name == "Button" for s in syms)

    def test_jsx_component_kind(self):
        """PascalCase functions in JSX files are classified as COMPONENT."""
        code = "export function Card({ title }) {\n    return <div>{title}</div>;\n}\n"
        syms, _, _ = _parse_jsx(code)
        comp = next(s for s in syms if s.name == "Card")
        assert comp.kind == SymbolKind.COMPONENT

    def test_tsx_component_kind(self):
        """PascalCase functions in TSX files are classified as COMPONENT."""
        code = "export function Modal({ children }: Props) {\n    return <div>{children}</div>;\n}\n"
        syms, _, _ = _parse_tsx(code)
        comp = next(s for s in syms if s.name == "Modal")
        assert comp.kind == SymbolKind.COMPONENT

    def test_jsx_component_exported(self):
        code = "export function Alert({ msg }) {\n    return <span>{msg}</span>;\n}\n"
        syms, _, _ = _parse_jsx(code)
        comp = next(s for s in syms if s.name == "Alert")
        assert comp.exported is True

    def test_jsx_arrow_component_extracted(self):
        code = "export const Button = ({ label }) => <button>{label}</button>;\n"
        syms, _, _ = _parse_jsx(code)
        assert any(s.name == "Button" for s in syms)

    def test_jsx_arrow_component_kind(self):
        code = "export const Input = ({ value }) => <input value={value} />;\n"
        syms, _, _ = _parse_jsx(code)
        comp = next(s for s in syms if s.name == "Input")
        assert comp.kind == SymbolKind.COMPONENT

    def test_lowercase_function_not_component(self):
        """Lowercase functions in JSX files are FUNCTION, not COMPONENT."""
        code = "function handleClick() {}\n"
        syms, _, _ = _parse_jsx(code)
        fn = next(s for s in syms if s.name == "handleClick")
        assert fn.kind == SymbolKind.FUNCTION


# ── React Hooks ───────────────────────────────────────────────────────────────

class TestHook:
    def test_hook_extracted(self):
        code = "export function useCounter(initial) {\n    return initial;\n}\n"
        syms, _, _ = _parse_jsx(code)
        assert any(s.name == "useCounter" for s in syms)

    def test_hook_kind(self):
        """use* functions with uppercase third character are HOOK."""
        code = "export function useTheme() {\n    return {};\n}\n"
        syms, _, _ = _parse_jsx(code)
        hook = next(s for s in syms if s.name == "useTheme")
        assert hook.kind == SymbolKind.HOOK

    def test_tsx_hook_kind(self):
        code = "export function useAuth(): AuthState {\n    return {} as AuthState;\n}\n"
        syms, _, _ = _parse_tsx(code)
        hook = next(s for s in syms if s.name == "useAuth")
        assert hook.kind == SymbolKind.HOOK


# ── Multiple Components ────────────────────────────────────────────────────────

class TestMultiple:
    def test_multiple_components(self):
        code = (
            "export function Header() { return <h1/>; }\n"
            "export function Footer() { return <footer/>; }\n"
            "export function Sidebar() { return <aside/>; }\n"
        )
        syms, _, _ = _parse_jsx(code)
        names = {s.name for s in syms}
        assert {"Header", "Footer", "Sidebar"}.issubset(names)

    def test_all_components_have_component_kind(self):
        code = (
            "export function Nav() { return <nav/>; }\n"
            "export function Logo() { return <img/>; }\n"
        )
        syms, _, _ = _parse_jsx(code)
        component_syms = [s for s in syms if s.name in ("Nav", "Logo")]
        assert all(s.kind == SymbolKind.COMPONENT for s in component_syms)
