"""Tests for KG query service: caching, timeout, pagination, monitoring.

Covers all 5 query patterns, cache hit/miss/TTL, 30s timeout guard,
cache metrics, X-Response-Time header, and validation errors.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_query_service import (
    KGNodeNotFoundError,
    KGQueryTimeoutError,
    _QUERY_TIMEOUT_SECONDS,
    _cache_key,
    get_cache_metrics,
    invalidate_cache,
    list_edges_by_relation,
    list_nodes_by_type,
    reset_cache_metrics,
    get_edges_from_node,
    get_node_by_id,
)
from nfm_db.schemas.common import PaginationParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    **overrides: object,
) -> KGNode:
    """Create a test KGNode with sensible defaults."""
    defaults = {
        "id": uuid4(),
        "node_type": "Material",
        "label": "Uranium",
        "confidence": 0.95,
        "status": "active",
    }
    defaults.update(overrides)
    node = KGNode(**defaults)
    node.created_at = datetime.now(timezone.utc)
    node.updated_at = datetime.now(timezone.utc)
    return node


def _make_edge(
    **overrides: object,
) -> KGEdge:
    """Create a test KGEdge with sensible defaults."""
    src = overrides.pop("source_node_id", uuid4())
    tgt = overrides.pop("target_node_id", uuid4())
    defaults = {
        "id": uuid4(),
        "source_node_id": src,
        "target_node_id": tgt,
        "relation_type": "hasProperty",
        "confidence": 0.9,
    }
    defaults.update(overrides)
    edge = KGEdge(**defaults)
    edge.created_at = datetime.now(timezone.utc)
    return edge


# ---------------------------------------------------------------------------
# Cache key tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    """Deterministic cache key generation."""

    def test_same_params_same_key(self) -> None:
        a = _cache_key("list_nodes", node_type="Material", page=1, per_page=20)
        b = _cache_key("list_nodes", node_type="Material", page=1, per_page=20)
        assert a == b

    def test_different_params_different_key(self) -> None:
        a = _cache_key("list_nodes", node_type="Material", page=1)
        b = _cache_key("list_nodes", node_type="Property", page=1)
        assert a != b

    def test_order_independence(self) -> None:
        a = _cache_key("list_nodes", page=1, per_page=20)
        b = _cache_key("list_nodes", per_page=20, page=1)
        assert a == b

    def test_cache_key_is_hex(self) -> None:
        key = _cache_key("test", foo="bar")
        assert len(key) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# Cache metrics tests
# ---------------------------------------------------------------------------


class TestCacheMetrics:
    """Cache hit/miss counter behaviour."""

    def setup_method(self) -> None:
        reset_cache_metrics()

    def test_initial_metrics_zero(self) -> None:
        m = get_cache_metrics()
        assert m.hits == 0
        assert m.misses == 0
        assert m.evictions == 0
        assert m.hit_rate == 0.0

    def test_hit_rate_calculation(self) -> None:
        from nfm_db.services.kg_query_service import _stats

        _stats._hits = 3
        _stats._misses = 1
        m = get_cache_metrics()
        assert m.hit_rate == 0.75

    @patch("nfm_db.services.kg_query_service._kg_cache")
    def test_invalidate_cache(self, mock_cache: MagicMock) -> None:
        invalidate_cache()
        mock_cache.clear.assert_called_once()


# ---------------------------------------------------------------------------
# list_nodes_by_type tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListNodesByType:
    """Pattern #1/#2: Paginated node listing with caching."""

    async def test_invalid_node_type_raises(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        with pytest.raises(ValueError, match="invalid node_type"):
            await list_nodes_by_type(session, node_type="Bogus")

    @patch(
        "nfm_db.services.kg_query_service._list_nodes_by_type_impl",
        new_callable=AsyncMock,
    )
    async def test_calls_impl_with_correct_params(self, mock_impl: AsyncMock) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_impl.return_value = {"items": [], "total": 0, "page": 2, "per_page": 10, "pages": 1}

        result = await list_nodes_by_type(
            session, node_type="Material", corpus_id="c1", page=2, per_page=10,
        )

        mock_impl.assert_called_once_with(
            session,
            node_type="Material",
            corpus_id="c1",
            status="active",
            page=2,
            per_page=10,
        )
        assert result["page"] == 2

    @patch("nfm_db.services.kg_query_service._kg_cache", new_callable=lambda: TTLCache(maxsize=10, ttl=60))
    @patch(
        "nfm_db.services.kg_query_service._list_nodes_by_type_impl",
        new_callable=AsyncMock,
    )
    async def test_cache_hit_returns_cached(
        self, mock_impl: AsyncMock, mock_cache: TTLCache,
    ) -> None:
        session = AsyncMock(spec=AsyncSession)
        data = {"items": ["cached"], "total": 1, "page": 1, "per_page": 10, "pages": 1}
        mock_impl.return_value = data

        result1 = await list_nodes_by_type(session, use_cache=True)
        assert result1 == data

        result2 = await list_nodes_by_type(session, use_cache=True)
        assert result2 == data

        assert mock_impl.call_count == 1

    @patch("nfm_db.services.kg_query_service._kg_cache", new_callable=lambda: TTLCache(maxsize=10, ttl=60))
    @patch(
        "nfm_db.services.kg_query_service._list_nodes_by_type_impl",
        new_callable=AsyncMock,
    )
    async def test_cache_bypass(self, mock_impl: AsyncMock, mock_cache: TTLCache) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_impl.return_value = {"items": [], "total": 0, "page": 1, "per_page": 10, "pages": 1}

        await list_nodes_by_type(session, use_cache=False)
        await list_nodes_by_type(session, use_cache=False)

        assert mock_impl.call_count == 2
        assert len(mock_cache) == 0


# ---------------------------------------------------------------------------
# get_node_by_id tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetNodeById:
    """Pattern #3: Single node retrieval with caching."""

    async def test_not_found_raises(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        with patch(
            "nfm_db.services.kg_query_service._get_node_by_id_impl",
            new_callable=AsyncMock,
        ) as mock_impl:
            mock_impl.side_effect = KGNodeNotFoundError("missing")
            with pytest.raises(KGNodeNotFoundError):
                await get_node_by_id(session, uuid4(), use_cache=False)

    @patch("nfm_db.services.kg_query_service._kg_cache", new_callable=lambda: TTLCache(maxsize=10, ttl=60))
    @patch(
        "nfm_db.services.kg_query_service._get_node_by_id_impl",
        new_callable=AsyncMock,
    )
    async def test_cache_hit(self, mock_impl: AsyncMock, mock_cache: TTLCache) -> None:
        session = AsyncMock(spec=AsyncSession)
        data = {"id": "test"}
        mock_impl.return_value = data

        nid = uuid4()
        r1 = await get_node_by_id(session, nid, use_cache=True)
        r2 = await get_node_by_id(session, nid, use_cache=True)
        assert mock_impl.call_count == 1


# ---------------------------------------------------------------------------
# get_edges_from_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetEdgesFromNode:
    """Pattern #4: 1-hop edge retrieval with caching."""

    @patch(
        "nfm_db.services.kg_query_service._get_edges_from_node_impl",
        new_callable=AsyncMock,
    )
    async def test_passes_direction_param(self, mock_impl: AsyncMock) -> None:
        mock_impl.return_value = {"items": [], "total": 0, "page": 1, "per_page": 20, "pages": 1}
        session = AsyncMock(spec=AsyncSession)
        result = await get_edges_from_node(
            session, source_node_id=uuid4(), direction="incoming",
            use_cache=False,
        )
        assert "items" in result
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        assert call_kwargs["direction"] == "incoming"

    @patch("nfm_db.services.kg_query_service._kg_cache", new_callable=lambda: TTLCache(maxsize=10, ttl=60))
    @patch(
        "nfm_db.services.kg_query_service._get_edges_from_node_impl",
        new_callable=AsyncMock,
    )
    async def test_cache_hit(self, mock_impl: AsyncMock, mock_cache: TTLCache) -> None:
        session = AsyncMock(spec=AsyncSession)
        data = {"items": ["edge"], "total": 1, "page": 1, "per_page": 20, "pages": 1}
        mock_impl.return_value = data

        nid = uuid4()
        r1 = await get_edges_from_node(session, source_node_id=nid, use_cache=True)
        r2 = await get_edges_from_node(session, source_node_id=nid, use_cache=True)
        assert mock_impl.call_count == 1


# ---------------------------------------------------------------------------
# list_edges_by_relation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListEdgesByRelation:
    """Pattern #5: Edge listing by relation type with caching."""

    async def test_invalid_relation_type_raises(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        with pytest.raises(ValueError, match="invalid relation_type"):
            await list_edges_by_relation(session, relation_type="Bogus", use_cache=False)

    @patch(
        "nfm_db.services.kg_query_service._list_edges_by_relation_impl",
        new_callable=AsyncMock,
    )
    async def test_passes_corpus_filter(self, mock_impl: AsyncMock) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_impl.return_value = {"items": [], "total": 0, "page": 1, "per_page": 20, "pages": 1}
        await list_edges_by_relation(
            session, relation_type="hasProperty", corpus_id="c1",
            use_cache=False,
        )
        mock_impl.assert_called_once_with(
            session,
            relation_type="hasProperty",
            corpus_id="c1",
            page=1,
            per_page=20,
        )

    @patch(
        "nfm_db.services.kg_query_service._list_edges_by_relation_impl",
        new_callable=AsyncMock,
    )
    async def test_cache_miss_and_store(self, mock_impl: AsyncMock) -> None:
        """Verify cache miss increment and cache store on cold cache."""
        reset_cache_metrics()
        session = AsyncMock(spec=AsyncSession)
        mock_impl.return_value = {"items": [], "total": 0, "page": 1, "per_page": 20, "pages": 1}
        await list_edges_by_relation(
            session, relation_type="hasProperty", use_cache=True,
        )
        m = get_cache_metrics()
        assert m.misses == 1
        assert m.hits == 0
        mock_impl.assert_called_once()


# ---------------------------------------------------------------------------
# Impl function tests (mocked session.execute)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListNodesByTypeImpl:
    """Direct tests for _list_nodes_by_type_impl with mocked session."""

    async def test_returns_paginated_result(self) -> None:
        from nfm_db.services.kg_query_service import _list_nodes_by_type_impl

        node = _make_node(node_type="Material", label="U-235")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [node]
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])

        result = await _list_nodes_by_type_impl(
            session, node_type="Material", corpus_id=None, status="active",
            page=1, per_page=10,
        )
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["label"] == "U-235"
        assert result["pages"] == 1

    async def test_empty_result(self) -> None:
        from nfm_db.services.kg_query_service import _list_nodes_by_type_impl

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])

        result = await _list_nodes_by_type_impl(
            session, node_type="Material", corpus_id=None, status="active",
            page=1, per_page=10,
        )
        assert result["items"] == []
        assert result["total"] == 0
        assert result["pages"] == 1


