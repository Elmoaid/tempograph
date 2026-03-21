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
SCHEMA_VERSION = 2

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
        self._init_schema()

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
        self._conn.commit()
        return len(stale)

    def load_all(self) -> tuple[dict[str, FileInfo], dict[str, Symbol], list[Edge]]:
        """Load entire graph from DB into memory.

        Uses tuple positional access instead of sqlite3.Row dict access to avoid
        per-field string key lookups. Benchmarked savings: ~3.7ms on 290 files /
        1510 symbols / 7211 edges (20% improvement over dict-access baseline).
        """
        orig_factory = self._conn.row_factory
        self._conn.row_factory = None  # raw tuples: faster positional access

        try:
            # files: path(0) language(1) line_count(2) byte_size(3) symbols_json(4) imports_json(5)
            file_rows = self._conn.execute(
                "SELECT path, language, line_count, byte_size, symbols_json, imports_json FROM files"
            ).fetchall()

            # symbols: id(0) name(1) qualified_name(2) kind(3) language(4) file_path(5)
            #          line_start(6) line_end(7) signature(8) doc(9) parent_id(10)
            #          exported(11) complexity(12) byte_size(13)
            sym_rows = self._conn.execute(
                "SELECT id, name, qualified_name, kind, language, file_path, "
                "line_start, line_end, signature, doc, parent_id, exported, complexity, byte_size "
                "FROM symbols"
            ).fetchall()

            # edges: kind(0) source_id(1) target_id(2) line(3)
            edge_rows = self._conn.execute(
                "SELECT kind, source_id, target_id, line FROM edges"
            ).fetchall()
        finally:
            self._conn.row_factory = orig_factory

        jl = json.loads
        get_lang = _LANGUAGE_MAP.get
        get_kind = _SYMBOL_KIND_MAP.get
        get_edge_kind = _EDGE_KIND_MAP.get
        unk_lang = Language.UNKNOWN
        unk_kind = SymbolKind.UNKNOWN

        files: dict[str, FileInfo] = {
            r[0]: FileInfo(
                path=r[0], language=get_lang(r[1], unk_lang),
                line_count=r[2], byte_size=r[3], symbols=jl(r[4]), imports=jl(r[5]),
            )
            for r in file_rows
        }

        symbols: dict[str, Symbol] = {
            r[0]: Symbol(
                id=r[0], name=r[1], qualified_name=r[2],
                kind=get_kind(r[3], unk_kind), language=get_lang(r[4], unk_lang),
                file_path=r[5], line_start=r[6], line_end=r[7],
                signature=r[8] or "", doc=r[9] or "",
                parent_id=r[10], exported=bool(r[11]),
                complexity=r[12], byte_size=r[13],
            )
            for r in sym_rows
        }

        edges: list[Edge] = [
            Edge(kind=k, source_id=r[1], target_id=r[2], line=r[3])
            for r in edge_rows
            if (k := get_edge_kind(r[0])) is not None
        ]

        return files, symbols, edges

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

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
