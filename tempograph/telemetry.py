"""Usage telemetry and feedback logging — JSONL files in .tempograph/."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

USAGE_FILE = "usage.jsonl"
FEEDBACK_FILE = "feedback.jsonl"

# Central store — all telemetry from every repo copies here for cross-repo analysis
# Use ~/.tempograph/global/ so it works regardless of where the package is installed
CENTRAL_DIR = Path.home() / ".tempograph" / "global"


def _append_jsonl(file_path: Path, data: dict) -> None:
    """Append a single JSON line to a file. Creates parent dirs if needed."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(file_path, "a") as f:
            f.write(json.dumps(data, default=str) + "\n")
    except OSError:
        pass  # non-fatal — telemetry should never break the tool


def _base_entry(repo_path: str) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "repo": Path(repo_path).name,
        "repo_path": str(Path(repo_path).resolve()),
    }


def _telemetry_dir(repo_path: str) -> Path:
    return Path(repo_path).resolve() / ".tempograph"


def log_usage(repo_path: str, **kwargs) -> None:
    """Log a tool/CLI invocation to local .tempograph/ and central global store."""
    entry = _base_entry(repo_path)
    entry.update(kwargs)
    _append_jsonl(_telemetry_dir(repo_path) / USAGE_FILE, entry)
    _append_jsonl(CENTRAL_DIR / USAGE_FILE, entry)


def log_feedback(repo_path: str, **kwargs) -> None:
    """Log agent feedback to local .tempograph/ and central global store."""
    entry = _base_entry(repo_path)
    entry.update(kwargs)
    _append_jsonl(_telemetry_dir(repo_path) / FEEDBACK_FILE, entry)
    _append_jsonl(CENTRAL_DIR / FEEDBACK_FILE, entry)


def is_empty_result(output: str) -> bool:
    """Detect low-value/empty results from known render.py patterns."""
    prefixes = ("No symbols matching", "File '", "No dead code", "No results for", "No external dependencies", "No changed files")
    return any(output.startswith(p) for p in prefixes)
