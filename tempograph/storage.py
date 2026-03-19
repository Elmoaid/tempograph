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
SCHEMA_VERSION = 1


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
                imports_json TEXT DEFAULT '[]'
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

        # Insert file record
        cur.execute(
            "INSERT OR REPLACE INTO files (path, hash, language, line_count, byte_size, symbols_json, imports_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel_path, file_hash, language, line_count, byte_size,
             json.dumps([s.id for s in symbols]), json.dumps(imports)),
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
        """Load entire graph from DB into memory."""
        files: dict[str, FileInfo] = {}
        for row in self._conn.execute("SELECT * FROM files").fetchall():
            files[row["path"]] = FileInfo(
                path=row["path"],
                language=Language(row["language"]) if row["language"] in Language.__members__.values() else Language.UNKNOWN,
                line_count=row["line_count"],
                byte_size=row["byte_size"],
                symbols=json.loads(row["symbols_json"]),
                imports=json.loads(row["imports_json"]),
            )

        symbols: dict[str, Symbol] = {}
        for row in self._conn.execute("SELECT * FROM symbols").fetchall():
            try:
                kind = SymbolKind(row["kind"])
            except ValueError:
                kind = SymbolKind.UNKNOWN
            try:
                lang = Language(row["language"])
            except ValueError:
                lang = Language.UNKNOWN
            symbols[row["id"]] = Symbol(
                id=row["id"],
                name=row["name"],
                qualified_name=row["qualified_name"],
                kind=kind,
                language=lang,
                file_path=row["file_path"],
                line_start=row["line_start"],
                line_end=row["line_end"],
                signature=row["signature"] or "",
                doc=row["doc"] or "",
                parent_id=row["parent_id"],
                exported=bool(row["exported"]),
                complexity=row["complexity"],
                byte_size=row["byte_size"],
            )

        edges: list[Edge] = []
        for row in self._conn.execute("SELECT * FROM edges").fetchall():
            try:
                kind = EdgeKind(row["kind"])
            except ValueError:
                continue
            edges.append(Edge(
                kind=kind,
                source_id=row["source_id"],
                target_id=row["target_id"],
                line=row["line"],
            ))

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

    def symbol_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM symbols").fetchone()
        return row["c"] if row else 0

    def file_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM files").fetchone()
        return row["c"] if row else 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
