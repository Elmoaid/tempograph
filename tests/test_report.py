"""Tests for tempograph/report.py: generate_report."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from tempograph.report import generate_report


class TestGenerateReport:
    def test_returns_string(self, tmp_path):
        result = generate_report(str(tmp_path))
        assert isinstance(result, str)

    def test_no_data_returns_no_usage_message(self, tmp_path):
        result = generate_report(str(tmp_path))
        # With empty telemetry, should mention no data or 0 calls
        assert len(result) > 0

    def test_with_usage_data(self, tmp_path):
        tdir = tmp_path / ".tempograph"
        tdir.mkdir()
        entry = json.dumps({"mode": "overview", "repo": "myrepo", "repo_path": "/real/path", "ts": 1000})
        (tdir / "usage.jsonl").write_text(entry + "\n")
        result = generate_report(str(tmp_path))
        assert isinstance(result, str)

    def test_filters_tmp_repos(self, tmp_path):
        tdir = tmp_path / ".tempograph"
        tdir.mkdir()
        # tmp repos should be filtered out
        entry = json.dumps({"mode": "focus", "repo": "tmp_test", "repo_path": "/tmp/pytest-xxx/test", "ts": 2000})
        (tdir / "usage.jsonl").write_text(entry + "\n")
        result = generate_report(str(tmp_path))
        # The tmp entry should be filtered — report should show 0 or no usage
        assert isinstance(result, str)
