"""Knowledge Graph query service with caching, timeout, and monitoring.

Provides cached, timeout-guarded access to KG node and edge queries.
Cache key = SHA-256 hash of (query_type + sorted params).
TTL = 300s, max entries = 512.  All queries capped at 30s.

Top-5 query patterns covered:
  1. List nodes by type  (paginated)
  2. List nodes by corpus + type  (paginated)
  3. Get node by ID
  4. Get edges from a node  (1-hop outgoing, paginated)
  5. Get edges by relation type  (paginated)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cachetools import TTLCache
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, VALID_NODE_TYPES, VALID_RELATION_TYPES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 300
_CACHE_MAX_SIZE = 512
_QUERY_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Cache metrics (immutable snapshot — safe to read from any context)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheMetrics:
    """Point-in-time cache performance counters."""

    hits: int
    misses: int
    evictions: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class _CacheStats:
    """Mutable accumulator — internal, never exposed directly."""

    __slots__ = ("_hits", "_misses", "_evictions")

    def __init__(self) -> None:
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def snapshot(self) -> CacheMetrics:
        return CacheMetrics(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
        )

    def reset(self) -> None:
        self._hits = 0
        self._misses = 0
        self._evictions = 0


_stats = _CacheStats()


def get_cache_metrics() -> CacheMetrics:
    """Return a frozen snapshot of the current cache metrics."""
    return _stats.snapshot()


def reset_cache_metrics() -> None:
    """Reset all cache counters to zero (useful in tests)."""
    _stats.reset()


# ---------------------------------------------------------------------------
# TTLCache with eviction callback
# ---------------------------------------------------------------------------


def _on_cache_eviction(_, __, ___) -> None:
    _stats._evictions += 1
    logger.debug("KG query cache eviction (total=%d)", _stats._evictions)


_kg_cache: TTLCache[str, Any] = TTLCache(
    maxsize=_CACHE_MAX_SIZE,
    ttl=_CACHE_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Cache-key helpers
# ---------------------------------------------------------------------------


def _cache_key(query_type: str, **params: Any) -> str:
    """Deterministic SHA-256 cache key from query type + sorted params."""
    raw = json.dumps(
        {"t": query_type, "p": {k: str(v) for k, v in sorted(params.items())}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class KGQueryTimeoutError(RuntimeError):
    """Raised when a KG query exceeds the configured timeout."""


class KGNodeNotFoundError(LookupError):
    """Raised when a requested KG node ID does not exist."""


# ---------------------------------------------------------------------------
# Query functions (each covers one of the top-5 patterns)
# ---------------------------------------------------------------------------


async def list_nodes_by_type(
    session: AsyncSession,
    *,
    node_type: str | None = None,
    corpus_id: str | None = None,
    status: str = "active",
    page: int = 1,
    per_page: int = 20,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Pattern #1 / #2: List nodes with optional type/corpus filter (paginated).

    Args:
        session: Async database session.
        node_type: Optional filter by entity type (Material, Property, …).
        corpus_id: Optional corpus identifier.
        status: Node status filter (default 'active').
        page: 1-based page number.
        per_page: Page size (1–100).
        use_cache: Whether to check/populate the cache.

    Returns:
        Dict with ``items``, ``total``, ``page``, ``per_page``, ``pages``.

    Raises:
        ValueError: If ``node_type`` is not a recognised type.
    """
    if node_type is not None and node_type not in VALID_NODE_TYPES:
        raise ValueError(f"invalid node_type: {node_type!r}")

    cache_params = dict(
        node_type=node_type,
        corpus_id=corpus_id,
        status=status,
        page=page,
        per_page=per_page,
    )
    key = _cache_key("list_nodes", **cache_params)

    if use_cache:
        cached = _kg_cache.get(key)
        if cached is not None:
            _stats._hits += 1
            return cached
        _stats._misses += 1

    result = await _execute_with_timeout(
        _list_nodes_by_type_impl,
        session,
        node_type=node_type,
        corpus_id=corpus_id,
        status=status,
        page=page,
        per_page=per_page,
    )

    if use_cache:
        _kg_cache[key] = result
    return result