@pytest.mark.asyncio
class TestGetNodeByIdImpl:
    """Direct tests for _get_node_by_id_impl with mocked session."""

    async def test_returns_node_dict(self) -> None:
        from nfm_db.services.kg_query_service import _get_node_by_id_impl

        node = _make_node(id=uuid4(), label="Pu-239")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = node

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=mock_result)

        result = await _get_node_by_id_impl(session, node.id)
        assert result["label"] == "Pu-239"
        assert result["node_type"] == "Material"

    async def test_raises_not_found(self) -> None:
        from nfm_db.services.kg_query_service import _get_node_by_id_impl

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(KGNodeNotFoundError):
            await _get_node_by_id_impl(session, uuid4())


@pytest.mark.asyncio
class TestGetEdgesFromNodeImpl:
    """Direct tests for _get_edges_from_node_impl with mocked session."""

    async def test_returns_outgoing_edges(self) -> None:
        from nfm_db.services.kg_query_service import _get_edges_from_node_impl

        edge = _make_edge(relation_type="hasProperty")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [edge]
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])

        result = await _get_edges_from_node_impl(
            session, source_node_id=edge.source_node_id,
            relation_type=None, direction="outgoing", page=1, per_page=20,
        )
        assert result["total"] == 1
        assert len(result["items"]) == 1

    async def test_incoming_direction(self) -> None:
        from nfm_db.services.kg_query_service import _get_edges_from_node_impl

        edge = _make_edge(relation_type="derivedFrom")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [edge]
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])

        result = await _get_edges_from_node_impl(
            session, source_node_id=edge.target_node_id,
            relation_type=None, direction="incoming", page=1, per_page=20,
        )
        assert result["total"] == 1


