"""Tests for session-based mode prediction."""
import json
import pytest
from pathlib import Path

from tempograph.predict import (
    build_transition_matrix,
    build_transition_matrix_2nd,
    predict_next,
    predict_next_2nd,
    suggest_prefetch,
)


@pytest.fixture
def usage_repo(tmp_path):
    """Create a repo with synthetic usage.jsonl."""
    tg_dir = tmp_path / ".tempograph"
    tg_dir.mkdir()
    entries = [
        {"mode": "overview"}, {"mode": "focus"}, {"mode": "hotspots"},
        {"mode": "overview"}, {"mode": "focus"}, {"mode": "hotspots"},
        {"mode": "overview"}, {"mode": "focus"}, {"mode": "hotspots"},
        {"mode": "overview"}, {"mode": "focus"}, {"mode": "hotspots"},
        {"mode": "overview"}, {"mode": "focus"}, {"mode": "hotspots"},
        {"mode": "overview"}, {"mode": "focus"}, {"mode": "blast_radius"},
        {"mode": "symbols"}, {"mode": "file_map"},
        {"mode": "symbols"}, {"mode": "file_map"},
        {"mode": "symbols"}, {"mode": "file_map"},
        {"mode": "symbols"}, {"mode": "file_map"},
        {"mode": "symbols"}, {"mode": "file_map"},
    ]
    with open(tg_dir / "usage.jsonl", "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return str(tmp_path)


class TestTransitionMatrix:
    def test_builds_from_usage(self, usage_repo):
        matrix = build_transition_matrix(usage_repo, min_count=3)
        assert "overview" in matrix
        assert "focus" in matrix
        assert "symbols" in matrix

    def test_probabilities_sum_to_one_or_less(self, usage_repo):
        matrix = build_transition_matrix(usage_repo, min_count=1)
        for src, transitions in matrix.items():
            total = sum(p for _, p in transitions)
            assert total <= 1.01, f"{src} probabilities sum to {total}"

    def test_skips_self_transitions(self, usage_repo):
        matrix = build_transition_matrix(usage_repo, min_count=1)
        for src, transitions in matrix.items():
            dsts = [d for d, _ in transitions]
            assert src not in dsts, f"{src} has self-transition"

    def test_empty_for_nonexistent_repo(self, tmp_path):
        matrix = build_transition_matrix(str(tmp_path))
        assert matrix == {}

    def test_sorted_by_probability(self, usage_repo):
        matrix = build_transition_matrix(usage_repo, min_count=1)
        for src, transitions in matrix.items():
            probs = [p for _, p in transitions]
            assert probs == sorted(probs, reverse=True)


class TestPredictNext:
    def test_predicts_hotspots_after_focus(self, usage_repo):
        preds = predict_next(usage_repo, "focus")
        assert len(preds) > 0
        assert preds[0][0] == "hotspots"

    def test_predicts_focus_after_overview(self, usage_repo):
        preds = predict_next(usage_repo, "overview")
        assert len(preds) > 0
        assert preds[0][0] == "focus"

    def test_returns_empty_for_unknown_mode(self, usage_repo):
        preds = predict_next(usage_repo, "nonexistent_mode")
        assert preds == []

    def test_top_k_limits(self, usage_repo):
        preds = predict_next(usage_repo, "focus", top_k=1)
        assert len(preds) <= 1


class TestSuggestPrefetch:
    def test_suggests_above_threshold(self, usage_repo):
        suggestions = suggest_prefetch(usage_repo, "symbols", threshold=0.5)
        assert "file_map" in suggestions

    def test_no_suggestions_below_threshold(self, usage_repo):
        suggestions = suggest_prefetch(usage_repo, "focus", threshold=0.99)
        assert suggestions == []


@pytest.fixture
def usage_repo_2nd(tmp_path):
    """Repo with a repeating trigram pattern to test second-order prediction."""
    tg_dir = tmp_path / ".tempograph"
    tg_dir.mkdir()
    # Repeat the trigram (overview, focus, hotspots) 5x for 2nd-order threshold.
    # Also add (overview, focus, blast_radius) 3x to create a 2nd context split.
    entries = (
        [{"mode": "overview"}, {"mode": "focus"}, {"mode": "hotspots"}] * 5
        + [{"mode": "diff_context"}, {"mode": "focus"}, {"mode": "blast_radius"}] * 4
        + [{"mode": "symbols"}, {"mode": "file_map"}, {"mode": "dependencies"}] * 5
    )
    with open(tg_dir / "usage.jsonl", "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return str(tmp_path)


class TestTransitionMatrix2nd:
    def test_builds_from_trigrams(self, usage_repo_2nd):
        matrix = build_transition_matrix_2nd(usage_repo_2nd, min_count=3)
        assert ("overview", "focus") in matrix
        assert ("symbols", "file_map") in matrix

    def test_probabilities_correct(self, usage_repo_2nd):
        matrix = build_transition_matrix_2nd(usage_repo_2nd, min_count=3)
        # (overview, focus) -> hotspots should be 100% (5/5 times)
        preds = dict(matrix[("overview", "focus")])
        assert "hotspots" in preds
        assert abs(preds["hotspots"] - 1.0) < 0.01

    def test_context_splits_prediction(self, usage_repo_2nd):
        # (diff_context, focus) -> blast_radius vs (overview, focus) -> hotspots
        matrix = build_transition_matrix_2nd(usage_repo_2nd, min_count=3)
        if ("diff_context", "focus") in matrix and ("overview", "focus") in matrix:
            dc_focus_preds = dict(matrix[("diff_context", "focus")])
            ov_focus_preds = dict(matrix[("overview", "focus")])
            # They should predict DIFFERENT next modes
            dc_top = max(dc_focus_preds, key=dc_focus_preds.get)
            ov_top = max(ov_focus_preds, key=ov_focus_preds.get)
            assert dc_top != ov_top

    def test_empty_for_nonexistent_repo(self, tmp_path):
        matrix = build_transition_matrix_2nd(str(tmp_path))
        assert matrix == {}

    def test_sorted_by_probability(self, usage_repo_2nd):
        matrix = build_transition_matrix_2nd(usage_repo_2nd, min_count=1)
        for key, transitions in matrix.items():
            probs = [p for _, p in transitions]
            assert probs == sorted(probs, reverse=True)

    def test_skips_self_transitions(self, usage_repo_2nd):
        matrix = build_transition_matrix_2nd(usage_repo_2nd, min_count=1)
        for (a, b), transitions in matrix.items():
            dsts = [d for d, _ in transitions]
            assert b not in dsts, f"({a}, {b}) self-transitions to {b}"


class TestPredictNext2nd:
    def test_uses_context_to_improve_prediction(self, usage_repo_2nd):
        # 2nd-order should give 100% for (overview, focus) -> hotspots
        preds = predict_next_2nd(usage_repo_2nd, "overview", "focus")
        assert len(preds) > 0
        assert preds[0][0] == "hotspots"
        assert abs(preds[0][1] - 1.0) < 0.01

    def test_different_context_different_prediction(self, usage_repo_2nd):
        from_overview = predict_next_2nd(usage_repo_2nd, "overview", "focus", top_k=1)
        from_diff = predict_next_2nd(usage_repo_2nd, "diff_context", "focus", top_k=1)
        if from_overview and from_diff:
            assert from_overview[0][0] != from_diff[0][0]

    def test_falls_back_to_first_order(self, usage_repo_2nd):
        # Unknown prev_mode → fall back to first-order
        preds_2nd = predict_next_2nd(usage_repo_2nd, "UNKNOWN_MODE", "overview", top_k=3)
        preds_1st = predict_next(usage_repo_2nd, "overview", top_k=3)
        assert preds_2nd == preds_1st

    def test_top_k_limits(self, usage_repo_2nd):
        preds = predict_next_2nd(usage_repo_2nd, "overview", "focus", top_k=1)
        assert len(preds) <= 1

    def test_returns_empty_for_unknown_both(self, usage_repo_2nd):
        preds = predict_next_2nd(usage_repo_2nd, "UNKNOWN", "ALSO_UNKNOWN")
        assert preds == []


class TestSuggestPrefetchWithPrev:
    def test_prev_mode_uses_2nd_order(self, usage_repo_2nd):
        # With prev='overview', current='focus' → should suggest hotspots at high prob
        suggestions = suggest_prefetch(usage_repo_2nd, "focus", threshold=0.5, prev_mode="overview")
        assert "hotspots" in suggestions

    def test_no_prev_falls_back_to_first_order(self, usage_repo_2nd):
        without_prev = suggest_prefetch(usage_repo_2nd, "file_map", threshold=0.5)
        with_unknown_prev = suggest_prefetch(usage_repo_2nd, "file_map", threshold=0.5, prev_mode="UNKNOWN")
        # Both should return same results (first-order fallback for unknown prev)
        assert set(without_prev) == set(with_unknown_prev)
