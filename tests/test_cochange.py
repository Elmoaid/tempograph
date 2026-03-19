"""Tests for git co-change matrix."""
import pytest
from unittest.mock import patch

from tempograph.git import cochange_matrix


class TestCochangeMatrix:
    def test_returns_dict(self):
        result = cochange_matrix(".", n_commits=50)
        assert isinstance(result, dict)

    def test_values_are_sorted_by_frequency(self):
        result = cochange_matrix(".", n_commits=100)
        for file_path, partners in result.items():
            freqs = [freq for _, freq in partners]
            assert freqs == sorted(freqs, reverse=True), f"{file_path} partners not sorted"

    def test_max_10_partners(self):
        result = cochange_matrix(".", n_commits=200)
        for file_path, partners in result.items():
            assert len(partners) <= 10

    def test_no_self_coupling(self):
        result = cochange_matrix(".", n_commits=100)
        for file_path, partners in result.items():
            partner_files = [f for f, _ in partners]
            assert file_path not in partner_files, f"{file_path} coupled with itself"

    def test_frequencies_between_0_and_1(self):
        result = cochange_matrix(".", n_commits=100)
        for file_path, partners in result.items():
            for partner, freq in partners:
                assert 0 < freq <= 1.0, f"{file_path}->{partner} freq={freq} out of range"

    def test_empty_on_non_git(self, tmp_path):
        result = cochange_matrix(str(tmp_path), n_commits=50)
        assert result == {}

    def test_symmetric_coupling(self):
        """If A couples with B, B should couple with A (unless B's list is at the 10-partner cap).

        cochange_matrix truncates each file's partner list to 10 entries. When B has exactly 10
        partners, A may validly be absent from B's list because higher-frequency partners fill
        all slots. The asymmetry is expected truncation behaviour, not a bug.
        """
        MAX_PARTNERS = 10
        result = cochange_matrix(".", n_commits=100)
        for file_a, partners in result.items():
            for file_b, freq_ab in partners:
                if file_b in result:
                    b_partners_list = result[file_b]
                    if len(b_partners_list) >= MAX_PARTNERS:
                        continue  # B's list is at cap; omission of A is expected
                    b_partners = dict(b_partners_list)
                    assert file_a in b_partners, f"{file_a}->{file_b} but not reverse"
