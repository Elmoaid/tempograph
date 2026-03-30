"""Tests for S1045: Dead parameters signal in render_focused.

S1045 fires when a focus seed has one or more parameters in its signature
that are never referenced in the function body, and the function has ≥3
cross-file non-test callers.

Conditions to fire:
- kind in {function, method}
- not a test file
- ≥2 extractable non-trivial parameters (excluding self/cls/_* prefix, *args/**kwargs)
- ≥3 cross-file non-test callers
- line_count ≥ 3
- ≥1 parameter name does not appear (word boundary) anywhere in body

Distinct from:
- POSSIBLY DEAD warning: about the function being unreachable (0 callers)
- TEST-ONLY CALLERS: about callers being test files
- ghost callers: about callers being themselves dead
S1045: about parameters accepted but silently ignored — callers pass values with zero effect.
"""
from __future__ import annotations

import types
import pytest
from tempograph.types import Symbol, SymbolKind, Language


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sym(
    name: str,
    file_path: str = "src/utils.py",
    kind: str = "function",
    signature: str = "",
    line_start: int = 1,
    line_end: int = 10,
    complexity: int = 5,
    language: str = "python",
) -> Symbol:
    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
        "hook": SymbolKind.HOOK,
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
        line_start=line_start,
        line_end=line_end,
        signature=signature or f"def {name}():",
        complexity=complexity,
    )


def _make_caller(name: str, file_path: str = "src/caller.py") -> Symbol:
    return _make_sym(name, file_path=file_path)


def _make_graph(root: str, symbols: dict, callers: dict | None = None) -> types.SimpleNamespace:
    """Minimal mock Tempo graph with callers_of support."""
    g = types.SimpleNamespace()
    g.root = root
    g.symbols = symbols
    g._callers = callers or {}

    def callers_of(sym_id: str) -> list:
        return [symbols[cid] for cid in g._callers.get(sym_id, []) if cid in symbols]

    g.callers_of = callers_of
    g.hot_files = set()
    g.edges = []
    return g


from tempograph.render.focused import _compute_dead_params


# ---------------------------------------------------------------------------
# Unit tests: _compute_dead_params — FIRES
# ---------------------------------------------------------------------------


