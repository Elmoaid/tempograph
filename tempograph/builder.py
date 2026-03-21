"""Build a Tempo from a repository root."""
from __future__ import annotations

import concurrent.futures
import fnmatch
import json
import os
from pathlib import Path
from typing import Sequence

from .cache import check_cache, load_cache, make_cache_entry, save_cache
from .storage import GraphDB, content_hash
from .types import Tempo, Edge, EdgeKind, FileInfo, Language, Symbol, SymbolKind, EXTENSION_TO_LANGUAGE
from .parser import FileParser
from .git import is_git_repo, recently_modified_files, changed_files_vs_head

DEFAULT_IGNORE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "target", "dist", "build",
    ".next", ".nuxt", ".svelte-kit", "vendor", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache",
    ".cargo", "pkg", "coverage", ".turbo", ".cache",
})

DEFAULT_IGNORE_FILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
    "poetry.lock", "Pipfile.lock", "composer.lock",
})

# Languages with custom handlers (high-quality parsing)
CUSTOM_HANDLER_LANGUAGES = frozenset({
    Language.PYTHON, Language.TYPESCRIPT, Language.TSX,
    Language.JAVASCRIPT, Language.JSX, Language.RUST, Language.GO,
    Language.JAVA, Language.CSHARP, Language.RUBY,
})

# Non-code languages that should never be parsed for symbols
_NON_CODE_LANGUAGES = frozenset({
    Language.JSON, Language.TOML, Language.YAML, Language.CSS,
    Language.HTML, Language.BASH, Language.MARKDOWN, Language.UNKNOWN,
})

def _is_parseable(lang: Language) -> bool:
    """Check if a language can be parsed for symbols (custom handler or generic via language pack)."""
    if lang in _NON_CODE_LANGUAGES:
        return False
    if lang in CUSTOM_HANDLER_LANGUAGES:
        return True
    # Try language pack for extended languages
    try:
        from tempograph.parser import _get_ts_language
        return _get_ts_language(lang) is not None
    except Exception:
        return False

# Backward compat alias
PARSEABLE_LANGUAGES = CUSTOM_HANDLER_LANGUAGES

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB — skip huge generated files


