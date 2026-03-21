from __future__ import annotations

from ..types import Tempo, EdgeKind, FileInfo, Language

def render_dependencies(graph: Tempo) -> str:
    """Render dependency analysis: circular imports and layer structure."""
    lines = ["Dependency Analysis:", ""]

    cycles = graph.detect_circular_imports()
    if cycles:
        lines.append(f"CIRCULAR IMPORTS ({len(cycles)} cycles):")
        for i, cycle in enumerate(cycles[:10], 1):
            chain = " → ".join(c.rsplit("/", 1)[-1] for c in cycle)
            lines.append(f"  {i}. {chain}")
        if len(cycles) > 10:
            lines.append(f"  ... +{len(cycles) - 10} more")
        lines.append("")
    else:
        lines.append("No circular imports detected.")
        lines.append("")

    layers = graph.dependency_layers()
    lines.append(f"Dependency layers ({len(layers)} levels):")
    for i, layer in enumerate(layers):
        if len(layer) > 10:
            shown = ", ".join(f.rsplit("/", 1)[-1] for f in layer[:8])
            lines.append(f"  Layer {i}: {shown} ... +{len(layer) - 8} more ({len(layer)} total)")
        else:
            shown = ", ".join(f.rsplit("/", 1)[-1] for f in layer)
            lines.append(f"  Layer {i}: {shown}")

    return "\n".join(lines)


def render_architecture(graph: Tempo) -> str:
    """High-level architecture view: modules, their roles, and inter-module dependencies."""
    # Group files into modules (top-level directories)
    modules: dict[str, list[str]] = {}
    for fp in sorted(graph.files):
        parts = fp.split("/")
        module = parts[0] if len(parts) > 1 else "."
        modules.setdefault(module, []).append(fp)

    # Build inter-module import edges
    import_edges: dict[str, dict[str, int]] = {}  # source_module → {target_module: count}
    for edge in graph.edges:
        if edge.kind == EdgeKind.IMPORTS:
            src_parts = edge.source_id.split("/")
            tgt_parts = edge.target_id.split("/")
            src_mod = src_parts[0] if len(src_parts) > 1 else "."
            tgt_mod = tgt_parts[0] if len(tgt_parts) > 1 else "."
            if src_mod != tgt_mod:
                import_edges.setdefault(src_mod, {})
                import_edges[src_mod][tgt_mod] = import_edges[src_mod].get(tgt_mod, 0) + 1

    # Cross-module call edges
    call_edges: dict[str, dict[str, int]] = {}
    for edge in graph.edges:
        if edge.kind in (EdgeKind.CALLS, EdgeKind.RENDERS):
            src_file = graph.symbols[edge.source_id].file_path if edge.source_id in graph.symbols else ""
            tgt_file = graph.symbols[edge.target_id].file_path if edge.target_id in graph.symbols else ""
            if src_file and tgt_file:
                src_parts = src_file.split("/")
                tgt_parts = tgt_file.split("/")
                src_mod = src_parts[0] if len(src_parts) > 1 else "."
                tgt_mod = tgt_parts[0] if len(tgt_parts) > 1 else "."
                if src_mod != tgt_mod:
                    call_edges.setdefault(src_mod, {})
                    call_edges[src_mod][tgt_mod] = call_edges[src_mod].get(tgt_mod, 0) + 1

    lines = ["Architecture Overview:", ""]

    # Module summary
    lines.append("Modules:")
    for mod in sorted(modules, key=lambda m: -len(modules[m])):
        files = modules[mod]
        # Gather stats for this module
        total_lines = sum(graph.files[f].line_count for f in files if f in graph.files)
        sym_count = sum(len(graph.files[f].symbols) for f in files if f in graph.files)
        langs = set(graph.files[f].language.value for f in files if f in graph.files)
        lang_str = ", ".join(sorted(langs))
        lines.append(f"  {mod}/ — {len(files)} files, {sym_count} symbols, {total_lines:,} lines [{lang_str}]")

        # Top exported symbols in this module
        top_syms = []
        for f in files:
            for sid in graph.files.get(f, FileInfo("", Language.UNKNOWN, 0, 0)).symbols:
                sym = graph.symbols.get(sid)
                if sym and sym.exported and sym.parent_id is None:
                    top_syms.append(sym)
        top_syms.sort(key=lambda s: -s.line_count)
        if top_syms:
            shown = top_syms[:5]
            names = ", ".join(f"{s.name}({s.kind.value})" for s in shown)
            extra = f" +{len(top_syms) - 5}" if len(top_syms) > 5 else ""
            lines.append(f"    exports: {names}{extra}")
    lines.append("")

    # Inter-module dependencies
    all_deps = {}
    for src in set(list(import_edges.keys()) + list(call_edges.keys())):
        targets: dict[str, int] = {}
        for tgt, n in import_edges.get(src, {}).items():
            targets[tgt] = targets.get(tgt, 0) + n
        for tgt, n in call_edges.get(src, {}).items():
            targets[tgt] = targets.get(tgt, 0) + n
        if targets:
            all_deps[src] = targets

    if all_deps:
        lines.append("Module dependencies:")
        for src in sorted(all_deps, key=lambda s: -sum(all_deps[s].values())):
            targets = sorted(all_deps[src].items(), key=lambda x: -x[1])
            dep_str = ", ".join(f"{tgt}({n})" for tgt, n in targets[:6])
            extra = f" +{len(targets) - 6}" if len(targets) > 6 else ""
            lines.append(f"  {src} → {dep_str}{extra}")

        # Circular module dependency detection: flag A→B→A at the module level.
        # File-level cycles are detected by deps mode; this is the coarser module view.
        _circ_pairs: list[tuple[str, str]] = []
        for _m1 in all_deps:
            for _m2 in all_deps.get(_m1, {}):
                if _m1 in all_deps.get(_m2, {}):
                    _pair = tuple(sorted([_m1, _m2]))
                    if _pair not in _circ_pairs:
                        _circ_pairs.append(_pair)  # type: ignore[arg-type]
        if _circ_pairs:
            _cp_str = ", ".join(f"{a} ↔ {b}" for a, b in _circ_pairs)
            lines.append(f"\n⚠ circular module deps: {_cp_str} — architectural smell, consider decoupling")

        # S117: Hub modules — modules imported by many others.
        # High in-degree = core infrastructure; changes here ripple everywhere.
        # Only shown when 2+ modules import from a single module AND the
        # total edge count is significant (>= 10).
        _in_degree: dict[str, int] = {}
        _in_modules: dict[str, set[str]] = {}
        for _src_mod, _targets in all_deps.items():
            for _tgt_mod, _n in _targets.items():
                _in_degree[_tgt_mod] = _in_degree.get(_tgt_mod, 0) + _n
                _in_modules.setdefault(_tgt_mod, set()).add(_src_mod)
        _hubs = [
            (_in_degree[m], len(_in_modules[m]), m)
            for m in _in_degree
            if len(_in_modules[m]) >= 2 and _in_degree[m] >= 10
        ]
        if _hubs:
            _hubs.sort(key=lambda x: -x[0])
            _hub_parts = [f"{m} ({n_mods} dependents, {n_edges} edges)" for n_edges, n_mods, m in _hubs[:2]]
            lines.append(f"Hub modules: {', '.join(_hub_parts)} — high change risk")
    else:
        lines.append("No cross-module dependencies detected.")

    return "\n".join(lines)
