"""Tests for bench/changelocal/evaluate.py and bench/changelocal/run.py utilities."""
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from bench.changelocal.evaluate import file_metrics, aggregate
from bench.changelocal.run import _parse_file_list
from bench.changelocal.context import model_context_budget


class TestFileMetrics:
    def test_perfect_match(self):
        m = file_metrics(["a.py", "b.py"], ["a.py", "b.py"])
        assert m["precision"] == 1.0
        assert m["recall"] == 1.0
        assert m["f1"] == 1.0
        assert m["exact_match"] is True
        assert m["missed_any"] is False
        assert m["tp"] == 2
        assert m["fp"] == 0
        assert m["fn"] == 0

    def test_all_wrong(self):
        m = file_metrics(["c.py"], ["a.py", "b.py"])
        assert m["precision"] == 0.0
        assert m["recall"] == 0.0
        assert m["f1"] == 0.0
        assert m["exact_match"] is False
        assert m["missed_any"] is True
        assert m["tp"] == 0
        assert m["fp"] == 1
        assert m["fn"] == 2

    def test_empty_prediction(self):
        m = file_metrics([], ["a.py", "b.py"])
        assert m["precision"] == 0.0
        assert m["recall"] == 0.0
        assert m["f1"] == 0.0
        assert m["tp"] == 0
        assert m["fp"] == 0
        assert m["fn"] == 2
        assert m["missed_any"] is True

    def test_empty_actual(self):
        """If nothing changed (edge case), F1=1 when prediction is also empty."""
        m = file_metrics([], [])
        assert m["f1"] == 0.0  # both empty: undefined, defaults to 0

    def test_partial_match(self):
        m = file_metrics(["a.py", "c.py"], ["a.py", "b.py"])
        assert m["tp"] == 1
        assert m["fp"] == 1
        assert m["fn"] == 1
        assert abs(m["precision"] - 0.5) < 1e-9
        assert abs(m["recall"] - 0.5) < 1e-9
        assert abs(m["f1"] - 0.5) < 1e-9

    def test_high_recall_low_precision(self):
        """Predict many files: all actual covered but with extra noise."""
        m = file_metrics(["a.py", "b.py", "c.py", "d.py"], ["a.py", "b.py"])
        assert m["recall"] == 1.0
        assert m["precision"] == 0.5
        assert abs(m["f1"] - 2 * 0.5 * 1.0 / 1.5) < 1e-9
        assert m["missed_any"] is False

    def test_high_precision_low_recall(self):
        """Predict one correct file, miss the other."""
        m = file_metrics(["a.py"], ["a.py", "b.py"])
        assert m["precision"] == 1.0
        assert m["recall"] == 0.5
        assert abs(m["f1"] - 2 * 1.0 * 0.5 / 1.5) < 1e-9
        assert m["missed_any"] is True

    def test_missed_files_listed(self):
        m = file_metrics(["a.py"], ["a.py", "b.py", "c.py"])
        assert sorted(m["missed_files"]) == ["b.py", "c.py"]
        assert m["extra_files"] == []

    def test_extra_files_listed(self):
        m = file_metrics(["a.py", "z.py"], ["a.py"])
        assert m["missed_files"] == []
        assert m["extra_files"] == ["z.py"]

    def test_order_independent(self):
        """Prediction order doesn't affect scores."""
        m1 = file_metrics(["a.py", "b.py"], ["b.py", "a.py"])
        m2 = file_metrics(["b.py", "a.py"], ["a.py", "b.py"])
        assert m1["f1"] == m2["f1"]
        assert m1["exact_match"] is True


class TestAggregate:
    def test_empty_results(self):
        assert aggregate([]) == {}

    def test_single_result(self):
        m = file_metrics(["a.py"], ["a.py"])
        agg = aggregate([m])
        assert agg["n"] == 1
        assert agg["f1"] == 1.0
        assert agg["precision"] == 1.0
        assert agg["recall"] == 1.0
        assert agg["miss_rate"] == 0.0
        assert agg["exact_match"] == 1.0

    def test_averages_f1(self):
        m1 = file_metrics(["a.py"], ["a.py"])        # f1=1.0
        m2 = file_metrics(["c.py"], ["a.py"])        # f1=0.0
        agg = aggregate([m1, m2])
        assert abs(agg["f1"] - 0.5) < 1e-9
        assert agg["n"] == 2

    def test_miss_rate(self):
        m1 = file_metrics(["a.py"], ["a.py"])        # missed_any=False
        m2 = file_metrics([], ["a.py"])              # missed_any=True
        m3 = file_metrics(["a.py"], ["a.py"])        # missed_any=False
        agg = aggregate([m1, m2, m3])
        assert abs(agg["miss_rate"] - 1/3) < 1e-9

    def test_avg_predicted_actual(self):
        m1 = file_metrics(["a.py", "b.py"], ["a.py"])
        m2 = file_metrics(["c.py"], ["d.py", "e.py"])
        agg = aggregate([m1, m2])
        assert agg["avg_predicted"] == 1.5
        assert agg["avg_actual"] == 1.5


class TestParseFileList:
    def test_json_array(self):
        response = '["src/app.py", "src/utils.py"]'
        assert _parse_file_list(response) == ["src/app.py", "src/utils.py"]

    def test_json_in_prose(self):
        response = 'I predict these files: ["src/app.py", "lib/router.js"] based on...'
        assert _parse_file_list(response) == ["src/app.py", "lib/router.js"]

    def test_empty_json_array(self):
        assert _parse_file_list("[]") == []

    def test_fallback_quoted_paths(self):
        """Non-JSON response: extract quoted file paths."""
        response = 'The files are "src/app.py" and "tests/test_app.py"'
        result = _parse_file_list(response)
        assert "src/app.py" in result
        assert "tests/test_app.py" in result

    def test_no_files_returns_empty(self):
        assert _parse_file_list("I don't know which files to change.") == []

    def test_filters_non_strings_in_json(self):
        """JSON array with mixed types — only strings returned."""
        response = '["src/app.py", 42, null, "src/utils.py"]'
        result = _parse_file_list(response)
        assert result == ["src/app.py", "src/utils.py"]


class TestModelContextBudget:
    def test_32b_returns_3000(self):
        assert model_context_budget("qwen2.5-coder:32b") == 3000

    def test_14b_returns_800(self):
        assert model_context_budget("llama3.2:14b") == 800

    def test_7b_returns_800(self):
        assert model_context_budget("codellama:7b") == 800

    def test_no_param_size_returns_3000(self):
        """Models without a size parameter default to full budget."""
        assert model_context_budget("gpt-4o") == 3000
        assert model_context_budget("claude-3-sonnet") == 3000

    def test_case_insensitive(self):
        assert model_context_budget("Qwen2.5-Coder:32B") == 3000
        assert model_context_budget("LLaMA:14B") == 800