def build_graph(
    root: str | Path,
    *,
    ignore_dirs: frozenset[str] = DEFAULT_IGNORE_DIRS,
    ignore_files: frozenset[str] = DEFAULT_IGNORE_FILES,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    exclude_dirs: Sequence[str] | None = None,
    use_cache: bool = True,
    use_config: bool = True,
    use_db: bool = True,
) -> Tempo:
    root = Path(root).resolve()
    # Normalize exclude_dirs: "a,b" string → ["a", "b"] list (str is Sequence[str] in Python,
    # so passing a comma-separated string would silently iterate over characters without this).
    if isinstance(exclude_dirs, str):
        exclude_dirs = [d.strip() for d in exclude_dirs.split(",") if d.strip()]

    # Merge exclude_dirs from .tempo/config.json, matching CLI and MCP server behavior.
    # Pass use_config=False to bypass (e.g. for tests that need raw unfiltered graph).
    if use_config:
        cfg_path = root / ".tempo" / "config.json"
        if cfg_path.exists():
            try:
                cfg_exclude = json.loads(cfg_path.read_text()).get("exclude_dirs", [])
                if cfg_exclude:
                    provided = list(exclude_dirs) if exclude_dirs else []
                    exclude_dirs = list(dict.fromkeys(cfg_exclude + provided)) or None
            except (json.JSONDecodeError, OSError):
                pass

    graph = Tempo(root=str(root))

    # Detect Tauri project: has src-tauri/ or tauri.conf.json
    is_tauri = (root / "src-tauri").is_dir() or (root / "tauri.conf.json").exists()

    # Open SQLite DB for persistent storage (preferred) or fall back to JSON cache
    db = GraphDB(root) if use_db else None
    cache = load_cache(root) if (use_cache and not use_db) else {}
    new_cache: dict = {}
    cache_hits = 0
    current_files: set[str] = set()

    # Bulk-fetch stored {path: (hash, mtime_ns)} — one query replaces per-file hash lookups.
    # mtime_ns check (nanosecond precision) skips read_bytes()+md5() for unchanged files.
    stored_files: dict[str, tuple[str, int]] = db.get_stored_files() if db else {}

    for file_path, rel_path in _walk_files(root, ignore_dirs, ignore_files, include_patterns, exclude_patterns, exclude_dirs):
        ext = file_path.suffix.lower()
        language = EXTENSION_TO_LANGUAGE.get(ext, Language.UNKNOWN)

        try:
            stat = file_path.stat()
            if stat.st_size > MAX_FILE_SIZE:
                continue
        except (OSError, PermissionError):
            continue

        # Fast path: mtime_ns match means file is unchanged — skip read + hash entirely.
        stored_entry = stored_files.get(rel_path)
        if stored_entry is not None and stat.st_mtime_ns == stored_entry[1]:
            current_files.add(rel_path)
            cache_hits += 1
            continue

        try:
            source = file_path.read_bytes()
        except (OSError, PermissionError):
            continue

        current_files.add(rel_path)
        line_count = source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)
        file_hash = content_hash(source)
        file_mtime_ns = stat.st_mtime_ns

        if _is_parseable(language):
            # Reach here only when mtime changed; check hash to determine if content changed.
            if not db:
                cached = check_cache(cache, rel_path, source) if use_cache else None
                if cached:
                    cache_hits += 1
                    symbols = [_sym_from_dict(d) for d in cached["symbols"]]
                    edges = [_edge_from_dict(d) for d in cached["edges"]]
                    imports = cached["imports"]
                    new_cache[rel_path] = cached
                else:
                    symbols, edges, imports = _parse_file(rel_path, language, source, is_tauri)
                    if use_cache:
                        new_cache[rel_path] = make_cache_entry(
                            source,
                            [_sym_to_dict(s) for s in symbols],
                            [_edge_to_dict(e) for e in edges],
                            imports,
                        )
            elif stored_entry is not None and stored_entry[0] == file_hash:
                # Mtime changed but hash unchanged (e.g. `touch`): update mtime_ns only.
                db.update_file_mtime(rel_path, file_mtime_ns)
                cache_hits += 1
                symbols, edges, imports = [], [], []
            else:
                # New file or content changed — parse and store.
                symbols, edges, imports = _parse_file(rel_path, language, source, is_tauri)
                db.update_file(rel_path, file_hash, language.value, line_count,
                               len(source), symbols, edges, imports, mtime_ns=file_mtime_ns)

            # For DB path, skip per-file graph building — load_all() handles it
            if not db:
                file_info = FileInfo(
                    path=rel_path, language=language, line_count=line_count,
                    byte_size=len(source), symbols=[s.id for s in symbols], imports=imports,
                )
                for sym in symbols:
                    graph.symbols[sym.id] = sym
                graph.edges.extend(edges)
                graph.files[rel_path] = file_info
        else:
            if not db:
                file_info = FileInfo(
                    path=rel_path, language=language, line_count=line_count,
                    byte_size=len(source),
                )
                graph.files[rel_path] = file_info
            else:
                # Store non-parseable files in DB too (for file map/overview).
                # Reach here only when mtime changed; check hash to avoid re-storing.
                if stored_entry is not None and stored_entry[0] == file_hash:
                    db.update_file_mtime(rel_path, file_mtime_ns)
                else:
                    db.update_file(rel_path, file_hash, language.value, line_count,
                                   len(source), [], [], [], mtime_ns=file_mtime_ns)

    # Load entire graph from DB in one shot
    if db:
        db.remove_stale_files(current_files)
        files, symbols, edges = db.load_all()
        graph.files = files
        graph.symbols = symbols
        graph.edges = edges
        graph._db = db  # type: ignore[attr-defined]  — kept open for hybrid search

    if use_cache and not use_db and new_cache:
        save_cache(root, new_cache)

    graph._cache_hits = cache_hits  # type: ignore[attr-defined]

    # Resolve import edges: match import statements to actual files
    _resolve_imports(graph, root)
    # Resolve call edges: match target names to actual symbol IDs
    _resolve_edges(graph)
    graph.build_indexes()

    # Temporal weighting: populate hot_files from working-tree or recent history.
    # Source files only — test files and docs are excluded so that test symbols
    # don't outrank implementations for ambiguous queries.
    if is_git_repo(str(root)):
        all_hot = _get_hot_files(str(root))
        graph.hot_files = {f for f in all_hot if _is_hot_source_file(f)}

    return graph


