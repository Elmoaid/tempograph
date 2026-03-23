"""Tests for callsite line annotations in focus mode callers section (S20)."""

from unittest.mock import patch

import pytest

from tempograph.types import Edge, EdgeKind, FileInfo, Language, Symbol, SymbolKind, Tempo


def _make_graph(tmp_path, edges, symbols=None):
    """Build a minimal Tempo graph with explicit symbols and edges."""
    root = str(tmp_path)
    if symbols is None:
        symbols = []

    graph = Tempo(root=root)
    for sym in symbols:
        graph.symbols[sym.id] = sym
        graph.files.setdefault(
            sym.file_path,
            FileInfo(path=sym.file_path, language=sym.language, line_count=100, byte_size=5000, symbols=[]),
        ).symbols.append(sym.id)
    graph.edges = list(edges)
    graph.build_indexes()
    return graph


def _target_sym():
    return Symbol(
        id="target.py::process_data",
        name="process_data",
        qualified_name="process_data",
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path="target.py",
        line_start=10,
        line_end=30,
        exported=True,
    )


def _caller_sym(name="do_work", file_path="caller.py", line_start=1, line_end=50):
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        exported=True,
    )


class TestFocusCallsiteLines:
    """Verify callsite line annotations in focus mode callers section."""

    def test_single_callsite_shows_line(self, tmp_path):
        """One caller with one callsite shows [line N]."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller = _caller_sym()
        edges = [Edge(EdgeKind.CALLS, caller.id, target.id, line=45)]
        graph = _make_graph(tmp_path, edges, [target, caller])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[line 45]" in output

    def test_multiple_callsites_shows_two_lines(self, tmp_path):
        """One caller with multiple callsites shows [lines N, M] (lowest two)."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller = _caller_sym()
        edges = [
            Edge(EdgeKind.CALLS, caller.id, target.id, line=23),
            Edge(EdgeKind.CALLS, caller.id, target.id, line=67),
            Edge(EdgeKind.CALLS, caller.id, target.id, line=99),
        ]
        graph = _make_graph(tmp_path, edges, [target, caller])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[lines 23, 67]" in output

    def test_zero_line_not_shown(self, tmp_path):
        """When edge.line == 0, no bracket annotation appears."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller = _caller_sym()
        edges = [Edge(EdgeKind.CALLS, caller.id, target.id, line=0)]
        graph = _make_graph(tmp_path, edges, [target, caller])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[line" not in output
        # Caller should still appear
        assert "do_work" in output

    def test_callsite_lines_in_output(self, tmp_path):
        """Integration: build graph, run render_focused, assert [line appears."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller_a = _caller_sym("validate_user", "auth.py")
        caller_b = _caller_sym("handle_request", "api.py")
        edges = [
            Edge(EdgeKind.CALLS, caller_a.id, target.id, line=45),
            Edge(EdgeKind.CALLS, caller_b.id, target.id, line=23),
            Edge(EdgeKind.CALLS, caller_b.id, target.id, line=67),
        ]
        graph = _make_graph(tmp_path, edges, [target, caller_a, caller_b])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[line 45]" in output
        assert "[lines 23, 67]" in output


# ── S27: Multi-symbol focus ──────────────────────────────────────────────────

class TestMultiSymbolFocus:
    def _make_two_symbol_graph(self, tmp_path):
        auth = Symbol(
            id="auth.py::authenticate",
            name="authenticate", qualified_name="authenticate",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="auth.py", line_start=1, line_end=10, exported=True,
        )
        logout = Symbol(
            id="auth.py::logout",
            name="logout", qualified_name="logout",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="auth.py", line_start=12, line_end=20, exported=True,
        )
        return _make_graph(tmp_path, [], [auth, logout])

    def test_pipe_separator_merges_seeds(self, tmp_path):
        from tempograph.render import render_focused
        graph = self._make_two_symbol_graph(tmp_path)
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "authenticate | logout")
        assert "authenticate" in out
        assert "logout" in out

    def test_pipe_separator_header(self, tmp_path):
        from tempograph.render import render_focused
        graph = self._make_two_symbol_graph(tmp_path)
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "authenticate | logout")
        assert "Focus: authenticate | logout" in out

    def test_single_query_unchanged(self, tmp_path):
        from tempograph.render import render_focused
        graph = self._make_two_symbol_graph(tmp_path)
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "authenticate")
        assert "Focus: authenticate" in out
        assert "|" not in out.split("\n")[0]

    def test_no_duplicate_seeds(self, tmp_path):
        """Same symbol queried via two parts → appears only once in output."""
        from tempograph.render import render_focused
        graph = self._make_two_symbol_graph(tmp_path)
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "authenticate | authenticate")
        # Should appear exactly once as a seed (depth 0), not duplicated
        assert out.count("● function authenticate") == 1


