"""ChromaDB semantic search for NFM data sources.

Provides vector similarity search over data source abstracts using
sentence-transformers embeddings stored in a persistent ChromaDB collection.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import chromadb
import chromadb.utils.embedding_functions as _ef_module

logger = logging.getLogger(__name__)

# Default persist directory
_DEFAULT_PERSIST_DIR = "data/chroma"
_COLLECTION_NAME = "nfm_sources"

# Module-level singleton
_client = None
_collection = None
_embedding_fn = None


def _get_persist_dir() -> str:
    """Return the ChromaDB persist directory from env or default."""
    return os.environ.get("CHROMA_PERSIST_DIR", _DEFAULT_PERSIST_DIR)


def _init_embedding_function():
    """Lazy-initialize the sentence-transformers embedding function.

    Returns a callable that takes a list of strings and returns
    a list of embedding vectors.
    """
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn

    try:
        SentenceTransformerEmbeddingFunction = getattr(
            _ef_module, "SentenceTransformerEmbeddingFunction", None
        )
        if SentenceTransformerEmbeddingFunction is None:
            logger.info("SentenceTransformerEmbeddingFunction not available")
            return None

        model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        _embedding_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
        logger.info("Initialized embedding function: %s", model_name)
        return _embedding_fn
    except (ImportError, Exception):
        logger.warning(
            "sentence-transformers not installed; "
            "falling back to default ChromaDB embedding"
        )
        return None


def get_collection():
    """Get or create the ChromaDB collection for NFM sources.

    Lazy-initializes the client and collection on first access.
    """
    global _client, _collection

    if _collection is not None:
        return _collection

    persist_dir = _get_persist_dir()
    _client = chromadb.PersistentClient(path=persist_dir)
    embedding_fn = _init_embedding_function()

    _collection = _client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"description": "NFM data sources semantic index"},
        embedding_function=embedding_fn,  # type: ignore[arg-type]
    )
    logger.info(
        "ChromaDB collection '%s' ready (%d documents)",
        _COLLECTION_NAME,
        _collection.count(),
    )
    return _collection


def build_semantic_index(
    sources: list[dict[str, Any]],
) -> int:
    """Build or update the semantic index from data source records.

    Args:
        sources: List of dicts, each containing at least ``id`` and ``abstract``.
            Other fields (``title``, ``doi``, ``journal``, ``year``, ``source_type``)
            are stored as metadata for filtering.

    Returns:
        Number of documents upserted into the index.
    """
    collection = get_collection()

    if not sources:
        logger.info("No sources to index")
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for source in sources:
        source_id = str(source.get("id", ""))
        abstract = source.get("abstract", "") or ""
        title = source.get("title", "") or ""

        # Combine title + abstract for richer embeddings
        text = f"{title}. {abstract}".strip()
        if not text or text == ".":
            continue

        ids.append(source_id)
        documents.append(text)
        metadatas.append({
            "title": source.get("title", ""),
            "doi": source.get("doi", ""),
            "journal": source.get("journal", ""),
            "year": str(source.get("year", "")),
            "source_type": source.get("source_type", ""),
            "external_key": source.get("external_key", ""),
        })

    if not ids:
        logger.info("No documents with text to index")
        return 0

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    logger.info("Indexed %d documents into ChromaDB", len(ids))
    return len(ids)


def semantic_search(
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    year_range: str | None = None,
) -> list[dict[str, Any]]:
    """Search the semantic index for sources similar to the query.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.
        source_type: Optional filter (e.g., ``"journal_article"``).
        year_range: Optional filter as ``"YYYY-YYYY"`` (e.g., ``"2020-2025"``).

    Returns:
        List of result dicts with ``id``, ``document``, ``metadata``, ``distance``.
    """
    collection = get_collection()

    where: dict[str, Any] = {}
    if source_type:
        where["source_type"] = source_type

    if year_range and "-" in year_range:
        parts = year_range.split("-", 1)
        try:
            start_year = int(parts[0].strip())
            end_year = int(parts[1].strip())
            where["year"] = {"$gte": str(start_year), "$lte": str(end_year)}
        except (ValueError, IndexError):
            logger.warning("Invalid year_range: %s", year_range)

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, max(collection.count(), 1)),
        where=where if where else None,
    )

    if not results or not results.get("ids") or not results["ids"][0]:
        return []

    # ChromaDB returns lists of lists (one per query)
    return [
        {
            "id": results["ids"][0][i],
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        }
        for i in range(len(results["ids"][0]))
    ]
