"""Test fixtures — prevent tests from writing to real global telemetry."""
import os
import pytest


@pytest.fixture(autouse=True)
def sandbox_global_telemetry(tmp_path, monkeypatch):
    """Redirect global telemetry to temp dir. Local per-repo telemetry still works normally."""
    monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path / "global")


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    """Set git identity so tests that create commits work on CI."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t.com")


@pytest.fixture(autouse=True)
def clear_builder_cache():
    """Clear module-level Tempo cache between tests to prevent cross-test contamination."""
    from tempograph.builder import clear_tempo_cache
    clear_tempo_cache()
    yield
    clear_tempo_cache()