class TestFocusDeadCallerAnnotation:
    """S43: ghost callers annotated [dead?] when caller has 0 callers itself."""

    def _make_graph_with_callers(self, tmp_path, caller_has_callers: bool):
        target = Symbol(
            id="core.py::process",
            name="process", qualified_name="process",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="core.py", line_start=1, line_end=10, exported=True,
        )
        ghost = Symbol(
            id="util.py::ghost_fn",
            name="ghost_fn", qualified_name="ghost_fn",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="util.py", line_start=1, line_end=5, exported=False,
        )
        caller_of_ghost = Symbol(
            id="app.py::main",
            name="main", qualified_name="main",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="app.py", line_start=1, line_end=5, exported=False,
        )
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=ghost.id, target_id=target.id),
        ]
        if caller_has_callers:
            edges.append(Edge(kind=EdgeKind.CALLS, source_id=caller_of_ghost.id, target_id=ghost.id))
        syms = [target, ghost]
        if caller_has_callers:
            syms.append(caller_of_ghost)
        return _make_graph(tmp_path, edges, syms)

    def test_dead_caller_annotation_fires(self, tmp_path):
        """Ghost caller (0 callers, not exported) gets [dead?] and unreachable summary."""
        from unittest.mock import patch
        from tempograph.render import render_focused
        graph = self._make_graph_with_callers(tmp_path, caller_has_callers=False)
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "process")
        assert "[dead?]" in out
        assert "unreachable" in out

    def test_dead_caller_annotation_absent_when_caller_is_live(self, tmp_path):
        """If the caller has its own callers, no [dead?] annotation."""
        from unittest.mock import patch
        from tempograph.render import render_focused
        graph = self._make_graph_with_callers(tmp_path, caller_has_callers=True)
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "process")
        assert "[dead?]" not in out


class TestFocusApexPath:
    """S45: apex path — nearest symbol with no non-test callers, shown in focus depth-0."""

    def _sym(self, name, file_path="core.py"):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path=file_path, line_start=1, line_end=10, exported=True,
        )

    def test_apex_shows_nearest_entry_point_hop(self, tmp_path):
        """Chain: main → handler → core::process. Process sees apex: main [2 hops]."""
        from unittest.mock import patch
        from tempograph.render import render_focused
        process = self._sym("process", "core.py")
        handler = self._sym("handle_request", "handler.py")
        main = self._sym("main", "app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=handler.id, target_id=process.id),
            Edge(kind=EdgeKind.CALLS, source_id=main.id, target_id=handler.id),
        ]
        graph = _make_graph(tmp_path, edges, [process, handler, main])
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "process")
        assert "apex:" in out
        # main has no callers → it's the apex at 2 hops
        assert "main" in out
        assert "2 hops" in out

    def test_apex_self_when_no_callers(self, tmp_path):
        """Symbol with no callers at all shows 'apex: self [entry]'."""
        from unittest.mock import patch
        from tempograph.render import render_focused
        entry = self._sym("run", "main.py")
        graph = _make_graph(tmp_path, [], [entry])
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "run")
        assert "apex: self [entry]" in out

    def test_apex_suppressed_when_only_test_callers(self, tmp_path):
        """Symbol called only from test files: apex line suppressed (no interesting chain)."""
        from unittest.mock import patch
        from tempograph.render import render_focused
        target = self._sym("helper", "utils.py")
        test_caller = Symbol(
            id="tests/test_utils.py::test_helper",
            name="test_helper", qualified_name="test_helper",
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path="tests/test_utils.py", line_start=1, line_end=5, exported=False,
        )
        edges = [Edge(kind=EdgeKind.CALLS, source_id=test_caller.id, target_id=target.id)]
        graph = _make_graph(tmp_path, edges, [target, test_caller])
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(graph, "helper")
        # No apex line when caller chain is test-only
        assert "apex:" not in out


