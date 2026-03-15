PLUGIN = {
    "name": "map",
    "depends": [],
    "provides": ["map"],
    "default": True,
    "description": "File tree with top symbols per file",
}

def run(graph, **kwargs) -> str:
    from tempograph.render import render_map
    return render_map(graph)
