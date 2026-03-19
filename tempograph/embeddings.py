"""Embedding generation for semantic code search.

Uses fastembed with BAAI/bge-small-en-v1.5 (33MB, 384-dim) for local
embedding generation. No API keys, no cloud — runs entirely on CPU.

Usage:
    from tempograph.embeddings import embed_symbols
    embed_symbols(db)  # embeds all symbols without vectors into the DB
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import GraphDB


# Lazy-loaded model singleton
_model = None
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_DIMENSIONS = 384
_BATCH_SIZE = 64


def _get_model():
    global _model
    if _model is None:
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(_MODEL_NAME)
        except ImportError:
            return None
    return _model


def _symbol_text(symbol_id: str, name: str, qualified_name: str,
                 signature: str, doc: str, file_path: str, kind: str) -> str:
    """Build a text representation of a symbol for embedding."""
    parts = [f"{kind} {qualified_name}"]
    if signature and signature != name:
        parts.append(signature)
    if doc:
        parts.append(doc)
    parts.append(f"in {file_path}")
    return " | ".join(parts)


def embed_symbols(db: "GraphDB", force: bool = False) -> int:
    """Generate embeddings for all symbols in the DB that don't have vectors yet.

    Args:
        db: GraphDB instance with init_vectors() already called
        force: if True, re-embed all symbols even if they already have vectors

    Returns:
        Number of symbols embedded
    """
    model = _get_model()
    if model is None:
        return 0

    if not db.init_vectors(dimensions=_DIMENSIONS):
        return 0

    # Get symbols that need embedding
    if force:
        rows = db._conn.execute(
            "SELECT id, name, qualified_name, signature, doc, file_path, kind FROM symbols"
        ).fetchall()
    else:
        # Only embed symbols without existing vectors
        rows = db._conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.signature, s.doc, s.file_path, s.kind "
            "FROM symbols s "
            "LEFT JOIN symbol_vectors v ON s.id = v.symbol_id "
            "WHERE v.symbol_id IS NULL"
        ).fetchall()

    if not rows:
        return 0

    # Build text representations
    texts = []
    sym_ids = []
    for row in rows:
        text = _symbol_text(
            row["id"], row["name"], row["qualified_name"],
            row["signature"] or "", row["doc"] or "",
            row["file_path"], row["kind"],
        )
        texts.append(text)
        sym_ids.append(row["id"])

    # Generate embeddings in batches
    count = 0
    for batch_start in range(0, len(texts), _BATCH_SIZE):
        batch_texts = texts[batch_start:batch_start + _BATCH_SIZE]
        batch_ids = sym_ids[batch_start:batch_start + _BATCH_SIZE]

        embeddings = list(model.embed(batch_texts))

        items = [(sid, emb.tolist()) for sid, emb in zip(batch_ids, embeddings)]
        db.upsert_vectors_batch(items)
        count += len(items)

    return count


def embed_query(query: str) -> list[float] | None:
    """Embed a search query for vector similarity search."""
    model = _get_model()
    if model is None:
        return None
    embeddings = list(model.embed([query]))
    return embeddings[0].tolist()