def load_from_snapshot(repo_slug: str) -> Tempo:
    """Load a Tempo graph from a pre-built snapshot (no file parsing).

    The snapshot must have been downloaded first:
        python3 -m tempograph snapshot --repo <org/repo>

    Raises FileNotFoundError if the snapshot db does not exist.
    """
    from .snapshots import snapshot_path, snapshot_db_path

    db_path = snapshot_db_path(repo_slug)
    if not db_path.exists():
        snap_root = snapshot_path(repo_slug)
        raise FileNotFoundError(
            f"Snapshot not found: {db_path}\n"
            f"Run: python3 -m tempograph snapshot --repo {repo_slug}"
        )

    snap_root = snapshot_path(repo_slug)
    db = GraphDB(snap_root)
    graph = Tempo(root=str(snap_root))
    files, symbols, edges = db.load_all()
    graph.files = files
    graph.symbols = symbols
    graph.edges = edges
    graph._db = db  # type: ignore[attr-defined]
    _resolve_edges(graph)
    graph.build_indexes()
    return graph


def _parse_file(rel_path: str, language: Language, source: bytes, is_tauri: bool) -> tuple:
    """Parse a file with tree-sitter. Returns (symbols, edges, imports)."""
    try:
        parser = FileParser(rel_path, language, source, is_tauri=is_tauri)
        return parser.parse()
    except Exception:
        return [], [], []


# Path patterns that identify test and documentation files.
# These are excluded from the temporal bonus to prevent test symbols
# from outranking implementation symbols in search results.
_TEST_SEGMENTS = frozenset({"test", "tests", "__tests__", "spec", "specs", "__spec__"})
_TEST_PREFIXES = ("test_", "spec_")
_TEST_SUFFIXES = (
    "_test.py", "_spec.py",
    "_test.ts", "_spec.ts", ".spec.ts", ".test.ts",
    "_test.js", "_spec.js", ".spec.js", ".test.js",
    "_test.go", "_test.rb",
)
_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc"})


