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


# ---------------------------------------------------------------------------
# S49: _build_callees_block — callee complexity (cx) annotation at depth=0
# ---------------------------------------------------------------------------

class TestFocusCalleeCxAnnotation:
    """Verify S49 callee cx annotation in focus mode _build_callees_block (depth=0 only)."""

    def _fn_sym(self, name, file_path, cx=0, line_start=1):
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
            complexity=cx,
        )

    def test_high_cx_callee_annotated(self, tmp_path):
        """Callee with cx > 15 gets (cx=N) annotation at depth=0."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("dispatcher", "core.py", cx=3)
        callee = self._fn_sym("build_graph", "builder.py", cx=47)
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        calls_line = result[0]
        assert "(cx=47)" in calls_line, f"Expected '(cx=47)' in: {calls_line!r}"

    def test_low_cx_callee_not_annotated(self, tmp_path):
        """Callee with cx ≤ 15 does NOT get cx annotation (avoids noise)."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("process", "app.py", cx=5)
        callee = self._fn_sym("small_helper", "utils.py", cx=3)
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        calls_line = result[0]
        assert "(cx=" not in calls_line, f"Low-cx callee should not have cx annotation; got: {calls_line!r}"

    def test_zero_cx_callee_not_annotated(self, tmp_path):
        """Callee with cx=0 (unknown) does NOT get cx annotation."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("main", "main.py", cx=0)
        callee = self._fn_sym("run", "runner.py", cx=0)
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        assert "(cx=" not in result[0], f"Zero-cx callee should not have cx annotation; got: {result[0]!r}"

    def test_depth1_no_cx_annotation(self, tmp_path):
        """At depth=1, callees do NOT get cx annotation (depth=0 only)."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("child", "child.py", cx=2)
        callee = self._fn_sym("heavy_fn", "heavy.py", cx=60)
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 1, graph, "")
        assert result, "Expected calls line at depth=1"
        calls_line = result[0]
        assert "(cx=" not in calls_line, f"Depth=1 callee should not have cx annotation; got: {calls_line!r}"

    def test_class_callee_no_cx_annotation(self, tmp_path):
        """Class-kind callees do NOT get cx annotation (only function/method)."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("factory", "factory.py", cx=5)
        callee = Symbol(
            id="models.py::BigModel",
            name="BigModel",
            qualified_name="BigModel",
            kind=SymbolKind.CLASS,
            language=Language.PYTHON,
            file_path="models.py",
            line_start=1,
            line_end=200,
            exported=True,
            complexity=50,  # complex class, but not a function
        )
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        calls_line = result[0]
        assert "(cx=" not in calls_line, f"Class-kind callee should not have cx annotation; got: {calls_line!r}"


class TestCallerDomain:
    """Unit tests for _caller_domain helper (S50)."""

    def test_standard_module(self):
        from tempograph.render._utils import _caller_domain
        assert _caller_domain("tempograph/server.py") == "server"

    def test_subpackage_uses_second_level(self):
        from tempograph.render._utils import _caller_domain
        assert _caller_domain("tempograph/render/focused.py") == "render"

    def test_dunder_main_becomes_cli(self):
        from tempograph.render._utils import _caller_domain
        assert _caller_domain("tempograph/__main__.py") == "cli"

    def test_root_level_file(self):
        from tempograph.render._utils import _caller_domain
        assert _caller_domain("server.py") == "server"

    def test_ui_package(self):
        from tempograph.render._utils import _caller_domain
        assert _caller_domain("tempo/ui/src/App.tsx") == "ui"


class TestFocusCallerDomainDiversity:
    """S50: cross-cutting annotation when callers span 3+ distinct subsystems."""

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

    def _make_caller(self, name, file_path):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
            exported=True,
        )

    def test_cross_cutting_shown_when_3_domains(self, tmp_path):
        """Callers from server/, render/, and cli → 'cross-cutting' annotation."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn_sym("build_graph", "tempograph/builder.py")
        callers = [
            self._make_caller("focus_handler", "tempograph/server.py"),
            self._make_caller("main_cli", "tempograph/__main__.py"),
            self._make_caller("render_focused", "tempograph/render/focused.py"),
        ]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id)
            for c in callers
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        full = "\n".join(result)
        assert "cross-cutting" in full, f"Expected 'cross-cutting' for 3 distinct domains; got:\n{full}"
        assert "3 subsystems" in full, f"Expected '3 subsystems'; got:\n{full}"

    def test_cross_cutting_absent_when_same_domain(self, tmp_path):
        """All callers from same domain (render/) → no cross-cutting annotation."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn_sym("count_tokens", "tempograph/render/_utils.py")
        callers = [
            self._make_caller("render_focused", "tempograph/render/focused.py"),
            self._make_caller("render_diff", "tempograph/render/diff.py"),
            self._make_caller("render_blast", "tempograph/render/blast.py"),
        ]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id)
            for c in callers
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        full = "\n".join(result)
        assert "cross-cutting" not in full, f"Should not show cross-cutting for same domain; got:\n{full}"

    def test_cross_cutting_absent_at_depth1(self, tmp_path):
        """Cross-cutting annotation only fires at depth=0, not depth=1."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn_sym("helper", "tempograph/builder.py")
        callers = [
            self._make_caller("fn_a", "tempograph/server.py"),
            self._make_caller("fn_b", "tempograph/__main__.py"),
            self._make_caller("fn_c", "tempograph/render/focused.py"),
        ]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id)
            for c in callers
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 1, graph, [], {}, None, "")
        full = "\n".join(result)
        assert "cross-cutting" not in full, f"Depth=1 should not show cross-cutting; got:\n{full}"

    def test_two_domains_not_flagged(self, tmp_path):
        """Two distinct domains is not enough — need 3+ to fire."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn_sym("process", "tempograph/builder.py")
        callers = [
            self._make_caller("fn_a", "tempograph/server.py"),
            self._make_caller("fn_b", "tempograph/server.py"),  # same domain, different caller
            self._make_caller("fn_c", "tempograph/render/focused.py"),
        ]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id)
            for c in callers
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        full = "\n".join(result)
        assert "cross-cutting" not in full, f"2 domains should not trigger cross-cutting; got:\n{full}"


class TestKwCallersCap:
    """S32: kw_callers capped at 8 to prevent hub symbols generating unreadable caller lists."""

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

    def _make_caller(self, name, file_path):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
            exported=True,
        )

    def test_kw_callers_capped_at_8(self, tmp_path):
        """15 keyword-matching callers → only 8 shown + '+7 more' overflow."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn_sym("count_tokens", "tempograph/render/_utils.py")
        kw_callers = [
            self._make_caller(f"render_fn_{i}", "tempograph/render/focused.py")
            for i in range(15)
        ]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id)
            for c in kw_callers
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + kw_callers)

        result = _build_callers_block(seed, 0, graph, ["render"], {}, None, "")
        full = "\n".join(result)
        assert "called by:" in full
        # 15 total, 8 kw shown. shown_count = 8 + max_other(3) = 11, overflow = 15-11 = 4.
        assert "+4 more" in full, f"Expected '+4 more' overflow; got:\n{full}"
        shown_before_overflow = full.split("called by:")[1].split("+")[0]
        caller_count = shown_before_overflow.count("render_fn_")
        assert caller_count == 8, f"Expected 8 kw_callers shown, got {caller_count};\n{full}"

    def test_fewer_than_8_kw_callers_all_shown(self, tmp_path):
        """5 keyword callers → all 5 shown (under cap, no truncation)."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn_sym("helper", "tempograph/render/_utils.py")
        kw_callers = [
            self._make_caller(f"render_fn_{i}", "tempograph/render/focused.py")
            for i in range(5)
        ]
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id)
            for c in kw_callers
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + kw_callers)

        result = _build_callers_block(seed, 0, graph, ["render"], {}, None, "")
        full = "\n".join(result)
        assert "called by:" in full
        assert "more" not in full, f"Should not overflow with only 5 callers; got:\n{full}"
        caller_count = full.count("render_fn_")
        assert caller_count == 5, f"Expected 5 callers shown, got {caller_count};\n{full}"


# ---------------------------------------------------------------------------
# S51: _build_callees_block — sole-use callee annotation at depth=0
# ---------------------------------------------------------------------------

class TestFocusSoleUseCallee:
    """S51: callees that are only called from the seed get [sole-use] annotation."""

    def _fn_sym(self, name, file_path, cx=0, line_start=1):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=line_start,
            line_end=line_start + 10,
            exported=False,
            complexity=cx,
        )

    def test_sole_use_callee_annotated(self, tmp_path):
        """Callee with exactly one production caller (the seed) gets [sole-use]."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("process_data", "app.py")
        callee = self._fn_sym("_validate_internal", "helpers.py")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        calls_line = result[0]
        assert "[sole-use]" in calls_line, f"Expected '[sole-use]' for callee with 1 production caller; got: {calls_line!r}"

    def test_multi_caller_callee_not_annotated(self, tmp_path):
        """Callee called from multiple places does NOT get [sole-use]."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("process_data", "app.py")
        callee = self._fn_sym("shared_helper", "utils.py")
        other_caller = self._fn_sym("other_fn", "other.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=other_caller.id, target_id=callee.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, other_caller])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        calls_line = result[0]
        assert "[sole-use]" not in calls_line, f"Multi-caller callee should not get [sole-use]; got: {calls_line!r}"

    def test_sole_use_absent_at_depth1(self, tmp_path):
        """[sole-use] annotation only fires at depth=0, not depth=1."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("inner_fn", "inner.py")
        callee = self._fn_sym("only_mine", "private.py")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 1, graph, "")
        assert result, "Expected calls line at depth=1"
        calls_line = result[0]
        assert "[sole-use]" not in calls_line, f"Depth=1 should not show [sole-use]; got: {calls_line!r}"

    def test_test_caller_does_not_count_as_production_caller(self, tmp_path):
        """A callee called from seed + test file is still sole-use in production."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("compute", "app.py")
        callee = self._fn_sym("_inner_logic", "app.py")
        test_caller = self._fn_sym("test_compute", "tests/test_app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_caller.id, target_id=callee.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, test_caller])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        calls_line = result[0]
        assert "[sole-use]" in calls_line, f"Test caller should not count — callee should still be [sole-use]; got: {calls_line!r}"

    def test_no_callers_at_all_still_sole_use(self, tmp_path):
        """A callee with zero graph-recorded callers but called by seed: handled gracefully."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("entry", "main.py")
        callee = self._fn_sym("private_helper", "utils.py")
        # Edge only recorded as outgoing from seed (callee side may be uncaptured in real graphs)
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])

        result = _build_callees_block(seed, 0, graph, "")
        assert result, "Expected calls line"
        # callee has 1 production caller (seed) → sole-use
        assert "[sole-use]" in result[0], f"Expected [sole-use] for single-caller callee; got: {result[0]!r}"


