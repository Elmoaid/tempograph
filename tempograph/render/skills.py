from __future__ import annotations

from ..types import Tempo

def render_skills(graph: Tempo, query: str = "", *, max_tokens: int = 4000) -> str:
    """Return a catalog of coding patterns and conventions for this codebase.

    Useful for agents that need to write new code following project conventions
    (naming, plugin structure, module roles, repeated idioms).
    """
    try:
        from tempo.plugins.skills import get_patterns
        return get_patterns(graph, query=query, max_tokens=max_tokens)
    except ImportError:
        return "Skills plugin not available. Install tempo package."
