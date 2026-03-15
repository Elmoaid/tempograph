"""Token optimizer — minimize token spend across all operations.

Tracks token usage per mode/query and learns which context strategies
produce the best signal-to-token ratio.
"""
from __future__ import annotations

import json
from pathlib import Path

PLUGIN = {
    "name": "tokens",
    "depends": [],
    "provides": ["token_stats"],
    "default": True,
    "description": "Token budget optimizer — track and minimize token spend",
}


class TokenTracker:
    """Track token usage and find optimization opportunities."""

    def __init__(self, repo_path: str):
        self._path = Path(repo_path) / ".tempo" / "token_log.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, mode: str, tokens: int, useful: bool = True) -> None:
        entry = {"mode": mode, "tokens": tokens, "useful": useful}
        with open(self._path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def stats(self) -> dict:
        if not self._path.exists():
            return {"total": 0, "by_mode": {}}
        entries = []
        for line in self._path.read_text().splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        by_mode: dict[str, dict] = {}
        total = 0
        for e in entries:
            mode = e.get("mode", "unknown")
            tokens = e.get("tokens", 0)
            total += tokens
            if mode not in by_mode:
                by_mode[mode] = {"total": 0, "count": 0, "useful": 0, "wasted": 0}
            by_mode[mode]["total"] += tokens
            by_mode[mode]["count"] += 1
            if e.get("useful", True):
                by_mode[mode]["useful"] += tokens
            else:
                by_mode[mode]["wasted"] += tokens
        return {"total": total, "by_mode": by_mode}

    def recommend_budget(self, mode: str) -> int:
        """Recommend a token budget based on historical usage."""
        s = self.stats()
        mode_stats = s["by_mode"].get(mode)
        if not mode_stats or mode_stats["count"] < 3:
            return 4000  # default
        avg = mode_stats["useful"] // mode_stats["count"]
        return max(500, min(8000, int(avg * 1.2)))  # 20% headroom


def run(graph, **kwargs) -> str:
    tracker = TokenTracker(graph.root)
    s = tracker.stats()
    if not s["by_mode"]:
        return "No token usage data yet. Use tempo to generate data."
    lines = [f"Token Usage: {s['total']:,} total", ""]
    for mode, ms in sorted(s["by_mode"].items(), key=lambda x: -x[1]["total"]):
        avg = ms["total"] // ms["count"] if ms["count"] else 0
        waste = f" ({ms['wasted']:,} wasted)" if ms["wasted"] else ""
        lines.append(f"  {mode:<14} {ms['total']:>8,} tok  ({ms['count']} calls, avg {avg:,}){waste}")
    return "\n".join(lines)