class TestFocusCochangeCohort:
    """Tests for the co-change cohort section (S46).

    The cohort section shows files that historically co-change with the seed file
    but are NOT already visible in the BFS focus output — pure git-coupling signal.
    """

    def _sym(self, name, file_path):
        return Symbol(
            id=f"{file_path}::{name}", name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path=file_path, line_start=1, line_end=10, exported=True,
        )

    def test_cohort_appears_when_cochanged_files_not_in_bfs(self, tmp_path):
        """Cohort shows files with ≥3 co-changes that are absent from the BFS output."""
        from unittest.mock import patch
        from tempograph.render.focused import _render_cochange_cohort_section
        from tempograph.types import Tempo

        graph = Tempo(root=str(tmp_path))
        seen = {"seed.py"}  # files already shown in focus
        cochange_data = [
            {"path": "storage.py", "count": 12},
            {"path": "cache.py", "count": 8},
        ]
        with patch("tempograph.git.cochange_pairs", return_value=cochange_data):
            result = _render_cochange_cohort_section(graph, ["seed.py"], seen)

        assert "Co-change cohort" in result
        assert "storage.py" in result
        assert "12 times" in result
        assert "cache.py" in result
        assert "8 times" in result

    def test_cohort_filters_files_already_in_bfs(self, tmp_path):
        """Files already visible in BFS are excluded from the cohort."""
        from unittest.mock import patch
        from tempograph.render.focused import _render_cochange_cohort_section
        from tempograph.types import Tempo

        graph = Tempo(root=str(tmp_path))
        # storage.py is already in the focus BFS output
        seen = {"seed.py", "storage.py"}
        cochange_data = [
            {"path": "storage.py", "count": 12},  # in seen → filtered out
            {"path": "cache.py", "count": 8},     # not in seen → shown
        ]
        with patch("tempograph.git.cochange_pairs", return_value=cochange_data):
            result = _render_cochange_cohort_section(graph, ["seed.py"], seen)

        assert "storage.py" not in result
        assert "cache.py" in result

    def test_cohort_empty_when_all_cochanged_files_in_bfs(self, tmp_path):
        """No cohort section when all co-changed files are already in the BFS output."""
        from unittest.mock import patch
        from tempograph.render.focused import _render_cochange_cohort_section
        from tempograph.types import Tempo

        graph = Tempo(root=str(tmp_path))
        seen = {"seed.py", "storage.py", "cache.py"}
        cochange_data = [
            {"path": "storage.py", "count": 12},
            {"path": "cache.py", "count": 8},
        ]
        with patch("tempograph.git.cochange_pairs", return_value=cochange_data):
            result = _render_cochange_cohort_section(graph, ["seed.py"], seen)

        assert result == ""

    def test_cohort_empty_when_no_cochanged_files(self, tmp_path):
        """No cohort section when cochange_pairs returns empty list."""
        from unittest.mock import patch
        from tempograph.render.focused import _render_cochange_cohort_section
        from tempograph.types import Tempo

        graph = Tempo(root=str(tmp_path))
        with patch("tempograph.git.cochange_pairs", return_value=[]):
            result = _render_cochange_cohort_section(graph, ["seed.py"], set())

        assert result == ""

    def test_cohort_capped_at_five(self, tmp_path):
        """Cohort shows at most 5 files even if cochange_pairs returns more."""
        from unittest.mock import patch
        from tempograph.render.focused import _render_cochange_cohort_section
        from tempograph.types import Tempo

        graph = Tempo(root=str(tmp_path))
        cochange_data = [{"path": f"file{i}.py", "count": 10 - i} for i in range(8)]
        with patch("tempograph.git.cochange_pairs", return_value=cochange_data):
            result = _render_cochange_cohort_section(graph, ["seed.py"], set())

        shown = [line for line in result.splitlines() if "file" in line and "times" in line]
        assert len(shown) <= 5


