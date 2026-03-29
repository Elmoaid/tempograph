"""Core data types for the semantic code graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Minimal test-file check used by Tempo._find_top_complexity_sym_id().
# Cannot import from render/_utils.py (circular dependency). Covers common conventions.
def _is_test_file_simple(file_path: str) -> bool:
    name = file_path.rsplit("/", 1)[-1]
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".spec.ts")
        or name.endswith(".test.js")
        or name.endswith(".spec.js")
    )


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    ENUM_MEMBER = "enum_member"
    STRUCT = "struct"
    TRAIT = "trait"
    IMPL = "impl"
    FIELD = "field"
    PROPERTY = "property"
    HOOK = "hook"           # React hooks
    COMPONENT = "component"  # React/Vue components
    COMMAND = "command"      # CLI commands, palette commands
    ROUTE = "route"         # API routes
    TEST = "test"
    UNKNOWN = "unknown"


class EdgeKind(str, Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    USES_TYPE = "uses_type"
    CONTAINS = "contains"      # parent→child (module→class, class→method)
    REFERENCES = "references"  # generic reference
    EXPORTS = "exports"
    OVERRIDES = "overrides"
    RENDERS = "renders"        # component renders another component


class Language(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    JAVASCRIPT = "javascript"
    JSX = "jsx"
    RUST = "rust"
    GO = "go"
    JAVA = "java"
    CSHARP = "csharp"
    RUBY = "ruby"
    JSON = "json"
    TOML = "toml"
    YAML = "yaml"
    CSS = "css"
    HTML = "html"
    BASH = "bash"
    MARKDOWN = "markdown"
    # Extended languages (via tree-sitter-language-pack generic handler)
    PHP = "php"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    DART = "dart"
    SCALA = "scala"
    ELIXIR = "elixir"
    LUA = "lua"
    PERL = "perl"
    ZIG = "zig"
    CPP = "cpp"
    C = "c"
    FSHARP = "fsharp"
    HASKELL = "haskell"
    OCAML = "ocaml"
    CLOJURE = "clojure"
    ERLANG = "erlang"
    R = "r"
    JULIA = "julia"
    OBJC = "objc"
    UNKNOWN = "unknown"


EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TSX,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JSX,
    ".rs": Language.RUST,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".cs": Language.CSHARP,
    ".rb": Language.RUBY,
    ".json": Language.JSON,
    ".toml": Language.TOML,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".css": Language.CSS,
    ".html": Language.HTML,
    ".sh": Language.BASH,
    ".bash": Language.BASH,
    ".md": Language.MARKDOWN,
    # Extended languages
    ".php": Language.PHP,
    ".swift": Language.SWIFT,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".dart": Language.DART,
    ".scala": Language.SCALA,
    ".sc": Language.SCALA,
    ".ex": Language.ELIXIR,
    ".exs": Language.ELIXIR,
    ".lua": Language.LUA,
    ".pl": Language.PERL,
    ".pm": Language.PERL,
    ".zig": Language.ZIG,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".hxx": Language.CPP,
    ".c": Language.C,
    ".h": Language.C,
    ".fs": Language.FSHARP,
    ".fsx": Language.FSHARP,
    ".fsi": Language.FSHARP,
    ".hs": Language.HASKELL,
    ".ml": Language.OCAML,
    ".mli": Language.OCAML,
    ".clj": Language.CLOJURE,
    ".cljs": Language.CLOJURE,
    ".cljc": Language.CLOJURE,
    ".erl": Language.ERLANG,
    ".r": Language.R,
    ".R": Language.R,
    ".jl": Language.JULIA,
    ".m": Language.OBJC,
}


@dataclass(slots=True)
class Symbol:
    id: str                          # unique: "path/to/file.ts::ClassName.methodName"
    name: str                        # simple name: "methodName"
    qualified_name: str              # "ClassName.methodName"
    kind: SymbolKind
    language: Language
    file_path: str
    line_start: int
    line_end: int
    signature: str = ""              # "fn process(input: &str) -> Result<Output>"
    doc: str = ""                    # first line of docstring/comment
    parent_id: str | None = None     # containing symbol
    exported: bool = True
    complexity: int = 0              # rough cyclomatic complexity
    byte_size: int = 0              # raw size of the symbol's text

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1


@dataclass(slots=True)
class Edge:
    kind: EdgeKind
    source_id: str   # symbol or file id
    target_id: str   # symbol or file id
    line: int = 0    # where the reference occurs


@dataclass(frozen=True, slots=True)
class FileInfo:
    path: str
    language: Language
    line_count: int
    byte_size: int
    symbols: list[str] = field(default_factory=list)   # symbol ids
    imports: list[str] = field(default_factory=list)    # raw import strings


@dataclass
class Tempo:
    root: str
    files: dict[str, FileInfo] = field(default_factory=dict)
    symbols: dict[str, Symbol] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # Files touched in recent git history — used for temporal ranking bonus.
    # Populated by build_graph() when inside a git repo. Paths are relative to root.
    hot_files: set[str] = field(default_factory=set)

    # precomputed indexes (built after parsing)
    _callers: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _callees: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _children: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _importers: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _out_imports: dict[str, list[str]] = field(default_factory=dict, repr=False)  # source_file → [target_files] for IMPORTS edges
    _subtypes: dict[str, list[str]] = field(default_factory=dict, repr=False)   # parent_name → [child symbol ids]
    _renderers: dict[str, list[str]] = field(default_factory=dict, repr=False)  # target → [sources that render it]
    # Cached ID of the highest-complexity non-test symbol — computed once in build_indexes().
    # Used by S528 in diff.py to avoid scanning all symbols on every render call (0.51ms saved).
    _top_complexity_sym_id: str = field(default="", repr=False)
    # Cached map of top-5 hotspot file paths → (top_sym_name, caller_count).
    # Keyed by file path (non-test functions, ranked by cross-file caller count).
    # Computed once in build_indexes(). Used by S534 in diff.py (0.39ms saved per render call).
    _top5_hotspot_files: dict[str, tuple[str, int]] = field(default_factory=dict, repr=False)
    # Precomputed lowercase strings for search scoring — built lazily on first search call.
    # Avoids 5N .lower() calls per query (1.98ms at N=7,952). Tuple: (name, qname, sig, doc, fp).
    _lower_cache: dict[str, tuple[str, str, str, str, str]] = field(default_factory=dict, repr=False)

    def build_indexes(self) -> None:
        # Fast path: load cached indexes from DB if edge count matches (warm build).
        # Pickle deserialize (~1.5ms) is faster than recomputing from edges (~4ms).
        db = getattr(self, '_db', None)
        edge_count = len(self.edges)
        if db is not None:
            cached = db.load_indexes(edge_count)
            if cached is not None:
                self._callers = cached['callers']
                self._callees = cached['callees']
                self._children = cached['children']
                self._importers = cached['importers']
                self._out_imports = cached.get('out_imports', {})
                self._renderers = cached['renderers']
                self._subtypes = cached['subtypes']
                self._top_complexity_sym_id = self._find_top_complexity_sym_id()
                self._top5_hotspot_files = self._find_top5_hotspot_files()
                return

        # Local variable binding avoids repeated attribute and global lookups in the hot loop.
        # 'is' comparison is correct for enum singletons and skips __eq__ dispatch overhead.
        _CALLS = EdgeKind.CALLS
        _CONTAINS = EdgeKind.CONTAINS
        _IMPORTS = EdgeKind.IMPORTS
        _RENDERS = EdgeKind.RENDERS
        _INHERITS = EdgeKind.INHERITS
        _IMPLEMENTS = EdgeKind.IMPLEMENTS
        callers = self._callers
        callees = self._callees
        children = self._children
        importers = self._importers
        out_imports = self._out_imports
        renderers = self._renderers
        subtypes = self._subtypes
        callers.clear(); callees.clear(); children.clear()
        importers.clear(); out_imports.clear(); renderers.clear(); subtypes.clear()
        for edge in self.edges:
            k = edge.kind
            src = edge.source_id
            tgt = edge.target_id
            if k is _CALLS:
                callers.setdefault(tgt, []).append(src)
                callees.setdefault(src, []).append(tgt)
            elif k is _CONTAINS:
                children.setdefault(src, []).append(tgt)
            elif k is _IMPORTS:
                importers.setdefault(tgt, []).append(src)
                out_imports.setdefault(src, []).append(tgt)
            elif k is _RENDERS:
                renderers.setdefault(tgt, []).append(src)
            elif k is _INHERITS or k is _IMPLEMENTS:
                subtypes.setdefault(tgt, []).append(src)
        # Deduplicate — callers/callees carry ~98% of all duplicate entries.
        # children and subtypes have near-zero dupes; skip them to save iteration.
        _fromkeys = dict.fromkeys
        for d in (callers, callees, importers, out_imports, renderers):
            for kk, v in d.items():
                if len(v) > 1:
                    d[kk] = list(_fromkeys(v))

        # Cache computed indexes for next warm build.
        if db is not None:
            try:
                db.save_indexes({
                    'callers': callers, 'callees': callees,
                    'children': children, 'importers': importers,
                    'out_imports': out_imports,
                    'renderers': renderers, 'subtypes': subtypes,
                }, edge_count)
            except Exception:
                pass

        self._top_complexity_sym_id = self._find_top_complexity_sym_id()
        self._top5_hotspot_files = self._find_top5_hotspot_files()

    def _find_top_complexity_sym_id(self) -> str:
        """Find highest-complexity non-test symbol ID. Called once in build_indexes()."""
        top = max(
            (s for s in self.symbols.values()
             if s.complexity and not _is_test_file_simple(s.file_path)),
            key=lambda s: s.complexity,
            default=None,
        )
        return top.id if top else ""

    def _find_top5_hotspot_files(self) -> dict[str, tuple[str, int]]:
        """Build top-5 hotspot file map: file_path → (top_sym_name, caller_count).

        Scans _callers once at build time. Result is static per graph build.
        Used by S534 in diff.py to avoid O(N) _callers scan on every render call (0.39ms saved).
        """
        counts: list[tuple[int, str, str]] = []  # (caller_count, sym_name, file_path)
        for sid, cs in self._callers.items():
            if len(cs) >= 3:
                sym = self.symbols.get(sid)
                if sym and not _is_test_file_simple(sym.file_path) and sym.kind.value in ("function", "method"):
                    counts.append((len(cs), sym.name, sym.file_path))
        counts.sort(reverse=True)
        result: dict[str, tuple[str, int]] = {}
        for n, name, fp in counts:
            if fp not in result:
                result[fp] = (name, n)
            if len(result) == 5:
                break
        return result

    def callers_of(self, symbol_id: str) -> list[Symbol]:
        return [self.symbols[s] for s in self._callers.get(symbol_id, []) if s in self.symbols]

    def callees_of(self, symbol_id: str) -> list[Symbol]:
        return [self.symbols[s] for s in self._callees.get(symbol_id, []) if s in self.symbols]

    def children_of(self, symbol_id: str) -> list[Symbol]:
        return [self.symbols[s] for s in self._children.get(symbol_id, []) if s in self.symbols]

    def importers_of(self, file_path: str) -> list[str]:
        return self._importers.get(file_path, [])

    def outgoing_imports_of(self, file_path: str) -> list[str]:
        """Return file paths that file_path imports (outgoing IMPORTS edges). O(1) index lookup."""
        return self._out_imports.get(file_path, [])

    def renderers_of(self, symbol_id: str) -> list[Symbol]:
        return [self.symbols[s] for s in self._renderers.get(symbol_id, []) if s in self.symbols]

    def subtypes_of(self, name: str) -> list[Symbol]:
        """Find classes that inherit from or implement the given type name.

        Handles both unresolved bare-name targets and fully-resolved sym_id targets
        (after _resolve_edges, INHERITS edge target_id becomes the full sym_id).
        """
        result_ids: list[str] = list(self._subtypes.get(name, []))
        # If bare name: also look up via the symbol's resolved ID
        if "::" not in name:
            for sym in self.find_symbol(name):
                result_ids.extend(self._subtypes.get(sym.id, []))
        seen: set[str] = set()
        out: list[Symbol] = []
        for sid in result_ids:
            if sid not in seen and sid in self.symbols:
                seen.add(sid)
                out.append(self.symbols[sid])
        return out

    def symbols_in_file(self, file_path: str) -> list[Symbol]:
        fi = self.files.get(file_path)
        if not fi:
            return []
        return [self.symbols[sid] for sid in fi.symbols if sid in self.symbols]

    def find_symbol(self, name: str) -> list[Symbol]:
        results = []
        name_lower = name.lower()
        for sym in self.symbols.values():
            if sym.name.lower() == name_lower or sym.qualified_name.lower() == name_lower:
                results.append(sym)
        return results

    _STOP_WORDS = frozenset({
        "a", "an", "the", "in", "on", "at", "to", "for", "of", "is", "it",
        "by", "as", "or", "and", "but", "not", "with", "from", "into", "that",
        "this", "be", "do", "has", "have", "had", "was", "are", "can", "will",
        "should", "would", "could", "my", "its", "their", "our", "your", "all",
        "add", "fix", "get", "set", "new", "use", "make", "find", "show",
        "update", "change", "create", "delete", "remove", "support", "implement",
        # GitHub PR template words (not code identifiers)
        "merge", "pull", "request", "pr", "via", "branch", "commit",
    })

    def search_symbols(self, query: str) -> list[Symbol]:
        return [sym for _, sym in self.search_symbols_scored(query)]

    def search_symbols_scored(self, query: str, use_hybrid: bool = True) -> list[tuple[float, Symbol]]:
        import re as _re

        # Try hybrid search (FTS5 + vector) if DB is attached, in sync, and has vectors
        if (use_hybrid and hasattr(self, '_db') and self._db is not None
                and getattr(self._db, '_has_vectors', False)):
            result = self._search_hybrid(query)
            if result:
                return result

        query_lower = query.lower()
        # Split on whitespace AND common separators (/, -, #, @) to handle paths/refs
        raw_tokens = _re.split(r'[\s/\-#@]+', query_lower)
        # For CamelCase tokens, inject split parts as ADDITIONAL tokens alongside the literal.
        # "buildGraph" keeps "buildgraph" (for substring matching, e.g. "testbuildgraph")
        # and adds "build" + "graph" (so snake_case `build_graph` matches via conjunction).
        camel_parts: list[str] = []
        for part in _re.split(r'[\s/\-#@]+', query):
            if _re.search(r'[a-z][A-Z]', part):  # has CamelCase boundary
                for sub in _re.sub(r'([a-z])([A-Z])', r'\1 \2', part).split():
                    camel_parts.append(sub.lower())
        all_raw = list(dict.fromkeys(raw_tokens + camel_parts))
        tokens = [t for t in all_raw
                  if t not in self._STOP_WORDS and len(t) > 1
                  and _re.match(r'^[a-z][a-z0-9_]*$', t)]  # valid identifier chars only
        if not tokens:
            tokens = [t for t in query_lower.split() if len(t) > 1]

        # Build lowercase cache once per Tempo lifetime (2.4ms one-time, saves 1.98ms/query after).
        if not self._lower_cache:
            for sym in self.symbols.values():
                self._lower_cache[sym.id] = (
                    sym.name.lower(),
                    sym.qualified_name.lower(),
                    sym.signature.lower(),
                    sym.doc.lower(),
                    sym.file_path.lower(),
                )

        # IDF penalty: for multi-token queries, tokens that appear in many symbol names
        # are generic words (test, handler, coverage) and should get lower weight.
        # This prevents "add test coverage for parser" from flooding results with test classes.
        _idf_factors: dict[str, float] = {}
        if len(tokens) > 1:
            _n_syms = len(self.symbols)
            if _n_syms > 0:
                for t in tokens:
                    _freq = sum(1 for _nl, _, _, _, _ in self._lower_cache.values() if t in _nl)
                    _ratio = _freq / _n_syms
                    if _ratio > 0.15:    # very common (>15%): heavy penalty
                        _idf_factors[t] = 0.3
                    elif _ratio > 0.05:  # moderately common (5-15%): mild penalty
                        _idf_factors[t] = 0.6
                    # else: rare/specific → factor = 1.0 (default)

        # Hoist query-level regex check — same result for every symbol, no need to repeat N times.
        _is_simple_query = bool(_re.match(r'^[a-z][a-z0-9]*$', query_lower))

        results: list[tuple[float, Symbol]] = []
        for sym in self.symbols.values():
            name_lower, qname_lower, sig_lower, doc_lower, fp_lower = self._lower_cache[sym.id]
            score = 0.0
            matched_count = 0  # only name/qname/sig/doc matches (used for conjunction bonus)
            # Exact snake-normalized match: "buildGraph" → "buildgraph" == "build_graph" stripped.
            # Prevents test classes (TestBuildGraph qname has "buildgraph" substring) from
            # outranking the actual snake_case symbol. Applied once, before the token loop.
            _name_stripped = name_lower.replace('_', '').replace('-', '')
            if _is_simple_query and _name_stripped == query_lower:
                score += 15.0
                matched_count += 1
            for token in tokens:
                weight = min(len(token) / 3, 2.0) * _idf_factors.get(token, 1.0)
                if token == name_lower:
                    score += 10.0 * weight
                    matched_count += 1
                elif token in qname_lower:
                    score += 5.0 * weight
                    matched_count += 1
                elif token in sig_lower:
                    score += 3.0 * weight
                    matched_count += 1
                elif token in doc_lower:
                    score += 1.0 * weight
                    matched_count += 1
                elif token in fp_lower:
                    # File path matches: weak signal — don't count toward conjunction bonus
                    score += 0.3 * weight
            if score > 0:
                # Conjunction bonus: symbols matching multiple query tokens rank much higher
                # Only counts name/qname/sig/doc matches, not file path matches
                if len(tokens) > 1 and matched_count > 1:
                    score += matched_count * 4.0
                if sym.exported:
                    score += 2.0
                callers = self.callers_of(sym.id)
                cross_file = sum(1 for c in callers if c.file_path != sym.file_path)
                score += min(cross_file, 5) * 0.5
                if sym.kind in (SymbolKind.COMPONENT, SymbolKind.HOOK):
                    score += 1.5
                elif sym.kind == SymbolKind.CLASS:
                    score += 1.0
                # Temporal bonus: symbols in recently-modified files rank slightly higher.
                # Kept small (0.3) — analytical measurement (N=25) showed 2.5 caused
                # -3.62% MRR regression in realistic conditions; 0.3 is neutral (0 regressions).
                if self.hot_files and sym.file_path in self.hot_files:
                    score += 0.3
                results.append((score, sym))
        results.sort(key=lambda x: (-x[0], x[1].file_path, x[1].line_start))
        return results

    def _search_hybrid(self, query: str) -> list[tuple[float, Symbol]]:
        """Search using hybrid FTS5 + vector with RRF, then apply structural bonuses."""
        try:
            from .embeddings import embed_query
            query_emb = embed_query(query)
        except (ImportError, Exception):
            query_emb = None

        try:
            hybrid_results = self._db.search_hybrid(query, query_emb, limit=50)
        except Exception:
            hybrid_results = []
        if not hybrid_results:
            # Fallback to linear scan
            return self.search_symbols_scored(query, use_hybrid=False)

        results: list[tuple[float, Symbol]] = []
        for rrf_score, sym_id in hybrid_results:
            sym = self.symbols.get(sym_id)
            if sym is None:
                continue
            # Start with RRF score scaled up (RRF scores are small ~0.01-0.03)
            score = rrf_score * 100.0
            # Apply structural bonuses (same as linear scan path)
            if sym.exported:
                score += 2.0
            callers = self.callers_of(sym.id)
            cross_file = sum(1 for c in callers if c.file_path != sym.file_path)
            score += min(cross_file, 5) * 0.5
            if sym.kind in (SymbolKind.COMPONENT, SymbolKind.HOOK):
                score += 1.5
            elif sym.kind == SymbolKind.CLASS:
                score += 1.0
            if self.hot_files and sym.file_path in self.hot_files:
                score += 0.3
            results.append((score, sym))

        results.sort(key=lambda x: (-x[0], x[1].file_path, x[1].line_start))
        return results

    def detect_circular_imports(self) -> list[list[str]]:
        """Detect circular import chains. Returns list of cycles as file path lists."""
        adj: dict[str, set[str]] = {}
        for edge in self.edges:
            if edge.kind == EdgeKind.IMPORTS:
                adj.setdefault(edge.source_id, set()).add(edge.target_id)

        cycles: list[list[str]] = []
        visited: set[str] = set()
        path: list[str] = []
        on_stack: set[str] = set()

        def dfs(node: str) -> None:
            if node in on_stack:
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                cycles.append(cycle)
                return
            if node in visited:
                return
            visited.add(node)
            on_stack.add(node)
            path.append(node)
            for neighbor in adj.get(node, []):
                dfs(neighbor)
            path.pop()
            on_stack.remove(node)

        for node in sorted(adj.keys()):
            if node not in visited:
                dfs(node)

        return cycles

    def dependency_layers(self) -> list[list[str]]:
        """Group files into dependency layers. Layer 0 = leaf files (no imports),
        Layer N = files that only import from layers < N."""
        adj: dict[str, set[str]] = {}
        all_files: set[str] = set()
        for edge in self.edges:
            if edge.kind == EdgeKind.IMPORTS:
                adj.setdefault(edge.source_id, set()).add(edge.target_id)
                all_files.add(edge.source_id)
                all_files.add(edge.target_id)

        layers: list[list[str]] = []
        assigned: set[str] = set()

        remaining = all_files.copy()
        while remaining:
            layer = []
            for f in sorted(remaining):
                deps = adj.get(f, set()) - assigned
                deps = deps & remaining
                if not deps:
                    layer.append(f)
            if not layer:
                layers.append(sorted(remaining))
                break
            layers.append(layer)
            assigned.update(layer)
            remaining -= set(layer)

        return layers

    def find_dead_code(self) -> list[Symbol]:
        """Find exported symbols never referenced by other files.
        Methods are checked individually — a used class doesn't save its unused methods."""
        # Track cross-file references (same-file calls don't prove external use)
        referenced_cross_file: set[str] = set()
        referenced_any: set[str] = set()
        for edge in self.edges:
            if edge.kind == EdgeKind.CONTAINS:
                continue
            referenced_any.add(edge.target_id)
            # Cross-file: source and target are in different files
            src_file = edge.source_id.split("::")[0] if "::" in edge.source_id else ""
            tgt_file = edge.target_id.split("::")[0] if "::" in edge.target_id else ""
            if src_file and tgt_file and src_file != tgt_file:
                referenced_cross_file.add(edge.target_id)

        dead: list[Symbol] = []
        for sym in self.symbols.values():
            if not sym.exported:
                continue
            if sym.kind in (SymbolKind.MODULE, SymbolKind.UNKNOWN, SymbolKind.TEST):
                continue
            if sym.name in ("main", "__init__", "__main__", "init", "Main"):
                continue
            # For top-level symbols: dead if no cross-file references AND no same-file references
            if not sym.parent_id:
                if sym.id not in referenced_cross_file and sym.id not in referenced_any:
                    dead.append(sym)
            else:
                # For methods: dead if neither the method itself NOR via parent is cross-file referenced
                # But only flag if parent IS cross-file referenced (otherwise parent is already dead)
                if sym.parent_id in referenced_cross_file and sym.id not in referenced_any:
                    dead.append(sym)

        dead.sort(key=lambda s: (s.file_path, s.line_start))
        return dead

    @property
    def stats(self) -> dict[str, Any]:
        lang_counts: dict[str, int] = {}
        for fi in self.files.values():
            lang_counts[fi.language.value] = lang_counts.get(fi.language.value, 0) + 1
        kind_counts: dict[str, int] = {}
        for sym in self.symbols.values():
            kind_counts[sym.kind.value] = kind_counts.get(sym.kind.value, 0) + 1
        return {
            "files": len(self.files),
            "symbols": len(self.symbols),
            "edges": len(self.edges),
            "languages": lang_counts,
            "symbol_kinds": kind_counts,
            "total_lines": sum(fi.line_count for fi in self.files.values()),
        }
