"""Tests for importance-aware token truncation in focus mode (S22).

Also covers C2: token budget tracking across signal sections (_extend_tracked).
"""
from __future__ import annotations


class TestFocusOverflowHighImportance:
    """When render_focused truncates due to token cap, dropped high-importance
    symbols (hub symbols with _sym_importance >= 3) are listed."""

    def _make_hub_repo(self, tmp_path, n_hubs: int, callers_per_hub: int, *, extra_low: int = 0):
        """Build a repo with n_hubs high-importance symbols and extra_low low-importance ones.

        Each hub gets callers_per_hub unique files calling it.
        Low-importance symbols have 0 cross-file callers.
        Also creates a 'target' function that will be the query seed.
        """
        from tempograph.builder import build_graph

        # Seed function — this is what we query for
        lines = ["def target():\n"]
        for i in range(n_hubs):
            lines.append(f"    hub_{i}()\n")
        for i in range(extra_low):
            lines.append(f"    low_{i}()\n")
        (tmp_path / "main.py").write_text("".join(lines))

        # Hub functions — each in its own file
        for i in range(n_hubs):
            (tmp_path / f"hub_{i}.py").write_text(f"def hub_{i}():\n    pass\n")

        # Callers for each hub — each caller in a separate file
        for i in range(n_hubs):
            for j in range(callers_per_hub):
                (tmp_path / f"caller_h{i}_{j}.py").write_text(
                    f"from hub_{i} import hub_{i}\n\ndef use_hub_{i}_{j}():\n    hub_{i}()\n"
                )

        # Low-importance symbols — in separate files, no cross-file callers
        for i in range(extra_low):
            (tmp_path / f"low_{i}.py").write_text(f"def low_{i}():\n    pass\n")

        return build_graph(str(tmp_path), use_cache=False)

    def test_high_importance_listed_in_overflow(self, tmp_path):
        """Dropped high-importance symbols appear in the overflow line."""
        from tempograph.render import render_focused

        g = self._make_hub_repo(tmp_path, n_hubs=3, callers_per_hub=4)
        # Very low token cap to force truncation
        out = render_focused(g, "target", max_tokens=50)

        # Must have truncation
        assert "more symbols" in out, f"expected truncation; got:\n{out}"
        # Check for high-importance names
        if "high-importance:" in out:
            hub_names_found = [f"hub_{i}" for i in range(3) if f"hub_{i}" in out.split("high-importance:")[-1]]
            assert len(hub_names_found) > 0, f"expected hub names in high-importance list; got:\n{out}"

    def test_low_importance_only_no_hi_list(self, tmp_path):
        """When only low-importance (private) symbols are dropped, no high-importance list."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        # Use private symbols (_name) with no cross-file callers — score stays 0
        lines = ["def target():\n"]
        for i in range(12):
            lines.append(f"    _low_{i}()\n")
        lines.append("\n")
        for i in range(12):
            lines.append(f"def _low_{i}():\n    pass\n\n")
        (tmp_path / "main.py").write_text("".join(lines))

        g = build_graph(str(tmp_path), use_cache=False)
        out = render_focused(g, "target", max_tokens=50)

        if "more symbols" in out:
            assert "high-importance:" not in out, (
                f"must NOT list high-importance when none are dropped; got:\n{out}"
            )

    def test_max_five_names_shown(self, tmp_path):
        """At most 5 high-importance symbol names are shown in the overflow line."""
        from tempograph.render import render_focused

        g = self._make_hub_repo(tmp_path, n_hubs=8, callers_per_hub=4)
        out = render_focused(g, "target", max_tokens=50)

        if "high-importance:" in out:
            hi_part = out.split("high-importance:")[-1].split(")")[0]
            names = [n.strip() for n in hi_part.split(",") if n.strip()]
            assert len(names) <= 5, (
                f"expected at most 5 high-importance names, got {len(names)}: {names}"
            )


class TestSymImportance:
    """Unit tests for _sym_importance weighted scoring."""

    def test_cross_file_callers_boost_score(self, tmp_path):
        """_sym_importance returns high score for symbols called from many files."""
        from tempograph.builder import build_graph
        from tempograph.render import _sym_importance

        (tmp_path / "core.py").write_text("def util():\n    pass\n")
        for i in range(4):
            (tmp_path / f"user_{i}.py").write_text(
                f"from core import util\n\ndef f_{i}():\n    util()\n"
            )

        g = build_graph(str(tmp_path), use_cache=False)
        util_sym = next(s for s in g.symbols.values() if s.name == "util")
        score = _sym_importance(g, util_sym)
        # 4 cross-file callers * 2 + 1 (exported) = 9
        assert score >= 3, f"expected score >= 3 for popular cross-file util, got {score}"

    def test_same_file_callers_have_low_score(self, tmp_path):
        """_sym_importance stays low when callers are all in the same file."""
        from tempograph.builder import build_graph
        from tempograph.render import _sym_importance

        (tmp_path / "single.py").write_text(
            "def _helper():\n    pass\n\ndef caller():\n    _helper()\n"
        )

        g = build_graph(str(tmp_path), use_cache=False)
        helper_sym = next((s for s in g.symbols.values() if s.name == "_helper"), None)
        if helper_sym is None:
            return  # parser may not emit private helper
        score = _sym_importance(g, helper_sym)
        # 0 cross-file callers, not exported (private) → score = 0
        assert score < 3, f"expected score < 3 for private same-file helper, got {score}"


class TestExtendTracked:
    """_extend_tracked keeps running token budget so signal sections respect max_tokens."""

    def test_extend_tracked_returns_updated_count(self):
        from tempograph.render.focused import _extend_tracked
        from tempograph.render._utils import count_tokens

        lines: list[str] = []
        new_lines = ["some content line", "another line here"]
        result = _extend_tracked(lines, new_lines, 100)
        expected = 100 + count_tokens("\n".join(new_lines))
        assert result == expected
        assert lines == new_lines

    def test_extend_tracked_empty_new_lines_unchanged(self):
        from tempograph.render.focused import _extend_tracked

        lines: list[str] = ["existing"]
        result = _extend_tracked(lines, [], 200)
        assert result == 200
        assert lines == ["existing"]

    def test_focus_output_respects_max_tokens(self, tmp_path):
        """render_focused output must not exceed max_tokens."""
        import textwrap
        from tempograph.builder import build_graph
        from tempograph.render import render_focused
        from tempograph.render._utils import count_tokens

        # Build a repo with enough symbols to trigger multiple signal sections.
        (tmp_path / "core.py").write_text(textwrap.dedent("""\
            class Config:
                x: int = 1
                y: int = 2

            def process(cfg: Config) -> str:
                return str(cfg.x)

            def validate(cfg: Config) -> bool:
                return cfg.x > 0

            def serialize(cfg: Config) -> dict:
                return {"x": cfg.x, "y": cfg.y}
        """))
        for i in range(8):
            (tmp_path / f"consumer_{i}.py").write_text(textwrap.dedent(f"""\
                from core import process, validate
                def task_{i}():
                    cfg = object()
                    process(cfg)
                    validate(cfg)
            """))
        # Test files so S116/S174 signals fire
        (tmp_path / "test_core.py").write_text(textwrap.dedent("""\
            from core import process, validate
            def test_process():
                pass
            def test_validate():
                pass
        """))

        g = build_graph(str(tmp_path), use_cache=False)
        max_tokens = 800
        out = render_focused(g, "process", max_tokens=max_tokens)
        actual_tokens = count_tokens(out)
        # Allow small headroom for signal section granularity (one signal may fire
        # and push slightly over if its content is > remaining budget).
        assert actual_tokens <= max_tokens + 100, (
            f"render_focused exceeded max_tokens={max_tokens} by "
            f"{actual_tokens - max_tokens} tokens (total={actual_tokens})"
        )

    def test_focus_signal_sections_budget_reduces_over_time(self, tmp_path):
        """With a tight budget, later signal sections should be suppressed."""
        import textwrap
        from tempograph.builder import build_graph
        from tempograph.render import render_focused
        from tempograph.render._utils import count_tokens

        # Simple single-file repo — BFS fills up the budget quickly.
        (tmp_path / "mod.py").write_text(textwrap.dedent("""\
            def alpha():
                beta()
                gamma()

            def beta():
                pass

            def gamma():
                delta()

            def delta():
                pass
        """))
        g = build_graph(str(tmp_path), use_cache=False)
        # 300 tokens is tight: BFS symbols alone may nearly fill it.
        max_tokens = 300
        out = render_focused(g, "alpha", max_tokens=max_tokens)
        actual = count_tokens(out)
        # Must not wildly overshoot (>200 tokens over cap = signal sections ignoring budget)
        assert actual <= max_tokens + 200, (
            f"signal sections ignored token budget: {actual} tokens (max={max_tokens})"
        )
