"""SQLite persistent graph storage — replaces JSON cache with structured DB.

Stores symbols, edges, and file metadata in .tempograph/graph.db with WAL mode
for concurrent read access. Content-hashing determines which files need reparsing.
FTS5 provides full-text search over symbol names and signatures.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .types import Edge, EdgeKind, FileInfo, Language, Symbol, SymbolKind

CACHE_DIR = ".tempograph"
DB_FILE = "graph.db"
SCHEMA_VERSION = 8

# Module-level enum lookup dicts — O(1) vs try/except, ~31% faster in load_all()
_SYMBOL_KIND_MAP: dict[str, SymbolKind] = {v.value: v for v in SymbolKind}
_LANGUAGE_MAP: dict[str, Language] = {v.value: v for v in Language}
_EDGE_KIND_MAP: dict[str, EdgeKind] = {v.value: v for v in EdgeKind}


def content_hash(source: bytes) -> str:
    return hashlib.md5(source).hexdigest()


class GraphDB:
    """SQLite-backed persistent graph storage with WAL mode."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        db_dir = self.root / CACHE_DIR
        db_dir.mkdir(exist_ok=True)
        self.db_path = db_dir / DB_FILE
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._batching = False
        self._last_edge_count: int = 0  # set by load_all for use by save_resolved_edges_blob
        self._last_sym_count: int = 0   # set by load_all for use by save_resolved_edges_blob
        self._init_schema()

    def begin_batch(self) -> None:
        """Start a batch — suppresses per-call commits until end_batch()."""
        self._batching = True

    def end_batch(self) -> None:
        """End a batch — commits all pending writes in one transaction."""
        self._batching = False
        self._conn.commit()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(f"""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                language TEXT NOT NULL,
                line_count INTEGER NOT NULL,
                byte_size INTEGER NOT NULL,
                symbols_json TEXT DEFAULT '[]',
                imports_json TEXT DEFAULT '[]',
                mtime_ns INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS symbols (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                language TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                signature TEXT DEFAULT '',
                doc TEXT DEFAULT '',
                parent_id TEXT,
                exported INTEGER DEFAULT 1,
                complexity INTEGER DEFAULT 0,
                byte_size INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS edges (
                kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                line INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
                name, qualified_name, signature, doc, file_path,
                content=symbols, content_rowid=rowid,
                tokenize='unicode61'
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
                INSERT INTO symbols_fts(rowid, name, qualified_name, signature, doc, file_path)
                VALUES (new.rowid, new.name, new.qualified_name, new.signature, new.doc, new.file_path);
            END;

            CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
                INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, signature, doc, file_path)
                VALUES ('delete', old.rowid, old.name, old.qualified_name, old.signature, old.doc, old.file_path);
            END;

            INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');

            CREATE TABLE IF NOT EXISTS indexes_blob (
                edge_count INTEGER PRIMARY KEY,
                data BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edges_blob (
                edge_count INTEGER PRIMARY KEY,
                data BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS symbols_blob (
                sym_count INTEGER PRIMARY KEY,
                data BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resolved_edges_blob (
                edge_sym_key TEXT PRIMARY KEY,
                data BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files_blob (
                file_count INTEGER PRIMARY KEY,
                data BLOB NOT NULL
            );
        """)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply incremental schema migrations based on stored schema_version."""
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 1
        if version < 2:
            # Add mtime_ns column for mtime-based early-skip optimization
            try:
                self._conn.execute("ALTER TABLE files ADD COLUMN mtime_ns INTEGER DEFAULT 0")
                self._conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass  # column already exists
        if version < 3:
            # Add indexes_blob table — stores build_indexes pickle as BLOB directly,
            # replacing the hex-inside-JSON encoding that inflated 966KB to 1.9MB and
            # cost 2.5ms extra per warm build (json.loads + bytes.fromhex overhead).
            try:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS indexes_blob "
                    "(edge_count INTEGER PRIMARY KEY, data BLOB NOT NULL)"
                )
                # Drop old hex-encoded cache from meta table (now stale)
                self._conn.execute("DELETE FROM meta WHERE key='indexes_cache'")
                self._conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass
        if version < 4:
            # Add edges_blob table — caches raw edge tuples as a single BLOB row,
            # replacing per-row SQLite fetch (14.5ms) with pickle.loads (3.7ms).
            # Savings: ~11ms per warm build (53% reduction in edge load time).
            try:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS edges_blob "
                    "(edge_count INTEGER PRIMARY KEY, data BLOB NOT NULL)"
                )
                self._conn.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass
        if version < 5:
            # Add symbols_blob table — caches raw symbol tuples as a single BLOB row,
            # replacing per-row SQLite fetch (~10ms) with pickle.loads (~2.5ms).
            # Savings: ~7ms per warm build on 5465 symbols (74% reduction in symbol load time).
            try:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS symbols_blob "
                    "(sym_count INTEGER PRIMARY KEY, data BLOB NOT NULL)"
                )
                self._conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass
        if version < 6:
            # Add resolved_edges_blob table — caches post-resolution edge tuples (after
            # _resolve_imports and _resolve_edges). On a warm-build cache hit, both resolution
            # steps are skipped entirely. Savings: ~14ms per warm build (25%).
            try:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS resolved_edges_blob "
                    "(edge_sym_key TEXT PRIMARY KEY, data BLOB NOT NULL)"
                )
                self._conn.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass
        if version < 7:
            # Pre-resolve enum formats in blobs: symbols_blob now stores (SymbolKind, Language)
            # enum objects instead of string values; resolved_edges_blob now stores EdgeKind enums
            # instead of string kind values. Skips per-object dict.get() on warm builds.
            # Also adds files_blob to cache file rows with decoded json (no json.loads on warm build).
            # All three format changes are breaking, so drop and recreate the affected tables.
            # Savings: ~5.3ms (edge enum) + ~1.1ms (files blob) + ~0.7ms (sym enum) = ~7.1ms total.
            try:
                self._conn.execute("DROP TABLE IF EXISTS symbols_blob")
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS symbols_blob "
                    "(sym_count INTEGER PRIMARY KEY, data BLOB NOT NULL)"
                )
                self._conn.execute("DROP TABLE IF EXISTS resolved_edges_blob")
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS resolved_edges_blob "
                    "(edge_sym_key TEXT PRIMARY KEY, data BLOB NOT NULL)"
                )
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS files_blob "
                    "(file_count INTEGER PRIMARY KEY, data BLOB NOT NULL)"
                )
                self._conn.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass
        if version < 8:
            # Blob string interning: intern repeated strings (symbol IDs, file paths) before
            # pickling so pickle uses memo references for duplicates. Reduces blob sizes by
            # 21–66% and speeds up pickle.loads by 17–37% on warm builds.
            # Edge blob: 2601KB → 892KB (-66%), load: 3.6ms → 2.3ms (-36%)
            # Sym blob: 1919KB → 1514KB (-21%), load: 2.3ms → 1.9ms (-17%)
            # Idx blob: 931KB → 358KB (-61%), load: 1.4ms → 1.0ms (-27%)
            # Drop blobs to force regeneration with interning on next build.
            try:
                self._conn.execute("DELETE FROM resolved_edges_blob")
                self._conn.execute("DELETE FROM symbols_blob")
                self._conn.execute("DELETE FROM indexes_blob")
                self._conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
                self._conn.commit()
            except Exception:
                pass

    def get_stored_files(self) -> dict[str, tuple[str, int]]:
        """Bulk-fetch {rel_path: (hash, mtime_ns)} for all stored files in one query.

        Used by build_graph() to check mtime before reading file contents.
        Avoids 1 DB query per file on the warm-build fast path.
        """
        rows = self._conn.execute("SELECT path, hash, mtime_ns FROM files").fetchall()
        return {row["path"]: (row["hash"], row["mtime_ns"] or 0) for row in rows}

    def update_file_mtime(self, rel_path: str, mtime_ns: int) -> None:
        """Update only the mtime_ns for an existing file record (e.g. after `touch`)."""
        self._conn.execute(
            "UPDATE files SET mtime_ns = ? WHERE path = ?", (mtime_ns, rel_path)
        )
        if not self._batching:
            self._conn.commit()

    def file_hash_matches(self, rel_path: str, file_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT hash FROM files WHERE path = ?", (rel_path,)
        ).fetchone()
        return row is not None and row["hash"] == file_hash

    def update_file(
        self,
        rel_path: str,
        file_hash: str,
        language: str,
        line_count: int,
        byte_size: int,
        symbols: list[Symbol],
        edges: list[Edge],
        imports: list[str],
        mtime_ns: int = 0,
    ) -> None:
        cur = self._conn.cursor()
        # Remove old data for this file
        cur.execute("DELETE FROM symbols WHERE file_path = ?", (rel_path,))
        cur.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM symbols WHERE file_path = ?) OR source_id = ?",
            (rel_path, rel_path),
        )
        # The above won't catch edges from deleted symbols, so also clean by file prefix
        cur.execute(
            "DELETE FROM edges WHERE source_id LIKE ?",
            (rel_path + "::%",),
        )

        # Insert file record (with mtime_ns for next-build mtime-based early-skip)
        cur.execute(
            "INSERT OR REPLACE INTO files (path, hash, language, line_count, byte_size, symbols_json, imports_json, mtime_ns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rel_path, file_hash, language, line_count, byte_size,
             json.dumps([s.id for s in symbols]), json.dumps(imports), mtime_ns),
        )

        # Insert symbols
        if symbols:
            cur.executemany(
                "INSERT OR REPLACE INTO symbols "
                "(id, name, qualified_name, kind, language, file_path, line_start, line_end, "
                "signature, doc, parent_id, exported, complexity, byte_size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (s.id, s.name, s.qualified_name, s.kind.value, s.language.value,
                     s.file_path, s.line_start, s.line_end, s.signature, s.doc,
                     s.parent_id, int(s.exported), s.complexity, s.byte_size)
                    for s in symbols
                ],
            )

        # Insert edges from this file's symbols
        file_edges = [e for e in edges if e.source_id.startswith(rel_path)]
        if file_edges:
            cur.executemany(
                "INSERT INTO edges (kind, source_id, target_id, line) VALUES (?, ?, ?, ?)",
                [(e.kind.value, e.source_id, e.target_id, e.line) for e in file_edges],
            )

        if not self._batching:
            self._conn.commit()

    def remove_stale_files(self, current_files: set[str]) -> int:
        """Remove files from DB that no longer exist on disk. Returns count removed."""
        db_files = {
            row["path"]
            for row in self._conn.execute("SELECT path FROM files").fetchall()
        }
        stale = db_files - current_files
        if not stale:
            return 0
        for path in stale:
            self._conn.execute("DELETE FROM symbols WHERE file_path = ?", (path,))
            self._conn.execute("DELETE FROM edges WHERE source_id LIKE ?", (path + "::%",))
            self._conn.execute("DELETE FROM files WHERE path = ?", (path,))
        if not self._batching:
            self._conn.commit()
        return len(stale)

    def load_all(
        self, *, lazy_edges: bool = False
    ) -> tuple[dict[str, FileInfo], dict[str, Symbol], list[Edge], bool]:
        """Load entire graph from DB into memory.

        Uses tuple positional access instead of sqlite3.Row dict access to avoid
        per-field string key lookups. Benchmarked savings: ~3.7ms on 290 files /
        1510 symbols / 7211 edges (20% improvement over dict-access baseline).

        lazy_edges: skip edge loading entirely — useful for modes that only need
        files + symbols (overview, dead_code, hotspots). Saves ~10ms on load_all.

        Returns (files, symbols, edges, edges_pre_resolved). When edges_pre_resolved
        is True, edges include post-resolution IMPORTS and resolved CALLS edges from
        the previous build — caller should skip _resolve_imports and _resolve_edges.
        """
        orig_factory = self._conn.row_factory
        self._conn.row_factory = None  # raw tuples: faster positional access

        jl = json.loads
        get_lang = _LANGUAGE_MAP.get
        get_kind = _SYMBOL_KIND_MAP.get
        get_edge_kind = _EDGE_KIND_MAP.get
        unk_lang = Language.UNKNOWN
        unk_kind = SymbolKind.UNKNOWN

        try:
            # files: blob stores pre-resolved (path, Language_enum, line_count, byte_size,
            #        symbols_list, imports_list) — no json.loads or get_lang() on warm builds.
            # Blob key: file_count. Fallback: SQL SELECT + json.loads + pre-resolve before save.
            file_count = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            cached_file_rows = self.load_files_blob(file_count)
            if cached_file_rows is not None:
                file_rows = cached_file_rows
                _files_from_blob = True
            else:
                raw_file_rows = self._conn.execute(
                    "SELECT path, language, line_count, byte_size, symbols_json, imports_json FROM files"
                ).fetchall()
                # Pre-resolve enums and decode json before saving — so next warm build skips both
                file_rows = [
                    (r[0], get_lang(r[1], unk_lang), r[2], r[3], jl(r[4]), jl(r[5]))
                    for r in raw_file_rows
                ]
                _files_from_blob = False

            # symbols: blob stores pre-resolved (id, name, qual_name, SymbolKind, Language,
            #          file_path, line_start, line_end, signature, doc, parent_id, exported,
            #          complexity, byte_size) — no dict.get() or bool()/or"" on warm builds.
            # Blob key: sym_count. Fallback: SQL SELECT + pre-resolve before save.
            sym_count = self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            cached_sym_rows = self.load_symbols_blob(sym_count)
            if cached_sym_rows is not None:
                sym_rows = cached_sym_rows
                _syms_from_blob = True
            else:
                raw_sym_rows = self._conn.execute(
                    "SELECT id, name, qualified_name, kind, language, file_path, "
                    "line_start, line_end, signature, doc, parent_id, exported, complexity, byte_size "
                    "FROM symbols"
                ).fetchall()
                # Pre-resolve enum fields before saving so next warm build skips dict.get()
                sym_rows = [
                    (r[0], r[1], r[2],
                     get_kind(r[3], unk_kind), get_lang(r[4], unk_lang),
                     r[5], r[6], r[7],
                     r[8] or "", r[9] or "",
                     r[10], bool(r[11]), r[12], r[13])
                    for r in raw_sym_rows
                ]
                _syms_from_blob = False

            # edges: resolved_edges_blob stores post-resolution (EdgeKind_enum, src, tgt, line)
            #        tuples — skips _resolve_imports, _resolve_edges, AND per-edge dict.get().
            # Fast path hierarchy (warm-build):
            #   1. resolved_edges_blob: pre-resolved enum tuples (skip resolution + dict.get)
            #   2. edges_blob: pre-resolution string tuples (skip SQL, resolution still runs)
            #   3. SQL SELECT: cold-build path
            _edges_pre_resolved = False
            if not lazy_edges:
                edge_count = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                resolved_rows = self.load_resolved_edges_blob(edge_count, sym_count)
                if resolved_rows is not None:
                    edge_rows = resolved_rows
                    _edges_pre_resolved = True
                    _edges_from_blob = True
                else:
                    cached_rows = self.load_edges_blob(edge_count)
                    if cached_rows is not None:
                        edge_rows = cached_rows
                        _edges_from_blob = True
                    else:
                        edge_rows = self._conn.execute(
                            "SELECT kind, source_id, target_id, line FROM edges"
                        ).fetchall()
                        _edges_from_blob = False
            else:
                edge_count = 0
                edge_rows = []
                _edges_from_blob = False
        finally:
            self._conn.row_factory = orig_factory

        if not lazy_edges and not _edges_from_blob:
            self.save_edges_blob(edge_rows, edge_count)
        if not _syms_from_blob:
            self.save_symbols_blob(sym_rows, sym_count)
        if not _files_from_blob:
            self.save_files_blob(file_rows, file_count)

        # Store for use by save_resolved_edges_blob() after resolution
        self._last_edge_count = edge_count
        self._last_sym_count = sym_count

        # files: blob rows are pre-resolved (Language enum, decoded lists) — direct assignment
        # Positional args avoid keyword-lookup overhead (~26% faster than keyword form)
        files: dict[str, FileInfo] = {
            r[0]: FileInfo(r[0], r[1], r[2], r[3], r[4], r[5])
            for r in file_rows
        }

        # symbols: blob rows are pre-resolved (SymbolKind/Language enums, or"" applied, bool cast)
        # Positional args: id, name, qualified_name, kind, language, file_path,
        #                  line_start, line_end, signature, doc, parent_id, exported,
        #                  complexity, byte_size
        symbols: dict[str, Symbol] = {
            r[0]: Symbol(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13])
            for r in sym_rows
        }

        # edges: resolved_edges_blob rows are pre-resolved (EdgeKind enum) — no dict.get()
        # edges_blob and SQL rows use string kinds — requires get_edge_kind() lookup
        # Positional args: kind, source_id, target_id, line
        if _edges_pre_resolved:
            edges: list[Edge] = [
                Edge(r[0], r[1], r[2], r[3])
                for r in edge_rows
            ]
        else:
            edges = [
                Edge(k, r[1], r[2], r[3])
                for r in edge_rows
                if (k := get_edge_kind(r[0])) is not None
            ]

        return files, symbols, edges, _edges_pre_resolved

    def search_fts(self, query: str, limit: int = 20) -> list[tuple[float, str]]:
        """Full-text search over symbols. Returns (rank, symbol_id) pairs."""
        try:
            rows = self._conn.execute(
                "SELECT s.id, f.rank FROM symbols_fts f "
                "JOIN symbols s ON s.rowid = f.rowid "
                "WHERE symbols_fts MATCH ? ORDER BY f.rank LIMIT ?",
                (query, limit),
            ).fetchall()
            return [(row["rank"], row["id"]) for row in rows]
        except sqlite3.OperationalError:
            return []

    # ── Vector search (sqlite-vec) ──────────────────────────────

    def init_vectors(self, dimensions: int = 384) -> bool:
        """Initialize vector search table. Returns True if sqlite-vec is available."""
        try:
            import sqlite_vec
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS symbol_vectors
                USING vec0(embedding float[{dimensions}], symbol_id text)
            """)
            self._conn.commit()
            self._has_vectors = True
            self._vec_dimensions = dimensions
            return True
        except (ImportError, Exception):
            self._has_vectors = False
            return False

    def upsert_vector(self, symbol_id: str, embedding: list[float]) -> None:
        """Store or update a symbol's embedding vector."""
        if not getattr(self, '_has_vectors', False):
            return
        self._conn.execute(
            "DELETE FROM symbol_vectors WHERE symbol_id = ?", (symbol_id,)
        )
        self._conn.execute(
            "INSERT INTO symbol_vectors (embedding, symbol_id) VALUES (?, ?)",
            (json.dumps(embedding), symbol_id),
        )
        if not self._batching:
            self._conn.commit()

    def upsert_vectors_batch(self, items: list[tuple[str, list[float]]]) -> None:
        """Batch upsert symbol embeddings. items = [(symbol_id, embedding), ...]"""
        if not getattr(self, '_has_vectors', False) or not items:
            return
        ids = [i[0] for i in items]
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(
            f"DELETE FROM symbol_vectors WHERE symbol_id IN ({placeholders})", ids
        )
        self._conn.executemany(
            "INSERT INTO symbol_vectors (embedding, symbol_id) VALUES (?, ?)",
            [(json.dumps(emb), sid) for sid, emb in items],
        )
        if not self._batching:
            self._conn.commit()

    def search_vectors(self, query_embedding: list[float], limit: int = 20) -> list[tuple[float, str]]:
        """Vector similarity search. Returns (distance, symbol_id) pairs."""
        if not getattr(self, '_has_vectors', False):
            return []
        try:
            rows = self._conn.execute(
                "SELECT distance, symbol_id FROM symbol_vectors "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (json.dumps(query_embedding), limit),
            ).fetchall()
            return [(row[0], row[1]) for row in rows]
        except (sqlite3.OperationalError, Exception):
            return []

    def search_hybrid(
        self, query: str, query_embedding: list[float] | None = None,
        limit: int = 20, k: int = 60,
    ) -> list[tuple[float, str]]:
        """Hybrid search: FTS5 + vector similarity merged via Reciprocal Rank Fusion.

        k=60 is the RRF constant from Cormack et al. (SIGIR 2009).
        Returns (rrf_score, symbol_id) pairs sorted by score descending.
        """
        # FTS5 results
        fts_results = self.search_fts(query, limit=limit * 2)

        # Vector results (if available)
        vec_results = []
        if query_embedding and getattr(self, '_has_vectors', False):
            vec_results = self.search_vectors(query_embedding, limit=limit * 2)

        if not fts_results and not vec_results:
            return []

        # Reciprocal Rank Fusion
        scores: dict[str, float] = {}

        for rank, (_, sym_id) in enumerate(fts_results):
            scores[sym_id] = scores.get(sym_id, 0) + 1.0 / (k + rank + 1)

        for rank, (_, sym_id) in enumerate(vec_results):
            scores[sym_id] = scores.get(sym_id, 0) + 1.0 / (k + rank + 1)

        # Sort by RRF score descending
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [(score, sym_id) for sym_id, score in ranked[:limit]]

    def symbol_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM symbols").fetchone()
        return row["c"] if row else 0

    def file_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM files").fetchone()
        return row["c"] if row else 0

    def graph_stats(self) -> dict:
        """Comprehensive graph statistics for dashboards and monitoring."""
        stats: dict = {}
        stats["files"] = self.file_count()
        stats["symbols"] = self.symbol_count()
        stats["edges"] = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # Language breakdown
        lang_rows = self._conn.execute(
            "SELECT language, COUNT(*) as c FROM files GROUP BY language ORDER BY c DESC"
        ).fetchall()
        stats["languages"] = {row["language"]: row["c"] for row in lang_rows}

        # Symbol kind breakdown
        kind_rows = self._conn.execute(
            "SELECT kind, COUNT(*) as c FROM symbols GROUP BY kind ORDER BY c DESC"
        ).fetchall()
        stats["symbol_kinds"] = {row["kind"]: row["c"] for row in kind_rows}

        # DB size
        stats["db_size_bytes"] = self.db_path.stat().st_size if self.db_path.exists() else 0

        # Vector count
        if getattr(self, '_has_vectors', False):
            try:
                stats["vectors"] = self._conn.execute("SELECT COUNT(*) FROM symbol_vectors").fetchone()[0]
            except Exception:
                stats["vectors"] = 0
        else:
            stats["vectors"] = 0

        return stats

    def load_indexes(self, edge_count: int) -> dict | None:
        """Load cached build_indexes result if edge_count matches. Returns None on miss.

        Uses a dedicated BLOB column instead of hex-inside-JSON encoding.
        Eliminates json.loads(1.9MB) + bytes.fromhex(966KB) overhead (~2.5ms/warm build).
        """
        import pickle
        try:
            row = self._conn.execute(
                "SELECT data FROM indexes_blob WHERE edge_count=?", (edge_count,)
            ).fetchone()
            if row is None:
                return None
            return pickle.loads(row[0])
        except Exception:
            return None

    def save_indexes(self, indexes: dict, edge_count: int) -> None:
        """Persist build_indexes result for warm build fast-path (BLOB storage).

        String interning: callers/callees/importers dicts map sym_id→[sym_ids], with
        the same IDs appearing as keys and values across all 6 index dicts. Interning
        enables pickle memoization, reducing blob from 931KB to ~358KB (-61%) and load
        time from 1.4ms to ~1.0ms (-27%).
        """
        import pickle, sys
        _intern = sys.intern
        interned = {
            k: {_intern(sym): [_intern(s) for s in srcs] for sym, srcs in d.items()}
            for k, d in indexes.items()
        }
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO indexes_blob (edge_count, data) VALUES (?, ?)",
                (edge_count, sqlite3.Binary(pickle.dumps(interned, protocol=5))),
            )
            self._conn.commit()
        except Exception:
            pass

    def load_edges_blob(self, edge_count: int) -> list[tuple] | None:
        """Load cached raw edge tuples if edge_count matches. Returns None on miss.

        Stores edge rows as raw tuples (kind, source_id, target_id, line) via pickle,
        replacing per-row SQLite fetch (~14.5ms) with a single BLOB read (~3.7ms).
        Keyed by edge_count — same strategy as indexes_blob.
        """
        import pickle
        try:
            row = self._conn.execute(
                "SELECT data FROM edges_blob WHERE edge_count=?", (edge_count,)
            ).fetchone()
            if row is None:
                return None
            return pickle.loads(row[0])
        except Exception:
            return None

    def save_edges_blob(self, edge_rows: list[tuple], edge_count: int) -> None:
        """Persist raw edge tuples for warm build fast-path (BLOB storage).

        Serializes raw tuples (kind, source_id, target_id, line) — not Edge objects —
        since pickle.dumps(raw tuples, p5) = 3.8ms vs 19ms for Edge objects.
        """
        import pickle
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO edges_blob (edge_count, data) VALUES (?, ?)",
                (edge_count, sqlite3.Binary(pickle.dumps(edge_rows, protocol=5))),
            )
            self._conn.commit()
        except Exception:
            pass

    def load_symbols_blob(self, sym_count: int) -> list[tuple] | None:
        """Load cached raw symbol tuples if sym_count matches. Returns None on miss.

        Stores symbol rows as raw tuples via pickle, replacing per-row SQLite fetch
        (~10ms) with a single BLOB read (~2.5ms). Keyed by sym_count.
        """
        import pickle
        try:
            row = self._conn.execute(
                "SELECT data FROM symbols_blob WHERE sym_count=?", (sym_count,)
            ).fetchone()
            if row is None:
                return None
            return pickle.loads(row[0])
        except Exception:
            return None

    def save_symbols_blob(self, sym_rows: list[tuple], sym_count: int) -> None:
        """Persist pre-resolved symbol tuples for warm build fast-path (BLOB storage).

        Stores tuples with (SymbolKind, Language) enum objects instead of raw strings,
        so load_all() skips per-symbol dict.get() lookups on warm builds. ~0.7ms savings.

        String interning: file_path repeats ~38x (many symbols per file), parent_id ~3x.
        Interning enables pickle memoization, reducing blob from 1919KB to ~1514KB (-21%)
        and load time from 2.3ms to ~1.9ms (-17%).
        """
        import pickle, sys
        _intern = sys.intern
        interned = [
            (r[0], r[1], r[2], r[3], r[4],
             _intern(r[5]),                   # file_path: avg 38 syms/file — high repeat
             r[6], r[7], r[8], r[9],
             _intern(r[10]) if r[10] else r[10],  # parent_id: None or str
             r[11], r[12], r[13])
            for r in sym_rows
        ]
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO symbols_blob (sym_count, data) VALUES (?, ?)",
                (sym_count, sqlite3.Binary(pickle.dumps(interned, protocol=5))),
            )
            self._conn.commit()
        except Exception:
            pass

    def load_files_blob(self, file_count: int) -> list[tuple] | None:
        """Load pre-resolved file tuples if file_count matches. Returns None on miss.

        Blob stores (path, Language_enum, line_count, byte_size, symbols_list, imports_list)
        tuples — skips both SQL SELECT and json.loads() on warm builds. ~1.1ms savings.
        Keyed by file_count.
        """
        import pickle
        try:
            row = self._conn.execute(
                "SELECT data FROM files_blob WHERE file_count=?", (file_count,)
            ).fetchone()
            if row is None:
                return None
            return pickle.loads(row[0])
        except Exception:
            return None

    def save_files_blob(self, file_rows: list[tuple], file_count: int) -> None:
        """Persist pre-resolved file tuples for warm build fast-path (BLOB storage).

        Stores tuples with Language enum objects and decoded symbols/imports lists,
        so next warm build skips SQL fetch and json.loads for all files.
        """
        import pickle
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO files_blob (file_count, data) VALUES (?, ?)",
                (file_count, sqlite3.Binary(pickle.dumps(file_rows, protocol=5))),
            )
            self._conn.commit()
        except Exception:
            pass

    def load_resolved_edges_blob(self, edge_count: int, sym_count: int) -> list[tuple] | None:
        """Load post-resolution edge tuples for warm-build skip of _resolve_imports/_resolve_edges.

        Key: '{edge_count}:{sym_count}' — invalidated if edges or symbols change.
        Returns None on miss.
        """
        import pickle
        try:
            key = f"{edge_count}:{sym_count}"
            row = self._conn.execute(
                "SELECT data FROM resolved_edges_blob WHERE edge_sym_key=?", (key,)
            ).fetchone()
            if row is None:
                return None
            return pickle.loads(bytes(row[0]))
        except Exception:
            return None

    def save_resolved_edges_blob(self, edge_tuples: list[tuple], edge_count: int, sym_count: int) -> None:
        """Persist post-resolution edge tuples (including IMPORTS edges from _resolve_imports).

        Stored as pickled list of (EdgeKind_enum, source_id, target_id, line) tuples.
        Kind stored as enum (not string) so load_all() skips per-edge get_edge_kind() on hit.
        On next warm build, load_all() uses _edges_pre_resolved=True path: no dict.get(),
        no walrus-filter, no resolution steps. ~5.3ms savings on 28k+ edges.

        String interning: source_id and target_id repeat heavily (8127 unique IDs across
        28k+ edges, avg 7x repeat). Interning enables pickle memoization, reducing blob
        size from 2601KB to ~892KB (-66%) and load time from 3.6ms to ~2.3ms (-36%).
        """
        import pickle, sys
        _intern = sys.intern
        interned = [(k, _intern(src), _intern(tgt), line) for k, src, tgt, line in edge_tuples]
        try:
            key = f"{edge_count}:{sym_count}"
            self._conn.execute(
                "INSERT OR REPLACE INTO resolved_edges_blob (edge_sym_key, data) VALUES (?, ?)",
                (key, sqlite3.Binary(pickle.dumps(interned, protocol=5))),
            )
            self._conn.commit()
        except Exception:
            pass

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
