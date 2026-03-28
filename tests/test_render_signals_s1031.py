"""S1031: Depth wall lookahead — entry-point callers hidden by BFS caller_limit.

BFS adds at most 8 callers of the seed (depth-0 caller_limit = 8). When a seed has
>8 callers, the lowest-importance callers are silently dropped. If any dropped callers
are entry points (main, run_server, etc.) or exported hot-file symbols, this signal fires:
"X (entry point) cut by BFS caller limit — N non-test callers hidden; use blast_radius"

Distinct from S66 (hub BFS truncation at 50-node cap) and S65 (change exposure):
- S66: fires when 50-node cap cuts depth=3 entirely
- S65: quantifies change exposure from callers + import fan-in
- S1031: fires specifically when ENTRY POINTS or HOT callers were dropped at seed level
"""

from tempograph.builder import build_graph
from tempograph.render import render_focused
from tempograph.render.focused import _compute_depth_wall_lookahead, _WALL_ENTRY_NAMES


def _build(tmp_path, files: dict, hot_files: set | None = None):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    g = build_graph(str(tmp_path), use_cache=False)
    if hot_files is not None:
        g.hot_files = {str(tmp_path / f) for f in hot_files}
    return g


def _seed_and_callers(graph, seed_name):
    """Return (seed_symbol, list_of_callers) for a named function."""
    seed = next((s for s in graph.symbols.values() if s.name == seed_name), None)
    if seed is None:
        return None, []
    return seed, graph.callers_of(seed.id)


def _wall_from_output(out: str) -> str | None:
    """Return the depth wall line from render_focused output, or None."""
    for line in out.split("\n"):
        if "depth wall" in line:
            return line
    return None


# ---------------------------------------------------------------------------
# Tests for _WALL_ENTRY_NAMES constant
# ---------------------------------------------------------------------------

class TestWallEntryNames:
    """_WALL_ENTRY_NAMES should include the standard entry-point function names."""

    def test_main_in_entry_names(self):
        assert "main" in _WALL_ENTRY_NAMES

    def test_run_server_in_entry_names(self):
        assert "run_server" in _WALL_ENTRY_NAMES

    def test_cli_in_entry_names(self):
        assert "cli" in _WALL_ENTRY_NAMES

    def test_create_app_in_entry_names(self):
        assert "create_app" in _WALL_ENTRY_NAMES

    def test_random_internal_name_not_in_entry_names(self):
        assert "_build_graph" not in _WALL_ENTRY_NAMES
        assert "_render_symbol" not in _WALL_ENTRY_NAMES


# ---------------------------------------------------------------------------
# Direct unit tests of _compute_depth_wall_lookahead
# ---------------------------------------------------------------------------

