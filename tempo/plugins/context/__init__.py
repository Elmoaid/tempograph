"""Context engine — optimal context selection for any task.

Given a task description, selects the minimal set of structural context
that maximizes the probability of correct task completion while minimizing
token spend.

Strategy:
1. Extract key symbols/files from the task description
2. Build a relevance-scored subgraph around those symbols
3. Budget tokens: high-relevance context first, cut when budget reached
4. Return structured context ready for LLM consumption
"""
from __future__ import annotations

from tempograph.builder import build_graph
from tempograph.render import render_focused, render_blast_radius, render_overview, count_tokens

PLUGIN = {
    "name": "context",
    "depends": ["focus", "blast", "overview"],
    "provides": ["context"],
    "default": True,
    "description": "Optimal context selection — right context, minimum tokens",
}


def run(graph, *, query: str = "", file: str = "", max_tokens: int = 4000, **kwargs) -> str:
    task_type = kwargs.get("task_type") or ""
    return select_context(graph, task=query, target_file=file, budget=max_tokens, task_type=task_type)


def select_context(graph, *, task: str, target_file: str = "", budget: int = 4000, task_type: str = "") -> str:
    """Select optimal context for a task within a token budget.

    Priority order (highest signal per token):
    1. Blast radius of target file (if provided) — direct impact map
    2. Focused subgraph around task keywords — structural neighbors
    3. Overview — fallback orientation

    If task_type is provided, appends L2 learned strategy hint from TaskMemory.
    """
    parts: list[tuple[int, str]] = []  # (priority, content)
    used = 0

    # Priority 1: blast radius of target file
    if target_file and target_file in graph.files:
        blast = render_blast_radius(graph, target_file, query=task)
        cost = count_tokens(blast)
        if cost <= budget * 0.5:  # cap at 50% of budget
            parts.append((1, blast))
            used += cost

    # Priority 2: focused subgraph on task keywords
    if task:
        keywords = _extract_task_keywords(task)
        remaining = budget - used
        per_keyword = max(500, remaining // max(len(keywords), 1))
        for kw in keywords[:3]:
            focused = render_focused(graph, kw, max_tokens=per_keyword)
            if "No symbols matching" in focused:
                continue
            cost = count_tokens(focused)
            if used + cost <= budget:
                parts.append((2, focused))
                used += cost

    # Priority 3: overview as orientation (only if budget allows)
    if used < budget * 0.8:
        overview = render_overview(graph)
        cost = count_tokens(overview)
        if used + cost <= budget:
            parts.append((3, overview))
            used += cost

    if not parts:
        return render_overview(graph)

    # Sort by priority, join
    parts.sort(key=lambda x: x[0])
    sections = [content for _, content in parts]

    l2_hint = _get_l2_hint(graph.root, task_type) if task_type else ""
    header = f"[tempo context: {used:,} tokens, {len(sections)} sections{', ' + l2_hint if l2_hint else ''}]"
    return f"{header}\n\n" + "\n\n---\n\n".join(sections)


def _get_l2_hint(repo_path: str, task_type: str) -> str:
    """Return a short L2 strategy hint for task_type, or empty string if unavailable."""
    try:
        from tempo.plugins.learn import TaskMemory
        rec = TaskMemory(repo_path).get_recommendation(task_type)
        if rec and rec.get("best_modes"):
            modes = "+".join(rec["best_modes"])
            rate = int(rec.get("success_rate", 0) * 100)
            return f"L2({task_type}): try {modes} ({rate}% success)"
    except Exception:
        pass
    return ""


def _extract_task_keywords(task: str) -> list[str]:
    """Extract likely symbol names from a task description."""
    import re
    identifiers = re.findall(r'\b[A-Z][a-zA-Z0-9]+\b|\b[a-z_][a-z0-9_]{2,}\b', task)
    skip = {
        "the", "and", "for", "from", "with", "this", "that", "fix", "add",
        "update", "remove", "change", "bug", "feature", "use", "make", "new",
        "when", "not", "all", "can", "should", "would", "into", "also",
        "file", "function", "method", "class", "code", "need", "want",
        "like", "just", "please", "help", "how", "what", "why", "where",
    }
    seen = set()
    result = []
    for ident in identifiers:
        lower = ident.lower()
        if lower not in skip and lower not in seen and len(ident) > 2:
            seen.add(lower)
            result.append(ident)
    return result
