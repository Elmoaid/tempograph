"""Tests for kit system: KitDefinition, execute_kit, list_kits, custom kit loading, MCP run_kit."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from tempograph.kits import (
    KitDefinition,
    _BUILTIN_KITS,
    _load_custom_kits,
    execute_kit,
    get_all_kits,
    list_kits,
)

REPO_PATH = str(REPO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_graph():
    """Build a real graph from tempograph's own repo (used as fixture)."""
    from tempograph.builder import build_graph
    return build_graph(REPO_PATH, exclude_dirs=["bench", "archive"])


# ---------------------------------------------------------------------------
# KitDefinition
# ---------------------------------------------------------------------------

class TestKitDefinition:
    def test_builtin_kits_exist(self):
        assert set(_BUILTIN_KITS.keys()) == {"explore", "deep_dive", "change_prep", "code_review", "health"}

    def test_each_kit_has_steps_and_description(self):
        for name, kit in _BUILTIN_KITS.items():
            assert kit.steps, f"{name} has no steps"
            assert kit.description, f"{name} has no description"

    def test_each_kit_weights_cover_all_steps(self):
        for name, kit in _BUILTIN_KITS.items():
            for step in kit.steps:
                assert step in kit.weights, f"{name} missing weight for step '{step}'"

    def test_explore_kit_steps(self):
        assert _BUILTIN_KITS["explore"].steps == ["overview", "hotspots"]

    def test_deep_dive_kit_steps(self):
        assert _BUILTIN_KITS["deep_dive"].steps == ["focus", "blast"]

    def test_code_review_kit_steps(self):
        assert _BUILTIN_KITS["code_review"].steps == ["dead", "hotspots", "focus"]

    def test_health_kit_steps(self):
        assert _BUILTIN_KITS["health"].steps == ["hotspots", "dead"]

    def test_change_prep_kit_steps(self):
        assert _BUILTIN_KITS["change_prep"].steps == ["diff", "focus"]


# ---------------------------------------------------------------------------
# execute_kit
# ---------------------------------------------------------------------------

class TestExecuteKit:
    @pytest.fixture(scope="class")
    def graph(self):
        return _minimal_graph()

    def test_explore_returns_non_empty(self, graph):
        out = execute_kit(graph, _BUILTIN_KITS["explore"], max_tokens=2000)
        assert out.strip()

    def test_explore_contains_section_headers(self, graph):
        out = execute_kit(graph, _BUILTIN_KITS["explore"], max_tokens=2000)
        assert "── OVERVIEW ──" in out
        assert "── HOTSPOTS ──" in out

    def test_deep_dive_with_query(self, graph):
        out = execute_kit(graph, _BUILTIN_KITS["deep_dive"], query="render_overview", max_tokens=2000)
        assert "── FOCUS ──" in out

    def test_health_kit(self, graph):
        out = execute_kit(graph, _BUILTIN_KITS["health"], max_tokens=2000)
        assert "── HOTSPOTS ──" in out
        assert "── DEAD ──" in out

    def test_token_budget_respected(self, graph):
        from tempograph.render import count_tokens
        out = execute_kit(graph, _BUILTIN_KITS["explore"], max_tokens=500)
        # Allow up to 20% slack (line-trim is approximate)
        assert count_tokens(out) <= 600

    def test_empty_steps_returns_empty(self, graph):
        empty_kit = KitDefinition(steps=[], weights={})
        assert execute_kit(graph, empty_kit) == ""

    def test_custom_single_step_kit(self, graph):
        kit = KitDefinition(steps=["overview"], weights={"overview": 1.0}, description="test")
        out = execute_kit(graph, kit, max_tokens=1000)
        assert "── OVERVIEW ──" in out

    def test_unknown_step_returns_error_inline(self, graph):
        kit = KitDefinition(steps=["nonexistent"], weights={"nonexistent": 1.0})
        out = execute_kit(graph, kit)
        assert "[unknown mode: nonexistent]" in out


# ---------------------------------------------------------------------------
# list_kits / get_all_kits
# ---------------------------------------------------------------------------

class TestListKits:
    def test_list_kits_returns_five_builtins(self):
        kits = list_kits()
        assert len(kits) == 5
        assert "explore" in kits
        assert "health" in kits

    def test_list_kits_values_are_strings(self):
        kits = list_kits()
        for name, desc in kits.items():
            assert isinstance(desc, str), f"{name} description is not str"
            assert desc  # non-empty

    def test_get_all_kits_no_repo(self):
        all_kits = get_all_kits()
        assert set(all_kits.keys()) == set(_BUILTIN_KITS.keys())


# ---------------------------------------------------------------------------
# Custom kit loading from .tempo/kits.json
# ---------------------------------------------------------------------------