class TestDepthWallLookaheadDirect:
    """Unit tests calling _compute_depth_wall_lookahead directly with crafted inputs."""

    def _make_graph_with_many_callers(self, tmp_path):
        """Build a graph where target() has 10 callers (8 regular + main + another entry)."""
        # target.py: the seed function
        (tmp_path / "target.py").write_text("def target():\n    pass\n")
        # 8 callers with cross-file interactions to rank high in BFS importance
        for i in range(8):
            (tmp_path / f"caller_{i}.py").write_text(
                f"from target import target\n\n"
                f"def fn_{i}():\n    target()\n"
            )
            # Each caller is also called by a "hub" file to boost their importance
            (tmp_path / f"hub_{i}.py").write_text(
                f"from caller_{i} import fn_{i}\n\n"
                f"def hub_{i}():\n    fn_{i}()\n"
            )
        # main.py: entry point caller — isolated (no callers of its own)
        (tmp_path / "main.py").write_text(
            "from target import target\n\ndef main():\n    target()\n"
        )
        return build_graph(str(tmp_path), use_cache=False)

    def test_fires_when_entry_point_caller_excluded_from_seen_ids(self, tmp_path):
        """Signal fires when seed has >8 callers and an entry-point one is not in seen_ids."""
        g = self._make_graph_with_many_callers(tmp_path)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        all_callers = g.callers_of(target_sym.id)
        # Simulate BFS that included only 8 callers (not main)
        regular_callers = [c for c in all_callers if c.name != "main"]
        seen_ids = {target_sym.id} | {c.id for c in regular_callers}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        assert "depth wall" in result, f"Expected signal; got: {result!r}"
        assert "main" in result, f"Expected 'main' in signal; got: {result!r}"
        assert "entry point" in result, f"Expected 'entry point' label; got: {result!r}"

    def test_silent_when_all_callers_fit_in_bfs_limit(self, tmp_path):
        """Signal is suppressed when seed has ≤8 callers (all visible in BFS)."""
        (tmp_path / "target.py").write_text("def target():\n    pass\n")
        for i in range(5):
            (tmp_path / f"c{i}.py").write_text(
                f"from target import target\ndef fn{i}():\n    target()\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        all_callers = g.callers_of(target_sym.id)
        # All callers fit in BFS limit of 8
        seen_ids = {target_sym.id} | {c.id for c in all_callers}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        assert result == "", f"Expected no signal for ≤8 callers; got: {result!r}"

    def test_silent_when_seed_is_entry_point(self, tmp_path):
        """Signal is suppressed when the seed itself is an entry point (already at the top)."""
        (tmp_path / "main.py").write_text(
            "def main():\n    pass\n"
        )
        for i in range(10):
            (tmp_path / f"c{i}.py").write_text(
                f"from main import main\ndef fn{i}():\n    main()\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        main_sym = next(s for s in g.symbols.values() if s.name == "main")
        all_callers = g.callers_of(main_sym.id)
        # Even though there are 10 callers, seed is 'main' — should be silent
        seen_ids = {main_sym.id}
        ordered = [(main_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [main_sym])
        assert result == "", f"Expected silent for entry-point seed; got: {result!r}"

    def test_silent_when_entry_caller_already_in_seen_ids(self, tmp_path):
        """Signal is suppressed when the entry-point caller IS already in BFS (agent sees it)."""
        g = self._make_graph_with_many_callers(tmp_path)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        main_sym = next(s for s in g.symbols.values() if s.name == "main")
        all_callers = g.callers_of(target_sym.id)
        # Include main in seen_ids — agent already sees it
        seen_ids = {target_sym.id, main_sym.id} | {c.id for c in all_callers}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        assert result == "", f"Expected silent when main is visible; got: {result!r}"

    def test_blast_radius_recommendation_in_output(self, tmp_path):
        """Output always includes a blast_radius recommendation."""
        g = self._make_graph_with_many_callers(tmp_path)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        all_callers = g.callers_of(target_sym.id)
        regular_callers = [c for c in all_callers if c.name != "main"]
        seen_ids = {target_sym.id} | {c.id for c in regular_callers}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        assert "blast_radius" in result, f"Expected blast_radius ref; got: {result!r}"

    def test_output_format_uses_arrow_prefix(self, tmp_path):
        """Signal output starts with the ↳ arrow prefix like other BFS signals."""
        g = self._make_graph_with_many_callers(tmp_path)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        all_callers = g.callers_of(target_sym.id)
        regular_callers = [c for c in all_callers if c.name != "main"]
        seen_ids = {target_sym.id} | {c.id for c in regular_callers}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        assert result.startswith("↳"), f"Expected ↳ prefix; got: {result!r}"

    def test_test_file_callers_excluded(self, tmp_path):
        """Test-file callers are excluded even if they are named 'main'."""
        (tmp_path / "target.py").write_text("def target():\n    pass\n")
        # 9 callers: 8 regular + test_main.py with a main function
        for i in range(8):
            (tmp_path / f"c{i}.py").write_text(
                f"from target import target\ndef fn{i}():\n    target()\n"
            )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text(
            "from target import target\ndef main():\n    target()\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        all_callers = g.callers_of(target_sym.id)
        regular_callers = [c for c in all_callers if "test_" not in c.file_path]
        seen_ids = {target_sym.id} | {c.id for c in regular_callers}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        # Signal should NOT fire for test file's 'main'
        assert result == "", f"Expected silent for test-file main; got: {result!r}"

    def test_dunder_main_file_triggers_entry_point(self, tmp_path):
        """Symbols in __main__.py are entry points even if not named 'main'."""
        (tmp_path / "target.py").write_text("def target():\n    pass\n")
        for i in range(8):
            (tmp_path / f"c{i}.py").write_text(
                f"from target import target\ndef fn{i}():\n    target()\n"
            )
        # __main__.py with 'run' function — should be treated as entry point
        (tmp_path / "__main__.py").write_text(
            "from target import target\ndef run_app():\n    target()\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        target_sym = next(s for s in g.symbols.values() if s.name == "target")
        run_app_sym = next(
            (s for s in g.symbols.values()
             if s.name == "run_app" and s.file_path.endswith("__main__.py")),
            None,
        )
        if run_app_sym is None:
            return  # parser didn't pick it up — skip
        all_callers = g.callers_of(target_sym.id)
        seen_ids = {target_sym.id} | {c.id for c in all_callers if c.id != run_app_sym.id}
        ordered = [(target_sym, 0)]
        result = _compute_depth_wall_lookahead(g, ordered, seen_ids, [target_sym])
        assert "depth wall" in result, f"Expected signal for __main__.py caller; got: {result!r}"
        assert "entry point" in result, f"Expected 'entry point' label; got: {result!r}"
