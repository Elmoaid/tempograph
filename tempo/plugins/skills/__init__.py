"""Skills library — coding pattern catalog for this codebase.

Extracts repeated patterns, conventions, and idioms from the code graph.
Agents use this to write code that follows existing project conventions
instead of guessing or inventing new patterns.

Pattern types:
- Function families: groups of functions sharing a prefix (render_*, _handle_*, etc.)
- Class conventions: structural patterns in class definitions
- Module conventions: what each module exports and why
- Idiom catalog: repeated parameter/return patterns
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

PLUGIN = {
    "name": "skills",
    "depends": [],
    "provides": ["skills", "patterns"],
    "default": True,
    "description": "Coding pattern library — project conventions and idioms for agents",
}


def run(graph, *, query: str = "", max_tokens: int = 4000, **kwargs) -> str:
    return get_patterns(graph, query=query, max_tokens=max_tokens)


def get_patterns(graph, *, query: str = "", max_tokens: int = 4000) -> str:
    """Return relevant coding patterns for the repo, filtered by query if given."""
    catalog = _build_catalog(graph)
    if query:
        catalog = _filter_catalog(catalog, query)
    return _render_catalog(catalog, max_tokens=max_tokens)


# ── Pattern extraction ─────────────────────────────────────────────────────

def _build_catalog(graph) -> dict:
    """Extract all pattern types from the graph."""
    return {
        "function_families": _detect_function_families(graph),
        "module_conventions": _detect_module_conventions(graph),
        "param_idioms": _detect_param_idioms(graph),
        "class_patterns": _detect_class_patterns(graph),
    }


def _detect_function_families(graph) -> list[dict]:
    """Group functions by shared prefix to detect naming conventions."""
    from tempograph.types import SymbolKind

    # Collect all function names by file
    prefix_groups: dict[str, list[dict]] = defaultdict(list)

    for sym in graph.symbols.values():
        if sym.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            continue
        name = sym.name

        # Extract prefix (up to first _ for snake_case, or camel prefix)
        prefix = _extract_prefix(name)
        if prefix and len(prefix) >= 3:  # skip trivial prefixes
            prefix_groups[prefix].append({
                "name": name,
                "file": sym.file_path,
                "exported": sym.exported,
                "complexity": sym.complexity,
            })

    # Keep only prefixes with 2+ members (actual families)
    families = []
    for prefix, members in sorted(prefix_groups.items(), key=lambda x: -len(x[1])):
        if len(members) < 2:
            continue
        # Deduplicate examples — if all have the same name (e.g. plugin run() fns),
        # it's an entry point pattern, not a naming family. Skip unless unique names exist.
        unique_names = list(dict.fromkeys(m["name"] for m in members))
        if len(unique_names) == 1:
            continue  # single-name group: entry point pattern, not a naming family
        files = list({m["file"] for m in members})
        families.append({
            "prefix": prefix,
            "count": len(members),
            "examples": unique_names[:5],
            "files": files[:3],
            "exported": any(m["exported"] for m in members),
        })

    return families[:20]  # top 20 families


def _detect_module_conventions(graph) -> list[dict]:
    """Detect what each key module exports and its structural role."""
    mods = []
    for fpath, finfo in graph.files.items():
        if finfo.language is None:
            continue
        syms = graph.symbols_in_file(fpath)
        if not syms:
            continue

        exported = [s for s in syms if s.exported]
        internal = [s for s in syms if not s.exported]
        importers = len(graph.importers_of(fpath))

        if importers < 2 and not exported:
            continue  # skip isolated files

        # Detect module role from structure
        role = _infer_module_role(fpath, exported, internal)
        if role:
            mods.append({
                "file": fpath,
                "role": role,
                "exports": [s.name for s in exported[:6]],
                "importers": importers,
            })

    return sorted(mods, key=lambda m: -m["importers"])[:15]


def _detect_param_idioms(graph) -> list[dict]:
    """Detect repeated parameter patterns across functions."""
    from tempograph.types import SymbolKind

    param_pattern_counts: dict[str, int] = defaultdict(int)

    for sym in graph.symbols.values():
        if sym.kind != SymbolKind.FUNCTION:
            continue
        sig = sym.signature or ""
        # Extract parameter names (heuristic: words after ( and before ))
        m = re.search(r'\(([^)]*)\)', sig)
        if not m:
            continue
        params = [p.strip().split(':')[0].strip().lstrip('*') for p in m.group(1).split(',')]
        params = [p for p in params if p and p not in ('self', 'cls', '')]
        if params:
            key = ", ".join(sorted(params))
            param_pattern_counts[key] += 1

    idioms = [
        {"params": k, "count": v}
        for k, v in sorted(param_pattern_counts.items(), key=lambda x: -x[1])
        if v >= 3  # only repeated patterns
    ]
    return idioms[:10]


def _detect_class_patterns(graph) -> list[dict]:
    """Detect structural patterns in class definitions."""
    from tempograph.types import SymbolKind

    patterns = []
    # Find dict-style plugin registrations (PLUGIN = {...} pattern)
    plugin_dicts = []
    for sym in graph.symbols.values():
        if sym.kind in (SymbolKind.VARIABLE, SymbolKind.CONSTANT) and sym.name == "PLUGIN" and sym.exported:
            plugin_dicts.append(sym.file_path)

    if plugin_dicts:
        patterns.append({
            "name": "Plugin registration",
            "description": "Module-level PLUGIN dict declares capabilities",
            "convention": "PLUGIN = {\"name\": ..., \"depends\": [...], \"provides\": [...], \"default\": bool, \"description\": ...}",
            "files": plugin_dicts[:5],
            "count": len(plugin_dicts),
        })

    # Find run() entry points (plugin contract)
    run_fns = [s for s in graph.symbols.values()
               if s.kind == SymbolKind.FUNCTION and s.name == "run" and s.exported]
    if len(run_fns) >= 2:
        patterns.append({
            "name": "Plugin entry point",
            "description": "Plugins expose a run(graph, **kwargs) -> str function",
            "convention": "def run(graph, *, query: str = '', max_tokens: int = 4000, **kwargs) -> str",
            "files": [s.file_path for s in run_fns[:5]],
            "count": len(run_fns),
        })

    # Find dataclass/TypedDict patterns
    from tempograph.types import SymbolKind
    classes = [s for s in graph.symbols.values() if s.kind == SymbolKind.CLASS]
    if classes:
        dataclasses = [c for c in classes if "@dataclass" in (c.signature or "")]
        if dataclasses:
            patterns.append({
                "name": "Dataclass",
                "description": "Data models use @dataclass decorator",
                "convention": "@dataclass\nclass Foo:\n    field: type",
                "files": [c.file_path for c in dataclasses[:3]],
                "count": len(dataclasses),
            })

    return patterns


# ── Helpers ────────────────────────────────────────────────────────────────

def _extract_prefix(name: str) -> str:
    """Extract the functional prefix from a snake_case or camelCase name."""
    # Private prefix: _foo_bar → _foo
    if name.startswith("_") and "_" in name[1:]:
        parts = name[1:].split("_")
        return "_" + parts[0] if parts[0] else ""

    # Snake case: render_foo_bar → render
    if "_" in name:
        return name.split("_")[0]

    # camelCase: renderFooBar → render
    m = re.match(r'^([a-z]+)', name)
    return m.group(1) if m else ""


def _infer_module_role(fpath: str, exported: list, internal: list) -> str:
    """Infer a module's architectural role from its path and exports."""
    base = Path(fpath).stem
    export_names = {s.name for s in exported}
    internal_names = {s.name for s in internal}

    if "render" in base or any("render" in n for n in export_names):
        return "renderer"
    if "server" in base or "mcp" in base:
        return "MCP server"
    if "builder" in base or "build" in base:
        return "graph builder"
    if "parser" in base or "parse" in base:
        return "parser"
    if "types" in base or "models" in base:
        return "types/models"
    if "cache" in base:
        return "cache"
    if "test" in fpath or "spec" in fpath:
        return "tests"
    if "__main__" in base or "cli" in base:
        return "CLI entry point"
    if "registry" in base:
        return "plugin registry"
    if "config" in base:
        return "configuration"
    if "__init__" in base:
        return "plugin module"
    return ""


