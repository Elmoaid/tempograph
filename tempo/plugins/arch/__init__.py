PLUGIN = {
    "name": "arch",
    "depends": [],
    "provides": ["arch"],
    "default": True,
    "description": "Architecture layers — module grouping and dependency direction",
}

def run(graph, **kwargs) -> str:
    from tempograph.render import render_architecture
    return render_architecture(graph)
