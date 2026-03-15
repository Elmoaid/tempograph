PLUGIN = {
    "name": "hotspots",
    "depends": [],
    "provides": ["hotspots"],
    "default": True,
    "description": "Complexity and coupling hotspots — refactor targets",
}

def run(graph, *, top_n: int = 20, **kwargs) -> str:
    from tempograph.render import render_hotspots
    return render_hotspots(graph, top_n=top_n)
