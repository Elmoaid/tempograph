PLUGIN = {
    "name": "diff",
    "depends": [],
    "provides": ["diff"],
    "default": True,
    "description": "Git diff context — structural context for changed files",
}

def run(graph, *, changed_files: list[str] | None = None, max_tokens: int = 6000, **kwargs) -> str:
    from tempograph.render import render_diff_context
    return render_diff_context(graph, changed_files or [], max_tokens=max_tokens)
