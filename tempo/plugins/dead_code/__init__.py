PLUGIN = {
    "name": "dead_code",
    "depends": [],
    "provides": ["dead"],
    "default": True,
    "description": "Dead code detection with confidence scoring",
}

def run(graph, *, max_symbols: int = 50, **kwargs) -> str:
    from tempograph.render import render_dead_code
    return render_dead_code(graph, max_symbols=max_symbols)