def _get_hot_files(repo: str) -> set[str]:
    """Return candidate hot file paths for temporal weighting.

    Priority: working-tree changes (staged + unstaged) reflect what the developer
    is actively editing right now. If the working tree is clean, fall back to the
    last 2 commits so a freshly-committed session still gets a signal.

    Both git subprocesses run in parallel threads (independent read-only ops).
    On a clean repo this saves ~9ms vs sequential fallback (36% reduction).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_working = ex.submit(changed_files_vs_head, repo)
        fut_recent = ex.submit(recently_modified_files, repo, 2)
        working = set(fut_working.result())
        if working:
            return working
        return fut_recent.result()


def _is_hot_source_file(path: str) -> bool:
    """Return True if path is a source file eligible for temporal bonus.

    Excludes test files, spec files, and documentation files.
    """
    parts = path.replace("\\", "/").split("/")
    name = parts[-1] if parts else path
    # Exclude files inside test/spec directories
    if any(p.lower() in _TEST_SEGMENTS for p in parts[:-1]):
        return False
    # Exclude files whose name starts or ends with test/spec patterns
    nl = name.lower()
    if any(nl.startswith(p) for p in _TEST_PREFIXES):
        return False
    if any(nl.endswith(s) for s in _TEST_SUFFIXES):
        return False
    # Exclude documentation files by extension
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext.lower() in _DOC_EXTENSIONS:
        return False
    return True


def _sym_to_dict(sym: Symbol) -> dict:
    return {
        "id": sym.id, "name": sym.name, "qn": sym.qualified_name,
        "kind": sym.kind.value, "lang": sym.language.value,
        "fp": sym.file_path, "ls": sym.line_start, "le": sym.line_end,
        "sig": sym.signature, "doc": sym.doc, "pid": sym.parent_id,
        "exp": sym.exported, "cx": sym.complexity, "bs": sym.byte_size,
    }


def _sym_from_dict(d: dict) -> Symbol:
    return Symbol(
        id=d["id"], name=d["name"], qualified_name=d["qn"],
        kind=SymbolKind(d["kind"]), language=Language(d["lang"]),
        file_path=d["fp"], line_start=d["ls"], line_end=d["le"],
        signature=d.get("sig", ""), doc=d.get("doc", ""),
        parent_id=d.get("pid"), exported=d.get("exp", True),
        complexity=d.get("cx", 0), byte_size=d.get("bs", 0),
    )


def _edge_to_dict(e: Edge) -> dict:
    return {"k": e.kind.value, "s": e.source_id, "t": e.target_id, "l": e.line}


def _edge_from_dict(d: dict) -> Edge:
    return Edge(kind=EdgeKind(d["k"]), source_id=d["s"], target_id=d["t"], line=d.get("l", 0))


def _walk_files(
    root: Path,
    ignore_dirs: frozenset[str],
    ignore_files: frozenset[str],
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
    exclude_dirs: Sequence[str] | None = None,
):
    # Normalize exclude_dirs to path prefixes (strip trailing slashes)
    _exclude_prefixes = [p.rstrip("/") for p in (exclude_dirs or [])]

    # Precompute root string length once to avoid per-entry relative_to() calls
    root_str = str(root)
    root_len = len(root_str)

    for dirpath, dirnames, filenames in os.walk(root):
        # String-slice instead of Path.relative_to() — avoids ~45 Path objects per walk
        rel_dir = dirpath[root_len + 1:] if len(dirpath) > root_len else "."

        # Filter directories in-place — skip ignored names and excluded prefixes
        dirnames[:] = [
            d for d in dirnames
            if d not in ignore_dirs
            and not d.startswith(".")
            and not any(
                (rel_dir == "." and d == p) or
                (rel_dir + "/" + d == p) or
                (rel_dir + "/" + d).startswith(p + "/")
                for p in _exclude_prefixes
            )
        ]
        dirnames.sort()

        # Skip files inside excluded path prefixes
        if _exclude_prefixes and rel_dir != "." and any(
            rel_dir == p or rel_dir.startswith(p + "/") for p in _exclude_prefixes
        ):
            continue

        for filename in sorted(filenames):
            if filename in ignore_files or filename.startswith("."):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in EXTENSION_TO_LANGUAGE:
                continue

            # Precompute rel_path using string ops — avoids Path.relative_to() per file
            rel = filename if rel_dir == "." else rel_dir + "/" + filename

            if include_patterns:
                if not any(fnmatch.fnmatch(rel, p) for p in include_patterns):
                    continue
            if exclude_patterns:
                if any(fnmatch.fnmatch(rel, p) for p in exclude_patterns):
                    continue

            yield Path(dirpath, filename), rel


_RESOLVE_KINDS = frozenset([EdgeKind.CALLS, EdgeKind.RENDERS, EdgeKind.INHERITS, EdgeKind.IMPLEMENTS])


def _resolve_edges(graph: Tempo) -> None:
    """Resolve symbolic call targets to actual symbol IDs where possible.
    Uses scope-aware resolution: same file > imported file > exported symbol > any."""
    # Build a name→id lookup
    name_to_ids: dict[str, list[str]] = {}
    for sym in graph.symbols.values():
        name_to_ids.setdefault(sym.name, []).append(sym.id)
        if sym.qualified_name != sym.name:
            name_to_ids.setdefault(sym.qualified_name, []).append(sym.id)

    # Fast path: skip all remaining work if no edge has a resolvable target.
    # Common when all symbolic call targets are stdlib/external (no candidates in name_to_ids).
    if not any(
        e.kind in _RESOLVE_KINDS
        and "::" not in e.target_id
        and (name_to_ids.get(e.target_id) or ("." in e.target_id and name_to_ids.get(e.target_id.rsplit(".", 1)[-1])))
        for e in graph.edges
    ):
        return

    # Build file → imported files lookup for scope-aware resolution
    file_imports: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.IMPORTS:
            file_imports.setdefault(edge.source_id, set()).add(edge.target_id)

    # Build file → imported symbol names (from raw import strings like "from x import foo")
    file_imported_names: dict[str, set[str]] = {}
    for fp, fi in graph.files.items():
        names: set[str] = set()
        for imp in fi.imports:
            # "from x import a, b" or "import { a, b } from 'x'"
            if "import" in imp:
                # Python: from x import a, b, c
                if "from" in imp and "import" in imp:
                    after_import = imp.split("import", 1)[-1]
                    for part in after_import.split(","):
                        name = part.strip().split(" as ")[0].strip().strip("{}")
                        if name and name != "*":
                            names.add(name)
                # JS: import { a, b } from 'x'
                elif "{" in imp:
                    brace_content = imp.split("{", 1)[-1].split("}", 1)[0]
                    for part in brace_content.split(","):
                        name = part.strip().split(" as ")[0].strip()
                        if name:
                            names.add(name)
        file_imported_names[fp] = names

    def _pick_best(source_id: str, candidates: list[str]) -> str:
        if len(candidates) == 1:
            return candidates[0]
        source_file = source_id.split("::")[0]
        # Priority 1: same file
        same_file = [c for c in candidates if c.startswith(source_file + "::")]
        if same_file:
            return same_file[0]
        # Priority 2: symbol explicitly imported by name
        imported_names = file_imported_names.get(source_file, set())
        if imported_names:
            for c in candidates:
                sym = graph.symbols.get(c)
                if sym and sym.name in imported_names:
                    c_file = c.split("::")[0]
                    # Bonus: also from an imported file
                    if c_file in file_imports.get(source_file, set()):
                        return c
            # Fallback: name match without file match
            for c in candidates:
                sym = graph.symbols.get(c)
                if sym and sym.name in imported_names:
                    return c
        # Priority 3: imported file
        imported_files = file_imports.get(source_file, set())
        if imported_files:
            for c in candidates:
                c_file = c.split("::")[0]
                if c_file in imported_files:
                    return c
        # Priority 4: exported symbol
        for c in candidates:
            sym = graph.symbols.get(c)
            if sym and sym.exported:
                return c
        return candidates[0]

    resolved: list[Edge] = []
    for edge in graph.edges:
        if edge.kind in _RESOLVE_KINDS and "::" not in edge.target_id:
            candidates = name_to_ids.get(edge.target_id, [])
            # If qualified name (Type.method) didn't match, try bare name
            if not candidates and "." in edge.target_id:
                bare = edge.target_id.rsplit(".", 1)[-1]
                candidates = name_to_ids.get(bare, [])
            if candidates:
                target = _pick_best(edge.source_id, candidates)
                resolved.append(Edge(edge.kind, edge.source_id, target, edge.line))
            else:
                resolved.append(edge)
        else:
            resolved.append(edge)

    graph.edges = resolved


def _resolve_imports(graph: Tempo, root: Path) -> None:
    """Create IMPORTS edges by resolving import paths to actual files."""
    import re

    # Build a lookup of file stems and paths
    path_lookup: dict[str, str] = {}  # stem → rel_path
    for fp in graph.files:
        stem = fp.rsplit("/", 1)[-1].rsplit(".", 1)[0]  # "Canvas" from "src/components/Canvas.tsx"
        path_lookup[stem] = fp
        path_lookup[fp] = fp

    # Patterns for extracting import targets
    js_from_re = re.compile(r"""from\s+['"]([^'"]+)['"]""")
    py_from_re = re.compile(r"""from\s+(\S+)\s+import\s+(.+)""")
    py_names_re = re.compile(r"""from\s+\S+\s+import\s+(.+)""")
    rust_use_re = re.compile(r"""use\s+(?:crate::)?(\S+)""")

    for fp, fi in graph.files.items():
        for imp_str in fi.imports:
            targets: list[str] = []

            # JS/TS: from './foo' or from '../lib/bar'
            m = js_from_re.search(imp_str)
            if m:
                raw = m.group(1)
                # Resolve relative path
                if raw.startswith("."):
                    base_dir = fp.rsplit("/", 1)[0] if "/" in fp else "."
                    parts = raw.split("/")
                    resolved_parts = base_dir.split("/")
                    for part in parts:
                        if part == ".":
                            continue
                        elif part == "..":
                            if resolved_parts:
                                resolved_parts.pop()
                        else:
                            resolved_parts.append(part)
                    candidate_base = "/".join(resolved_parts)
                    # Try with extensions
                    for ext in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"):
                        candidate = candidate_base + ext
                        if candidate in graph.files:
                            targets.append(candidate)
                            break
                else:
                    # Package import — try to match by last segment
                    stem = raw.rsplit("/", 1)[-1]
                    if stem in path_lookup:
                        targets.append(path_lookup[stem])

            # Python: from foo.bar import baz, Qux  (absolute and relative)
            py_imported_names: list[str] = []
            if not targets:
                m = py_from_re.search(imp_str)
                if m:
                    raw_mod = m.group(1)
                    # Count leading dots for relative imports
                    dots = len(raw_mod) - len(raw_mod.lstrip("."))
                    if dots:
                        # Relative: resolve against current file's directory
                        file_parts = fp.split("/")[:-1]  # strip filename
                        for _ in range(dots - 1):  # one dot = same dir, two dots = parent
                            if file_parts:
                                file_parts.pop()
                        suffix = raw_mod[dots:].replace(".", "/")  # e.g. "types"
                        mod = "/".join(file_parts + [suffix]) if suffix else "/".join(file_parts)
                    else:
                        mod = raw_mod.replace(".", "/")
                    for ext in (".py", "/__init__.py"):
                        candidate = mod + ext
                        if candidate in graph.files:
                            targets.append(candidate)
                            # Extract named imports for symbol-level edges
                            names_str = m.group(2).strip().strip("()")
                            py_imported_names = [
                                n.strip().split(" as ")[0].strip()
                                for n in names_str.split(",")
                                if n.strip() and not n.strip().startswith("#")
                            ]
                            break

            # Rust: use crate::foo::bar — try multiple source directories
            if not targets:
                m = rust_use_re.search(imp_str)
                if m:
                    raw_mod = m.group(1).rstrip(";")
                    parts = raw_mod.split("::")
                    # Try resolving progressively: first module, then deeper
                    # Also try src-tauri/src/ for Tauri projects
                    source_dirs = ["src"]
                    # Detect if file is in src-tauri/
                    if fp.startswith("src-tauri/"):
                        source_dirs = ["src-tauri/src"]
                    for src_dir in source_dirs:
                        for depth in range(len(parts), 0, -1):
                            mod_path = "/".join(parts[:depth])
                            for candidate in (
                                f"{src_dir}/{mod_path}.rs",
                                f"{src_dir}/{mod_path}/mod.rs",
                            ):
                                if candidate in graph.files:
                                    targets.append(candidate)
                                    break
                            if targets:
                                break
                        if targets:
                            break

            for target in targets:
                graph.edges.append(Edge(EdgeKind.IMPORTS, fp, target))
                # Create symbol-level CALLS edges for named Python imports
                # This prevents false-positive dead code flags for exported names
                for name in py_imported_names:
                    sym_id = f"{target}::{name}"
                    if sym_id in graph.symbols:
                        graph.edges.append(Edge(EdgeKind.CALLS, fp, sym_id))
