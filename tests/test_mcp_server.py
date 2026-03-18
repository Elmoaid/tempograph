"""Tests for MCP server: all 16 tools, JSON output, error codes, edge cases."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

# Use tempograph's own repo as test fixture
REPO_PATH = str(REPO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_json(raw: str) -> dict:
    return json.loads(raw)


def assert_ok(raw: str) -> dict:
    d = parse_json(raw)
    assert d["status"] == "ok", f"Expected ok, got: {d}"
    assert "data" in d
    assert "tokens" in d
    assert "duration_ms" in d
    assert isinstance(d["tokens"], int)
    assert isinstance(d["duration_ms"], int)
    return d


def assert_error(raw: str, code: str) -> dict:
    d = parse_json(raw)
    assert d["status"] == "error", f"Expected error, got: {d}"
    assert d["code"] == code, f"Expected {code}, got: {d['code']}"
    assert "message" in d
    return d


# ---------------------------------------------------------------------------
# Import all tools
# ---------------------------------------------------------------------------

from tempograph.server import (
    index_repo, overview, focus, hotspots, blast_radius,
    diff_context, dead_code, lookup, symbols, file_map,
    dependencies, architecture, stats, report_feedback,
    learn_recommendation, prepare_context, get_patterns,
)


# ---------------------------------------------------------------------------
# Tool count
# ---------------------------------------------------------------------------

def test_tool_count():
    from tempograph.server import mcp
    assert len(mcp._tool_manager._tools) == 17


def test_agent_guide_bench_numbers():
    """Guard against fabricated bench numbers in AGENT_GUIDE.md.

    Verified canonical results (python3 -m bench.changelocal.analyze --canonical):
    - precision_filter: +3.7% F1 (p=0.21, ns), n=159
    - adaptive: +6.9% F1 (p=0.035*), n=159

    Numbers above 10% for precision_filter are fabricated. This test catches reinsertion.
    Canonical bench command must be present so users can verify independently.
    """
    agent_guide = Path(__file__).parent.parent / "AGENT_GUIDE.md"
    content = agent_guide.read_text()
    # Fabricated numbers that have been reinserted multiple times by automated tasks
    assert "+13.4%" not in content, "Fabricated precision_filter +13.4% in AGENT_GUIDE"
    assert "+13.8%" not in content, "Fabricated precision_filter +13.8% in AGENT_GUIDE"
    assert "+13.9%" not in content, "Fabricated precision_filter +13.9% in AGENT_GUIDE"
    assert "+13.2%" not in content, "Fabricated precision_filter +13.2% in AGENT_GUIDE"
    assert "p=0.022" not in content, "Fabricated p=0.022 in AGENT_GUIDE"
    assert "p=0.014" not in content, "Fabricated p=0.014 in AGENT_GUIDE"
    # Verified numbers must be present
    assert "+3.7%" in content, "Verified precision_filter +3.7% missing from AGENT_GUIDE"
    assert "+6.9%" in content, "Verified adaptive +6.9% missing from AGENT_GUIDE"
    assert "p=0.035" in content, "Verified adaptive p=0.035 missing from AGENT_GUIDE"
    # Must include verify command so users can check numbers themselves
    assert "bench.changelocal.analyze" in content, "AGENT_GUIDE must include a verify command"


# ---------------------------------------------------------------------------
# JSON output — every tool returns valid JSON with correct shape
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_index_repo(self):
        assert_ok(index_repo(REPO_PATH, output_format="json"))

    def test_overview(self):
        assert_ok(overview(REPO_PATH, output_format="json"))

    def test_focus(self):
        assert_ok(focus(REPO_PATH, "build_graph", output_format="json"))

    def test_hotspots(self):
        assert_ok(hotspots(REPO_PATH, output_format="json"))

    def test_blast_radius(self):
        assert_ok(blast_radius(REPO_PATH, query="build_graph", output_format="json"))

    def test_diff_context(self):
        assert_ok(diff_context(REPO_PATH, changed_files="tempograph/server.py", output_format="json"))

    def test_dead_code(self):
        assert_ok(dead_code(REPO_PATH, output_format="json"))

    def test_lookup(self):
        assert_ok(lookup(REPO_PATH, "where is build_graph?", output_format="json"))

    def test_symbols(self):
        assert_ok(symbols(REPO_PATH, output_format="json"))

    def test_file_map(self):
        assert_ok(file_map(REPO_PATH, output_format="json"))

    def test_dependencies(self):
        assert_ok(dependencies(REPO_PATH, output_format="json"))

    def test_architecture(self):
        assert_ok(architecture(REPO_PATH, output_format="json"))

    def test_stats(self):
        assert_ok(stats(REPO_PATH, output_format="json"))

    def test_learn_recommendation(self):
        raw = learn_recommendation(REPO_PATH, output_format="json")
        d = parse_json(raw)
        assert d["status"] in ("ok", "error")  # ok if tempo installed, error if not

    def test_prepare_context(self):
        r = assert_ok(prepare_context(REPO_PATH, task="fix search ranking",
                                      exclude_dirs="archive", output_format="json"))
        assert "Focus:" in r["data"]
        assert r["tokens"] > 100


# ---------------------------------------------------------------------------
# Text output — backwards compatible, no JSON wrapper
# ---------------------------------------------------------------------------

class TestTextOutput:
    def test_overview_text(self):
        raw = overview(REPO_PATH)
        assert not raw.startswith("{")
        assert "repo:" in raw.lower() or "files" in raw.lower()

    def test_focus_text(self):
        raw = focus(REPO_PATH, "build_graph")
        assert not raw.startswith("{")

    def test_hotspots_text(self):
        raw = hotspots(REPO_PATH)
        assert not raw.startswith("{")

    def test_lookup_text(self):
        raw = lookup(REPO_PATH, "where is build_graph?")
        assert "build_graph" in raw

    def test_dead_code_text(self):
        raw = dead_code(REPO_PATH)
        assert not raw.startswith("{")


# ---------------------------------------------------------------------------
# Error codes — machine-readable
# ---------------------------------------------------------------------------

class TestErrorCodes:
    def test_repo_not_found_json(self):
        assert_error(overview("/nonexistent/repo", output_format="json"), "REPO_NOT_FOUND")

    def test_repo_not_found_text(self):
        raw = overview("/nonexistent/repo")
        assert raw.startswith("[ERROR:REPO_NOT_FOUND]")

    def test_not_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert_error(diff_context(tmp, output_format="json"), "NOT_GIT_REPO")

    def test_invalid_params_blast_radius(self):
        assert_error(blast_radius(REPO_PATH, output_format="json"), "INVALID_PARAMS")

    def test_not_git_repo_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = diff_context(tmp)
            assert "[ERROR:NOT_GIT_REPO]" in raw

    def test_invalid_params_empty_focus_query(self):
        assert_error(focus(REPO_PATH, "", output_format="json"), "INVALID_PARAMS")

    def test_invalid_params_whitespace_focus_query(self):
        assert_error(focus(REPO_PATH, "   ", output_format="json"), "INVALID_PARAMS")

    def test_feedback_bad_repo(self):
        raw = report_feedback("/nonexistent/repo", "focus", True, "test")
        assert "ERROR" in raw

    def test_dead_code_max_tokens(self):
        r1 = assert_ok(dead_code(REPO_PATH, max_tokens=500, output_format="json"))
        r2 = assert_ok(dead_code(REPO_PATH, max_tokens=8000, output_format="json"))
        assert r1["tokens"] <= r2["tokens"] + 50


# ---------------------------------------------------------------------------
# Parameter behavior
# ---------------------------------------------------------------------------

class TestParameters:
    def test_blast_radius_query_over_file(self):
        """When both file_path and query are given, query takes precedence."""
        r = assert_ok(blast_radius(REPO_PATH, file_path="tempograph/server.py",
                                   query="build_graph", output_format="json"))
        assert "build_graph" in r["data"]

    def test_blast_radius_file_only(self):
        r = assert_ok(blast_radius(REPO_PATH, file_path="tempograph/server.py",
                                   output_format="json"))
        assert "server.py" in r["data"]

    def test_diff_context_explicit_files_no_git(self):
        """Passing changed_files explicitly should not require git."""
        with tempfile.TemporaryDirectory() as tmp:
            # Non-git dir, but with explicit files — should not error NOT_GIT_REPO
            raw = diff_context(tmp, changed_files="foo.py", output_format="json")
            d = parse_json(raw)
            # May be ok (empty impact) or error (build failed on empty dir), but NOT "NOT_GIT_REPO"
            if d["status"] == "error":
                assert d["code"] != "NOT_GIT_REPO"

    def test_focus_max_tokens(self):
        r1 = assert_ok(focus(REPO_PATH, "build_graph", max_tokens=500, output_format="json"))
        r2 = assert_ok(focus(REPO_PATH, "build_graph", max_tokens=4000, output_format="json"))
        assert r1["tokens"] <= r2["tokens"] + 50  # smaller budget → fewer tokens

    def test_hotspots_top_n(self):
        r1 = assert_ok(hotspots(REPO_PATH, top_n=3, output_format="json"))
        r2 = assert_ok(hotspots(REPO_PATH, top_n=20, output_format="json"))
        assert r1["tokens"] <= r2["tokens"]

    def test_file_map_max_symbols(self):
        r = assert_ok(file_map(REPO_PATH, max_symbols_per_file=2, output_format="json"))
        assert r["tokens"] > 0


# ---------------------------------------------------------------------------
# Token budget sanity
# ---------------------------------------------------------------------------

class TestTokenBudgets:
    def test_overview_cheap(self):
        r = assert_ok(overview(REPO_PATH, output_format="json"))
        assert r["tokens"] < 1000

    def test_stats_cheap(self):
        r = assert_ok(stats(REPO_PATH, output_format="json"))
        assert r["tokens"] < 300

    def test_focus_bounded(self):
        r = assert_ok(focus(REPO_PATH, "build_graph", max_tokens=2000, output_format="json"))
        assert r["tokens"] < 3000  # some overhead is ok

    def test_lookup_cheap(self):
        r = assert_ok(lookup(REPO_PATH, "where is render_overview?", output_format="json"))
        assert r["tokens"] < 1000


# ---------------------------------------------------------------------------
# Feedback tool
# ---------------------------------------------------------------------------

class TestFeedback:
    def test_feedback_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path)
        raw = report_feedback(str(tmp_path), "overview", True, "great output")
        assert "recorded" in raw.lower()

    def test_feedback_negative(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path)
        raw = report_feedback(str(tmp_path), "focus", False, "missing context")
        assert "recorded" in raw.lower()

    def test_feedback_no_note(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path)
        raw = report_feedback(str(tmp_path), "hotspots", True)
        assert "recorded" in raw.lower()


# ---------------------------------------------------------------------------
# Prepare context (batch tool)
# ---------------------------------------------------------------------------

class TestPrepareContext:
    def test_includes_overview_and_focus(self):
        r = assert_ok(prepare_context(REPO_PATH, task="graph building",
                                      exclude_dirs="archive", output_format="json"))
        assert "## Repo:" in r["data"]
        assert "Focus:" in r["data"]

    def test_respects_token_budget(self):
        r = assert_ok(prepare_context(REPO_PATH, task="graph building",
                                      max_tokens=2000, exclude_dirs="archive",
                                      output_format="json"))
        assert r["tokens"] <= 2500  # some overhead ok

    def test_includes_hotspots_for_change_tasks(self):
        r = assert_ok(prepare_context(REPO_PATH, task="fix search ranking bug",
                                      task_type="debug", exclude_dirs="archive",
                                      output_format="json"))
        assert "Hotspots" in r["data"] or r["tokens"] > 500

    def test_error_on_bad_repo(self):
        assert_error(prepare_context("/nonexistent", task="anything",
                                     output_format="json"), "REPO_NOT_FOUND")

    def test_text_mode(self):
        r = prepare_context(REPO_PATH, task="overview", exclude_dirs="archive")
        assert not r.startswith("{")
        assert "Focus:" in r

    def test_change_localization_path_for_pr_title(self):
        # PR title format triggers per-keyword focus (not general-task path)
        # "extract-cl-keywords" → CamelCase "ExtractClKeywords" → finds _extract_cl_keywords (≤10 files)
        # precision_filter=False to test the focus path in isolation (not the broad-skip gate)
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #595 from encode/extract-cl-keywords",
            exclude_dirs="archive", output_format="json", precision_filter=False,
        ))
        assert "Focus:" in r["data"]
        assert "KEY FILES" in r["data"]

    def test_change_localization_trunk_branch_uses_overview(self):
        # Trunk branches (master/main) → keywords=[] → selective overview fallback
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #1 from org/main",
            exclude_dirs="archive", output_format="json",
        ))
        # Should inject full overview: render_overview contains "entry points:" section
        assert "entry points:" in r["data"]

    def test_change_localization_docs_branch_suppresses_overview(self):
        # Docs branches (docs-*, readme-*) → keywords=[] but overview suppressed.
        # Flask "docs-javascript" -0.402 regression: overview steers model toward docs/conf.py.
        # Guard: when _is_docs_branch_task → skip overview, model uses training knowledge.
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #636 from pallets/docs-javascript",
            exclude_dirs="archive", output_format="json",
        ))
        # render_overview contains "entry points:"; suppressed for docs branches
        assert "entry points:" not in r["data"]
        assert "Focus:" not in r["data"]

    def test_change_localization_keywords_failed_no_overview(self):
        # Keywords exist but focus finds nothing → no overview injection.
        # "add-fix-update" → generic words → focus fails/empty → no overview (keywords non-empty).
        # Evidence: overview for non-empty failed keywords hurts high-baseline repos.
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #100 from org/add-fix-update",
            exclude_dirs="archive", output_format="json",
        ))
        # Keywords exist but generic → no focus, no overview
        assert "entry points:" not in r["data"]

    def test_change_localization_broad_keyword_path_fallback(self):
        # Keywords that match >10 symbols trigger path-based fallback.
        # If keyword matches a directory name (e.g. "render") → path fallback → KEY FILES (path match)
        # rather than symbol focus.
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #200 from org/fix-render-output",
            exclude_dirs="archive", output_format="json",
        ))
        # "render" maps to tempograph/render.py — should appear in output somehow
        # Either as KEY FILES from focus (≤10 files) or KEY FILES (path match)
        assert r["status"] == "ok"  # At minimum, no crash on broad keyword

    def test_adaptive_gating_high_overlap_skips_injection(self):
        # When baseline_predicted_files covers all KEY FILES (100% overlap), injection is skipped.
        # Bench evidence (Phase 5.27, n=83): overlap>=0.5 → 0 F1 delta (model already knows).
        import re
        task = "Merge pull request #1 from org/fix-render-focused"
        # First: baseline call to confirm KEY FILES are produced for this task
        base_r = assert_ok(prepare_context(REPO_PATH, task=task, output_format="json"))
        if "KEY FILES" not in base_r["data"]:
            pytest.skip("Task produces no KEY FILES — gating path can't trigger")
        # Extract the file paths listed in KEY FILES section
        key_file_paths = re.findall(r'  (\S+\.(?:py|js|ts))', base_r["data"])
        assert key_file_paths, "KEY FILES present but no paths parsed"
        # Second call with those exact files as baseline → 100% overlap → skip injection
        gated_r = assert_ok(prepare_context(
            REPO_PATH, task=task,
            baseline_predicted_files=key_file_paths,
            output_format="json",
        ))
        # Gating triggered: returns "" (model already correct — 0 F1 loss, saves tokens)
        assert gated_r["data"].strip() == ""

    def test_adaptive_gating_low_overlap_injects_context(self):
        # When baseline_predicted_files don't overlap with KEY FILES, full context is injected.
        import re
        task = "Merge pull request #1 from org/fix-render-focused"
        base_r = assert_ok(prepare_context(REPO_PATH, task=task, output_format="json"))
        if "KEY FILES" not in base_r["data"]:
            pytest.skip("Task produces no KEY FILES")
        # Pass completely unrelated files as baseline → 0% overlap → inject normally
        gated_r = assert_ok(prepare_context(
            REPO_PATH, task=task,
            baseline_predicted_files=["unrelated/file.py", "another/unrelated.py"],
            output_format="json",
        ))
        assert "KEY FILES" in gated_r["data"]

    def test_adaptive_gating_none_baseline_no_change(self):
        # Without baseline_predicted_files (None default), normal flow is unchanged.
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #1 from org/fix-render-focused",
            output_format="json",
        ))
        assert r["status"] == "ok"
        assert "## Repo:" in r["data"]

    def test_json_output_includes_key_files_and_injected(self):
        # JSON mode: key_files is a parsed list, injected is a bool.
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #1 from org/fix-render-focused",
            output_format="json",
        ))
        assert "key_files" in r, "JSON output must include key_files"
        assert "injected" in r, "JSON output must include injected"
        assert isinstance(r["key_files"], list)
        assert isinstance(r["injected"], bool)
        # Regression: key_files must be bare paths (no ":line-range" annotations)
        # Fix: 50f706f — used as baseline_predicted_files in adaptive pipeline where
        # bare path matching is required.
        import re
        for path in r["key_files"]:
            assert not re.search(r":\d+-\d+$", path), (
                f"key_files must not contain line ranges, got: {path!r}"
            )

    def test_json_output_injected_false_on_gating(self):
        # When gating triggers (high overlap), injected=False and key_files=[].
        import re
        task = "Merge pull request #1 from org/fix-render-focused"
        base_r = assert_ok(prepare_context(REPO_PATH, task=task, output_format="json"))
        if not base_r.get("key_files"):
            pytest.skip("Task produces no KEY FILES")
        gated_r = assert_ok(prepare_context(
            REPO_PATH, task=task,
            baseline_predicted_files=base_r["key_files"],
            output_format="json",
        ))
        assert gated_r["injected"] is False
        assert gated_r["key_files"] == []
        assert gated_r["data"].strip() == ""

    def test_json_output_injected_true_when_context_added(self):
        # When context is injected (no gating), injected=True and key_files is non-empty.
        task = "Merge pull request #1 from org/fix-render-focused"
        r = assert_ok(prepare_context(
            REPO_PATH, task=task,
            baseline_predicted_files=["unrelated/file.py"],  # 0% overlap → inject
            output_format="json",
        ))
        if "KEY FILES" not in r["data"]:
            pytest.skip("Task produces no KEY FILES for this repo")
        assert r["injected"] is True
        assert len(r["key_files"]) > 0

    def test_docs_component_branch_no_injection(self):
        # Branches with "docs" as a component (e.g. pr/5309-docs-view-custom-auth)
        # should suppress context injection — same as pure docs/ prefix branches.
        # Regression evidence: DRF "pr/5309-docs-view-custom-auth" → old code injected auth
        # context (F1 0.33→0.00); new component regex correctly suppresses injection.
        r = assert_ok(prepare_context(
            REPO_PATH,
            task="Merge pull request #5448 from org/pr/5309-docs-view-custom-auth\n"
                 "Allow setting custom authentication on docs view.",
            output_format="json",
        ))
        # Docs filter fires → keywords=[] + _is_docs_branch_task=True → no context
        assert "Focus:" not in r["data"]
        assert "entry points:" not in r["data"]

    def test_generic_keyword_path_fallback_skipped(self):
        # Generic keywords (e.g. "path", "route") that match >5 file paths should NOT
        # be used for path fallback — they produce noise (router.js, request.js, etc.)
        # Evidence: fastify "path-alias" → "path" matches 8+ files → KEY FILES = noise.
        # Fix: len(unique_paths) <= 5 threshold on direct keyword path fallback.
        # CamelCase/snake_case parts already had this threshold — now made consistent.
        from tempograph.render import _extract_cl_keywords, render_prepare
        from tempograph.builder import build_graph
        graph = build_graph(REPO_PATH)
        # This repo has many files with "path" in their path (build.py, server.py, etc.)
        # We just verify the threshold logic doesn't crash and produces consistent output
        output = render_prepare(
            graph,
            task="Merge pull request #74 from jsumners/path-alias\nAdd path alias for url",
        )
        # When baseline=0.286 (high-baseline) and all path keyword fallbacks are blocked
        # by <=5 threshold, output should be minimal (no noisy KEY FILES (path match))
        assert isinstance(output, str)  # function completes without error

    def test_version_bump_branch_produces_no_keywords(self):
        # Version bump PRs should not inject context — "version", "bump", "dependencies"
        # are not code symbol names. Prevents fastapi fix-10 style harm (d=-0.513).
        from tempograph.render import _extract_cl_keywords
        # Version branch
        task1 = "Merge pull request #14 from encode/version-0.1.5\nVersion 0.1.5"
        kw1 = _extract_cl_keywords(task1)
        assert kw1 == [], f"Version branch should yield empty keywords: {kw1}"
        # Pin dependencies
        task2 = "Merge pull request #11 from tiangolo/fix-10\nPin versions of dependencies and bump version"
        kw2 = _extract_cl_keywords(task2)
        assert kw2 == [], f"Version/deps body should yield empty keywords: {kw2}"

    def test_github_patch_branch_strips_username(self):
        # GitHub auto-generated "username-patch-N" branches should not yield
        # the username as a priority CamelCase keyword.
        # Evidence: "Freezerburn-patch-1-reb" → "Freezerburn" → false path match.
        from tempograph.render import _extract_cl_keywords
        task = "Merge pull request #999 from Freezerburn/Freezerburn-patch-1-reb\n" \
               "Add streaming support for response body"
        kw = _extract_cl_keywords(task)
        # "Freezerburn" should NOT appear in keywords
        assert "Freezerburn" not in kw, f"Username leaked into keywords: {kw}"
        # Body keywords should be present (streaming is a real code concept)
        kw_lower = [k.lower() for k in kw]
        assert any("stream" in k for k in kw_lower), f"Expected 'stream' in keywords: {kw}"

    def test_camelcase_path_part_skips_generic_words(self):
        # CamelCase keyword parts that are generic programming words (import, test, type...)
        # should NOT be used for path matching — they cause false positive path hits.
        # Regression evidence: "AuthtokenImport" split → "Import" → matches tests/importable/
        # instead of finding nothing (correct: no authtoken symbols → empty context).
        from tempograph.render import _extract_cl_keywords
        # Test that "AuthtokenImport" is extracted (the composite keyword)
        task = "Merge pull request #3785 from sheppard/authtoken-import\n" \
               "don't import authtoken model until needed"
        kw = _extract_cl_keywords(task)
        # Verify keywords are extracted (the function still works)
        assert isinstance(kw, list)
        # The specific issue: prepare_context should not output "importable" path matches
        r = assert_ok(prepare_context(
            REPO_PATH,
            task=task,
            output_format="json",
        ))
        # Should not match tests/importable/ via CamelCase "Import" split
        assert "importable" not in r["data"]

    def test_camelcase_field_exception_parts_skipped(self):
        # "Field" and "Exception" as CamelCase suffix parts should be skipped.
        # E.g. "DurationField" → split → ["Duration", "Field"] — "Field" must NOT match field_mapping.py.
        # "RaiseException" → split → ["Raise", "Exception"] — "Exception" must NOT match exceptions.py.
        from tempograph.render import _extract_cl_keywords
        task_field = "Merge pull request #1 from user/add-duration-field\n"
        kw_field = _extract_cl_keywords(task_field)
        # "DurationField" should be extracted as composite keyword
        assert any("field" in k.lower() or "duration" in k.lower() for k in kw_field), \
            f"Expected field/duration keyword: {kw_field}"
        # prepare_context should NOT output "field_mapping" path hit via CamelCase "Field" split
        r = assert_ok(prepare_context(REPO_PATH, task=task_field, output_format="json"))
        assert "field_mapping" not in r["data"]

        task_exc = "Merge pull request #2 from user/add-raise-exception\n"
        r2 = assert_ok(prepare_context(REPO_PATH, task=task_exc, output_format="json"))
        # tempograph self-repo has no exceptions.py, but verify "exception" part doesn't produce
        # any path fallback that shouldn't be there
        assert isinstance(r2["data"], str)


# ---------------------------------------------------------------------------
# Exclude dirs
# ---------------------------------------------------------------------------

class TestExcludeDirs:
    def test_exclude_reduces_symbols(self):
        """Excluding archive/ should produce fewer symbols."""
        r_all = assert_ok(overview(REPO_PATH, output_format="json"))
        r_excl = assert_ok(overview(REPO_PATH, exclude_dirs="archive", output_format="json"))
        # The excluded version should mention fewer files
        assert "archive" not in r_excl["data"] or r_excl["data"].count("archive") < r_all["data"].count("archive")

    def test_exclude_on_hotspots(self):
        """Hotspots with archive excluded should not show archive symbols."""
        r = assert_ok(hotspots(REPO_PATH, exclude_dirs="archive", output_format="json"))
        # Top hotspots should be from real code, not archive
        lines = r["data"].split("\n")
        for line in lines[:10]:
            assert "archive/" not in line

    def test_exclude_comma_separated(self):
        """Multiple dirs can be excluded with commas."""
        r = assert_ok(dead_code(REPO_PATH, exclude_dirs="archive,bench", output_format="json"))
        assert r["status"] == "ok"

    def test_exclude_on_focus(self):
        r = assert_ok(focus(REPO_PATH, "build_graph", exclude_dirs="archive", output_format="json"))
        assert r["status"] == "ok"

    def test_exclude_on_blast_radius(self):
        r = assert_ok(blast_radius(REPO_PATH, query="build_graph", exclude_dirs="archive", output_format="json"))
        assert r["status"] == "ok"


# ---------------------------------------------------------------------------
# Search ranking
# ---------------------------------------------------------------------------

class TestSearchRanking:
    def test_exported_ranks_higher(self):
        """Exported symbols should rank above non-exported with same text match."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        results = g.search_symbols("config")
        # Config class (exported) should be in top 3
        top_names = [s.qualified_name for s in results[:5]]
        assert any("Config" in n for n in top_names)

    def test_cross_file_callers_boost(self):
        """Symbols with many cross-file callers should rank higher."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        results = g.search_symbols("config")
        # Config.get has 70+ cross-file callers — should be top 3
        top_names = [s.qualified_name for s in results[:3]]
        assert any("get" in n.lower() or "Config" in n for n in top_names)

    def test_stop_words_filtered(self):
        """Task verbs and common words should not dominate search results."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        # "fix the build graph function" should match build_graph, not random .fix() methods
        results = g.search_symbols("fix the build graph function")
        top_names = [s.qualified_name for s in results[:5]]
        assert any("build_graph" in n for n in top_names), f"build_graph not in top 5: {top_names}"

    def test_longer_tokens_weighted_higher(self):
        """Longer, more specific tokens should score higher than short ones."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        results = g.search_symbols("parser complexity")
        top_names = [s.qualified_name for s in results[:5]]
        # Should find complexity-related symbols in parser, not random matches
        assert any("complex" in n.lower() or "parser" in n.lower() for n in top_names)

    def test_conjunction_bonus_multi_token(self):
        """Symbols matching multiple query tokens should rank above single-token matches."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        scored = g.search_symbols_scored("render dead code")
        # render_dead_code matches all 3 tokens — should be top result
        assert scored, "Expected results for 'render dead code'"
        top_sym = scored[0][1]
        assert "dead" in top_sym.name.lower() or "dead" in top_sym.qualified_name.lower(), \
            f"Expected dead_code symbol at top, got: {top_sym.qualified_name}"
        # Verify multi-match has higher score than a symbol matching only one token
        if len(scored) > 5:
            top_score = scored[0][0]
            fifth_score = scored[4][0]
            assert top_score > fifth_score, "Top score should be significantly higher"

    def test_seed_quality_gate_filters_low_relevance(self):
        """Focus mode should filter out low-scoring seeds instead of showing noise."""
        from tempograph.render import render_focused
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_focused(g, "render overview architecture")
        # Should focus on render-related symbols, not random matches
        assert "render" in output.lower()


