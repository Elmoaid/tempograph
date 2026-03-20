"""Tests for the pre-indexed snapshot system (tempograph/snapshots.py)."""
from __future__ import annotations

import sqlite3
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tempograph.snapshots import (
    SNAPSHOT_REGISTRY,
    _parse_repo,
    download_snapshot,
    is_downloaded,
    list_snapshots,
    snapshot_db_path,
    snapshot_path,
)


# ── _parse_repo ──────────────────────────────────────────────────────────────

def test_parse_repo_valid():
    assert _parse_repo("pallets/flask") == ("pallets", "flask")


def test_parse_repo_with_slashes():
    assert _parse_repo("/pallets/flask/") == ("pallets", "flask")


def test_parse_repo_invalid_no_slash():
    with pytest.raises(ValueError):
        _parse_repo("pallets")


def test_parse_repo_invalid_empty_part():
    with pytest.raises(ValueError):
        _parse_repo("/pallets/")


# ── snapshot_path / snapshot_db_path ─────────────────────────────────────────

def test_snapshot_path(tmp_path, monkeypatch):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    p = snapshot_path("pallets/flask")
    assert p == tmp_path / "snapshots" / "pallets" / "flask"


def test_snapshot_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    p = snapshot_db_path("pallets/flask")
    assert p == tmp_path / "snapshots" / "pallets" / "flask" / ".tempograph" / "graph.db"


# ── is_downloaded ─────────────────────────────────────────────────────────────

def test_is_downloaded_false(tmp_path, monkeypatch):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    assert not is_downloaded("pallets/flask")


def test_is_downloaded_true(tmp_path, monkeypatch):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    db_path = snapshot_db_path("pallets/flask")
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    assert is_downloaded("pallets/flask")


def test_is_downloaded_invalid_slug(tmp_path, monkeypatch):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    assert not is_downloaded("badslug")


# ── list_snapshots ────────────────────────────────────────────────────────────

def test_list_snapshots_sorted():
    repos = list_snapshots()
    assert repos == sorted(repos)
    assert "pallets/flask" in repos
    assert "django/django" in repos


def test_list_snapshots_all_in_registry():
    repos = list_snapshots()
    assert set(repos) == set(SNAPSHOT_REGISTRY)


# ── download_snapshot ─────────────────────────────────────────────────────────

def test_download_snapshot_unknown_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    result = download_snapshot("unknown/repo")
    assert result is False
    captured = capsys.readouterr()
    assert "not in snapshot registry" in captured.err


def test_download_snapshot_network_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")

    def fail_open(url, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fail_open)
    result = download_snapshot("pallets/flask")
    assert result is False
    captured = capsys.readouterr()
    assert "download failed" in captured.err
    # Partial file should be cleaned up
    assert not snapshot_db_path("pallets/flask").exists()


def test_download_snapshot_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")

    fake_data = b"FAKE_GRAPH_DB_CONTENT"

    class FakeResponse:
        headers = {"Content-Length": str(len(fake_data))}

        def read(self, n):
            if not hasattr(self, "_pos"):
                self._pos = 0
            chunk = fake_data[self._pos:self._pos + n]
            self._pos += n
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: FakeResponse())
    result = download_snapshot("pallets/flask")
    assert result is True
    db_path = snapshot_db_path("pallets/flask")
    assert db_path.exists()
    assert db_path.read_bytes() == fake_data
    captured = capsys.readouterr()
    assert "Snapshot saved" in captured.err


# ── CLI: snapshot subcommand ──────────────────────────────────────────────────

def test_cli_snapshot_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    from tempograph.__main__ import main
    rc = main(["snapshot", "--list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "pallets/flask" in captured.out
    assert "django/django" in captured.out


def test_cli_snapshot_repo_not_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")

    def fail_open(url, timeout=None):
        raise urllib.error.URLError("no internet")

    monkeypatch.setattr("urllib.request.urlopen", fail_open)
    from tempograph.__main__ import main
    rc = main(["snapshot", "--repo", "pallets/flask"])
    assert rc == 1


def test_cli_from_snapshot_missing(tmp_path, monkeypatch, capsys):
    """--from-snapshot with no downloaded snapshot should print error and exit 1."""
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")
    from tempograph.__main__ import main
    # repo arg is required by argparse but irrelevant when --from-snapshot is used
    rc = main([str(tmp_path), "--from-snapshot", "pallets/flask"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Snapshot not found" in captured.err or "error" in captured.err


def test_cli_from_snapshot_loads(tmp_path, monkeypatch, capsys):
    """--from-snapshot loads graph from existing db and renders overview."""
    monkeypatch.setattr("tempograph.snapshots.SNAPSHOTS_DIR", tmp_path / "snapshots")

    # Create a minimal valid graph.db at the expected snapshot location
    snap_root = tmp_path / "snapshots" / "pallets" / "flask"
    db_dir = snap_root / ".tempograph"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "graph.db"

    # Bootstrap schema via GraphDB
    from tempograph.storage import GraphDB
    db = GraphDB(snap_root)
    db.close()
    assert db_path.exists()

    from tempograph.__main__ import main
    rc = main([str(tmp_path), "--from-snapshot", "pallets/flask"])
    assert rc == 0
