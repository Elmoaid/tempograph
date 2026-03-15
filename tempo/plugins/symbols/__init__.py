PLUGIN = {
    "name": "symbols",
    "depends": [],
    "provides": ["symbols"],
    "default": True,
    "description": "Full symbol index with signatures",
}

def run(graph, **kwargs) -> str:
    from tempograph.render import render_symbols
    return render_symbols(graph)