# ---------------------------------------------------------------------------
# Implements edge detection
# ---------------------------------------------------------------------------

class TestImplementsEdges:
    def test_ts_implements(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language, EdgeKind
        code = b'class Svc implements Printable, Loggable { print() {} log() {} }'
        p = FileParser('test.ts', Language.TYPESCRIPT, code)
        _, edges, _ = p.parse()
        impl = {e.target_id for e in edges if e.kind == EdgeKind.IMPLEMENTS}
        assert "Printable" in impl, f"Missing Printable: {impl}"
        assert "Loggable" in impl, f"Missing Loggable: {impl}"

    def test_ts_extends_vs_implements(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language, EdgeKind
        code = b'class Report extends Document implements Exportable { export() {} }'
        p = FileParser('test.ts', Language.TYPESCRIPT, code)
        _, edges, _ = p.parse()
        inherits = {e.target_id for e in edges if e.kind == EdgeKind.INHERITS}
        impl = {e.target_id for e in edges if e.kind == EdgeKind.IMPLEMENTS}
        assert "Document" in inherits
        assert "Exportable" in impl


# ---------------------------------------------------------------------------
# Import type skipping
# ---------------------------------------------------------------------------

class TestImportTypeSkipping:
    def test_type_only_import_no_edge(self):
        """import type { X } from 'Y' should not create an imports edge."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        # tempo.ts has 'import type { TempoResult } from "../App"'
        # This should NOT create an import edge from tempo.ts to App.tsx
        for e in g.edges:
            if "tempo.ts" in e.source_id and e.kind.value == "imports" and "App" in e.target_id:
                pytest.fail(f"Type-only import created edge: {e.source_id} -> {e.target_id}")

    def test_no_false_circular_imports(self):
        """With import type skipping, no circular imports in the UI code."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        cycles = g.detect_circular_imports()
        # Filter to UI cycles only
        ui_cycles = [c for c in cycles if any("ui/src" in f for f in c)]
        assert len(ui_cycles) == 0, f"False circular imports: {ui_cycles}"


# ---------------------------------------------------------------------------
# Java parser
# ---------------------------------------------------------------------------

class TestJavaParser:
    def test_class_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public class Foo { public void bar() {} private int baz() { return 1; } }'
        p = FileParser('Foo.java', Language.JAVA, code)
        syms, edges, imports = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Foo" in names
        assert "Foo.bar" in names
        assert "Foo.baz" in names
        foo = next(s for s in syms if s.name == "Foo")
        assert foo.kind.value == "class"
        assert foo.exported is True
        bar = next(s for s in syms if s.name == "bar")
        assert bar.exported is True
        baz = next(s for s in syms if s.name == "baz")
        assert baz.exported is False

    def test_interface_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'interface Repo { void save(); List<User> findAll(); }'
        p = FileParser('Repo.java', Language.JAVA, code)
        syms, _, _ = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Repo" in names
        assert "Repo.save" in names
        assert "Repo.findAll" in names
        repo = next(s for s in syms if s.name == "Repo")
        assert repo.kind.value == "interface"

    def test_enum_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'enum Color { RED, GREEN, BLUE }'
        p = FileParser('Color.java', Language.JAVA, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Color" for s in syms)

    def test_constructor(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Svc { public Svc(int x) { this.x = x; } }'
        p = FileParser('Svc.java', Language.JAVA, code)
        syms, edges, _ = p.parse()
        assert any(s.qualified_name == "Svc.Svc" for s in syms)
        assert any(e.kind.value == "contains" for e in edges)

    def test_imports(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'import java.util.List;\nimport java.util.Map;\nclass A {}'
        p = FileParser('A.java', Language.JAVA, code)
        _, _, imports = p.parse()
        assert len(imports) == 2
        assert any("List" in i for i in imports)

    def test_call_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class A { void run() { svc.findAll(); validate(); } void validate() {} }'
        p = FileParser('A.java', Language.JAVA, code)
        syms, edges, _ = p.parse()
        call_edges = [e for e in edges if e.kind.value == "calls"]
        targets = {e.target_id for e in call_edges}
        assert "svc.findAll" in targets
        assert "validate" in targets

    def test_inheritance(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Dog extends Animal implements Runnable { void bark() {} }'
        p = FileParser('Dog.java', Language.JAVA, code)
        syms, edges, _ = p.parse()
        inherit_edges = [e for e in edges if e.kind.value == "inherits"]
        targets = {e.target_id for e in inherit_edges}
        assert "Animal" in targets or any("Animal" in t for t in targets)

    def test_implements_edges(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language, EdgeKind
        code = b'class Svc implements Serializable, Comparable<Svc> { public int compareTo(Svc o) { return 0; } }'
        p = FileParser('Svc.java', Language.JAVA, code)
        _, edges, _ = p.parse()
        impl_edges = [e for e in edges if e.kind == EdgeKind.IMPLEMENTS]
        targets = {e.target_id for e in impl_edges}
        assert "Serializable" in targets, f"Missing Serializable: {targets}"
        assert "Comparable" in targets, f"Missing Comparable: {targets}"

    def test_overloaded_constructors(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Svc {\n  Svc() {}\n  Svc(int x) {}\n  Svc(String s, int n) {}\n}'
        p = FileParser('Svc.java', Language.JAVA, code)
        syms, _, _ = p.parse()
        ctors = [s for s in syms if s.qualified_name == "Svc.Svc"]
        assert len(ctors) == 3, f"Expected 3 constructors, got {len(ctors)}"
        # All must have unique IDs
        ids = {s.id for s in ctors}
        assert len(ids) == 3, f"Constructor IDs not unique: {ids}"


# ---------------------------------------------------------------------------
# C# parser
# ---------------------------------------------------------------------------

class TestCSharpParser:
    def test_class_and_methods(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public class Svc { public void Run() {} private int Calc() { return 1; } }'
        p = FileParser('Svc.cs', Language.CSHARP, code)
        syms, edges, _ = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Svc" in names
        assert "Svc.Run" in names
        assert "Svc.Calc" in names
        svc = next(s for s in syms if s.name == "Svc")
        assert svc.exported is True
        calc = next(s for s in syms if s.name == "Calc")
        assert calc.exported is False

    def test_interface(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public interface IRepo { void Save(); }'
        p = FileParser('IRepo.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "IRepo" and s.kind.value == "interface" for s in syms)
        assert any(s.qualified_name == "IRepo.Save" for s in syms)

    def test_struct_and_enum(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public struct Vec2 { public int X; } public enum Color { Red, Green }'
        p = FileParser('Types.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Vec2" and s.kind.value == "struct" for s in syms)
        assert any(s.name == "Color" for s in syms)

    def test_constructor(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Svc { public Svc(int x) { } }'
        p = FileParser('Svc.cs', Language.CSHARP, code)
        syms, edges, _ = p.parse()
        assert any(s.qualified_name == "Svc.Svc" for s in syms)

    def test_overloaded_constructors(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Svc {\n  public Svc() { }\n  public Svc(int x) { }\n  public Svc(string s) { }\n}'
        p = FileParser('Svc.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        ctors = [s for s in syms if s.qualified_name == "Svc.Svc"]
        assert len(ctors) == 3, f"Expected 3 constructors, got {len(ctors)}"
        ids = {s.id for s in ctors}
        assert len(ids) == 3, f"Constructor IDs not unique: {ids}"

    def test_property(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language, SymbolKind
        code = b'class User { public string Name { get; set; } }'
        p = FileParser('User.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        prop = next((s for s in syms if s.qualified_name == "User.Name"), None)
        assert prop is not None
        assert prop.kind == SymbolKind.PROPERTY

    def test_using_directives(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'using System;\nusing System.Linq;\nclass A {}'
        p = FileParser('A.cs', Language.CSHARP, code)
        _, _, imports = p.parse()
        assert len(imports) == 2

    def test_call_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class A { void Run() { _repo.Save(); Validate(); } void Validate() {} }'
        p = FileParser('A.cs', Language.CSHARP, code)
        _, edges, _ = p.parse()
        call_targets = {e.target_id for e in edges if e.kind.value == "calls"}
        assert "_repo.Save" in call_targets
        assert "Validate" in call_targets

    def test_inheritance(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language, EdgeKind
        code = b'class Dog : Animal, IRunnable { void Bark() {} }'
        p = FileParser('Dog.cs', Language.CSHARP, code)
        _, edges, _ = p.parse()
        inherit_targets = {e.target_id for e in edges if e.kind == EdgeKind.INHERITS}
        impl_targets = {e.target_id for e in edges if e.kind == EdgeKind.IMPLEMENTS}
        assert "Animal" in inherit_targets, f"Missing Animal in inherits: {inherit_targets}"
        assert "IRunnable" in impl_targets, f"Missing IRunnable in implements: {impl_targets}"

    def test_namespace(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'namespace App { public class Foo { public void Bar() {} } }'
        p = FileParser('Foo.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Foo" for s in syms)
        assert any(s.qualified_name == "Foo.Bar" for s in syms)


# ---------------------------------------------------------------------------
# Ruby parser
# ---------------------------------------------------------------------------

class TestRubyParser:
    def test_class_and_methods(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Foo\n  def bar\n    baz\n  end\n  def baz; end\nend'
        p = FileParser('foo.rb', Language.RUBY, code)
        syms, _, _ = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Foo" in names
        assert "Foo.bar" in names
        assert "Foo.baz" in names

    def test_module(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'module Services\n  class Svc\n    def run; end\n  end\nend'
        p = FileParser('svc.rb', Language.RUBY, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Services" for s in syms)
        assert any(s.name == "Svc" for s in syms)

    def test_inheritance(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Dog < Animal\n  def bark; end\nend'
        p = FileParser('dog.rb', Language.RUBY, code)
        _, edges, _ = p.parse()
        inherit_targets = {e.target_id for e in edges if e.kind.value == "inherits"}
        assert "Animal" in inherit_targets

    def test_requires(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b"require 'json'\nrequire_relative 'user'\nclass A; end"
        p = FileParser('a.rb', Language.RUBY, code)
        _, _, imports = p.parse()
        assert len(imports) == 2

    def test_call_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class A\n  def run\n    validate()\n    @repo.save(x)\n  end\n  def validate; end\nend'
        p = FileParser('a.rb', Language.RUBY, code)
        _, edges, _ = p.parse()
        call_targets = {e.target_id for e in edges if e.kind.value == "calls"}
        assert "validate" in call_targets


# ---------------------------------------------------------------------------
# Render module — token caps and noise detection
# ---------------------------------------------------------------------------

class TestRenderTokenCaps:
    def test_symbols_max_tokens(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_symbols, count_tokens
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        # Capped
        output = render_symbols(g, max_tokens=2000)
        tokens = count_tokens(output)
        assert tokens <= 2500  # allow some overhead
        assert "truncated" in output
        # Unlimited
        full = render_symbols(g, max_tokens=0)
        assert count_tokens(full) > tokens

    def test_map_max_tokens(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_map, count_tokens
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_map(g, max_tokens=1500)
        tokens = count_tokens(output)
        assert tokens <= 2000
        assert "truncated" in output

    def test_map_unlimited(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_map, count_tokens
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_map(g, max_tokens=0)
        assert "truncated" not in output

    def test_noise_detection(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_overview
        g = build_graph(REPO_PATH)  # without exclude
        ov = render_overview(g)
        assert "SUGGESTED EXCLUDES" in ov
        assert "archive" in ov.lower()

    def test_focus_file_context(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_focused
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_focused(g, "build_graph", max_tokens=4000)
        # Should have the "Also in these files" section
        assert "Focus:" in output

    def test_namespace(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'namespace App { public class Foo { public void Bar() {} } }'
        p = FileParser('Foo.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Foo" for s in syms)
        assert any(s.qualified_name == "Foo.Bar" for s in syms)


# ---------------------------------------------------------------------------
# CommonJS export detection
# ---------------------------------------------------------------------------

class TestCommonJSExports:
    def test_module_exports_class(self):
        """module.exports = class Foo {} → class marked exported."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'module.exports = class Application extends Emitter { constructor() {} }'
        p = FileParser('app.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        app = next((s for s in syms if s.name == 'Application'), None)
        assert app is not None, "Application class not found"
        assert app.exported, "Application should be exported"

    def test_module_exports_identifier(self):
        """module.exports = fastify → fastify function marked exported."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'function fastify(opts) { return {} }\nmodule.exports = fastify'
        p = FileParser('fastify.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        fn = next((s for s in syms if s.name == 'fastify'), None)
        assert fn is not None, "fastify function not found"
        assert fn.exported, "fastify should be exported via module.exports"

    def test_module_exports_function_expression(self):
        """module.exports = function override(...) {} → named fn exported."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'module.exports = function override(old, fn) { return fn }'
        p = FileParser('override.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        fn = next((s for s in syms if s.name == 'override'), None)
        assert fn is not None, "override function not found"
        assert fn.exported, "override should be exported"

    def test_module_exports_shorthand_object(self):
        """module.exports = { buildRouting, foo } → symbols marked exported."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'function buildRouting() {}\nfunction foo() {}\nmodule.exports = { buildRouting, foo }'
        p = FileParser('route.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        routing = next((s for s in syms if s.name == 'buildRouting'), None)
        foo = next((s for s in syms if s.name == 'foo'), None)
        assert routing and routing.exported, "buildRouting should be exported"
        assert foo and foo.exported, "foo should be exported"

    def test_module_exports_object_methods(self):
        """module.exports = { get header() {}, redirect() {} } → methods extracted."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'module.exports = {\n  get header() { return this.res.headers },\n  redirect(url) { this.res.redirect(url) }\n}'
        p = FileParser('response.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        names = {s.name for s in syms}
        assert 'header' in names, f"header method not extracted: {names}"
        assert 'redirect' in names, f"redirect method not extracted: {names}"
        for s in syms:
            if s.name in ('header', 'redirect'):
                assert s.exported, f"{s.name} should be exported"

    def test_exports_dot_anonymous_function(self):
        """exports.normalizeType = function(type){} → symbol named from prop."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = (
            b'exports.normalizeType = function(type){ return type.split(";")[0].trim() }\n'
            b'exports.compileETag = function(val){\n  return val\n}\n'
            b'exports.setCharset = function setCharset(type, charset){\n  return type\n}\n'
        )
        p = FileParser('utils.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        names = {s.name for s in syms}
        assert 'normalizeType' in names, f"normalizeType not extracted: {names}"
        assert 'compileETag' in names, f"compileETag not extracted: {names}"
        assert 'setCharset' in names, f"setCharset not extracted: {names}"
        for s in syms:
            if s.name in ('normalizeType', 'compileETag', 'setCharset'):
                assert s.exported, f"{s.name} should be exported"

    def test_const_proto_module_exports_methods(self):
        """const proto = module.exports = { method() {} } → methods extracted."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'const proto = module.exports = {\n  inspect() { return this.toJSON() },\n  onerror(err) { console.error(err) }\n}'
        p = FileParser('context.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        names = {s.name for s in syms}
        assert 'inspect' in names, f"inspect not extracted: {names}"
        assert 'onerror' in names, f"onerror not extracted: {names}"

    def test_es_export_default_identifier(self):
        """export default settle → settle marked exported."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'const settle = (resolve, reject, response) => {};\nexport default settle'
        p = FileParser('settle.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        s = next((s for s in syms if s.name == 'settle'), None)
        assert s is not None, "settle not found"
        assert s.exported, "settle should be exported via export default"

    def test_es_export_named_clause(self):
        """export { foo, bar } → both marked exported."""
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'function buildFullPath(a, b) {}\nfunction mergeConfig(a, b) {}\nexport { buildFullPath, mergeConfig }'
        p = FileParser('path.js', Language.JAVASCRIPT, code)
        syms, _, _ = p.parse()
        by_name = {s.name: s for s in syms}
        assert by_name.get('buildFullPath') and by_name['buildFullPath'].exported
        assert by_name.get('mergeConfig') and by_name['mergeConfig'].exported


# ---------------------------------------------------------------------------
# get_patterns tool
# ---------------------------------------------------------------------------

class TestGetPatterns:
    def test_returns_ok(self):
        result = get_patterns(REPO_PATH, output_format="json")
        d = assert_ok(result)
        assert len(d["data"]) > 0

    def test_query_filter(self):
        result = get_patterns(REPO_PATH, query="render", output_format="json")
        d = assert_ok(result)
        # query filter should return relevant output (render_ family is large)
        assert "render" in d["data"].lower()

    def test_invalid_repo(self):
        result = get_patterns("/nonexistent/path", output_format="json")
        assert_error(result, "REPO_NOT_FOUND")

    def test_text_output(self):
        result = get_patterns(REPO_PATH)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Temporal symbol weighting (recently-modified files boost)
# ---------------------------------------------------------------------------

class TestTemporalSymbolWeighting:
    def test_recently_modified_files_returns_set_for_git_repo(self):
        from tempograph.git import recently_modified_files
        result = recently_modified_files(REPO_PATH)
        assert isinstance(result, set)
        # tempograph is an active repo — must have recent commits
        assert len(result) > 0

    def test_recently_modified_files_empty_for_non_git_dir(self, tmp_path):
        from tempograph.git import recently_modified_files
        result = recently_modified_files(str(tmp_path))
        assert result == set()

    def test_build_graph_populates_hot_files(self):
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH)
        # Git repo — hot_files should be populated
        assert isinstance(g.hot_files, set)
        assert len(g.hot_files) > 0

    def test_hot_files_scoring_bonus(self):
        """Symbols in hot_files should outscore identical symbols outside hot_files."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        # Pick a symbol file that exists in the graph
        all_syms = list(g.symbols.values())
        assert all_syms, "Expected symbols in graph"
        target_sym = all_syms[0]

        # With hot_files containing target_sym's file
        g.hot_files = {target_sym.file_path}
        scored_hot = g.search_symbols_scored(target_sym.name)

        # With hot_files empty
        g.hot_files = set()
        scored_cold = g.search_symbols_scored(target_sym.name)

        # Find target_sym score in both
        hot_score = next((sc for sc, s in scored_hot if s.id == target_sym.id), None)
        cold_score = next((sc for sc, s in scored_cold if s.id == target_sym.id), None)

        if hot_score is not None and cold_score is not None:
            assert hot_score > cold_score, (
                f"Expected hot-file bonus: hot={hot_score} cold={cold_score} for {target_sym.name}"
            )

    def test_hot_files_excludes_test_files(self):
        """build_graph should not include test files in hot_files."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH)
        for f in g.hot_files:
            parts = f.replace("\\", "/").split("/")
            assert not any(p.lower() in {"test", "tests", "__tests__", "spec", "specs"} for p in parts[:-1]), \
                f"Test directory file should not be in hot_files: {f}"
            name = parts[-1].lower()
            assert not name.startswith("test_"), f"Test file should not be in hot_files: {f}"
            assert not name.endswith(("_test.py", "_spec.py")), f"Test file should not be in hot_files: {f}"

    def test_is_hot_source_file(self):
        """_is_hot_source_file correctly classifies source vs test/doc files."""
        from tempograph.builder import _is_hot_source_file

        # Source files: eligible
        assert _is_hot_source_file("tempograph/render.py") is True
        assert _is_hot_source_file("src/main.ts") is True
        assert _is_hot_source_file("bench/changelocal/context.py") is True

        # Test files: excluded
        assert _is_hot_source_file("tests/test_mcp_server.py") is False
        assert _is_hot_source_file("test/foo_test.py") is False
        assert _is_hot_source_file("src/app.spec.ts") is False
        assert _is_hot_source_file("__tests__/app.ts") is False

        # Documentation: excluded
        assert _is_hot_source_file("notes/2026-03-18_meta-review.md") is False
        assert _is_hot_source_file("README.rst") is False
        assert _is_hot_source_file("docs/guide.txt") is False
