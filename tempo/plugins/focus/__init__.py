PLUGIN = {
    "name": "focus",
    "depends": [],
    "provides": ["focus"],
    "default": True,
    "description": "Task-specific subgraph based on a query — BFS expansion with token budget",
}

def run(graph, *, query: str = "", max_tokens: int = 4000, **kwargs) -> str:
    from tempograph.render import render_focused
    return render_focused(graph, query, max_tokens=max_tokens)
