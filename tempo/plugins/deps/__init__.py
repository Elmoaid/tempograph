PLUGIN = {
    "name": "deps",
    "depends": [],
    "provides": ["deps"],
    "default": True,
    "description": "External dependency analysis",
}

def run(graph, **kwargs) -> str:
    from tempograph.render import render_dependencies
    return render_dependencies(graph)