class TestBuildSeedNameTestLines:
    """Tests for _build_seed_name_test_lines (S47): name-pattern + import-based coverage."""

    def _fn_sym(self, name, file_path, line_start=1):
        return Symbol(
            id=f"{file_path}::{name}", name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=Language.PYTHON,
            file_path=file_path, line_start=line_start, line_end=line_start + 10,
            exported=True,
        )

    def test_found_by_name_match(self, tmp_path):
        """Name-matched test_<seed_name> function in test file → emits 'tests found:' line."""
        from tempograph.render.focused import _build_seed_name_test_lines

        seed = self._fn_sym("process_data", "module.py")
        test_fn = self._fn_sym("test_process_data", "test_module.py")
        graph = _make_graph(tmp_path, edges=[], symbols=[seed, test_fn])

        result = _build_seed_name_test_lines(seed, graph, "")
        assert result, "Expected a 'tests found:' line"
        assert "tests found:" in result[0]
        assert "test_module.py" in result[0]
        assert "test_process_data" in result[0]

    def test_found_by_import_only(self, tmp_path):
        """Test file imports seed file but has no name-matched function → still emits line."""
        from tempograph.render.focused import _build_seed_name_test_lines

        seed = self._fn_sym("my_func", "module.py")
        # No test_my_func symbol — just import edge
        graph = _make_graph(tmp_path, edges=[
            Edge(kind=EdgeKind.IMPORTS, source_id="tests/test_module.py", target_id="module.py"),
        ], symbols=[seed])

        result = _build_seed_name_test_lines(seed, graph, "")
        assert result, "Expected a 'tests found:' line from import-based detection"
        assert "tests found:" in result[0]
        assert "test_module.py" in result[0]

    def test_skips_test_function(self, tmp_path):
        """Symbol in a test file is skipped — it IS a test, not something being tested."""
        from tempograph.render.focused import _build_seed_name_test_lines

        test_sym = self._fn_sym("test_something", "test_foo.py")
        graph = _make_graph(tmp_path, edges=[], symbols=[test_sym])

        result = _build_seed_name_test_lines(test_sym, graph, "")
        assert result == []

    def test_no_duplicate_when_already_caller(self, tmp_path):
        """Files already shown by caller-based _build_seed_test_lines are excluded."""
        from tempograph.render.focused import _build_seed_name_test_lines

        seed = self._fn_sym("my_func", "module.py")
        # test_fn is both a name match AND a caller of seed
        test_fn = self._fn_sym("test_my_func", "test_module.py")
        graph = _make_graph(tmp_path, edges=[
            Edge(kind=EdgeKind.CALLS, source_id="test_module.py::test_my_func", target_id="module.py::my_func"),
        ], symbols=[seed, test_fn])

        result = _build_seed_name_test_lines(seed, graph, "")
        # test_module.py is already in caller_basenames → no output
        assert result == []

    def test_caps_at_three_function_names(self, tmp_path):
        """When 5 test functions match, output shows first 3 + '+N more' suffix."""
        from tempograph.render.focused import _build_seed_name_test_lines

        seed = self._fn_sym("compute", "math.py")
        test_fns = [
            self._fn_sym(f"test_compute_{i}", "test_math.py", line_start=10 + i)
            for i in range(5)
        ]
        graph = _make_graph(tmp_path, edges=[], symbols=[seed] + test_fns)

        result = _build_seed_name_test_lines(seed, graph, "")
        assert result, "Expected a 'tests found:' line"
        line = result[0]
        assert "+2 more" in line, f"Expected '+2 more' cap indicator in: {line!r}"

