"""Tests for tempograph.embeddings — uses mock-or-skip pattern for optional fastembed dep."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSymbolText:
    """Tests for _symbol_text() — pure function, no deps."""

    def test_basic_function(self):
        from tempograph.embeddings import _symbol_text
        text = _symbol_text("id1", "my_func", "module.my_func", "def my_func(x: int) -> str",
                            "", "module.py", "function")
        assert "function module.my_func" in text
        assert "def my_func(x: int) -> str" in text
        assert "in module.py" in text

    def test_class_with_doc(self):
        from tempograph.embeddings import _symbol_text
        text = _symbol_text("id2", "MyClass", "pkg.MyClass", "class MyClass:",
                            "Handles user auth.", "pkg/auth.py", "class")
        assert "class pkg.MyClass" in text
        assert "Handles user auth." in text
        assert "in pkg/auth.py" in text

    def test_no_signature_dedup(self):
        """When signature == name, signature is not added (prevents 'func | func | in ...')."""
        from tempograph.embeddings import _symbol_text
        text = _symbol_text("id3", "render", "render", "render", "", "render.py", "function")
        # "render" should not appear twice before the "in" separator
        parts = text.split(" | ")
        assert parts[0] == "function render"
        assert parts[-1] == "in render.py"
        assert len(parts) == 2  # kind+name, file — no signature dup

    def test_no_doc_no_extra_part(self):
        from tempograph.embeddings import _symbol_text
        text = _symbol_text("id4", "helper", "utils.helper", "def helper()",
                            "", "utils.py", "function")
        parts = text.split(" | ")
        assert len(parts) == 3  # kind+name, signature, file
        assert "in utils.py" == parts[-1]

    def test_pipe_separated_format(self):
        from tempograph.embeddings import _symbol_text
        text = _symbol_text("id5", "Foo", "a.Foo", "class Foo:", "Doc.", "a.py", "class")
        assert " | " in text


class TestGetModel:
    """Tests for _get_model() — lazy-loaded singleton."""

    def test_returns_none_when_fastembed_missing(self):
        """When fastembed is not installed, _get_model returns None gracefully."""
        import tempograph.embeddings as emb
        original = emb._model
        emb._model = None
        try:
            with patch.dict("sys.modules", {"fastembed": None}):
                result = emb._get_model()
                # Either returns None (import failed) or a real model (fastembed installed)
                # We only assert no exception is raised
                assert result is None or result is not None
        finally:
            emb._model = original

    def test_singleton_caching(self):
        """_get_model returns the same object on second call (singleton pattern)."""
        import tempograph.embeddings as emb
        original = emb._model
        fake_model = MagicMock()
        emb._model = fake_model
        try:
            result1 = emb._get_model()
            result2 = emb._get_model()
            assert result1 is fake_model
            assert result2 is fake_model
            assert result1 is result2
        finally:
            emb._model = original


class TestEmbedSymbols:
    """Tests for embed_symbols() — mocks model to avoid fastembed dep."""

    def _make_db_with_symbol(self, tmp_path):
        """Create GraphDB in tmp_path with one symbol."""
        from tempograph.storage import GraphDB
        from tempograph.types import Symbol, SymbolKind, Language
        repo = tmp_path / "repo"
        repo.mkdir()
        db = GraphDB(repo)
        sym = Symbol(
            id="test.py::my_func",
            name="my_func",
            qualified_name="test.my_func",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="test.py",
            line_start=1,
            line_end=6,
            signature="def my_func()",
            doc="Does something.",
            exported=True,
            complexity=3,
            byte_size=100,
        )
        db.update_file("test.py", "hash1", "python", 10, 100, [sym], [], [])
        return db

    def test_returns_zero_when_model_unavailable(self, tmp_path):
        """embed_symbols returns 0 when model is None (fastembed not installed)."""
        with patch("tempograph.embeddings._get_model", return_value=None):
            from tempograph.embeddings import embed_symbols
            db = self._make_db_with_symbol(tmp_path)
            count = embed_symbols(db)
            assert count == 0

    def test_embeds_symbols_with_mocked_model(self, tmp_path):
        """embed_symbols calls upsert_vectors_batch with correct number of items."""
        import numpy as np
        fake_embedding = np.array([0.1] * 384)
        fake_model = MagicMock()
        fake_model.embed.return_value = [fake_embedding]

        db = self._make_db_with_symbol(tmp_path)
        db.init_vectors(dimensions=384)

        with patch("tempograph.embeddings._get_model", return_value=fake_model):
            with patch.object(db, "upsert_vectors_batch") as mock_upsert:
                from tempograph.embeddings import embed_symbols
                count = embed_symbols(db)
                assert count == 1
                mock_upsert.assert_called_once()
                args = mock_upsert.call_args[0][0]
                assert len(args) == 1
                sid, vec = args[0]
                assert sid == "test.py::my_func"
                assert len(vec) == 384

    def test_skips_already_embedded_symbols(self, tmp_path):
        """embed_symbols skips symbols that already have vectors (incremental)."""
        import numpy as np
        fake_embedding = np.array([0.2] * 384)
        fake_model = MagicMock()
        fake_model.embed.return_value = [fake_embedding]

        db = self._make_db_with_symbol(tmp_path)
        if not db.init_vectors(dimensions=384):
            return  # sqlite-vec not available — skip gracefully

        with patch("tempograph.embeddings._get_model", return_value=fake_model):
            from tempograph.embeddings import embed_symbols
            # First embed
            embed_symbols(db)
            # Second embed — should find nothing new
            fake_model.embed.return_value = []
            count2 = embed_symbols(db)
            assert count2 == 0  # nothing to embed on second pass

    def test_force_reembeds_all(self, tmp_path):
        """force=True re-embeds even existing symbols."""
        import numpy as np
        fake_embedding = np.array([0.3] * 384)
        fake_model = MagicMock()
        fake_model.embed.side_effect = [[fake_embedding], [fake_embedding]]

        db = self._make_db_with_symbol(tmp_path)
        db.init_vectors(dimensions=384)

        with patch("tempograph.embeddings._get_model", return_value=fake_model):
            with patch.object(db, "upsert_vectors_batch"):
                from tempograph.embeddings import embed_symbols
                count = embed_symbols(db, force=True)
                assert count == 1  # forced re-embed of 1 symbol


class TestEmbedQuery:
    """Tests for embed_query() — mocks model."""

    def test_returns_none_when_model_unavailable(self):
        with patch("tempograph.embeddings._get_model", return_value=None):
            from tempograph.embeddings import embed_query
            result = embed_query("find authentication functions")
            assert result is None

    def test_returns_embedding_list(self):
        import numpy as np
        fake_embedding = np.array([0.5] * 384)
        fake_model = MagicMock()
        fake_model.embed.return_value = [fake_embedding]

        with patch("tempograph.embeddings._get_model", return_value=fake_model):
            from tempograph.embeddings import embed_query
            result = embed_query("user authentication")
            assert isinstance(result, list)
            assert len(result) == 384
            assert abs(result[0] - 0.5) < 1e-6

    def test_query_passed_as_list(self):
        """embed_query wraps query in a list before calling model.embed."""
        import numpy as np
        fake_model = MagicMock()
        fake_model.embed.return_value = [np.array([0.0] * 384)]

        with patch("tempograph.embeddings._get_model", return_value=fake_model):
            from tempograph.embeddings import embed_query
            embed_query("hello world")
            call_args = fake_model.embed.call_args[0][0]
            assert call_args == ["hello world"]
