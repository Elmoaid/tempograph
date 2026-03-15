"""Tests for tempo kernel: registry, builder, graph, and core plugins."""
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# Ensure we import the tempo package from this repo
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_discovers_plugins():
    from tempo.kernel.registry import Registry
    r = Registry()
    r.discover()
    assert len(r.plugins) >= 10, "Expected at least 10 plugins"
    assert len(r.enabled) == len(r.plugins), "All default plugins should be enabled"


def test_registry_known_plugins():
    from tempo.kernel.registry import Registry
    r = Registry()
    r.discover()
    for name in ("overview", "focus", "blast", "dead_code", "hotspots"):
        assert name in r.plugins, f"Plugin '{name}' missing"


def test_registry_mode_map():
    from tempo.kernel.registry import Registry
    r = Registry()
    r.discover()
    resolved = r.resolve_mode("overview")
    assert resolved == "overview"
    runner = r.get_runner("overview")
    assert runner is not None


def test_registry_disable_enable():
    from tempo.kernel.registry import Registry
    r = Registry()
    r.discover()
    # Find a plugin no other plugin depends on
    deps_of_all = {d for p in r.plugins.values() for d in p.depends}
    leaf = next(n for n in r.plugins if n not in deps_of_all)
    success, _ = r.disable(leaf)
    assert success
    assert leaf not in r.enabled
    r.enable(leaf)
    assert leaf in r.enabled


# ---------------------------------------------------------------------------
# Builder + graph
# ---------------------------------------------------------------------------

@pytest.fixture()
def tiny_repo(tmp_path):
    """Create a minimal Python repo for graph tests."""
    (tmp_path / "main.py").write_text(textwrap.dedent("""\
        from utils import helper

        def main():
            helper()

        if __name__ == "__main__":
            main()
    """))
    (tmp_path / "utils.py").write_text(textwrap.dedent("""\
        def helper():
            return 42
    """))
    return tmp_path


def test_build_graph_returns_tempo(tiny_repo):
    from tempo.kernel.builder import build_graph
    g = build_graph(str(tiny_repo))
    assert g is not None
    assert g.stats["symbols"] > 0
    assert g.stats["files"] > 0


def test_build_graph_finds_symbols(tiny_repo):
    from tempo.kernel.builder import build_graph
    g = build_graph(str(tiny_repo))
    names = [s.name for s in g.symbols.values()]
    assert "main" in names
    assert "helper" in names


def test_build_graph_has_edges(tiny_repo):
    from tempo.kernel.builder import build_graph
    g = build_graph(str(tiny_repo))
    assert len(g.edges) > 0


def test_build_graph_caches(tiny_repo):
    from tempo.kernel.builder import build_graph
    import time
    g1 = build_graph(str(tiny_repo))
    t0 = time.time()
    g2 = build_graph(str(tiny_repo))
    elapsed = time.time() - t0
    assert elapsed < 0.5, "Second build should use cache and be fast"
    assert g2.stats["symbols"] == g1.stats["symbols"]


# ---------------------------------------------------------------------------
# Core plugin: overview
# ---------------------------------------------------------------------------

def test_overview_plugin_runs(tiny_repo):
    from tempo.kernel.builder import build_graph
    from tempo.kernel.registry import Registry
    g = build_graph(str(tiny_repo))
    r = Registry()
    r.discover()
    runner = r.get_runner("overview")
    assert runner is not None
    out = runner(g)
    assert "files" in out or "symbols" in out


# ---------------------------------------------------------------------------
# Core plugin: dead_code
# ---------------------------------------------------------------------------

def test_dead_code_plugin_runs(tiny_repo):
    from tempo.kernel.builder import build_graph
    from tempo.kernel.registry import Registry
    g = build_graph(str(tiny_repo))
    r = Registry()
    r.discover()
    runner = r.get_runner("dead")
    assert runner is not None
    out = runner(g)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def test_telemetry_log_usage(tmp_path):
    from tempo.kernel.telemetry import log_usage
    log_usage(str(tmp_path), source="test", mode="overview", tokens=100, duration_ms=10, empty=False)
    local_log = tmp_path / ".tempograph" / "usage.jsonl"
    assert local_log.exists()
    import json
    entry = json.loads(local_log.read_text().strip())
    assert entry["mode"] == "overview"
    assert entry["tokens"] == 100


def test_telemetry_log_feedback(tmp_path):
    from tempo.kernel.telemetry import log_feedback
    log_feedback(str(tmp_path), mode="focus", helpful=True, note="worked well")
    local_log = tmp_path / ".tempograph" / "feedback.jsonl"
    assert local_log.exists()
    import json
    entry = json.loads(local_log.read_text().strip())
    assert entry["helpful"] is True
    assert entry["mode"] == "focus"
