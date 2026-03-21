from __future__ import annotations

from ..types import Tempo, EdgeKind

def _extract_name_from_question(question: str) -> str:
    """Extract the likely symbol/file name from a natural language question."""
    q = question.strip().rstrip("?")
    for prefix in (
        "where is", "find", "locate", "definition of",
        "what calls", "who calls", "who uses", "callers of", "references to",
        "what does", "dependencies of", "callees of",
        "who imports", "what imports", "imported by", "what files import",
        "what renders", "show me",
        "what implements", "what extends", "subtypes of", "subclasses of",
        "what inherits from", "who inherits",
    ):
        if q.lower().startswith(prefix):
            q = q[len(prefix):].strip()
            break
    for suffix in ("defined", "called", "used", "rendered", "call", "import",
                    "class", "function", "method", "module", "interface", "type"):
        if q.lower().endswith(suffix):
            q = q[:-(len(suffix))].strip()
    # Strip articles and noise words
    for article in ("the", "a", "an"):
        if q.lower().startswith(article + " "):
            q = q[len(article) + 1:]
    q = q.strip("'\"` ")
    return q

def render_lookup(graph: Tempo, question: str) -> str:
    """Answer a specific question about the codebase."""
    q = question.lower()

    # "where is X defined?"
    if any(w in q for w in ("where is", "find", "locate", "definition of")):
        name = _extract_name_from_question(question)
        if name:
            symbols = graph.find_symbol(name)
            if symbols:
                lines = [f"'{name}' found in {len(symbols)} location(s):"]
                for sym in symbols[:10]:
                    lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.kind.value} {sym.qualified_name}")
                    if sym.signature:
                        lines.append(f"    {sym.signature[:150]}")
                    callers = graph.callers_of(sym.id)
                    if callers:
                        lines.append(f"    called by: {', '.join(c.qualified_name for c in callers[:5])}")
                    # S113: Rename risk — cross-file caller count tells agents how risky it is
                    # to rename or change this symbol's signature. HIGH = widespread use across
                    # many files; LOW = local use only. Only shown for functions/methods/classes.
                    if callers and sym.kind.value in ("function", "method", "class", "interface"):
                        _rn_files = len({c.file_path for c in callers if c.file_path != sym.file_path})
                        _rn_count = len(callers)
                        if _rn_files >= 3 or _rn_count >= 8:
                            lines.append(f"    rename risk: HIGH ({_rn_count} callers, {_rn_files} files)")
                        elif _rn_files >= 1 and _rn_count >= 2:
                            lines.append(f"    rename risk: MEDIUM ({_rn_count} callers, {_rn_files} external files)")
                return "\n".join(lines)
            else:
                # Fuzzy search
                results = graph.search_symbols(name)
                if results:
                    lines = [f"No exact match for '{name}'. Similar:"]
                    for sym in results[:5]:
                        lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.qualified_name}")
                    return "\n".join(lines)
                return f"'{name}' not found in the codebase."

    # "what calls X?" / "who uses X?"
    if any(w in q for w in ("what calls", "who calls", "who uses", "callers of", "references to")):
        name = _extract_name_from_question(question)
        if name:
            symbols = graph.find_symbol(name)
            if symbols:
                lines = []
                for sym in symbols[:3]:
                    callers = graph.callers_of(sym.id)
                    if callers:
                        lines.append(f"'{sym.qualified_name}' is called by:")
                        for c in callers[:15]:
                            lines.append(f"  {c.file_path}:{c.line_start} — {c.qualified_name}")
                    else:
                        lines.append(f"'{sym.qualified_name}' has no recorded callers.")
                return "\n".join(lines) if lines else f"'{name}' not found."
            return f"'{name}' not found."

    # "what does X call?" / "dependencies of X"
    if any(w in q for w in ("what does", "calls what", "dependencies", "callees")):
        name = _extract_name_from_question(question)
        if name:
            symbols = graph.find_symbol(name)
            if symbols:
                lines = []
                for sym in symbols[:3]:
                    callees = graph.callees_of(sym.id)
                    if callees:
                        lines.append(f"'{sym.qualified_name}' calls:")
                        for c in callees[:15]:
                            lines.append(f"  {c.file_path}:{c.line_start} — {c.qualified_name}")
                    else:
                        lines.append(f"'{sym.qualified_name}' has no recorded callees.")
                return "\n".join(lines) if lines else f"'{name}' not found."
            return f"'{name}' not found."

    # "what files import X?" / "who imports X?"
    if any(w in q for w in ("imports", "imported by", "who imports")):
        name = _extract_name_from_question(question)
        if name:
            # Search in file paths
            matching_files = [fp for fp in graph.files if name.lower() in fp.lower()]
            if matching_files:
                lines = []
                for fp in matching_files[:5]:
                    importers = graph.importers_of(fp)
                    if importers:
                        lines.append(f"'{fp}' is imported by:")
                        for imp in importers[:10]:
                            lines.append(f"  {imp}")
                    else:
                        lines.append(f"'{fp}' has no recorded importers.")
                return "\n".join(lines)

    # "what renders X?" / "where is X rendered?"
    if any(w in q for w in ("renders", "rendered", "jsx", "component tree")):
        name = _extract_name_from_question(question)
        if name:
            render_edges = [e for e in graph.edges if e.kind == EdgeKind.RENDERS and name.lower() in e.target_id.lower()]
            if render_edges:
                lines = [f"'{name}' is rendered by:"]
                for e in render_edges[:10]:
                    src = graph.symbols.get(e.source_id)
                    if src:
                        lines.append(f"  {src.file_path}:{e.line} — {src.qualified_name}")
                return "\n".join(lines)

    # "what implements X?" / "what extends X?" / "subtypes of X"
    if any(w in q for w in ("implements", "extends", "subtype", "subclass", "inherits from")):
        name = _extract_name_from_question(question)
        if name:
            subtypes = graph.subtypes_of(name)
            if subtypes:
                lines = [f"'{name}' is implemented/extended by:"]
                for sym in subtypes[:15]:
                    edge_kind = "implements" if any(
                        e.kind == EdgeKind.IMPLEMENTS and e.target_id == name and e.source_id == sym.id
                        for e in graph.edges
                    ) else "extends"
                    lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.qualified_name} ({edge_kind})")
                return "\n".join(lines)

    # Fallback: treat as search
    results = graph.search_symbols(question)
    if results:
        lines = [f"Search results for '{question}':"]
        for sym in results[:15]:
            lines.append(f"  {sym.file_path}:{sym.line_start} — {sym.kind.value} {sym.qualified_name}")
            if sym.signature:
                lines.append(f"    {sym.signature[:120]}")
        return "\n".join(lines)

    return f"No results for '{question}'."
