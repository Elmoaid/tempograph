"""BFS should prefer domain-specific callees over utility callees."""
import pytest
from tempograph.builder import build_graph
from tempograph.render import render_focused
from tempograph.render.focused import _bfs_expand, _is_utility_callee


class TestBFSUtilityDepriority:
    def test_domain_callee_appears_before_utility(self, tmp_path):
        """When BFS budget is limited, domain functions should appear before utilities."""
        (tmp_path / "logger.py").write_text("def log_message(msg): pass\n")
        (tmp_path / "auth.py").write_text("def validate_token(token): pass\n")
        (tmp_path / "handler.py").write_text(
            "from logger import log_message\n"
            "from auth import validate_token\n"
            "def handle_request(req):\n"
            "    log_message('processing')\n"
            "    return validate_token(req.token)\n"
        )
        # Make logger a hub (many callers = utility signal)
        for i in range(12):
            (tmp_path / f"mod_{i}.py").write_text(
                f"from logger import log_message\ndef fn_{i}(): log_message('x')\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        # Find seed symbol
        seed = next(s for s in g.symbols.values() if s.name == "handle_request")
        ordered, _ = _bfs_expand(g, [seed], {seed.file_path})
        # Extract depth-1 names in BFS order
        depth1_names = [s.name for s, d in ordered if d == 1]
        assert "validate_token" in depth1_names
        assert "log_message" in depth1_names
        vt_idx = depth1_names.index("validate_token")
        lm_idx = depth1_names.index("log_message")
        assert vt_idx < lm_idx, (
            f"Domain callee (validate_token) should appear before utility callee (log_message) "
            f"in BFS order, got {depth1_names}"
        )

    def test_is_utility_callee_detection(self, tmp_path):
        """_is_utility_callee correctly identifies hub symbols."""
        (tmp_path / "logger.py").write_text("def log_message(msg): pass\n")
        (tmp_path / "auth.py").write_text("def validate_token(token): pass\n")
        (tmp_path / "handler.py").write_text(
            "from logger import log_message\n"
            "from auth import validate_token\n"
            "def handle_request(req):\n"
            "    log_message('processing')\n"
            "    return validate_token(req.token)\n"
        )
        for i in range(12):
            (tmp_path / f"mod_{i}.py").write_text(
                f"from logger import log_message\ndef fn_{i}(): log_message('x')\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        log_sym = next(s for s in g.symbols.values() if s.name == "log_message")
        vt_sym = next(s for s in g.symbols.values() if s.name == "validate_token")
        assert _is_utility_callee(log_sym, g) is True, "log_message with 12+ cross-file callers should be utility"
        assert _is_utility_callee(vt_sym, g) is False, "validate_token with 1 caller should not be utility"

    def test_utility_callee_still_appears_with_budget(self, tmp_path):
        """Utility callees should still appear when there's enough budget."""
        (tmp_path / "logger.py").write_text("def log_message(msg): pass\n")
        (tmp_path / "handler.py").write_text(
            "from logger import log_message\n"
            "def handle(req):\n"
            "    log_message('processing')\n"
        )
        for i in range(12):
            (tmp_path / f"mod_{i}.py").write_text(
                f"from logger import log_message\ndef fn_{i}(): log_message('x')\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        out = render_focused(g, "handle", max_tokens=4000)
        # With ample budget, utility callees should still appear
        assert "log_message" in out or "logger" in out


class TestDirectionalPriority:
    def test_more_callers_than_callees_shown(self, tmp_path):
        """Depth-0 should show more callers (consumers) than callees (dependencies)."""
        # Create a function with 10 callers and 10 callees
        callees = "\n".join(f"from dep_{i} import d{i}" for i in range(10))
        calls = "; ".join(f"d{i}()" for i in range(10))
        (tmp_path / "target.py").write_text(
            f"{callees}\n"
            f"def target():\n"
            f"    {calls}\n"
        )
        for i in range(10):
            (tmp_path / f"dep_{i}.py").write_text(f"def d{i}(): pass\n")
        for i in range(10):
            (tmp_path / f"caller_{i}.py").write_text(
                f"from target import target\ndef c{i}(): target()\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        out = render_focused(g, "target", max_tokens=2000)
        # Count how many callers vs callees appear in output
        caller_count = sum(1 for i in range(10) if f"c{i}" in out)
        callee_count = sum(1 for i in range(10) if f" d{i}" in out)
        # With directional priority, callers should get more representation
        assert caller_count >= callee_count, (
            f"Callers ({caller_count}) should >= callees ({callee_count})"
        )

    def test_depth0_budget_constants(self, tmp_path):
        """Depth-0 BFS should use 12 caller / 6 callee limits."""
        # Create a seed with exactly 12 callers and 8 callees
        callees = "\n".join(f"from dep_{i} import d{i}" for i in range(8))
        calls = "; ".join(f"d{i}()" for i in range(8))
        (tmp_path / "seed.py").write_text(
            f"{callees}\n"
            f"def seed():\n"
            f"    {calls}\n"
        )
        for i in range(8):
            (tmp_path / f"dep_{i}.py").write_text(f"def d{i}(): pass\n")
        for i in range(12):
            (tmp_path / f"caller_{i}.py").write_text(
                f"from seed import seed\ndef c{i}(): seed()\n"
            )
        g = build_graph(str(tmp_path), use_cache=False)
        seed_sym = next(s for s in g.symbols.values() if s.name == "seed")
        ordered, _ = _bfs_expand(g, [seed_sym], {seed_sym.file_path})
        # Depth-1 entries are the callers/callees of the seed
        depth1 = [s for s, d in ordered if d == 1]
        callers_at_d1 = [s for s in depth1 if s.name.startswith("c")]
        callees_at_d1 = [s for s in depth1 if s.name.startswith("d")]
        # With 12-caller/6-callee budget, should see more callers than callees
        assert len(callers_at_d1) > len(callees_at_d1), (
            f"Expected more callers ({len(callers_at_d1)}) than callees ({len(callees_at_d1)}) at depth 1"
        )
