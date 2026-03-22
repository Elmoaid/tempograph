"""Ambient graph intelligence — generate per-directory LOD-1 context files.

Implements the supply-side fix for the CodeCompass adoption gap: pre-materialize
graph intelligence into static .tempograph-context.md files that agents discover
during normal Glob/Read/Grep exploration, with zero MCP call overhead.

All file paths from graph.files and sym.file_path are relative to repo_root.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .git import file_last_modified_days

if TYPE_CHECKING:
    from .types import Tempo

CONTEXT_FILENAME = ".tempograph-context.md"

# Token budget controls
_MAX_SYMBOLS_PER_DIR = 20
_MAX_EDGES_PER_SYMBOL = 3
_HOT_DAYS = 30


def _is_hot_dir(repo_root: str, dir_files: list[str]) -> bool:
    """Return True if any file in dir_files was modified within the last 30 days.

    dir_files contains relative paths (relative to repo_root).
    """
    for fp in dir_files:
        days = file_last_modified_days(repo_root, fp)
        if days is not None and days <= _HOT_DAYS:
            return True
    return False


def _lod1_section(graph: "Tempo", dir_files: list[str]) -> str:
    """LOD-1 symbol map: filename.py: sym_a, sym_b, sym_c"""
    lines = ["## Files and symbols"]
    for fp in sorted(dir_files):
        syms = graph.symbols_in_file(fp)
        if not syms:
            continue
        names = [s.name for s in syms[:_MAX_SYMBOLS_PER_DIR]]
        overflow = max(0, len(syms) - _MAX_SYMBOLS_PER_DIR)
        name_str = ", ".join(names)
        if overflow:
            name_str += f" (+{overflow} more)"
        lines.append(f"`{Path(fp).name}`: {name_str}")
    return "\n".join(lines)


def _cross_file_section(graph: "Tempo", dir_files: list[str]) -> str:
    """1-hop cross-file edges: callers/callees outside this directory.

    dir_files contains relative paths (relative to repo_root).
    """
    dir_set = set(dir_files)
    lines = ["## Cross-file relationships"]
    found = False
    for fp in sorted(dir_files):
        syms = graph.symbols_in_file(fp)
        for sym in syms[:_MAX_SYMBOLS_PER_DIR]:
            callers = [c for c in graph.callers_of(sym.id) if c.file_path not in dir_set]
            callees = [c for c in graph.callees_of(sym.id) if c.file_path not in dir_set]
            for caller in callers[:_MAX_EDGES_PER_SYMBOL]:
                lines.append(f"`{sym.name}` ← called by `{caller.name}` ({caller.file_path})")
                found = True
            for callee in callees[:_MAX_EDGES_PER_SYMBOL]:
                lines.append(f"`{sym.name}` → calls `{callee.name}` ({callee.file_path})")
                found = True
    if not found:
        lines.append("_(no cross-file calls detected)_")
    return "\n".join(lines)


def _test_mapping_section(graph: "Tempo", dir_files: list[str]) -> str:
    """Test coverage map using TDAD naming heuristics + import-based matching.

    dir_files contains relative paths (relative to repo_root).
    """
    test_files = [
        fp for fp in graph.files
        if (
            Path(fp).name.startswith("test_")
            or Path(fp).name.endswith("_test.py")
            or "/test/" in fp.replace("\\", "/")
            or "/tests/" in fp.replace("\\", "/")
        )
    ]
    lines = ["## Test coverage"]
    found = False
    for fp in sorted(dir_files):
        stem = Path(fp).stem
        matches: list[str] = []
        for tf in test_files:
            tf_name = Path(tf).name
            # Naming convention: test_<stem>.py or <stem>_test.py
            if f"test_{stem}" in tf_name or f"{stem}_test" in tf_name:
                matches.append(f"`{tf}` (name)")
                continue
            # Import-based: test file imports from this source file
            if fp in graph.importers_of(tf):
                entry = f"`{tf}` (import)"
                if entry not in matches:
                    matches.append(entry)
        if matches:
            found = True
            lines.append(f"`{Path(fp).name}`: tested by {', '.join(matches[:3])}")
    if not found:
        lines.append("_(no test files detected by naming convention)_")
    return "\n".join(lines)


def _freshness_section(repo_root: str, dir_files: list[str]) -> str:
    """Freshness scores from git history.

    dir_files contains relative paths (relative to repo_root).
    """
    lines = ["## Freshness"]
    for fp in sorted(dir_files):
        days = file_last_modified_days(repo_root, fp)
        name = Path(fp).name
        if days is None:
            lines.append(f"`{name}`: no git history")
        elif days <= 7:
            lines.append(f"`{name}`: {days}d ago [HOT]")
        elif days <= 30:
            lines.append(f"`{name}`: {days}d ago [WARM]")
        else:
            lines.append(f"`{name}`: {days}d ago [STABLE]")
    return "\n".join(lines)


def generate_ambient(graph: "Tempo", repo_root: str, hot_only: bool = True) -> dict[str, str]:
    """Generate per-directory LOD-1 context markdown content.

    Returns dict mapping relative directory path → context markdown string.
    Use write_ambient() to persist to disk.

    When hot_only=True, only directories with files changed in the last 30 days
    are included (skips stale dirs to keep output focused).
    """
    # Group relative file paths by parent directory (also relative)
    dir_to_files: dict[str, list[str]] = {}
    for fp in graph.files:
        dir_to_files.setdefault(str(Path(fp).parent), []).append(fp)

    result: dict[str, str] = {}
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")

    for dir_path, dir_files in sorted(dir_to_files.items()):
        if hot_only and not _is_hot_dir(repo_root, dir_files):
            continue

        recent_count = sum(
            1 for fp in dir_files
            if (d := file_last_modified_days(repo_root, fp)) is not None and d <= 7
        )

        header = (
            f"# tempograph-context | Generated: {now_str} | "
            f"Freshness: {recent_count} file(s) changed in 7d\n"
            f"<!-- Add {CONTEXT_FILENAME} to .gitignore -->"
        )
        lod1 = _lod1_section(graph, dir_files)
        cross = _cross_file_section(graph, dir_files)
        tests = _test_mapping_section(graph, dir_files)
        fresh = _freshness_section(repo_root, dir_files)
        result[dir_path] = f"{header}\n\n{lod1}\n\n{cross}\n\n{tests}\n\n{fresh}\n"

    return result


def write_ambient(contents: dict[str, str], repo_root: str) -> None:
    """Write .tempograph-context.md to each directory in contents.

    contents keys are relative directory paths (from generate_ambient).
    Files are written to repo_root / dir_path / CONTEXT_FILENAME.
    """
    root = Path(repo_root)
    for dir_path, content in contents.items():
        out_file = root / dir_path / CONTEXT_FILENAME
        out_file.write_text(content, encoding="utf-8")
