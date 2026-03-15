"""Learning engine — L1 per-task feedback loop.

Tracks what context was provided for each task and whether the task
succeeded. Over time, learns which context strategies work best for
different task types in this codebase.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PLUGIN = {
    "name": "learn",
    "depends": ["context", "tokens"],
    "provides": ["learn", "insights"],
    "default": True,
    "description": "Per-task learning — adapts context strategy based on outcomes",
}


class TaskMemory:
    """Persistent per-codebase learning from task outcomes."""

    def __init__(self, repo_path: str):
        self._dir = Path(repo_path) / ".tempo" / "learn"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log = self._dir / "tasks.jsonl"
        self._insights = self._dir / "insights.json"

    def record_task(self, task_type: str, context_modes: list[str],
                    tokens_used: int, success: bool, notes: str = "") -> None:
        entry = {
            "ts": time.time(),
            "task_type": task_type,
            "context_modes": context_modes,
            "tokens_used": tokens_used,
            "success": success,
            "notes": notes,
        }
        with open(self._log, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._update_insights()

    def _load_tasks(self) -> list[dict]:
        if not self._log.exists():
            return []
        tasks = []
        for line in self._log.read_text().splitlines():
            if line.strip():
                try:
                    tasks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return tasks

    def _update_insights(self) -> None:
        """Recompute insights from all task data."""
        tasks = self._load_tasks()
        if len(tasks) < 5:
            return

        insights = {}

        # Best context strategy per task type
        by_type: dict[str, list[dict]] = {}
        for t in tasks:
            tt = t.get("task_type", "unknown")
            if tt not in by_type:
                by_type[tt] = []
            by_type[tt].append(t)

        for tt, type_tasks in by_type.items():
            successes = [t for t in type_tasks if t.get("success")]
            if not successes:
                continue
            # Find most common context mode combo in successful tasks
            mode_counts: dict[str, int] = {}
            for t in successes:
                key = ",".join(sorted(t.get("context_modes", [])))
                mode_counts[key] = mode_counts.get(key, 0) + 1
            best = max(mode_counts, key=mode_counts.get)
            avg_tokens = sum(t["tokens_used"] for t in successes) // len(successes)
            insights[tt] = {
                "best_modes": best.split(","),
                "avg_tokens": avg_tokens,
                "success_rate": len(successes) / len(type_tasks),
                "sample_size": len(type_tasks),
            }

        with open(self._insights, "w") as f:
            json.dump(insights, f, indent=2)

    def get_recommendation(self, task_type: str) -> dict | None:
        """Get recommended context strategy for a task type."""
        if not self._insights.exists():
            return None
        try:
            insights = json.loads(self._insights.read_text())
            return insights.get(task_type)
        except (json.JSONDecodeError, OSError):
            return None

    def summary(self) -> str:
        tasks = self._load_tasks()
        if not tasks:
            return "No task data yet. Use tempo to build learning data."

        total = len(tasks)
        successes = sum(1 for t in tasks if t.get("success"))
        total_tokens = sum(t.get("tokens_used", 0) for t in tasks)

        lines = [
            f"Learning Engine: {total} tasks recorded",
            f"  Success rate: {successes}/{total} ({successes/total:.0%})",
            f"  Total tokens: {total_tokens:,}",
            "",
        ]

        if self._insights.exists():
            try:
                insights = json.loads(self._insights.read_text())
                lines.append("Learned strategies:")
                for tt, info in insights.items():
                    lines.append(
                        f"  {tt}: use [{', '.join(info['best_modes'])}] "
                        f"(~{info['avg_tokens']:,} tok, {info['success_rate']:.0%} success, n={info['sample_size']})"
                    )
            except (json.JSONDecodeError, OSError):
                pass

        return "\n".join(lines)


def run(graph, **kwargs) -> str:
    mem = TaskMemory(graph.root)
    return mem.summary()
