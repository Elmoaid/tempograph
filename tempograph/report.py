"""Usage and feedback report — reads .tempograph/ telemetry files."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def generate_report(repo_path: str) -> str:
    """Generate a usage + feedback summary. Reads local + global (cross-repo) data."""
    tdir = Path(repo_path).resolve() / ".tempograph"
    global_dir = Path.home() / ".tempograph" / "global"

    usage = _load_jsonl(tdir / "usage.jsonl")
    feedback = _load_jsonl(tdir / "feedback.jsonl")

    # Merge global cross-repo data (dedup by timestamp)
    global_usage = _load_jsonl(global_dir / "usage.jsonl")
    global_feedback = _load_jsonl(global_dir / "feedback.jsonl")
    local_ts = {e.get("ts") for e in usage}
    usage += [e for e in global_usage if e.get("ts") not in local_ts]
    local_fb_ts = {e.get("ts") for e in feedback}
    feedback += [e for e in global_feedback if e.get("ts") not in local_fb_ts]

    if not usage and not feedback:
        return "No telemetry data found. Run tempograph to generate usage data."

    lines: list[str] = ["Tempograph Usage Report", "=" * 40, ""]

    # ── Cross-repo summary ──
    repos = Counter(e.get("repo", "unknown") for e in usage)
    if len(repos) > 1 or (len(repos) == 1 and list(repos.keys())[0] != Path(repo_path).name):
        lines.append("Repos using tempograph:")
        for repo, count in repos.most_common():
            lines.append(f"  {repo:<30} {count:>4} invocations")
        lines.append("")

    # ── Usage summary ──
    if usage:
        # Filter out internal 'stats' mode — it's telemetry noise from pulse tasks
        agent_usage = [e for e in usage if (e.get("mode") or e.get("tool", "unknown")) != "stats"]
        stats_filtered = len(usage) - len(agent_usage)
        filtered_note = f" ({stats_filtered} internal stats filtered)" if stats_filtered else ""
        lines.append(f"Total invocations: {len(agent_usage)}{filtered_note}")
        sources = Counter(e.get("source", "unknown") for e in agent_usage)
        lines.append(f"Sources: {', '.join(f'{k}({v})' for k, v in sources.most_common())}")
        lines.append("")
        mode_counts: Counter = Counter()
        mode_tokens: defaultdict[str, list[int]] = defaultdict(list)
        mode_empty: Counter = Counter()
        for e in agent_usage:
            mode = e.get("mode") or e.get("tool", "unknown")
            mode_counts[mode] += 1
            if "tokens" in e:
                mode_tokens[mode].append(e["tokens"])
            if e.get("empty"):
                mode_empty[mode] += 1

        lines.append("Invocations by mode:")
        for mode, count in mode_counts.most_common():
            avg_tok = ""
            if mode_tokens[mode]:
                avg = sum(mode_tokens[mode]) // len(mode_tokens[mode])
                avg_tok = f"  avg {avg:,} tok"
            empty_pct = ""
            if mode_empty[mode]:
                pct = mode_empty[mode] / count * 100
                empty_pct = f"  {pct:.0f}% empty"
            bar = "#" * min(count, 30)
            lines.append(f"  {mode:<16} {count:>4}  {bar}{avg_tok}{empty_pct}")
        lines.append("")

        # Duration stats
        durations = [e["duration_ms"] for e in usage if "duration_ms" in e]
        if durations:
            lines.append(f"Duration: avg {sum(durations) // len(durations)}ms, max {max(durations)}ms")
            lines.append("")

    # ── Feedback summary ──
    if feedback:
        lines.append("Feedback Summary")
        lines.append("-" * 30)
        helpful = sum(1 for f in feedback if f.get("helpful"))
        unhelpful = len(feedback) - helpful
        lines.append(f"Total: {len(feedback)} reports ({helpful} helpful, {unhelpful} unhelpful)")
        lines.append("")

        # Unhelpful modes
        unhelpful_modes = Counter(
            f.get("mode", "unknown") for f in feedback if not f.get("helpful")
        )
        if unhelpful_modes:
            lines.append("Modes marked unhelpful:")
            for mode, count in unhelpful_modes.most_common():
                lines.append(f"  {mode}: {count}x")
            lines.append("")

        # Notes
        notes = [f for f in feedback if f.get("note")]
        if notes:
            lines.append("Recent feedback notes:")
            for f in notes[-5:]:
                mode = f.get("mode", "?")
                helpful_str = "+" if f.get("helpful") else "-"
                lines.append(f"  [{helpful_str}] {mode}: {f['note'][:100]}")
            lines.append("")

    return "\n".join(lines)