class TestFocusHotCalleeInstability:
    """S52: emit instability warning when ≥2 non-test callees live in hot_files."""

    def _fn_sym(self, name, file_path):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=20,
            exported=False,
            complexity=0,
        )

    def test_no_hot_callees_no_instability(self, tmp_path):
        """No hot_files set → no instability line."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("run", "app.py")
        c1 = self._fn_sym("alpha", "mod_a.py")
        c2 = self._fn_sym("beta", "mod_b.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c1.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c2.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, c1, c2])
        # hot_files is empty (default)
        result = _build_callees_block(seed, 0, graph, "")
        assert len(result) == 1, "Only calls line, no instability"
        assert "instability" not in result[0]

    def test_one_hot_callee_no_instability(self, tmp_path):
        """1 hot callee → threshold not met, no instability line."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("run", "app.py")
        c1 = self._fn_sym("hot_one", "hot.py")
        c2 = self._fn_sym("cold_one", "cold.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c1.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c2.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, c1, c2])
        graph.hot_files = {"hot.py"}

        result = _build_callees_block(seed, 0, graph, "")
        assert len(result) == 1, "1 hot callee should not fire instability"
        assert "instability" not in result[0]

    def test_two_hot_callees_fires(self, tmp_path):
        """≥2 hot callees → instability line appears."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("run", "app.py")
        c1 = self._fn_sym("hot_a", "hot_a.py")
        c2 = self._fn_sym("hot_b", "hot_b.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c1.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c2.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, c1, c2])
        graph.hot_files = {"hot_a.py", "hot_b.py"}

        result = _build_callees_block(seed, 0, graph, "")
        assert len(result) == 2, f"Expected calls + instability lines; got {result!r}"
        instability_line = result[1]
        assert "instability" in instability_line
        assert "2 hot callees" in instability_line
        assert "hot_a" in instability_line
        assert "hot_b" in instability_line

    def test_four_hot_callees_truncated(self, tmp_path):
        """4 hot callees → shows 3 names then '...'."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("run", "app.py")
        callees = [self._fn_sym(f"fn_{i}", f"hot_{i}.py") for i in range(4)]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id) for c in callees]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callees)
        graph.hot_files = {f"hot_{i}.py" for i in range(4)}

        result = _build_callees_block(seed, 0, graph, "")
        assert len(result) == 2
        instability_line = result[1]
        assert "4 hot callees" in instability_line
        assert "..." in instability_line

    def test_test_file_callee_not_counted(self, tmp_path):
        """Test file callees don't count toward instability threshold."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("run", "app.py")
        hot_prod = self._fn_sym("real_dep", "hot_prod.py")
        hot_test = self._fn_sym("test_helper", "tests/test_hot.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hot_prod.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hot_test.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, hot_prod, hot_test])
        graph.hot_files = {"hot_prod.py", "tests/test_hot.py"}

        result = _build_callees_block(seed, 0, graph, "")
        # Only 1 non-test hot callee → no instability
        assert len(result) == 1, f"Test callee must not count; got {result!r}"
        assert "instability" not in result[0]

    def test_depth1_no_instability(self, tmp_path):
        """Instability warning only fires at depth=0."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("inner", "inner.py")
        c1 = self._fn_sym("dep_a", "hot_a.py")
        c2 = self._fn_sym("dep_b", "hot_b.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c1.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c2.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, c1, c2])
        graph.hot_files = {"hot_a.py", "hot_b.py"}

        result = _build_callees_block(seed, 1, graph, "")
        assert len(result) == 1, "depth=1 should not emit instability"
        assert "instability" not in result[0]


# S53: _build_callees_block — depth=1 hot-first callee ordering
# --------------------------------------------------------------


class TestFocusCalleeRecencyDepth1:
    """S53: depth=1 callees are ordered hot-first (recently-modified files surface to top)."""

    def _fn_sym(self, name, file_path):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=20,
            exported=False,
            complexity=0,
        )

    def test_hot_callee_first_at_depth1(self, tmp_path):
        """At depth=1 a hot callee sorts before a cold one."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("middleware", "mid.py")
        cold = self._fn_sym("stable_util", "utils.py")
        hot = self._fn_sym("new_parser", "parser.py")
        # cold is added to edges first — without ordering it would appear first
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=cold.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hot.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, cold, hot])
        graph.hot_files = {"parser.py"}

        result = _build_callees_block(seed, 1, graph, "")
        assert result, "should produce a calls line"
        calls_line = result[0]
        assert "new_parser" in calls_line
        assert "stable_util" in calls_line
        hot_pos = calls_line.index("new_parser")
        cold_pos = calls_line.index("stable_util")
        assert hot_pos < cold_pos, f"hot callee should precede cold at depth=1; line: {calls_line!r}"

    def test_hot_callees_get_hot_annotation_at_depth1(self, tmp_path):
        """Hot callees at depth=1 still receive the [hot] annotation."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("handler", "handler.py")
        hot_callee = self._fn_sym("fresh_fn", "fresh.py")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hot_callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, hot_callee])
        graph.hot_files = {"fresh.py"}

        result = _build_callees_block(seed, 1, graph, "")
        assert result, "should produce a calls line"
        assert "[hot]" in result[0], f"hot callee should carry [hot] annotation at depth=1; got {result[0]!r}"

    def test_multiple_hot_callees_all_precede_cold(self, tmp_path):
        """Multiple hot callees all appear before cold callees at depth=1."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("orchestrator", "orch.py")
        cold_a = self._fn_sym("old_helper", "old_a.py")
        cold_b = self._fn_sym("legacy_fn", "old_b.py")
        hot_a = self._fn_sym("new_loader", "hot_a.py")
        hot_b = self._fn_sym("new_writer", "hot_b.py")
        # cold callees added to edges first
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=cold_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=cold_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hot_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hot_b.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, cold_a, cold_b, hot_a, hot_b])
        graph.hot_files = {"hot_a.py", "hot_b.py"}

        result = _build_callees_block(seed, 1, graph, "")
        calls_line = result[0]
        hot_a_pos = calls_line.index("new_loader")
        hot_b_pos = calls_line.index("new_writer")
        cold_a_pos = calls_line.index("old_helper")
        cold_b_pos = calls_line.index("legacy_fn")
        assert hot_a_pos < cold_a_pos, "hot_a should precede cold_a"
        assert hot_b_pos < cold_a_pos, "hot_b should precede cold_a"
        assert hot_a_pos < cold_b_pos, "hot_a should precede cold_b"
        assert hot_b_pos < cold_b_pos, "hot_b should precede cold_b"

    def test_no_hot_files_order_unchanged(self, tmp_path):
        """With empty hot_files all callees stay in insertion order at depth=1."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("root_fn", "root.py")
        c1 = self._fn_sym("alpha_fn", "alpha.py")
        c2 = self._fn_sym("beta_fn", "beta.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c1.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c2.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, c1, c2])
        # hot_files intentionally empty

        result = _build_callees_block(seed, 1, graph, "")
        calls_line = result[0]
        assert "alpha_fn" in calls_line
        assert "beta_fn" in calls_line
        assert "[hot]" not in calls_line, "no [hot] annotations when hot_files is empty"


class TestFocusRecursiveCallee:
    """S54: [recursive] annotation and summary line when a depth-0 seed calls itself."""

    def _fn_sym(self, name, file_path="mod.py"):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=20,
            exported=False,
            complexity=0,
        )

    def test_self_call_gets_recursive_annotation(self, tmp_path):
        """A callee that is the seed itself gets [recursive] in the calls line."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("recurse")
        other = self._fn_sym("helper", "util.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=seed.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=other.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, other])
        result = _build_callees_block(seed, 0, graph, "")
        calls_line = result[0]
        assert "[recursive]" in calls_line, f"Expected [recursive] annotation; got: {calls_line!r}"
        assert "recurse" in calls_line

    def test_recursive_summary_line_appears(self, tmp_path):
        """A recursive seed emits a ↳ recursive summary line at depth=0."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("depth_search")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=seed.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed])
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "recursive" in joined, f"Expected recursive summary line; got:\n{joined}"
        assert "base case" in joined, f"Expected base case hint; got:\n{joined}"

    def test_non_recursive_callee_no_annotation(self, tmp_path):
        """A callee with a different id gets no [recursive] annotation."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("orchestrate")
        callee = self._fn_sym("worker", "worker.py")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee])
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "[recursive]" not in joined
        assert "recursive" not in joined

    def test_recursive_annotation_absent_at_depth1(self, tmp_path):
        """At depth=1 a self-calling callee does NOT receive [recursive] annotation."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("inner")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=seed.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed])
        result = _build_callees_block(seed, 1, graph, "")
        joined = "\n".join(result)
        assert "[recursive]" not in joined, f"No [recursive] at depth=1; got:\n{joined}"
        assert "base case" not in joined

    def test_recursive_summary_absent_at_depth1(self, tmp_path):
        """The ↳ recursive summary line is suppressed at depth=1."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("recurse_shallow")
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=seed.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed])
        result = _build_callees_block(seed, 1, graph, "")
        assert all("recursive" not in line for line in result), f"No summary at depth=1; got:\n{result}"


class TestFocusUntestedCallee:
    """S55: [untested] annotation on callees with zero test callers when seed is tested."""

    def _fn_sym(self, name, file_path="mod.py"):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
            exported=False,
            complexity=0,
        )

    def test_untested_callee_annotated_when_seed_is_tested(self, tmp_path):
        """Callee with no test callers gets [untested] when seed has test callers."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("orchestrate", "app.py")
        callee = self._fn_sym("helper", "util.py")
        test_fn = self._fn_sym("test_orchestrate", "tests/test_app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_fn.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, test_fn])
        result = _build_callees_block(seed, 0, graph, "")
        calls_line = result[0]
        assert "[untested]" in calls_line, f"Expected [untested]; got: {calls_line!r}"

    def test_tested_callee_not_annotated(self, tmp_path):
        """Callee that has its own test callers is NOT annotated [untested]."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("orchestrate", "app.py")
        callee = self._fn_sym("helper", "util.py")
        test_seed = self._fn_sym("test_orchestrate", "tests/test_app.py")
        test_callee = self._fn_sym("test_helper", "tests/test_util.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_seed.id, target_id=seed.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_callee.id, target_id=callee.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, test_seed, test_callee])
        result = _build_callees_block(seed, 0, graph, "")
        calls_line = result[0]
        assert "[untested]" not in calls_line, f"Callee has tests, no [untested] expected; got: {calls_line!r}"

    def test_no_annotation_when_seed_has_no_test_callers(self, tmp_path):
        """[untested] is suppressed when seed itself has no test callers — signal would be noise."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("internal", "lib.py")
        callee = self._fn_sym("sub_helper", "lib.py")
        caller = self._fn_sym("main", "app.py")  # production caller only
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=caller.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, caller])
        result = _build_callees_block(seed, 0, graph, "")
        calls_line = result[0]
        assert "[untested]" not in calls_line, f"No [untested] when seed untested; got: {calls_line!r}"

    def test_untested_absent_at_depth1(self, tmp_path):
        """[untested] is never emitted at depth=1 (only depth=0 matters)."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("inner", "mod.py")
        callee = self._fn_sym("leaf", "util.py")
        test_fn = self._fn_sym("test_inner", "tests/test_mod.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_fn.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, test_fn])
        result = _build_callees_block(seed, 1, graph, "")
        joined = "\n".join(result)
        assert "[untested]" not in joined, f"[untested] must not appear at depth=1; got:\n{joined}"

    def test_class_callee_not_annotated_untested(self, tmp_path):
        """Class callees are never annotated [untested] — only function/method kind."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("factory", "app.py")
        class_sym = Symbol(
            id="models.py::MyModel",
            name="MyModel",
            qualified_name="MyModel",
            kind=SymbolKind.CLASS,
            language=Language.PYTHON,
            file_path="models.py",
            line_start=1,
            line_end=20,
            exported=True,
            complexity=0,
        )
        test_fn = self._fn_sym("test_factory", "tests/test_app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=class_sym.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_fn.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, class_sym, test_fn])
        result = _build_callees_block(seed, 0, graph, "")
        calls_line = result[0]
        assert "[untested]" not in calls_line, f"Class callees get no [untested]; got: {calls_line!r}"


class TestFocusCoverageGap:
    """S56: ↳ coverage gap summary line when ≥2 eligible callees have zero test callers."""

    def _fn_sym(self, name, file_path="mod.py"):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
            exported=False,
            complexity=0,
        )

    def test_coverage_gap_fires_when_two_untested_callees(self, tmp_path):
        """Summary line appears when ≥2 eligible callees have zero test callers."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("dispatch", "app.py")
        callee_a = self._fn_sym("parse_config", "config.py")
        callee_b = self._fn_sym("load_data", "loader.py")
        test_seed = self._fn_sym("test_dispatch", "tests/test_app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_seed.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee_a, callee_b, test_seed])
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "coverage gap" in joined, f"Expected coverage gap summary; got:\n{joined}"
        assert "2/2 callees untested" in joined, f"Expected 2/2 ratio; got:\n{joined}"

    def test_coverage_gap_silent_when_only_one_untested_callee(self, tmp_path):
        """Threshold is ≥2 — a single untested callee is already annotated, no summary needed."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("handler", "app.py")
        callee = self._fn_sym("do_thing", "util.py")
        test_seed = self._fn_sym("test_handler", "tests/test_app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_seed.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee, test_seed])
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "coverage gap" not in joined, f"Single untested callee must not emit summary; got:\n{joined}"

    def test_coverage_gap_excludes_tested_callees_from_numerator(self, tmp_path):
        """Callee with its own test is counted in denominator but not numerator."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("process", "app.py")
        callee_tested = self._fn_sym("validated", "core.py")
        callee_untested_a = self._fn_sym("raw_parse", "io.py")
        callee_untested_b = self._fn_sym("write_out", "io.py")
        test_seed = self._fn_sym("test_process", "tests/test_app.py")
        test_callee = self._fn_sym("test_validated", "tests/test_core.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_tested.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_untested_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_untested_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_seed.id, target_id=seed.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_callee.id, target_id=callee_tested.id),
        ]
        graph = _make_graph(
            tmp_path,
            edges=edges,
            symbols=[seed, callee_tested, callee_untested_a, callee_untested_b, test_seed, test_callee],
        )
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "coverage gap" in joined, f"Expected coverage gap; got:\n{joined}"
        # 2 untested out of 3 eligible (tested callee is in denominator only)
        assert "2/3 callees untested" in joined, f"Expected 2/3 ratio; got:\n{joined}"

    def test_coverage_gap_absent_at_depth1(self, tmp_path):
        """Coverage gap summary is depth=0 only."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("inner", "mod.py")
        callee_a = self._fn_sym("leaf_a", "util.py")
        callee_b = self._fn_sym("leaf_b", "util.py")
        test_fn = self._fn_sym("test_inner", "tests/test_mod.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=test_fn.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee_a, callee_b, test_fn])
        result = _build_callees_block(seed, 1, graph, "")
        joined = "\n".join(result)
        assert "coverage gap" not in joined, f"No coverage gap at depth=1; got:\n{joined}"

    def test_coverage_gap_absent_when_seed_untested(self, tmp_path):
        """When seed has no test callers, coverage gap is suppressed — same guard as [untested]."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("internal_fn", "lib.py")
        callee_a = self._fn_sym("sub_a", "lib.py")
        callee_b = self._fn_sym("sub_b", "lib.py")
        prod_caller = self._fn_sym("main", "app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=prod_caller.id, target_id=seed.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, callee_a, callee_b, prod_caller])
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "coverage gap" not in joined, f"Coverage gap must not fire when seed is untested; got:\n{joined}"

    def test_coverage_gap_names_with_ellipsis_on_overflow(self, tmp_path):
        """When >3 untested callees, names list truncates with '...'."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn_sym("hub", "app.py")
        callees = [self._fn_sym(f"helper_{i}", f"util_{i}.py") for i in range(5)]
        test_seed = self._fn_sym("test_hub", "tests/test_app.py")
        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=c.id) for c in callees
        ] + [Edge(kind=EdgeKind.CALLS, source_id=test_seed.id, target_id=seed.id)]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callees + [test_seed])
        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "coverage gap" in joined, f"Expected coverage gap; got:\n{joined}"
        assert "..." in joined, f"Expected ellipsis for overflow names; got:\n{joined}"


