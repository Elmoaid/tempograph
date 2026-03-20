"""Pre-indexed snapshot system for popular OSS repos.

Snapshots are pre-built graph.db files that let agents use tempograph
with zero setup on popular repos.

Layout on disk (mirrors standard repo layout so GraphDB works unchanged):
    ~/.tempograph/snapshots/<org>/<repo>/.tempograph/graph.db
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

SNAPSHOTS_DIR = Path.home() / ".tempograph" / "snapshots"

# Registry: "org/repo" → URL of graph.db file.
# URLs follow GitHub Releases assets pattern. Actual files are published
# separately — these are stubs in v0 (download will fail gracefully).
SNAPSHOT_REGISTRY: dict[str, str] = {
    "pallets/flask": "https://github.com/anthropics/tempograph-snapshots/releases/latest/download/pallets-flask-graph.db",
    "django/django": "https://github.com/anthropics/tempograph-snapshots/releases/latest/download/django-django-graph.db",
    "encode/httpx": "https://github.com/anthropics/tempograph-snapshots/releases/latest/download/encode-httpx-graph.db",
    "expressjs/express": "https://github.com/anthropics/tempograph-snapshots/releases/latest/download/expressjs-express-graph.db",
    "tiangolo/fastapi": "https://github.com/anthropics/tempograph-snapshots/releases/latest/download/tiangolo-fastapi-graph.db",
}


def _parse_repo(repo: str) -> tuple[str, str]:
    """Parse 'org/repo' slug into (org, name). Raises ValueError on bad format."""
    parts = repo.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected 'org/repo' format, got: {repo!r}")
    return parts[0], parts[1]


def snapshot_path(repo: str) -> Path:
    """Return the root dir for a snapshot (acts as repo root for GraphDB)."""
    org, name = _parse_repo(repo)
    return SNAPSHOTS_DIR / org / name


def snapshot_db_path(repo: str) -> Path:
    """Return the graph.db path for a snapshot."""
    return snapshot_path(repo) / ".tempograph" / "graph.db"


def is_downloaded(repo: str) -> bool:
    """Return True if the snapshot db exists locally."""
    try:
        return snapshot_db_path(repo).exists()
    except ValueError:
        return False


def list_snapshots() -> list[str]:
    """Return sorted list of available repo slugs in the registry."""
    return sorted(SNAPSHOT_REGISTRY)


def download_snapshot(repo: str) -> bool:
    """Download a snapshot graph.db for the given org/repo slug.

    Returns True on success, False on failure (prints error to stderr).
    The snapshot is stored at snapshot_db_path(repo).
    """
    if repo not in SNAPSHOT_REGISTRY:
        available = ", ".join(list_snapshots())
        print(f"error: '{repo}' not in snapshot registry.", file=sys.stderr)
        print(f"Available: {available}", file=sys.stderr)
        return False

    url = SNAPSHOT_REGISTRY[repo]
    dest = snapshot_db_path(repo)
    dest.parent.mkdir(parents=True, exist_ok=True)

    print(f"Downloading snapshot for {repo} ...", file=sys.stderr)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(
                            f"\r  {pct}% ({downloaded // 1024} KB / {total // 1024} KB)",
                            end="",
                            file=sys.stderr,
                        )
            if total:
                print(file=sys.stderr)
        print(f"Snapshot saved to {dest}", file=sys.stderr)
        return True
    except urllib.error.URLError as exc:
        dest.unlink(missing_ok=True)
        print(f"error: download failed — {exc}", file=sys.stderr)
        print(
            "  Note: snapshot URLs are stubs in v0. "
            "Publish snapshots to GitHub Releases first.",
            file=sys.stderr,
        )
        return False
