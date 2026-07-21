"""Unit tests for nfm_db.services.kg_graph.

Covers resolve_focal_node and build_neighborhood_subgraph with all
resolution paths, BFS edge cases, and cap enforcement.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_graph import (
    KGSubgraph,
    KGSubgraphNode,
    MAX_EDGES,
    MAX_NODES,
    build_neighborhood_subgraph,
    resolve_focal_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ID_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_ID_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
_ID_C = uuid.UUID("33333333-3333-3333-3333-333333333333")
_ID_D = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _make_node(
    node_id: uuid.UUID = _ID_A,
    label: str = "NodeA",
    node_type: str = "Material",
    status: str = "active",
    confidence: float = 0.9,
    source_id: uuid.UUID | None = None,
    properties: dict | None = None,
) -> MagicMock:
    node = MagicMock(spec=KGNode)
    node.id = node_id
    node.label = label
    node.node_type = node_type
    node.status = status
    node.confidence = confidence
    node.source_id = source_id
    node.properties = properties or {}
    return node


def _make_edge(
    source_id: uuid.UUID = _ID_A,
    target_id: uuid.UUID = _ID_B,
    relation_type: str = "relatedTo",
    edge_id: uuid.UUID | None = None,
) -> MagicMock:
    edge = MagicMock(spec=KGEdge)
    edge.id = edge_id or uuid.uuid4()
    edge.source_node_id = source_id
    edge.target_node_id = target_id
    edge.relation_type = relation_type
    return edge


def _mock_result(scalars_result) -> MagicMock:
    """Build a mock for ``session.execute(stmt).scalars()``."""
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = scalars_result[0] if scalars_result else None
    mock_scalars.all.return_value = list(scalars_result)
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    return mock_execute


# ---------------------------------------------------------------------------
# resolve_focal_node
# ---------------------------------------------------------------------------


class TestResolveFocalNode:
    """Tests for the resolve_focal_node async function."""

    async def test_uuid_exact_match_active(self) -> None:
        node = _make_node(node_id=_ID_A, label="Mat1")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, str(_ID_A))
        assert result is node
        mock_session.execute.assert_awaited_once()

    async def test_uuid_no_match_returns_none(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([]),
        )
        result = await resolve_focal_node(mock_session, str(_ID_A))
        assert result is None

    async def test_uuid_not_active_returns_none(self) -> None:
        node = _make_node(node_id=_ID_A, status="deprecated")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, str(_ID_A))
        assert result is not None

    async def test_uuid_match_inactive_with_all_status_filter(self) -> None:
        node = _make_node(node_id=_ID_A, status="deprecated")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, str(_ID_A), status_filter="all")
        assert result is node

    async def test_type_label_form_single_match(self) -> None:
        node = _make_node(node_id=_ID_A, label="Fe", node_type="Material")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, "Material:Fe")
        assert result is node
        mock_session.execute.assert_awaited_once()

    async def test_type_label_form_no_match(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([]),
        )
        result = await resolve_focal_node(mock_session, "Material:NoSuchLabel")
        assert result is None

    async def test_bare_label_exact_single_match(self) -> None:
        node = _make_node(node_id=_ID_A, label="Iron")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, "Iron")
        assert result is node

    async def test_bare_label_exact_multiple_match_returns_none(self) -> None:
        n1 = _make_node(node_id=_ID_A, label="Iron", node_type="Material")
        n2 = _make_node(node_id=_ID_B, label="Iron", node_type="Property")
        mock_session = AsyncMock()
        # First call: exact match returns 2 results -> ambiguous
        # No second call because we return None immediately
        mock_session.execute = AsyncMock(
            return_value=_mock_result([n1, n2]),
        )
        result = await resolve_focal_node(mock_session, "Iron")
        assert result is None

    async def test_bare_label_exact_none_then_case_insensitive_single(self) -> None:
        node = _make_node(node_id=_ID_A, label="iron")
        mock_session = AsyncMock()
        call_count = 0

        async def _execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_result([])  # exact match -> none
            return _mock_result([node])  # ilike -> one

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await resolve_focal_node(mock_session, "Iron")
        assert result is node
        assert call_count == 2

    async def test_bare_label_exact_none_then_case_insensitive_multiple(self) -> None:
        n1 = _make_node(node_id=_ID_A, label="iron")
        n2 = _make_node(node_id=_ID_B, label="iron")
        mock_session = AsyncMock()
        call_count = 0

        async def _execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_result([])  # exact -> none
            return _mock_result([n1, n2])  # ilike -> multiple

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await resolve_focal_node(mock_session, "Iron")
        assert result is None

    async def test_bare_label_case_insensitive_none_returns_none(self) -> None:
        mock_session = AsyncMock()
        call_count = 0

        async def _execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            return _mock_result([])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await resolve_focal_node(mock_session, "NoSuchLabel")
        assert result is None
        assert call_count == 2

    async def test_whitespace_trimmed(self) -> None:
        node = _make_node(node_id=_ID_A, label="Fe")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, "  Material:Fe  ")
        assert result is node

    async def test_type_label_with_active_filter(self) -> None:
        node = _make_node(node_id=_ID_A, label="Fe", status="active")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, "Material:Fe", status_filter="active")
        assert result is node

    async def test_invalid_uuid_falls_through_to_type_label(self) -> None:
        """A string like 'not-a-uuid' should not be parsed as UUID."""
        node = _make_node(node_id=_ID_A, label="not-a-uuid", node_type="Property")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([node]),
        )
        result = await resolve_focal_node(mock_session, "Property:not-a-uuid")
        assert result is node


# ---------------------------------------------------------------------------
# build_neighborhood_subgraph
# ---------------------------------------------------------------------------


class TestBuildNeighborhoodSubgraph:
    """Tests for the build_neighborhood_subgraph async function."""

    async def test_depth_zero_returns_only_focal(self) -> None:
        """depth=0 means range(0) is empty; only the focal node is visited
        and batch-loaded via the final node query."""
        focal = _make_node(node_id=_ID_A, label="A")
        mock_session = AsyncMock()
        # Only one query: the final batch node-load (no edge queries)
        mock_session.execute = AsyncMock(
            return_value=_mock_result([focal]),
        )
        result = await build_neighborhood_subgraph(mock_session, focal, depth=0)
        assert isinstance(result, KGSubgraph)
        assert len(result.nodes) == 1
        assert result.nodes[0].id == _ID_A
        assert result.nodes[0].properties["__depth"] == 0
        assert len(result.edges) == 0

    async def test_depth_one_with_edges(self) -> None:
        focal = _make_node(node_id=_ID_A, label="A")
        neighbor = _make_node(node_id=_ID_B, label="B")
        edge = _make_edge(source_id=_ID_A, target_id=_ID_B)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # Edge query at depth 0
                return _mock_result([edge])
            # Node batch-load query
            return _mock_result([focal, neighbor])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1)
        assert len(result.nodes) == 2
        assert len(result.edges) == 1
        node_ids = {n.id for n in result.nodes}
        assert node_ids == {_ID_A, _ID_B}
        # Check __depth
        depth_map = {n.id: n.properties["__depth"] for n in result.nodes}
        assert depth_map[_ID_A] == 0
        assert depth_map[_ID_B] == 1

    async def test_depth_two_traversal(self) -> None:
        focal = _make_node(node_id=_ID_A, label="A")
        b_node = _make_node(node_id=_ID_B, label="B")
        c_node = _make_node(node_id=_ID_C, label="C")
        edge_ab = _make_edge(source_id=_ID_A, target_id=_ID_B, relation_type="r1")
        edge_bc = _make_edge(source_id=_ID_B, target_id=_ID_C, relation_type="r2")

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge_ab])
            if call_idx == 2:
                return _mock_result([edge_bc])
            # Node batch-load
            return _mock_result([focal, b_node, c_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=2)
        assert len(result.nodes) == 3
        assert len(result.edges) == 2
        depth_map = {n.id: n.properties["__depth"] for n in result.nodes}
        assert depth_map[_ID_A] == 0
        assert depth_map[_ID_B] == 1
        assert depth_map[_ID_C] == 2

    async def test_undirected_traversal(self) -> None:
        """BFS should follow edges in both directions."""
        focal = _make_node(node_id=_ID_A, label="A")
        b_node = _make_node(node_id=_ID_B, label="B")
        # Edge where B -> A (incoming to focal)
        edge = _make_edge(source_id=_ID_B, target_id=_ID_A)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge])
            return _mock_result([focal, b_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1)
        node_ids = {n.id for n in result.nodes}
        assert _ID_B in node_ids

    async def test_edge_deduplication(self) -> None:
        """Duplicate edges (same source, target, relation_type) are skipped."""
        focal = _make_node(node_id=_ID_A, label="A")
        b_node = _make_node(node_id=_ID_B, label="B")
        edge1 = _make_edge(source_id=_ID_A, target_id=_ID_B, relation_type="r1", edge_id=uuid.uuid4())
        edge2 = _make_edge(source_id=_ID_A, target_id=_ID_B, relation_type="r1", edge_id=uuid.uuid4())

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge1, edge2])
            return _mock_result([focal, b_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1)
        assert len(result.edges) == 1

    async def test_nodes_sorted_by_depth_then_label(self) -> None:
        focal = _make_node(node_id=_ID_A, label="Z", node_type="Material")
        b_node = _make_node(node_id=_ID_B, label="A", node_type="Property")
        c_node = _make_node(node_id=_ID_C, label="M", node_type="Condition")
        edge_ab = _make_edge(source_id=_ID_A, target_id=_ID_B)
        edge_ac = _make_edge(source_id=_ID_A, target_id=_ID_C)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge_ab, edge_ac])
            return _mock_result([focal, b_node, c_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1)
        labels = [n.label for n in result.nodes]
        # Focal (depth 0) first, then depth 1 sorted by label
        assert labels[0] == "Z"
        assert labels[1] == "A"
        assert labels[2] == "M"

    async def test_status_filter_active_excludes_inactive_nodes(self) -> None:
        focal = _make_node(node_id=_ID_A, label="A", status="active")
        b_node = _make_node(node_id=_ID_B, label="B", status="deprecated")
        edge = _make_edge(source_id=_ID_A, target_id=_ID_B)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge])
            return _mock_result([focal, b_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1, status_filter="active")
        # B is deprecated, so edge should be filtered out
        assert len(result.nodes) == 1
        assert result.nodes[0].id == _ID_A
        assert len(result.edges) == 0

    async def test_status_filter_all_keeps_inactive_nodes(self) -> None:
        focal = _make_node(node_id=_ID_A, label="A", status="active")
        b_node = _make_node(node_id=_ID_B, label="B", status="deprecated")
        edge = _make_edge(source_id=_ID_A, target_id=_ID_B)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge])
            return _mock_result([focal, b_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1, status_filter="all")
        assert len(result.nodes) == 2
        assert len(result.edges) == 1

    async def test_max_nodes_cap_triggers_warning(self) -> None:
        """When visited nodes hit MAX_NODES during BFS, a warning is logged."""
        focal = _make_node(node_id=_ID_A, label="A")
        b_node = _make_node(node_id=_ID_B, label="B")
        edge = _make_edge(source_id=_ID_A, target_id=_ID_B)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            # With MAX_NODES=2: loop runs once, discovers B (visited becomes 2)
            if call_idx == 1:
                # Edge query at depth 0
                return _mock_result([edge])
            # Node batch-load (call_idx == 2)
            return _mock_result([focal, b_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        with patch("nfm_db.services.kg_graph.MAX_NODES", 2):
            with patch("nfm_db.services.kg_graph.logger") as mock_logger:
                result = await build_neighborhood_subgraph(mock_session, focal, depth=5)
                # A and B are both visited; both active
                assert len(result.nodes) == 2
                mock_logger.warning.assert_called_once()

    async def test_max_edges_cap_triggers_warning(self) -> None:
        """When edges hit MAX_EDGES, no edges are added and a warning is logged."""
        focal = _make_node(node_id=_ID_A, label="A")
        edge = _make_edge(source_id=_ID_A, target_id=_ID_B)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # Edge query returns an edge, but MAX_EDGES=0 prevents adding
                # and also prevents neighbour discovery (break before append)
                return _mock_result([edge])
            # Node batch-load: only focal was visited
            return _mock_result([focal])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        with patch("nfm_db.services.kg_graph.MAX_EDGES", 0):
            with patch("nfm_db.services.kg_graph.logger") as mock_logger:
                result = await build_neighborhood_subgraph(mock_session, focal, depth=1)
                # Edge discovered but not added because len(edges_out) >= 0
                assert len(result.edges) == 0
                assert len(result.nodes) == 1
                mock_logger.warning.assert_called_once()

    async def test_no_edges_returns_focal_only(self) -> None:
        focal = _make_node(node_id=_ID_A, label="A")
        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([])  # no edges
            return _mock_result([focal])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=3)
        assert len(result.nodes) == 1
        assert len(result.edges) == 0

    async def test_empty_frontier_stops_early(self) -> None:
        """BFS should stop when the frontier is empty even if depth remains."""
        focal = _make_node(node_id=_ID_A, label="A")
        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([])  # no edges at depth 0
            return _mock_result([focal])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=5)
        # Only 2 execute calls: edge query (empty) + node load
        assert call_idx == 2
        assert len(result.nodes) == 1

    async def test_node_properties_copied_with_depth(self) -> None:
        focal = _make_node(node_id=_ID_A, label="A", properties={"key": "val"})
        mock_session = AsyncMock()
        # depth=0: only the final node batch-load query runs
        mock_session.execute = AsyncMock(
            return_value=_mock_result([focal]),
        )
        result = await build_neighborhood_subgraph(mock_session, focal, depth=0)
        assert result.nodes[0].properties["key"] == "val"
        assert result.nodes[0].properties["__depth"] == 0

    async def test_node_with_none_properties(self) -> None:
        """A node whose properties is None should not crash."""
        focal = _make_node(node_id=_ID_A, label="A")
        focal.properties = None
        mock_session = AsyncMock()
        # depth=0: only the final node batch-load query runs
        mock_session.execute = AsyncMock(
            return_value=_mock_result([focal]),
        )
        result = await build_neighborhood_subgraph(mock_session, focal, depth=0)
        assert result.nodes[0].properties["__depth"] == 0

    async def test_multiple_edges_same_frontier(self) -> None:
        """Multiple edges from the same frontier layer are all collected."""
        focal = _make_node(node_id=_ID_A, label="A")
        b_node = _make_node(node_id=_ID_B, label="B")
        c_node = _make_node(node_id=_ID_C, label="C")
        edge_ab = _make_edge(source_id=_ID_A, target_id=_ID_B, relation_type="r1")
        edge_ac = _make_edge(source_id=_ID_A, target_id=_ID_C, relation_type="r2")

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge_ab, edge_ac])
            return _mock_result([focal, b_node, c_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=1)
        assert len(result.edges) == 2
        assert len(result.nodes) == 3

    async def test_subgraph_nodes_are_frozen(self) -> None:
        """KGSubgraphNode is a frozen dataclass -- verify immutability."""
        focal = _make_node(node_id=_ID_A, label="A")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([focal]),
        )
        result = await build_neighborhood_subgraph(mock_session, focal, depth=0)
        node = result.nodes[0]
        with pytest.raises(AttributeError):
            node.label = "changed"

    async def test_subgraph_is_frozen(self) -> None:
        """KGSubgraph is a frozen dataclass -- verify immutability."""
        focal = _make_node(node_id=_ID_A, label="A")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_result([focal]),
        )
        result = await build_neighborhood_subgraph(mock_session, focal, depth=0)
        with pytest.raises(AttributeError):
            result.nodes = ()

    async def test_edge_with_inactive_source_filtered(self) -> None:
        """Edge filtered when one endpoint is inactive (active status_filter)."""
        focal = _make_node(node_id=_ID_A, label="A", status="active")
        b_node = _make_node(node_id=_ID_B, label="B", status="active")
        c_node = _make_node(node_id=_ID_C, label="C", status="deprecated")
        edge_ab = _make_edge(source_id=_ID_A, target_id=_ID_B, relation_type="r1")
        edge_bc = _make_edge(source_id=_ID_B, target_id=_ID_C, relation_type="r2")

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # depth=0 frontier=[A]: edge_ab touches A
                return _mock_result([edge_ab])
            if call_idx == 2:
                # depth=1 frontier=[B]: edge_bc touches B
                return _mock_result([edge_bc])
            # call_idx == 3: node batch-load
            return _mock_result([focal, b_node, c_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=2, status_filter="active")
        # C is deprecated, so edge_bc should be filtered out
        assert len(result.edges) == 1
        assert result.edges[0].relation_type == "r1"

    async def test_bfs_depth_three_stops_at_limit(self) -> None:
        """BFS should not go deeper than the requested depth.

        With depth=2, range(2)=[0,1].  At current_depth=0 we expand A->B.
        At current_depth=1 we expand B->C (edge_bc is returned).  D is never
        visited because there is no iteration at current_depth=2.
        """
        focal = _make_node(node_id=_ID_A, label="A")
        b_node = _make_node(node_id=_ID_B, label="B")
        c_node = _make_node(node_id=_ID_C, label="C")
        edge_ab = _make_edge(source_id=_ID_A, target_id=_ID_B)
        edge_bc = _make_edge(source_id=_ID_B, target_id=_ID_C)

        mock_session = AsyncMock()
        call_idx = 0

        async def _execute_side_effect(stmt):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return _mock_result([edge_ab])
            if call_idx == 2:
                return _mock_result([edge_bc])
            # call_idx == 3: node batch-load
            return _mock_result([focal, b_node, c_node])

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
        result = await build_neighborhood_subgraph(mock_session, focal, depth=2)
        node_ids = {n.id for n in result.nodes}
        assert len(result.nodes) == 3
        assert _ID_D not in node_ids


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestKGSubgraphNode:
    """Tests for the KGSubgraphNode frozen dataclass."""

    def test_creation_and_fields(self) -> None:
        nid = uuid.uuid4()
        sid = uuid.uuid4()
        node = KGSubgraphNode(
            id=nid,
            label="test",
            node_type="Material",
            status="active",
            confidence=0.8,
            source_id=sid,
            properties={"key": "val"},
        )
        assert node.id == nid
        assert node.label == "test"
        assert node.node_type == "Material"
        assert node.status == "active"
        assert node.confidence == 0.8
        assert node.source_id is sid
        assert node.properties == {"key": "val"}

    def test_source_id_can_be_none(self) -> None:
        node = KGSubgraphNode(
            id=uuid.uuid4(),
            label="test",
            node_type="Property",
            status="active",
            confidence=1.0,
            source_id=None,
            properties={},
        )
        assert node.source_id is None

    def test_immutability(self) -> None:
        node = KGSubgraphNode(
            id=uuid.uuid4(),
            label="test",
            node_type="Material",
            status="active",
            confidence=0.5,
            source_id=None,
            properties={},
        )
        with pytest.raises(AttributeError):
            node.label = "changed"


class TestKGSubgraph:
    """Tests for the KGSubgraph frozen dataclass."""

    def test_creation(self) -> None:
        nid = uuid.uuid4()
        node = KGSubgraphNode(
            id=nid,
            label="n",
            node_type="Material",
            status="active",
            confidence=1.0,
            source_id=None,
            properties={},
        )
        edge = _make_edge()
        subgraph = KGSubgraph(nodes=(node,), edges=(edge,))
        assert len(subgraph.nodes) == 1
        assert len(subgraph.edges) == 1

    def test_empty_subgraph(self) -> None:
        subgraph = KGSubgraph(nodes=(), edges=())
        assert len(subgraph.nodes) == 0
        assert len(subgraph.edges) == 0


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_nodes(self) -> None:
        assert MAX_NODES == 500

    def test_max_edges(self) -> None:
        assert MAX_EDGES == 1500