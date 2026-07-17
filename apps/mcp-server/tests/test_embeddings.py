"""Tests for ChromaDB semantic search (embeddings module).

Tests use a temporary ChromaDB directory and mock/skip
sentence-transformers when not installed.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nfm_mcp import embeddings as emb_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_embeddings():
    """Reset module-level singletons between tests."""
    emb_module._client = None
    emb_module._collection = None
    emb_module._embedding_fn = None
    yield
    emb_module._client = None
    emb_module._collection = None
    emb_module._embedding_fn = None


@pytest.fixture()
def persist_dir(tmp_path: tempfile.TemporaryDirectory) -> str:
    return str(tmp_path / "chroma_test")


# ---------------------------------------------------------------------------
# Embedding function tests
# ---------------------------------------------------------------------------


class TestInitEmbeddingFunction:
    """Test embedding function initialization."""

    def test_returns_none_when_not_installed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                emb_module._ef_module,
                "SentenceTransformerEmbeddingFunction",
                side_effect=ImportError("not installed"),
                create=True,
            ):
                result = emb_module._init_embedding_function()
                assert result is None

    def test_returns_function_when_installed(self) -> None:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                emb_module._ef_module,
                "SentenceTransformerEmbeddingFunction",
                mock_cls,
                create=True,
            ):
                result = emb_module._init_embedding_function()
                assert result is mock_instance

    def test_singleton_behavior(self) -> None:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                emb_module._ef_module,
                "SentenceTransformerEmbeddingFunction",
                mock_cls,
                create=True,
            ):
                first = emb_module._init_embedding_function()
                second = emb_module._init_embedding_function()
                assert first is second
                assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# Collection tests
# ---------------------------------------------------------------------------


class TestGetCollection:
    """Test ChromaDB collection creation."""

    def test_creates_persistent_collection(self, persist_dir: str) -> None:
        with patch.dict(os.environ, {"CHROMA_PERSIST_DIR": persist_dir}):
            with patch(
                "nfm_mcp.embeddings.SentenceTransformerEmbeddingFunction",
                side_effect=ImportError("not installed"),
                create=True,
            ):
                with patch("nfm_mcp.embeddings.chromadb") as mock_chroma:
                    mock_client = MagicMock()
                    mock_collection = MagicMock()
                    mock_collection.count.return_value = 0
                    mock_client.get_or_create_collection.return_value = mock_collection
                    mock_chroma.PersistentClient.return_value = mock_client

                    result = emb_module.get_collection()

                    mock_chroma.PersistentClient.assert_called_once_with(path=persist_dir)
                    mock_client.get_or_create_collection.assert_called_once()
                    assert result is mock_collection

    def test_returns_cached_collection(self, persist_dir: str) -> None:
        emb_module._collection = MagicMock()
        result = emb_module.get_collection()
        assert result is emb_module._collection


# ---------------------------------------------------------------------------
# Build index tests
# ---------------------------------------------------------------------------


class TestBuildSemanticIndex:
    """Test building the semantic index."""

    SAMPLE_SOURCES: list[dict[str, Any]] = [
        {
            "id": 1,
            "title": "Paper on UO2",
            "abstract": "Analysis of uranium dioxide fuel behavior",
            "doi": "10.1234/a",
            "journal": "JNM",
            "year": 2024,
            "source_type": "journal_article",
        },
        {
            "id": 2,
            "title": "Another Paper",
            "abstract": "Study of zirconium alloys",
            "doi": "10.5678/b",
            "journal": "Acta Materialia",
            "year": 2023,
            "source_type": "conference_paper",
        },
        {
            "id": 3,
            "title": "",
            "abstract": "",
            "doi": "",
            "journal": "",
            "year": 2022,
            "source_type": "journal_article",
        },
    ]

    def test_upserts_documents(self, persist_dir: str) -> None:
        mock_collection = MagicMock()
        emb_module._collection = mock_collection

        count = emb_module.build_semantic_index(self.SAMPLE_SOURCES)

        assert count == 2  # third item has no text
        mock_collection.upsert.assert_called_once()
        call_args = mock_collection.upsert.call_args
        assert len(call_args.kwargs["ids"]) == 2
        assert call_args.kwargs["ids"][0] == "1"

    def test_returns_zero_for_empty(self) -> None:
        mock_collection = MagicMock()
        emb_module._collection = mock_collection

        count = emb_module.build_semantic_index([])
        assert count == 0
        mock_collection.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Semantic search tests
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    """Test semantic search functionality."""

    def test_search_returns_results(self, persist_dir: str) -> None:
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["doc1", "doc2"]],
            "documents": [["Paper A text", "Paper B text"]],
            "metadatas": [[
                {"title": "Paper A", "doi": "10.1/a", "journal": "JNM", "year": "2024"},
                {"title": "Paper B", "doi": "10.2/b", "journal": "Nature", "year": "2023"},
            ]],
            "distances": [[0.1, 0.3]],
        }
        emb_module._collection = mock_collection

        results = emb_module.semantic_search("uranium fuel", top_k=5)

        assert len(results) == 2
        assert results[0]["id"] == "doc1"
        assert results[0]["distance"] == 0.1
        assert results[0]["metadata"]["title"] == "Paper A"

    def test_search_with_source_type_filter(self, persist_dir: str) -> None:
        mock_collection = MagicMock()
        mock_collection.count.return_value = 10
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["text"]],
            "metadatas": [[{"title": "Paper A"}]],
            "distances": [[0.2]],
        }
        emb_module._collection = mock_collection

        results = emb_module.semantic_search(
            "fuel", source_type="journal_article"
        )

        mock_collection.query.assert_called_once()
        call_kwargs = mock_collection.query.call_args.kwargs
        assert call_kwargs["where"] == {"source_type": "journal_article"}
        assert len(results) == 1

    def test_search_returns_empty_when_no_results(self, persist_dir: str) -> None:
        mock_collection = MagicMock()
        mock_collection.count.return_value = 0
        mock_collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        emb_module._collection = mock_collection

        results = emb_module.semantic_search("nonexistent")
        assert results == []

    def test_search_with_year_range(self, persist_dir: str) -> None:
        mock_collection = MagicMock()
        mock_collection.count.return_value = 10
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["text"]],
            "metadatas": [[{"title": "Paper A"}]],
            "distances": [[0.2]],
        }
        emb_module._collection = mock_collection

        emb_module.semantic_search("fuel", year_range="2020-2024")

        call_kwargs = mock_collection.query.call_args.kwargs
        assert "year" in call_kwargs["where"]

    def test_search_handles_invalid_year_range(self, persist_dir: str) -> None:
        mock_collection = MagicMock()
        mock_collection.count.return_value = 10
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["text"]],
            "metadatas": [[{"title": "Paper A"}]],
            "distances": [[0.2]],
        }
        emb_module._collection = mock_collection

        emb_module.semantic_search("fuel", year_range="invalid")
        call_kwargs = mock_collection.query.call_args.kwargs
        assert call_kwargs.get("where") is None
