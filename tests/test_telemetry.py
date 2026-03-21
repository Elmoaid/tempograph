"""Tests for tempograph/telemetry.py: is_empty_result."""
from __future__ import annotations

import pytest

from tempograph.telemetry import is_empty_result


class TestIsEmptyResult:
    def test_no_symbols_matching(self):
        assert is_empty_result("No symbols matching 'xyz'")

    def test_file_not_found(self):
        assert is_empty_result("File 'missing.py' not found")

    def test_no_dead_code(self):
        assert is_empty_result("No dead code detected — all exported symbols are referenced.")

    def test_no_results_for(self):
        assert is_empty_result("No results for query")

    def test_no_external_dependencies(self):
        assert is_empty_result("No external dependencies")

    def test_no_changed_files(self):
        assert is_empty_result("No changed files")

    def test_normal_output_not_empty(self):
        assert not is_empty_result("Focus: build_graph\n  caller: main")

    def test_empty_string_not_matching(self):
        assert not is_empty_result("")

    def test_partial_prefix_not_matching(self):
        assert not is_empty_result("There are no symbols matching")
