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


_MODE_TO_TASK_TYPE = {
    "focus": "code_navigation",
    "blast": "code_navigation",
    "lookup": "code_navigation",
    "dead": "cleanup",
    "hotspots": "refactor",
    "diff": "code_review",
    "overview": "orientation",
    "arch": "architecture",
    "deps": "dependency_audit",
    "context": "task_preparation",
    "quality": "output_review",
}


def infer_from_telemetry(repo_path: str) -> int:
    """Infer task outcomes from usage + feedback telemetry. Returns count of new records."""
    import hashlib
    from datetime import datetime, timezone
    from pathlib import Path

    usage_path = Path(repo_path) / ".tempograph" / "usage.jsonl"
    feedback_path = Path(repo_path) / ".tempograph" / "feedback.jsonl"

    if not usage_path.exists():
        return 0

    usage_lines = [json.loads(l) for l in usage_path.read_text().splitlines() if l.strip()]
    feedback_lines = []
    if feedback_path.exists():
        feedback_lines = [json.loads(l) for l in feedback_path.read_text().splitlines() if l.strip()]

    # Group usage into sessions (gap > 20 min = new session)
    sessions: list[list[dict]] = []
    current: list[dict] = []
    prev_ts = None
    for entry in sorted(usage_lines, key=lambda x: x.get("ts", "")):
        ts_str = entry.get("ts", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue
        if prev_ts and (ts - prev_ts).total_seconds() > 1200:  # 20 min gap
            if current:
                sessions.append(current)
                current = []
        current.append(entry)
        prev_ts = ts
    if current:
        sessions.append(current)

    # Build feedback lookup by timestamp proximity
    def find_feedback(session_end_ts, window_secs=3600):
        results = []
        for fb in feedback_lines:
            try:
                fb_ts = datetime.fromisoformat(fb.get("ts", ""))
                delta = abs((fb_ts - session_end_ts).total_seconds())
                if delta <= window_secs:
                    results.append(fb)
            except (ValueError, TypeError):
                pass
        return results

    mem = TaskMemory(repo_path)
    existing_tasks = mem._load_tasks()

    # Hash existing sessions to avoid duplicates
    def session_hash(session):
        key = "|".join(sorted(e.get("ts", "") for e in session))
        return hashlib.md5(key.encode()).hexdigest()[:8]

    existing_hashes = {t.get("session_hash") for t in existing_tasks if t.get("session_hash")}

    new_count = 0
    for session in sessions:
        sh = session_hash(session)
        if sh in existing_hashes:
            continue

        modes = [e.get("mode") for e in session if e.get("mode") not in ("stats", "report")]
        if not modes:
            continue

        total_tokens = sum(e.get("tokens", 0) for e in session)
        empty_count = sum(1 for e in session if e.get("empty"))

        # Determine task type from dominant mode
        mode_weights: dict[str, int] = {}
        for m in modes:
            tt = _MODE_TO_TASK_TYPE.get(m)
            if tt:
                mode_weights[tt] = mode_weights.get(tt, 0) + 1
        task_type = max(mode_weights, key=mode_weights.get) if mode_weights else "general"

        # Determine success: prefer explicit feedback, fall back to heuristic
        last_ts_str = session[-1].get("ts", "")
        try:
            last_ts = datetime.fromisoformat(last_ts_str)
        except ValueError:
            last_ts = datetime.now(timezone.utc)

        feedbacks = find_feedback(last_ts)
        if feedbacks:
            success = any(f.get("helpful") is True for f in feedbacks)
        else:
            # Heuristic: success if < 50% empty results
            success = empty_count < len(session) * 0.5

        entry = {
            "ts": time.time(),
            "task_type": task_type,
            "context_modes": list(dict.fromkeys(modes)),
            "tokens_used": total_tokens,
            "success": success,
            "notes": f"inferred from {len(session)} usage events",
            "session_hash": sh,
        }
        with open(mem._log, "a") as f:
            f.write(json.dumps(entry) + "\n")
        new_count += 1

    if new_count > 0:
        mem._update_insights()

    return new_count


def run(graph, **kwargs) -> str:
    mem = TaskMemory(graph.root)
    # Auto-populate from telemetry on each invocation
    new = infer_from_telemetry(graph.root)
    suffix = f"\n\n[Auto-inferred {new} new sessions from telemetry]" if new else ""
    return mem.summary() + suffix
