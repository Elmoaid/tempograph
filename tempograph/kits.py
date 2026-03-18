"""Kit system — composable task bundles combining multiple tempograph modes.

A kit is a named workflow that runs multiple modes in sequence and composes
their outputs into a single token-budgeted response.

Usage (CLI):  python3 -m tempograph . --kit explore
              python3 -m tempograph . --kit deep_dive --query render_focused
              python3 -m tempograph . --kit list
Usage (MCP):  run_kit(repo_path=".", kit="explore", query="main")
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .render import (
    count_tokens,
    render_architecture,
    render_blast_radius,
    render_dead_code,
    render_dependencies,
    render_diff_context,
    render_focused,
    render_hotspots,
    render_map,
    render_overview,
    render_skills,
)
from .types import Tempo


@dataclass
class KitDefinition:
    """A composable kit: ordered steps, per-step token weights, composition strategy."""
    steps: list[str]
    weights: dict[str, float]
    composition: str = "concat"
    description: str = ""
    conditions: dict[str, str] = field(default_factory=dict)


def _render_step(graph: Tempo, mode: str, query: str, max_tokens: int) -> str:
    """Render a single kit step. Returns empty string for unknown modes."""
    if mode == "overview":
        return render_overview(graph)
    elif mode == "hotspots":
        return render_hotspots(graph)
    elif mode == "focus":
        return render_focused(graph, query or "main", max_tokens=max_tokens)
    elif mode == "blast":
        return render_blast_radius(graph, "", query=query)
    elif mode == "dead":
        return render_dead_code(graph, max_tokens=max_tokens)
    elif mode == "arch":
        return render_architecture(graph)
    elif mode == "deps":
        return render_dependencies(graph)
    elif mode == "map":
        return render_map(graph, max_tokens=max_tokens)
    elif mode == "skills":
        return render_skills(graph, query, max_tokens=max_tokens)
    elif mode == "diff":
        return render_diff_context(graph, [], max_tokens=max_tokens)
    return f"[unknown mode: {mode}]"


# ── Built-in kits ─────────────────────────────────────────────────

_BUILTIN_KITS: dict[str, KitDefinition] = {
    "explore": KitDefinition(
        steps=["overview", "hotspots"],
        weights={"overview": 0.5, "hotspots": 0.5},
        description="Orient to a new codebase — structure overview + complexity hotspots.",
    ),
    "deep_dive": KitDefinition(
        steps=["focus", "blast"],
        weights={"focus": 0.6, "blast": 0.4},
        description="Deep-dive into a symbol — focused context + blast radius.",
    ),
    "change_prep": KitDefinition(
        steps=["diff", "focus"],
        weights={"diff": 0.5, "focus": 0.5},
        description="Prepare for a code change — diff context + focused symbol context.",
    ),
    "code_review": KitDefinition(
        steps=["dead", "hotspots", "focus"],
        weights={"dead": 0.3, "hotspots": 0.3, "focus": 0.4},
        description="Code review workflow — dead code + hotspot risk + symbol focus.",
    ),
    "health": KitDefinition(
        steps=["hotspots", "dead"],
        weights={"hotspots": 0.5, "dead": 0.5},
        description="Codebase health check — complexity hotspots + dead code candidates.",
    ),
}


def _load_custom_kits(repo_path: str) -> dict[str, KitDefinition]:
    """Load custom kits from .tempo/kits.json. Graceful on missing or malformed file."""
    kits_path = Path(repo_path) / ".tempo" / "kits.json"
    if not kits_path.exists():
        return {}
    try:
        raw = json.loads(kits_path.read_text())
        custom: dict[str, KitDefinition] = {}
        for name, spec in raw.items():
            steps = spec.get("steps", [])
            if not steps:
                continue
            n = len(steps)
            weights = spec.get("weights", {s: 1.0 / n for s in steps})
            custom[name] = KitDefinition(
                steps=steps,
                weights=weights,
                composition=spec.get("composition", "concat"),
                description=spec.get("description", ""),
            )
        return custom
    except (json.JSONDecodeError, OSError, AttributeError):
        return {}


def get_all_kits(repo_path: str = "") -> dict[str, KitDefinition]:
    """Return builtin kits merged with any custom kits from repo."""
    kits = {**_BUILTIN_KITS}
    if repo_path:
        kits.update(_load_custom_kits(repo_path))
    return kits


def list_kits(repo_path: str = "") -> dict[str, str]:
    """Return all available kits as name → description mapping."""
    return {name: kit.description for name, kit in get_all_kits(repo_path).items()}


def execute_kit(graph: Tempo, kit: KitDefinition, query: str = "", max_tokens: int = 4000) -> str:
    """Run all steps in a kit and compose into a token-budgeted output.

    Distributes max_tokens across steps by weight, renders each step,
    trims to budget, and concatenates with section headers.
    """
    if not kit.steps:
        return ""

    total_weight = sum(kit.weights.get(step, 1.0) for step in kit.steps)
    parts: list[str] = []
    remaining_tokens = max_tokens

    for i, step in enumerate(kit.steps):
        if remaining_tokens <= 50:
            break

        step_weight = kit.weights.get(step, 1.0)
        is_last = (i == len(kit.steps) - 1)
        step_tokens = remaining_tokens if is_last else max(200, int(max_tokens * step_weight / total_weight))

        output = _render_step(graph, step, query, step_tokens)
        if not output.strip():
            continue

        # Line-based trim if over budget
        if count_tokens(output) > step_tokens:
            lines = output.splitlines()
            trimmed: list[str] = []
            used = 0
            for line in lines:
                lt = count_tokens(line)
                if used + lt > step_tokens:
                    trimmed.append(f"[... trimmed at {step_tokens}-token budget ...]")
                    break
                trimmed.append(line)
                used += lt
            output = "\n".join(trimmed)

        parts.append(f"── {step.upper()} ──\n{output}")
        remaining_tokens -= count_tokens(output)

    return "\n\n".join(parts)
