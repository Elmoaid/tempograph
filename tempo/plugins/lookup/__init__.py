PLUGIN = {
    "name": "lookup",
    "depends": [],
    "provides": ["lookup"],
    "default": True,
    "description": "Answer specific questions: where is X, what calls X, who imports X",
}

def run(graph, *, query: str = "", **kwargs) -> str:
    from tempograph.render import render_lookup
    return render_lookup(graph, query)
