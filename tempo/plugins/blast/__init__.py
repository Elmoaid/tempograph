PLUGIN = {
    "name": "blast",
    "depends": [],
    "provides": ["blast"],
    "default": True,
    "description": "Blast radius analysis — what breaks if you change a file",
}

def run(graph, *, file: str = "", query: str = "", **kwargs) -> str:
    from tempograph.render import render_blast_radius
    return render_blast_radius(graph, file, query)
