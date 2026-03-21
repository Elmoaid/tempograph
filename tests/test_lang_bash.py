"""Tests for Bash/sh language handler (BashHandlerMixin)."""
from __future__ import annotations
import pytest
from tempograph.builder import build_graph


def _build(tmp_path, filename: str, content: str):
    (tmp_path / filename).write_text(content)
    return build_graph(str(tmp_path), use_cache=False)


class TestBashFunctions:
    def test_function_keyword_form_extracted(self, tmp_path):
        g = _build(tmp_path, "deploy.sh", "function build() {\n  echo ok\n}\n")
        assert any(s.name == "build" for s in g.symbols.values())

    def test_function_short_form_extracted(self, tmp_path):
        g = _build(tmp_path, "util.sh", "cleanup() {\n  rm -rf /tmp/work\n}\n")
        assert any(s.name == "cleanup" for s in g.symbols.values())

    def test_function_kind(self, tmp_path):
        g = _build(tmp_path, "lib.sh", "function greet() {\n  echo hi\n}\n")
        sym = next(s for s in g.symbols.values() if s.name == "greet")
        assert sym.kind.value == "function"

    def test_public_function_exported(self, tmp_path):
        g = _build(tmp_path, "api.sh", "deploy() {\n  echo deploying\n}\n")
        sym = next(s for s in g.symbols.values() if s.name == "deploy")
        assert sym.exported is True

    def test_private_function_not_exported(self, tmp_path):
        g = _build(tmp_path, "impl.sh", "_helper() {\n  echo internal\n}\n")
        sym = next(s for s in g.symbols.values() if s.name == "_helper")
        assert sym.exported is False

    def test_multiple_functions_extracted(self, tmp_path):
        g = _build(tmp_path, "ops.sh",
            "function start() { echo start; }\n"
            "stop() { echo stop; }\n"
        )
        names = {s.name for s in g.symbols.values()}
        assert "start" in names
        assert "stop" in names


class TestBashConstants:
    def test_uppercase_var_extracted(self, tmp_path):
        g = _build(tmp_path, "config.sh", "MAX_RETRIES=5\n")
        assert any(s.name == "MAX_RETRIES" for s in g.symbols.values())

    def test_uppercase_var_kind(self, tmp_path):
        g = _build(tmp_path, "config.sh", "DB_HOST=\"localhost\"\n")
        sym = next(s for s in g.symbols.values() if s.name == "DB_HOST")
        assert sym.kind.value == "constant"

    def test_readonly_var_extracted(self, tmp_path):
        g = _build(tmp_path, "consts.sh", "readonly VERSION=1.0\n")
        assert any(s.name == "VERSION" for s in g.symbols.values())

    def test_declare_r_var_extracted(self, tmp_path):
        g = _build(tmp_path, "consts.sh", "declare -r PI=3.14\n")
        assert any(s.name == "PI" for s in g.symbols.values())

    def test_lowercase_var_not_extracted(self, tmp_path):
        g = _build(tmp_path, "misc.sh", "tmp_file=/tmp/foo\n")
        names = {s.name for s in g.symbols.values()}
        assert "tmp_file" not in names


class TestBashImports:
    def test_source_import_captured(self, tmp_path):
        (tmp_path / "utils.sh").write_text("function util() {}\n")
        (tmp_path / "main.sh").write_text("source ./utils.sh\nfunction run() {}\n")
        g = build_graph(str(tmp_path), use_cache=False)
        main_fi = next(fi for fp, fi in g.files.items() if "main.sh" in fp)
        assert any("utils.sh" in imp for imp in main_fi.imports)

    def test_dot_import_captured(self, tmp_path):
        (tmp_path / "lib.sh").write_text("function lib_fn() {}\n")
        (tmp_path / "entry.sh").write_text(". ./lib.sh\nfunction main() {}\n")
        g = build_graph(str(tmp_path), use_cache=False)
        entry_fi = next(fi for fp, fi in g.files.items() if "entry.sh" in fp)
        assert any("lib.sh" in imp for imp in entry_fi.imports)