# ---------------------------------------------------------------------------
# S48: _build_fan_out_line — cross-file call fan-out risk indicator
# ---------------------------------------------------------------------------

class TestFocusFanOutRisk:
    """Verify S48 fan-out risk indicator in focus mode (depth=0 seeds)."""

    def _fn_sym(self, name, file_path, line_start=1):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=line_start,
            line_end=line_start + 10,
            exported=True,
        )

    def _callee(self, name, file_path, line_start=1):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=line_start,
            line_end=line_start + 5,
            exported=True,
        )

    def test_high_fan_out_shown(self, tmp_path):
        """Function calling into 8 distinct external modules → 'fan-out: HIGH'."""
        from tempograph.render.focused import _build_fan_out_line

        seed = self._fn_sym("orchestrate", "core.py")
        callees = [self._callee(f"helper_{i}", f"mod_{i}.py") for i in range(8)]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id)
            for c in callees
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callees)

        result = _build_fan_out_line(seed, graph, "")
        assert result, "Expected fan-out line for 8 cross-file callees"
        assert "fan-out: HIGH" in result[0], f"Expected 'fan-out: HIGH'; got: {result[0]!r}"
        assert "8 modules" in result[0], f"Expected '8 modules' in: {result[0]!r}"

    def test_medium_fan_out_shown(self, tmp_path):
        """Function calling into 5 distinct external modules → 'fan-out: MEDIUM'."""
        from tempograph.render.focused import _build_fan_out_line

        seed = self._fn_sym("dispatch", "router.py")
        callees = [self._callee(f"handler_{i}", f"handler_{i}.py") for i in range(5)]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id)
            for c in callees
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callees)

        result = _build_fan_out_line(seed, graph, "")
        assert result, "Expected fan-out line for 5 cross-file callees"
        assert "fan-out: MEDIUM" in result[0], f"Expected 'fan-out: MEDIUM'; got: {result[0]!r}"
        assert "5 modules" in result[0], f"Expected '5 modules' in: {result[0]!r}"

    def test_low_fan_out_not_shown(self, tmp_path):
        """Function calling into 3 distinct files → no fan-out line (LOW = suppressed)."""
        from tempograph.render.focused import _build_fan_out_line

        seed = self._fn_sym("process", "worker.py")
        callees = [self._callee(f"util_{i}", f"util_{i}.py") for i in range(3)]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id)
            for c in callees
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callees)

        result = _build_fan_out_line(seed, graph, "")
        assert result == [], f"Expected no fan-out line for 3 cross-file callees; got: {result}"

    def test_test_function_skipped(self, tmp_path):
        """Function named test_* is suppressed even with high fan-out."""
        from tempograph.render.focused import _build_fan_out_line

        seed = self._fn_sym("test_orchestrate", "tests/test_core.py")
        callees = [self._callee(f"helper_{i}", f"mod_{i}.py") for i in range(10)]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id)
            for c in callees
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callees)

        result = _build_fan_out_line(seed, graph, "")
        assert result == [], f"Test function should not emit fan-out line; got: {result}"

    def test_same_file_calls_excluded(self, tmp_path):
        """Calls to symbols in the same file don't count toward module count."""
        from tempograph.render.focused import _build_fan_out_line

        seed = self._fn_sym("run", "app.py")
        # 3 external callees + 10 same-file callees
        ext_callees = [self._callee(f"ext_{i}", f"ext_{i}.py") for i in range(3)]
        same_callees = [self._callee(f"internal_{i}", "app.py", line_start=50 + i) for i in range(10)]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id)
            for c in ext_callees + same_callees
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + ext_callees + same_callees)

        result = _build_fan_out_line(seed, graph, "")
        # Only 3 external files — should be suppressed (LOW)
        assert result == [], (
            f"Same-file calls must not inflate module count; got: {result}"
        )