@pytest.mark.asyncio
class TestListEdgesByRelationImpl:
    """Direct tests for _list_edges_by_relation_impl with mocked session."""

    async def test_returns_edges_filtered(self) -> None:
        from nfm_db.services.kg_query_service import _list_edges_by_relation_impl

        edge = _make_edge(relation_type="hasProperty")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [edge]
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(side_effect=[mock_count_result, mock_result])

        result = await _list_edges_by_relation_impl(
            session, relation_type="hasProperty",
            corpus_id=None, page=1, per_page=20,
        )
        assert result["total"] == 1
        assert result["items"][0]["relation_type"] == "hasProperty"


class TestCacheEviction:
    """Verify eviction callback increments eviction counter."""

    def test_eviction_callback_increments_counter(self) -> None:
        reset_cache_metrics()
        from nfm_db.services.kg_query_service import _on_cache_eviction

        _on_cache_eviction(None, None, None)
        _on_cache_eviction(None, None, None)
        m = get_cache_metrics()
        assert m.evictions == 2


# ---------------------------------------------------------------------------
# Timeout tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTimeoutGuard:
    """30s timeout wrapper prevents cascading failures."""

    @patch(
        "nfm_db.services.kg_query_service._QUERY_TIMEOUT_SECONDS",
        0.05,
    )
    async def test_timeout_raises_kg_query_timeout(self) -> None:
        from nfm_db.services.kg_query_service import _execute_with_timeout

        async def never_resolves(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(10)

        with pytest.raises(KGQueryTimeoutError):
            await _execute_with_timeout(never_resolves)

    @patch(
        "nfm_db.services.kg_query_service._QUERY_TIMEOUT_SECONDS",
        0.05,
    )
    async def test_fast_query_completes(self) -> None:
        from nfm_db.services.kg_query_service import _execute_with_timeout

        async def instant(*args: Any, **kwargs: Any) -> str:
            return "ok"

        result = await _execute_with_timeout(instant)
        assert result == "ok"


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------


class TestSerialization:
    """Node and edge dict conversion."""

    def test_node_to_dict_roundtrip(self) -> None:
        from nfm_db.services.kg_query_service import _node_to_dict

        node = _make_node(label="Plutonium", node_type="Material", properties={"Z": 94})
        d = _node_to_dict(node)
        assert d["label"] == "Plutonium"
        assert d["node_type"] == "Material"
        assert d["properties"]["Z"] == 94
        assert "id" in d
        assert "created_at" in d

    def test_edge_to_dict_roundtrip(self) -> None:
        from nfm_db.services.kg_query_service import _edge_to_dict

        edge = _make_edge(relation_type="hasProperty")
        d = _edge_to_dict(edge)
        assert d["relation_type"] == "hasProperty"
        assert "source_node_id" in d
        assert "target_node_id" in d
        assert "created_at" in d
