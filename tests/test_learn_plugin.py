"""Tests for tempo/plugins/learn: TaskMemory.get_recommendation, summary, infer_from_telemetry."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from tempo.plugins.learn import TaskMemory, infer_from_telemetry


# ── TaskMemory.get_recommendation ─────────────────────────────────────────────

class TestGetRecommendation:
    def test_no_insights_returns_none(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        result = mem.get_recommendation("code_navigation")
        assert result is None

    def test_returns_insight_for_known_type(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        insights = {
            "code_navigation": {
                "best_modes": ["focus", "blast"],
                "avg_tokens": 800,
                "success_rate": 0.9,
                "sample_size": 10,
            }
        }
        mem._insights.write_text(json.dumps(insights))
        result = mem.get_recommendation("code_navigation")
        assert result is not None
        assert result["best_modes"] == ["focus", "blast"]

    def test_returns_none_for_unknown_type(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        insights = {"code_navigation": {"best_modes": ["focus"]}}
        mem._insights.write_text(json.dumps(insights))
        result = mem.get_recommendation("nonexistent_type")
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        mem._insights.write_text("not json {{{")
        result = mem.get_recommendation("any")
        assert result is None


# ── TaskMemory.summary ────────────────────────────────────────────────────────

class TestTaskMemorySummary:
    def test_no_data_returns_message(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        result = mem.summary()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_with_tasks_shows_count(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        tasks = [
            {"task_type": "code_navigation", "success": True, "tokens_used": 500},
            {"task_type": "code_navigation", "success": True, "tokens_used": 600},
            {"task_type": "cleanup", "success": False, "tokens_used": 300},
            {"task_type": "cleanup", "success": True, "tokens_used": 400},
            {"task_type": "refactor", "success": True, "tokens_used": 700},
        ]
        mem._log.write_text("\n".join(json.dumps(t) for t in tasks))
        result = mem.summary()
        assert "5 tasks" in result

    def test_shows_success_rate(self, tmp_path):
        mem = TaskMemory(str(tmp_path))
        tasks = [
            {"task_type": "t", "success": True, "tokens_used": 100},
            {"task_type": "t", "success": False, "tokens_used": 100},
        ]
        mem._log.write_text("\n".join(json.dumps(t) for t in tasks))
        result = mem.summary()
        assert "1/2" in result or "50%" in result


# ── infer_from_telemetry ──────────────────────────────────────────────────────

class TestInferFromTelemetry:
    def test_no_usage_file_returns_zero(self, tmp_path):
        result = infer_from_telemetry(str(tmp_path))
        assert result == 0

    def test_returns_int(self, tmp_path):
        result = infer_from_telemetry(str(tmp_path))
        assert isinstance(result, int)

    def test_with_usage_data(self, tmp_path):
        tdir = tmp_path / ".tempograph"
        tdir.mkdir()
        now = datetime.now(timezone.utc)
        entries = [
            json.dumps({"mode": "focus", "ts": now.isoformat(), "repo": "r"}),
            json.dumps({"mode": "blast", "ts": (now + timedelta(seconds=30)).isoformat(), "repo": "r"}),
        ]
        (tdir / "usage.jsonl").write_text("\n".join(entries))
        result = infer_from_telemetry(str(tmp_path))
        assert isinstance(result, int)
        assert result >= 0
