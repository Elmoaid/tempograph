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
    """Generate a usage + feedback summary for a repo."""
    tdir = Path(repo_path).resolve() / ".tempograph"
    usage = _load_jsonl(tdir / "usage.jsonl")
    feedback = _load_jsonl(tdir / "feedback.jsonl")

    if not usage and not feedback:
        return "No telemetry data found. Run tempograph to generate usage data."

    lines: list[str] = ["Tempograph Usage Report", "=" * 40, ""]

    # ── Usage summary ──
    if usage:
        lines.append(f"Total invocations: {len(usage)}")
        sources = Counter(e.get("source", "unknown") for e in usage)
        lines.append(f"Sources: {', '.join(f'{k}({v})' for k, v in sources.most_common())}")
        lines.append("")

        # By mode/tool
        mode_counts: Counter = Counter()
        mode_tokens: defaultdict[str, list[int]] = defaultdict(list)
        mode_empty: Counter = Counter()
        for e in usage:
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
