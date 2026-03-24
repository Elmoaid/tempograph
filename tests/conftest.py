"""Test fixtures — prevent tests from writing to real global telemetry."""
import pytest


@pytest.fixture(autouse=True)
def sandbox_global_telemetry(tmp_path, monkeypatch):
    """Redirect global telemetry to temp dir. Local per-repo telemetry still works normally."""
    monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path / "global")


@pytest.fixture(autouse=True)
def clear_builder_cache():
    """Clear module-level Tempo cache between tests to prevent cross-test contamination."""
    from tempograph.builder import clear_tempo_cache
    clear_tempo_cache()
    yield
    clear_tempo_cache()
