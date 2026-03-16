"""Test fixtures — prevent tests from writing to real global telemetry."""
import pytest


@pytest.fixture(autouse=True)
def sandbox_global_telemetry(tmp_path, monkeypatch):
    """Redirect global telemetry to temp dir. Local per-repo telemetry still works normally."""
    monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path / "global")
