PLUGIN = {
    "name": "overview",
    "depends": [],
    "provides": ["overview"],
    "default": True,
    "description": "High-level repo summary — files, languages, symbols, structure",
}

def run(graph, **kwargs) -> str:
    from tempograph.render import render_overview
    return render_overview(graph)