class TestFocusPrimaryCallerConcentration:
    """S57: primary caller annotation when one file owns ≥60% of callers (and total ≥4)."""

    def _fn(self, name, file_path):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
            exported=True,
        )

    def test_fires_when_one_file_dominates(self, tmp_path):
        """3/4 callers from server.py (75%) → primary caller fires."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn("auth_check", "tempograph/auth.py")
        callers = [
            self._fn("handle_login", "tempograph/server.py"),
            self._fn("handle_refresh", "tempograph/server.py"),
            self._fn("handle_logout", "tempograph/server.py"),
            self._fn("cli_login", "tempograph/__main__.py"),
        ]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id) for c in callers]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        joined = "\n".join(result)
        assert "primary caller" in joined, f"Expected 'primary caller'; got:\n{joined}"
        assert "server.py" in joined, f"Expected 'server.py' in primary caller; got:\n{joined}"
        assert "3/4" in joined, f"Expected '3/4' count; got:\n{joined}"

    def test_fires_at_exact_60pct_threshold(self, tmp_path):
        """3/5 = 60% from one file → fires at exact boundary."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn("validate", "tempograph/core.py")
        callers = [
            self._fn("fn_a", "tempograph/server.py"),
            self._fn("fn_b", "tempograph/server.py"),
            self._fn("fn_c", "tempograph/server.py"),
            self._fn("fn_d", "tempograph/render/focused.py"),
            self._fn("fn_e", "tempograph/__main__.py"),
        ]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id) for c in callers]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        joined = "\n".join(result)
        assert "primary caller" in joined, f"Expected primary caller at 60%; got:\n{joined}"
        assert "3/5" in joined, f"Expected '3/5'; got:\n{joined}"

    def test_absent_when_below_threshold(self, tmp_path):
        """2/4 = 50% — below 60% threshold → no primary caller annotation."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn("process", "tempograph/core.py")
        callers = [
            self._fn("fn_a", "tempograph/server.py"),
            self._fn("fn_b", "tempograph/server.py"),
            self._fn("fn_c", "tempograph/__main__.py"),
            self._fn("fn_d", "tempograph/render/focused.py"),
        ]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id) for c in callers]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        joined = "\n".join(result)
        assert "primary caller" not in joined, f"Should not fire at 50%; got:\n{joined}"

    def test_absent_when_total_below_minimum(self, tmp_path):
        """3/3 = 100% but total < 4 → no primary caller (signal only meaningful at scale)."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn("helper", "tempograph/utils.py")
        callers = [
            self._fn("fn_a", "tempograph/server.py"),
            self._fn("fn_b", "tempograph/server.py"),
            self._fn("fn_c", "tempograph/server.py"),
        ]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id) for c in callers]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        joined = "\n".join(result)
        assert "primary caller" not in joined, f"Should not fire when total < 4; got:\n{joined}"

    def test_absent_when_dominant_is_same_file_as_seed(self, tmp_path):
        """All callers from seed's own file (private helper) → no primary caller annotation."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn("_format_line", "tempograph/render/focused.py")
        callers = [
            self._fn("render_fn_a", "tempograph/render/focused.py"),
            self._fn("render_fn_b", "tempograph/render/focused.py"),
            self._fn("render_fn_c", "tempograph/render/focused.py"),
            self._fn("render_fn_d", "tempograph/render/focused.py"),
        ]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id) for c in callers]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 0, graph, [], {}, None, "")
        joined = "\n".join(result)
        assert "primary caller" not in joined, f"Should not fire for same-file private helper; got:\n{joined}"

    def test_absent_at_depth1(self, tmp_path):
        """Primary caller only fires at depth=0."""
        from tempograph.render.focused import _build_callers_block

        seed = self._fn("auth_check", "tempograph/auth.py")
        callers = [
            self._fn("fn_a", "tempograph/server.py"),
            self._fn("fn_b", "tempograph/server.py"),
            self._fn("fn_c", "tempograph/server.py"),
            self._fn("fn_d", "tempograph/__main__.py"),
        ]
        edges = [Edge(kind=EdgeKind.CALLS, source_id=c.id, target_id=seed.id) for c in callers]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed] + callers)

        result = _build_callers_block(seed, 1, graph, [], {}, None, "")
        joined = "\n".join(result)
        assert "primary caller" not in joined, f"Should not fire at depth=1; got:\n{joined}"


class TestFocusOrphanCascade:
    """S58: orphan cascade annotation when sole-use callees themselves have sole-use sub-callees."""

    def _fn(self, name, file_path, kind=None):
        return Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=kind or SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
            exported=True,
        )

    def test_fires_when_sole_use_callees_have_sub_callees(self, tmp_path):
        """Seed → A [sole-use] → B, C, D [sole-use of A] → orphan cascade fires."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn("orchestrate", "app/main.py")
        helper_a = self._fn("_step_a", "app/main.py")
        sub_b = self._fn("_sub_b", "app/main.py")
        sub_c = self._fn("_sub_c", "app/main.py")
        sub_d = self._fn("_sub_d", "app/main.py")

        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=helper_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=helper_a.id, target_id=sub_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=helper_a.id, target_id=sub_c.id),
            Edge(kind=EdgeKind.CALLS, source_id=helper_a.id, target_id=sub_d.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, helper_a, sub_b, sub_c, sub_d])

        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "orphan cascade" in joined, f"Expected orphan cascade; got:\n{joined}"
        # 1 direct (helper_a) + 3 transitive (sub_b, sub_c, sub_d) = 4
        assert "4 private" in joined, f"Expected '4 private'; got:\n{joined}"

    def test_fires_when_multiple_sole_use_callees_cascade(self, tmp_path):
        """Seed → A [sole-use] → B, C and D [sole-use] → E, F → cascade from two hubs."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn("build_report", "reports/builder.py")
        hub_a = self._fn("_gather_data", "reports/builder.py")
        sub_b = self._fn("_fetch_raw", "reports/builder.py")
        sub_c = self._fn("_clean_raw", "reports/builder.py")
        hub_d = self._fn("_format_output", "reports/builder.py")
        sub_e = self._fn("_to_html", "reports/builder.py")
        sub_f = self._fn("_to_csv", "reports/builder.py")

        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hub_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=hub_d.id),
            Edge(kind=EdgeKind.CALLS, source_id=hub_a.id, target_id=sub_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=hub_a.id, target_id=sub_c.id),
            Edge(kind=EdgeKind.CALLS, source_id=hub_d.id, target_id=sub_e.id),
            Edge(kind=EdgeKind.CALLS, source_id=hub_d.id, target_id=sub_f.id),
        ]
        graph = _make_graph(
            tmp_path, edges=edges,
            symbols=[seed, hub_a, sub_b, sub_c, hub_d, sub_e, sub_f]
        )

        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "orphan cascade" in joined, f"Expected orphan cascade; got:\n{joined}"
        # 2 direct (hub_a, hub_d) + 4 transitive = 6
        assert "6 private" in joined, f"Expected '6 private'; got:\n{joined}"

    def test_absent_when_sole_use_callee_has_only_one_transitive(self, tmp_path):
        """Seed → A [sole-use] → B [sole-use of A] only — cascade <2 transitive, no fire."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn("run_check", "app/checks.py")
        helper_a = self._fn("_check_step", "app/checks.py")
        sub_b = self._fn("_validate_step", "app/checks.py")

        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=helper_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=helper_a.id, target_id=sub_b.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, helper_a, sub_b])

        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "orphan cascade" not in joined, f"Should not fire with 1 transitive; got:\n{joined}"

    def test_absent_when_no_sole_use_callees(self, tmp_path):
        """Callees with multiple callers → no [sole-use] → no cascade."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn("dispatch", "app/router.py")
        other = self._fn("also_calls", "app/other.py")
        callee = self._fn("handle_request", "app/handler.py")

        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=callee.id),
            Edge(kind=EdgeKind.CALLS, source_id=other.id, target_id=callee.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, other, callee])

        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "orphan cascade" not in joined, f"Should not fire when callee has multiple callers; got:\n{joined}"

    def test_absent_at_depth1(self, tmp_path):
        """Orphan cascade only fires at depth=0."""
        from tempograph.render.focused import _build_callees_block

        seed = self._fn("orchestrate", "app/main.py")
        helper_a = self._fn("_step_a", "app/main.py")
        sub_b = self._fn("_sub_b", "app/main.py")
        sub_c = self._fn("_sub_c", "app/main.py")

        edges = [
            Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=helper_a.id),
            Edge(kind=EdgeKind.CALLS, source_id=helper_a.id, target_id=sub_b.id),
            Edge(kind=EdgeKind.CALLS, source_id=helper_a.id, target_id=sub_c.id),
        ]
        graph = _make_graph(tmp_path, edges=edges, symbols=[seed, helper_a, sub_b, sub_c])

        result = _build_callees_block(seed, 1, graph, "")
        joined = "\n".join(result)
        assert "orphan cascade" not in joined, f"Should not fire at depth=1; got:\n{joined}"

    def test_transitive_chain_includes_out_of_view_callees(self, tmp_path):
        """Cascade counts sole-use callees beyond the 8 displayed — total reflects true chain."""
        from tempograph.render.focused import _build_callees_block

        # Seed has 9 sole-use callees (exceeds shown=8), each of the first 3 has 2 sole-use sub-callees
        seed = self._fn("heavy_orchestrator", "app/main.py")
        helpers = [self._fn(f"_h{i}", "app/main.py") for i in range(9)]
        sub_callees = []
        edges = [Edge(kind=EdgeKind.CALLS, source_id=seed.id, target_id=h.id) for h in helpers]
        # First 3 helpers each get 2 sole-use sub-callees
        for i in range(3):
            for j in range(2):
                sub = self._fn(f"_sub_{i}_{j}", "app/main.py")
                sub_callees.append(sub)
                edges.append(Edge(kind=EdgeKind.CALLS, source_id=helpers[i].id, target_id=sub.id))

        all_syms = [seed] + helpers + sub_callees
        graph = _make_graph(tmp_path, edges=edges, symbols=all_syms)

        result = _build_callees_block(seed, 0, graph, "")
        joined = "\n".join(result)
        assert "orphan cascade" in joined, f"Should fire even with callees beyond shown=8; got:\n{joined}"
        # 9 direct + 6 transitive = 15
        assert "15 private" in joined, f"Expected '15 private' (9+6); got:\n{joined}"