async def _list_nodes_by_type_impl(
    session: AsyncSession,
    *,
    node_type: str | None,
    corpus_id: str | None,
    status: str,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    """Pure implementation — no caching, no timeout."""
    base = select(KGNode).where(KGNode.status == status)
    if node_type is not None:
        base = base.where(KGNode.node_type == node_type)
    if corpus_id is not None:
        base = base.where(KGNode.corpus_id == corpus_id)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * per_page
    data_stmt = base.order_by(KGNode.created_at.desc()).offset(offset).limit(per_page)
    rows = (await session.execute(data_stmt)).scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": [_node_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


async def get_node_by_id(
    session: AsyncSession,
    node_id: UUID,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Pattern #3: Get a single node by UUID.

    Raises:
        KGNodeNotFoundError: If the node does not exist.
    """
    key = _cache_key("get_node", node_id=str(node_id))

    if use_cache:
        cached = _kg_cache.get(key)
        if cached is not None:
            _stats._hits += 1
            return cached
        _stats._misses += 1

    result = await _execute_with_timeout(
        _get_node_by_id_impl,
        session,
        node_id=node_id,
    )

    if use_cache:
        _kg_cache[key] = result
    return result


async def _get_node_by_id_impl(
    session: AsyncSession,
    node_id: UUID,
) -> dict[str, Any]:
    stmt = select(KGNode).where(KGNode.id == node_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise KGNodeNotFoundError(str(node_id))
    return _node_to_dict(row)


async def get_edges_from_node(
    session: AsyncSession,
    source_node_id: UUID,
    *,
    relation_type: str | None = None,
    direction: str = "outgoing",
    page: int = 1,
    per_page: int = 20,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Pattern #4: 1-hop edges from a node (paginated).

    Args:
        direction: 'outgoing' (default) or 'incoming'.
    """
    cache_params = dict(
        source_node_id=str(source_node_id),
        relation_type=relation_type,
        direction=direction,
        page=page,
        per_page=per_page,
    )
    key = _cache_key("edges_from_node", **cache_params)

    if use_cache:
        cached = _kg_cache.get(key)
        if cached is not None:
            _stats._hits += 1
            return cached
        _stats._misses += 1

    result = await _execute_with_timeout(
        _get_edges_from_node_impl,
        session,
        source_node_id=source_node_id,
        relation_type=relation_type,
        direction=direction,
        page=page,
        per_page=per_page,
    )

    if use_cache:
        _kg_cache[key] = result
    return result


async def _get_edges_from_node_impl(
    session: AsyncSession,
    source_node_id: UUID,
    *,
    relation_type: str | None,
    direction: str,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    if direction == "incoming":
        col = KGEdge.target_node_id
    else:
        col = KGEdge.source_node_id

    base = select(KGEdge).where(col == source_node_id)
    if relation_type is not None:
        base = base.where(KGEdge.relation_type == relation_type)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * per_page
    data_stmt = base.order_by(KGEdge.created_at.desc()).offset(offset).limit(per_page)
    rows = (await session.execute(data_stmt)).scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": [_edge_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


async def list_edges_by_relation(
    session: AsyncSession,
    relation_type: str,
    *,
    corpus_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Pattern #5: List edges by relation type (paginated)."""
    if relation_type not in VALID_RELATION_TYPES:
        raise ValueError(f"invalid relation_type: {relation_type!r}")

    cache_params = dict(
        relation_type=relation_type,
        corpus_id=corpus_id,
        page=page,
        per_page=per_page,
    )
    key = _cache_key("edges_by_relation", **cache_params)

    if use_cache:
        cached = _kg_cache.get(key)
        if cached is not None:
            _stats._hits += 1
            return cached
        _stats._misses += 1

    result = await _execute_with_timeout(
        _list_edges_by_relation_impl,
        session,
        relation_type=relation_type,
        corpus_id=corpus_id,
        page=page,
        per_page=per_page,
    )

    if use_cache:
        _kg_cache[key] = result
    return result


async def _list_edges_by_relation_impl(
    session: AsyncSession,
    relation_type: str,
    *,
    corpus_id: str | None,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    base = select(KGEdge).where(KGEdge.relation_type == relation_type)
    if corpus_id is not None:
        base = base.where(KGEdge.corpus_id == corpus_id)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * per_page
    data_stmt = base.order_by(KGEdge.created_at.desc()).offset(offset).limit(per_page)
    rows = (await session.execute(data_stmt)).scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": [_edge_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


# ---------------------------------------------------------------------------
# Serialization helpers (pure functions — no DB access)
# ---------------------------------------------------------------------------


def _node_to_dict(node: KGNode) -> dict[str, Any]:
    """Serialize a KGNode to a plain dict."""
    return {
        "id": str(node.id),
        "node_type": node.node_type,
        "label": node.label,
        "aliases": node.aliases,
        "properties": node.properties,
        "confidence": node.confidence,
        "status": node.status,
        "corpus_id": node.corpus_id,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


def _edge_to_dict(edge: KGEdge) -> dict[str, Any]:
    """Serialize a KGEdge to a plain dict."""
    return {
        "id": str(edge.id),
        "source_node_id": str(edge.source_node_id),
        "target_node_id": str(edge.target_node_id),
        "relation_type": edge.relation_type,
        "properties": edge.properties,
        "confidence": edge.confidence,
        "corpus_id": edge.corpus_id,
        "created_at": edge.created_at.isoformat() if edge.created_at else None,
    }


# ---------------------------------------------------------------------------
# Timeout wrapper
# ---------------------------------------------------------------------------


async def _execute_with_timeout(coro, *args: Any, **kwargs: Any) -> Any:
    """Wrap an async query coroutine with a 30s timeout.

    Logs elapsed time and raises ``KGQueryTimeoutError`` on expiry.
    """
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            coro(*args, **kwargs),
            timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        logger.warning(
            "KG query timed out after %.2fs", elapsed,
        )
        raise KGQueryTimeoutError(
            f"KG query exceeded {_QUERY_TIMEOUT_SECONDS}s timeout"
        ) from None
    else:
        elapsed = time.monotonic() - start
        logger.info("KG query completed in %.3fs", elapsed)
        return result


def invalidate_cache() -> None:
    """Clear the entire KG query cache. Useful after data mutations."""
    _kg_cache.clear()
    logger.info("KG query cache cleared")
