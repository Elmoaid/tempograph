"""Tests for S1043: Async gap signal in render_focused.

S1043 fires when a focus seed is an async function/method whose body
contains no 'await' expression — it runs synchronously, which misleads
callers about its execution model.

Conditions to fire:
- kind in {function, method}
- 'async ' present in signature
- line_count >= 3 (skip trivial 1-2-line async shims)
- body (lines after def line) has NO 'await' token
- body has NO 'yield' token (async generators are valid)
- not a test file

Distinct from:
- S81 ([async] annotation): S81 labels the symbol async in BFS detail;
  S1043 is a PRE-BFS header warning about a structural footgun.
- S239 (async blast): about async functions in blast radius, not focus seed.
- S279 (dead async fns): about async with 0 callers; S1043 fires regardless of caller count.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_graph(root: str, symbols: dict, callers: dict | None = None):
    """Build a minimal mock Tempo graph for unit tests."""
    import types
    g = types.SimpleNamespace()
    g.root = root
    g.symbols = symbols
    g._callers = callers or {}

    def callers_of(sym_id):
        return [
            symbols[cid] for cid in g._callers.get(sym_id, [])
            if cid in symbols
        ]
    g.callers_of = callers_of
    g.hot_files = set()
    g.edges = []
    return g


def _make_sym(
    name: str,
    file_path: str = "src/utils.py",
    kind: str = "function",
    signature: str = "",
    line_start: int = 1,
    line_end: int = 10,
    complexity: int = 5,
    language: str = "python",
):
    from tempograph.types import Symbol, SymbolKind, Language

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


# ---------------------------------------------------------------------------
# Unit tests: _compute_async_gap (with mock graph + real tmp files)
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_async_gap


class TestAsyncGapUnit:

    # ---- FIRES ----

    def test_fires_python_async_no_await(self, tmp_path):
        """Python async def with no await, >=3 lines → fires."""
        src = (
            "async def fetch_data(url):\n"
            "    items = load_from_cache(url)\n"
            "    return items\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.py").write_text(src)
        sym = _make_sym(
            "fetch_data",
            file_path="src/api.py",
            kind="function",
            signature="async def fetch_data(url):",
            line_start=1, line_end=3,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        result = _compute_async_gap([sym], g)
        assert "async gap" in result
        assert "fetch_data" in result
        assert "no await" in result.lower() or "await" in result.lower()

    def test_fires_ts_async_no_await(self, tmp_path):
        """TypeScript async function with no await, >=3 lines → fires."""
        src = (
            "async function getUser(id: string): Promise<User> {\n"
            "  const user = userCache.get(id);\n"
            "  return user ?? DEFAULT_USER;\n"
            "}\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "users.ts").write_text(src)
        sym = _make_sym(
            "getUser",
            file_path="src/users.ts",
            kind="function",
            signature="async function getUser(id: string): Promise<User>",
            line_start=1, line_end=4,
            language="typescript",
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        result = _compute_async_gap([sym], g)
        assert "async gap" in result
        assert "getUser" in result

    def test_fires_async_method(self, tmp_path):
        """Async method (kind=method) with no await → fires."""
        src = (
            "class Repo:\n"
            "    async def find_all(self):\n"
            "        items = list(self._store.values())\n"
            "        return items\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "repo.py").write_text(src)
        sym = _make_sym(
            "Repo.find_all",
            file_path="src/repo.py",
            kind="method",
            signature="    async def find_all(self):",
            line_start=2, line_end=4,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        result = _compute_async_gap([sym], g)
        assert "async gap" in result
        assert "find_all" in result

    def test_fires_multiple_seeds_one_gap(self, tmp_path):
        """Two seeds, one has gap and one has await → only gap seed reported."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text(
            "async def bad_fn():\n"
            "    x = compute()\n"
            "    return x\n"
            "\n"
            "async def good_fn():\n"
            "    result = await fetch()\n"
            "    return result\n"
        )
        bad = _make_sym(
            "bad_fn",
            file_path="src/mod.py",
            signature="async def bad_fn():",
            line_start=1, line_end=3,
        )
        good = _make_sym(
            "good_fn",
            file_path="src/mod.py",
            signature="async def good_fn():",
            line_start=5, line_end=7,
        )
        g = _make_mock_graph(str(tmp_path), {bad.id: bad, good.id: good})
        result = _compute_async_gap([bad, good], g)
        assert "bad_fn" in result
        assert "good_fn" not in result

    # ---- ABSENT ----

    def test_absent_has_await(self, tmp_path):
        """Async function WITH await → no signal."""
        src = (
            "async def fetch_data(url):\n"
            "    response = await http.get(url)\n"
            "    return response.json()\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.py").write_text(src)
        sym = _make_sym(
            "fetch_data",
            file_path="src/api.py",
            signature="async def fetch_data(url):",
            line_start=1, line_end=3,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_absent_async_generator_with_yield(self, tmp_path):
        """Async generator (no await, has yield) → no signal."""
        src = (
            "async def generate_items(n):\n"
            "    for i in range(n):\n"
            "        yield i\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "gen.py").write_text(src)
        sym = _make_sym(
            "generate_items",
            file_path="src/gen.py",
            signature="async def generate_items(n):",
            line_start=1, line_end=3,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_absent_short_function_two_lines(self, tmp_path):
        """2-line async function (common type-alias shim) → no signal."""
        src = "async def noop():\n    return None\n"
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "shim.py").write_text(src)
        sym = _make_sym(
            "noop",
            file_path="src/shim.py",
            signature="async def noop():",
            line_start=1, line_end=2,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_absent_sync_function(self, tmp_path):
        """Regular sync function → no signal."""
        src = (
            "def compute(x):\n"
            "    result = x * 2\n"
            "    return result\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "math.py").write_text(src)
        sym = _make_sym(
            "compute",
            file_path="src/math.py",
            signature="def compute(x):",
            line_start=1, line_end=3,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_absent_test_file(self, tmp_path):
        """Seed in test file → no signal (test async patterns are intentional)."""
        (tmp_path / "tests").mkdir()
        src = (
            "async def test_something():\n"
            "    result = run_fn()\n"
            "    assert result is not None\n"
        )
        (tmp_path / "tests" / "test_api.py").write_text(src)
        sym = _make_sym(
            "test_something",
            file_path="tests/test_api.py",
            signature="async def test_something():",
            line_start=1, line_end=3,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_absent_class_kind(self, tmp_path):
        """Class symbol with 'async' in name → no signal (kind=class)."""
        (tmp_path / "src").mkdir()
        src = "class AsyncProcessor:\n    pass\n"
        (tmp_path / "src" / "proc.py").write_text(src)
        sym = _make_sym(
            "AsyncProcessor",
            file_path="src/proc.py",
            kind="class",
            signature="class AsyncProcessor:",
            line_start=1, line_end=2,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_absent_empty_seeds(self, tmp_path):
        """Empty seeds list → empty string."""
        g = _make_mock_graph(str(tmp_path), {})
        assert _compute_async_gap([], g) == ""

    def test_absent_missing_file(self, tmp_path):
        """Source file does not exist → graceful skip, no signal."""
        sym = _make_sym(
            "ghost_fn",
            file_path="src/nonexistent.py",
            signature="async def ghost_fn():",
            line_start=1, line_end=5,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        assert _compute_async_gap([sym], g) == ""

    def test_fires_output_contains_advice(self, tmp_path):
        """Signal output mentions 'async' and actionable advice."""
        src = (
            "async def sync_looking():\n"
            "    data = load_data()\n"
            "    return process(data)\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "proc.py").write_text(src)
        sym = _make_sym(
            "sync_looking",
            file_path="src/proc.py",
            signature="async def sync_looking():",
            line_start=1, line_end=3,
        )
        g = _make_mock_graph(str(tmp_path), {sym.id: sym})
        result = _compute_async_gap([sym], g)
        assert result  # fires
        # Must mention the function and give actionable advice
        assert "sync_looking" in result
        assert "async" in result.lower()

    def test_fires_overflow_truncation(self, tmp_path):
        """More than 3 gap functions → first 3 shown + overflow count."""
        (tmp_path / "src").mkdir()
        lines_per_fn = "async def fn_{}():\n    x = do_work()\n    return x\n"
        src = "".join(lines_per_fn.format(i) for i in range(5))
        (tmp_path / "src" / "bulk.py").write_text(src)
        syms = []
        for i in range(5):
            start = i * 3 + 1
            syms.append(_make_sym(
                f"fn_{i}",
                file_path="src/bulk.py",
                signature=f"async def fn_{i}():",
                line_start=start, line_end=start + 2,
            ))
        g = _make_mock_graph(str(tmp_path), {s.id: s for s in syms})
        result = _compute_async_gap(syms, g)
        assert "async gap" in result
        assert "+2 more" in result


# ---------------------------------------------------------------------------
# Integration tests: build_graph + render_focused
# ---------------------------------------------------------------------------

class TestAsyncGapIntegration:

    def test_shown_in_render_focused(self, tmp_path):
        """End-to-end: render_focused shows async gap for no-await async fn."""
        from tempograph import build_graph
        from tempograph.render.focused import render_focused

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "service.py").write_text(
            "async def load_config(path):\n"
            "    with open(path) as f:\n"
            "        return f.read()\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        out = render_focused(g, "load_config")
        assert "async gap" in out, (
            f"Expected 'async gap' for async function without await; got:\n{out}"
        )

    def test_absent_in_render_focused_when_awaits(self, tmp_path):
        """End-to-end: render_focused silent when async fn has await."""
        from tempograph import build_graph
        from tempograph.render.focused import render_focused

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "service.py").write_text(
            "async def fetch_config(path):\n"
            "    result = await read_file(path)\n"
            "    return result\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        out = render_focused(g, "fetch_config")
        assert "async gap" not in out, (
            f"'async gap' must not appear when await is present; got:\n{out}"
        )

    def test_absent_for_sync_function(self, tmp_path):
        """End-to-end: render_focused silent for regular sync function."""
        from tempograph import build_graph
        from tempograph.render.focused import render_focused

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils.py").write_text(
            "def compute_total(items):\n"
            "    total = sum(x.value for x in items)\n"
            "    return total\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        out = render_focused(g, "compute_total")
        assert "async gap" not in out, (
            f"'async gap' must not appear for sync function; got:\n{out}"
        )