def _filter_catalog(catalog: dict, query: str) -> dict:
    """Filter catalog sections by query relevance."""
    q = query.lower()
    filtered = {}

    families = catalog.get("function_families", [])
    filtered["function_families"] = [
        f for f in families
        if q in f["prefix"].lower() or any(q in ex for ex in f["examples"])
    ] or families[:5]  # fallback to top 5 if no match

    mods = catalog.get("module_conventions", [])
    filtered["module_conventions"] = [
        m for m in mods
        if q in m["file"].lower() or q in m["role"].lower() or any(q in e for e in m["exports"])
    ] or mods[:3]

    filtered["param_idioms"] = catalog.get("param_idioms", [])[:5]

    patterns = catalog.get("class_patterns", [])
    filtered["class_patterns"] = [
        p for p in patterns
        if q in p["name"].lower() or q in p["description"].lower()
    ] or patterns

    return filtered


# ── Rendering ─────────────────────────────────────────────────────────────

def _render_catalog(catalog: dict, max_tokens: int = 4000) -> str:
    from tempograph.render import count_tokens

    lines = ["# Coding Patterns & Conventions", ""]
    sections = []

    # Function families
    families = catalog.get("function_families", [])
    if families:
        s = ["## Function Families", ""]
        s.append("Groups of functions sharing a naming prefix — use these when adding similar functionality:\n")
        for fam in families[:10]:
            examples_str = ", ".join(fam["examples"])
            s.append(f"  `{fam['prefix']}_*` ({fam['count']} functions): {examples_str}")
        sections.append("\n".join(s))

    # Class/module patterns
    class_pats = catalog.get("class_patterns", [])
    if class_pats:
        s = ["## Structural Patterns", ""]
        for p in class_pats:
            s.append(f"### {p['name']} ({p['count']} instances)")
            s.append(p["description"])
            s.append(f"```\n{p['convention']}\n```")
            s.append(f"Examples: {', '.join(p['files'][:3])}")
            s.append("")
        sections.append("\n".join(s))

    # Module conventions
    mods = catalog.get("module_conventions", [])
    if mods:
        s = ["## Module Roles", ""]
        for m in mods[:8]:
            exports_str = ", ".join(m["exports"][:4]) + ("..." if len(m["exports"]) > 4 else "")
            s.append(f"  {m['file']} [{m['role']}] → {exports_str}")
        sections.append("\n".join(s))

    # Parameter idioms
    idioms = catalog.get("param_idioms", [])
    if idioms:
        s = ["## Repeated Parameter Patterns", ""]
        s.append("Parameter combinations used 3+ times across the codebase:\n")
        for idiom in idioms[:6]:
            s.append(f"  ({idiom['params']})  — used {idiom['count']}×")
        sections.append("\n".join(s))

    body = "\n\n".join(sections)
    output = "\n".join(lines) + body

    # Token cap
    if count_tokens(output) > max_tokens:
        # Trim by removing last section until under budget
        while sections and count_tokens("\n\n".join(sections)) > max_tokens - 50:
            sections.pop()
        body = "\n\n".join(sections)
        output = "\n".join(lines) + body

    return output