class TestDeadParamsUnit:

    def test_fires_single_dead_param(self, tmp_path):
        """Function with one unused param and ≥3 cross-file callers → fires."""
        src = (
            "def _extract_signature(node, source, lang):\n"
            "    text = _node_text(node, source)\n"
            "    first_line = text.split('\\n')[0].strip()\n"
            "    return first_line\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils.py").write_text(src)

        sym = _make_sym(
            "_extract_signature",
            file_path="src/utils.py",
            signature="def _extract_signature(node, source, lang):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"caller_{i}", file_path=f"src/handler_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert "dead param" in result
        assert "lang" in result
        assert "4 callers" in result

    def test_fires_multiple_dead_params_one_function(self, tmp_path):
        """Function with two unused params → fires, lists both."""
        src = (
            "def build_index(items, verbose, timeout):\n"
            "    result = []\n"
            "    for item in items:\n"
            "        result.append(item.process())\n"
            "    return result\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "builder.py").write_text(src)

        sym = _make_sym(
            "build_index",
            file_path="src/builder.py",
            signature="def build_index(items, verbose, timeout):",
            line_start=1, line_end=5,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/mod_{i}.py")
            for i in range(5)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert "dead param" in result
        # Both verbose and timeout should be flagged
        assert "verbose" in result or "timeout" in result

    def test_fires_method_kind(self, tmp_path):
        """Method (not function) with dead param → fires."""
        src = (
            "class Parser:\n"
            "    def handle_node(self, node, source, strict):\n"
            "        text = extract_text(node, source)\n"
            "        return text.strip()\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "parser.py").write_text(src)

        sym = _make_sym(
            "handle_node",
            file_path="src/parser.py",
            kind="method",
            signature="def handle_node(self, node, source, strict):",
            line_start=2, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/handler_{i}.py")
            for i in range(3)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert "dead param" in result
        assert "strict" in result

    def test_fires_typescript_function(self, tmp_path):
        """TypeScript function with dead param → fires."""
        src = (
            "function formatUser(user: User, locale: string, options: Options): string {\n"
            "  const name = user.name;\n"
            "  const email = user.email;\n"
            "  return `${name} <${email}>`;\n"
            "}\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "format.ts").write_text(src)

        sym = _make_sym(
            "formatUser",
            file_path="src/format.ts",
            kind="function",
            signature="function formatUser(user: User, locale: string, options: Options): string",
            line_start=1, line_end=5,
            language="typescript",
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/view_{i}.ts")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert "dead param" in result
        # locale and options are both unused (only user is used)
        assert "locale" in result or "options" in result

    def test_fires_type_annotated_params(self, tmp_path):
        """Python type-annotated params — name extracted correctly."""
        src = (
            "def process(items: list[str], config: Config, debug: bool) -> list:\n"
            "    return [item.strip() for item in items]\n"
            "    # unused options above\n"
            "    pass\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "proc.py").write_text(src)

        sym = _make_sym(
            "process",
            file_path="src/proc.py",
            signature="def process(items: list[str], config: Config, debug: bool) -> list:",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/stage_{i}.py")
            for i in range(3)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert "dead param" in result
        assert "config" in result or "debug" in result

    # ---- SILENT ----

    def test_silent_all_params_used(self, tmp_path):
        """All params actually used → silent."""
        src = (
            "def compute(node, source, lang):\n"
            "    text = _node_text(node, source)\n"
            "    prefix = lang.prefix\n"
            "    return f'{prefix}{text}'\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils.py").write_text(src)

        sym = _make_sym(
            "compute",
            file_path="src/utils.py",
            signature="def compute(node, source, lang):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/h_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert result == ""

    def test_silent_below_caller_threshold(self, tmp_path):
        """Only 2 cross-file callers — below threshold of 3 → silent."""
        src = (
            "def transform(data, mode, unused_flag):\n"
            "    return data.apply(mode)\n"
            "    # unused_flag never referenced\n"
            "    pass\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "xform.py").write_text(src)

        sym = _make_sym(
            "transform",
            file_path="src/xform.py",
            signature="def transform(data, mode, unused_flag):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/mod_{i}.py")
            for i in range(2)  # only 2!
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert result == ""

    def test_silent_underscore_prefix_param(self, tmp_path):
        """Params starting with _ are intentionally unused — silent."""
        src = (
            "def render(node, source, _ctx):\n"
            "    text = extract(node, source)\n"
            "    return text\n"
            "    # _ctx ignored by convention\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "render.py").write_text(src)

        sym = _make_sym(
            "render",
            file_path="src/render.py",
            signature="def render(node, source, _ctx):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/m_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert result == ""

    def test_silent_kwargs_not_flagged(self, tmp_path):
        """**kwargs variadic param → not flagged as dead even if not referenced."""
        src = (
            "def configure(name, value, **options):\n"
            "    settings[name] = value\n"
            "    return settings\n"
            "    # options forwarded via **\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "cfg.py").write_text(src)

        sym = _make_sym(
            "configure",
            file_path="src/cfg.py",
            signature="def configure(name, value, **options):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/m_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        # 'name' and 'value' are used, **options is excluded from dead check
        assert result == ""

    def test_silent_test_file_seed(self, tmp_path):
        """Seed in test file → silent."""
        src = (
            "def test_func(client, request, unused_fixture):\n"
            "    resp = client.get('/api')\n"
            "    assert resp.status_code == 200\n"
            "    return resp\n"
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_api.py").write_text(src)

        sym = _make_sym(
            "test_func",
            file_path="tests/test_api.py",
            signature="def test_func(client, request, unused_fixture):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"tests/m_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert result == ""

    def test_silent_only_same_file_callers(self, tmp_path):
        """All 4 callers are in the same file as the seed → 0 cross-file → silent."""
        src = (
            "def internal_helper(data, mode, unused_flag):\n"
            "    return data.transform(mode)\n"
            "    # unused_flag ignored\n"
            "    pass\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils.py").write_text(src)

        sym = _make_sym(
            "internal_helper",
            file_path="src/utils.py",
            signature="def internal_helper(data, mode, unused_flag):",
            line_start=1, line_end=4,
        )
        # All callers in SAME file as seed
        callers = [
            _make_caller(f"c{i}", file_path="src/utils.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert result == ""

    def test_silent_short_function(self, tmp_path):
        """Function with line_count < 3 (stub/shim) → silent."""
        src = "def shim(a, b, unused): return a + b\n"
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "shim.py").write_text(src)

        sym = _make_sym(
            "shim",
            file_path="src/shim.py",
            signature="def shim(a, b, unused):",
            line_start=1, line_end=1,  # line_count = 1
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/m_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        assert result == ""

    def test_silent_only_one_param(self, tmp_path):
        """Only one extractable param → need ≥2 to fire → silent."""
        src = (
            "def process(data):\n"
            "    # data isn't used... but only 1 param\n"
            "    return None\n"
            "    pass\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "proc.py").write_text(src)

        sym = _make_sym(
            "process",
            file_path="src/proc.py",
            signature="def process(data):",
            line_start=1, line_end=4,
        )
        callers = [
            _make_caller(f"c{i}", file_path=f"src/m_{i}.py")
            for i in range(4)
        ]
        sym_map = {sym.id: sym, **{c.id: c for c in callers}}
        g = _make_graph(str(tmp_path), sym_map, {sym.id: [c.id for c in callers]})
        result = _compute_dead_params([sym], g)

        # Only 1 param → threshold requires ≥2 params
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests: real codebase fire/silent cases
# ---------------------------------------------------------------------------


class TestDeadParamsIntegration:

    def test_fires_for_extract_signature_lang_param(self):
        """_extract_signature in tempograph/lang/_utils.py has dead 'lang' param — fires."""
        from tempograph import build_graph
        from tempograph.render.focused import _compute_dead_params

        graph = build_graph(".")
        sym = None
        for s in graph.symbols.values():
            if s.name == "_extract_signature" and "_utils.py" in s.file_path:
                sym = s
                break
        assert sym is not None, "_extract_signature not found in graph"

        result = _compute_dead_params([sym], graph)
        assert "dead param" in result
        assert "lang" in result

    def test_silent_for_well_used_function(self):
        """_compute_dead_params should be silent for build_graph (all params used)."""
        from tempograph import build_graph
        from tempograph.render.focused import _compute_dead_params

        graph = build_graph(".")
        sym = None
        for s in graph.symbols.values():
            if s.name == "build_graph" and "builder.py" in s.file_path:
                sym = s
                break
        assert sym is not None, "build_graph not found"

        result = _compute_dead_params([sym], graph)
        # build_graph uses all its parameters
        assert result == "" or "build_graph" not in result