class TestCustomKitLoading:
    def test_missing_file_returns_empty(self, tmp_path):
        result = _load_custom_kits(str(tmp_path))
        assert result == {}

    def test_malformed_json_returns_empty(self, tmp_path):
        kit_file = tmp_path / ".tempo" / "kits.json"
        kit_file.parent.mkdir()
        kit_file.write_text("{not valid json")
        result = _load_custom_kits(str(tmp_path))
        assert result == {}

    def test_valid_custom_kit_loaded(self, tmp_path):
        tempo_dir = tmp_path / ".tempo"
        tempo_dir.mkdir()
        kit_data = {
            "my_kit": {
                "steps": ["overview", "dead"],
                "weights": {"overview": 0.6, "dead": 0.4},
                "description": "My custom kit",
                "composition": "concat",
            }
        }
        (tempo_dir / "kits.json").write_text(json.dumps(kit_data))
        result = _load_custom_kits(str(tmp_path))
        assert "my_kit" in result
        assert result["my_kit"].steps == ["overview", "dead"]
        assert result["my_kit"].description == "My custom kit"

    def test_custom_kit_with_no_description(self, tmp_path):
        tempo_dir = tmp_path / ".tempo"
        tempo_dir.mkdir()
        kit_data = {"minimal_kit": {"steps": ["hotspots"]}}
        (tempo_dir / "kits.json").write_text(json.dumps(kit_data))
        result = _load_custom_kits(str(tmp_path))
        assert "minimal_kit" in result
        assert result["minimal_kit"].description == ""

    def test_kit_with_empty_steps_skipped(self, tmp_path):
        tempo_dir = tmp_path / ".tempo"
        tempo_dir.mkdir()
        kit_data = {"bad_kit": {"steps": []}}
        (tempo_dir / "kits.json").write_text(json.dumps(kit_data))
        result = _load_custom_kits(str(tmp_path))
        assert "bad_kit" not in result

    def test_custom_kit_uniform_weights_if_not_specified(self, tmp_path):
        tempo_dir = tmp_path / ".tempo"
        tempo_dir.mkdir()
        kit_data = {"auto_weight": {"steps": ["overview", "hotspots", "dead"]}}
        (tempo_dir / "kits.json").write_text(json.dumps(kit_data))
        result = _load_custom_kits(str(tmp_path))
        kit = result["auto_weight"]
        for step in kit.steps:
            assert abs(kit.weights[step] - 1.0 / 3) < 0.001

    def test_get_all_kits_merges_custom(self, tmp_path):
        tempo_dir = tmp_path / ".tempo"
        tempo_dir.mkdir()
        kit_data = {"custom_explore": {"steps": ["arch"], "description": "arch only"}}
        (tempo_dir / "kits.json").write_text(json.dumps(kit_data))
        all_kits = get_all_kits(str(tmp_path))
        assert "explore" in all_kits           # builtin preserved
        assert "custom_explore" in all_kits    # custom added

    def test_custom_kit_overrides_builtin_name(self, tmp_path):
        tempo_dir = tmp_path / ".tempo"
        tempo_dir.mkdir()
        kit_data = {"explore": {"steps": ["dead"], "description": "overridden"}}
        (tempo_dir / "kits.json").write_text(json.dumps(kit_data))
        all_kits = get_all_kits(str(tmp_path))
        assert all_kits["explore"].steps == ["dead"]


# ---------------------------------------------------------------------------
# MCP run_kit tool
# ---------------------------------------------------------------------------

class TestMcpRunKit:
    def test_run_kit_explore_returns_ok(self):
        from tempograph.server import run_kit
        out = run_kit(REPO_PATH, "explore", max_tokens=2000, output_format="json",
                      exclude_dirs="bench,archive")
        d = json.loads(out)
        assert d["status"] == "ok"
        assert "── OVERVIEW ──" in d["data"]
        assert d["tokens"] > 0

    def test_run_kit_list_returns_all_kits(self):
        from tempograph.server import run_kit
        out = run_kit(REPO_PATH, "list")
        assert "explore" in out
        assert "health" in out
        assert "deep_dive" in out

    def test_run_kit_list_json(self):
        from tempograph.server import run_kit
        out = run_kit(REPO_PATH, "list", output_format="json")
        d = json.loads(out)
        assert d["status"] == "ok"

    def test_run_kit_unknown_returns_error(self):
        from tempograph.server import run_kit
        out = run_kit(REPO_PATH, "nonexistent_kit", output_format="json")
        d = json.loads(out)
        assert d["status"] == "error"
        assert "INVALID_PARAMS" in d["code"]

    def test_run_kit_invalid_repo_returns_error(self):
        from tempograph.server import run_kit
        out = run_kit("/nonexistent/path", "explore", output_format="json")
        d = json.loads(out)
        assert d["status"] == "error"

    def test_run_kit_health_text_output(self):
        from tempograph.server import run_kit
        out = run_kit(REPO_PATH, "health", max_tokens=1500, exclude_dirs="bench,archive")
        assert "── HOTSPOTS ──" in out
        assert "── DEAD ──" in out

    def test_run_kit_deep_dive_with_query(self):
        from tempograph.server import run_kit
        out = run_kit(REPO_PATH, "deep_dive", query="render_focused", max_tokens=2000,
                      exclude_dirs="bench,archive")
        assert "── FOCUS ──" in out


# ---------------------------------------------------------------------------
# CLI --kit flag
# ---------------------------------------------------------------------------

class TestCliKitFlag:
    def test_kit_list_exits_zero(self):
        from tempograph.__main__ import main
        rc = main([REPO_PATH, "--kit", "list"])
        assert rc == 0

    def test_kit_explore_runs(self, capsys):
        from tempograph.__main__ import main
        rc = main([REPO_PATH, "--kit", "explore", "--max-tokens", "1000",
                   "--no-log", "--exclude", "bench,archive"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "── OVERVIEW ──" in captured.out

    def test_kit_unknown_exits_one(self, capsys):
        from tempograph.__main__ import main
        rc = main([REPO_PATH, "--kit", "does_not_exist", "--no-log"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Unknown kit" in captured.err
