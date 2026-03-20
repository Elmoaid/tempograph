"""Tests for session-based mode prediction."""
import json
import pytest
from pathlib import Path

from tempograph.predict import build_transition_matrix, predict_next, suggest_prefetch


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
