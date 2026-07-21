"""Tests for nfm_db.schemas.kg_query (NFM-858 Pydantic models)."""

from __future__ import annotations

import uuid

import pytest

from nfm_db.schemas.kg_query import (
    KGEdgeResponse,
    KGNodeResponse,
    PathEdge,
    PathQueryRequest,
    PathQueryResponse,
    PathResult,
    PropertyQueryRequest,
    PropertyQueryResponse,
    RelationQueryRequest,
    RelationQueryResponse,
    _split_csv_relations,
)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TestSplitCsvRelations:
    def test_none_returns_none(self) -> None:
        assert _split_csv_relations(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _split_csv_relations("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _split_csv_relations("   ,  , ") is None

    def test_single_value(self) -> None:
        assert _split_csv_relations("hasProperty") == ["hasProperty"]

    def test_multiple_values(self) -> None:
        result = _split_csv_relations("hasProperty,measuredIn,relatedTo")
        assert result == ["hasProperty", "measuredIn", "relatedTo"]

    def test_strips_whitespace(self) -> None:
        result = _split_csv_relations(" hasProperty , measuredIn ")
        assert result == ["hasProperty", "measuredIn"]

    def test_list_passthrough(self) -> None:
        assert _split_csv_relations(["a", "b"]) == ["a", "b"]

    def test_list_of_ints_converted(self) -> None:
        assert _split_csv_relations([1, 2]) == ["1", "2"]

    def test_non_string_non_list_converted(self) -> None:
        assert _split_csv_relations(42) == ["42"]


class TestKGNodeResponse:
    def test_defaults(self) -> None:
        node = KGNodeResponse(id=_uuid(), node_type="Material", label="UO2")
        assert node.aliases == []
        assert node.properties == {}
        assert node.confidence == 1.0

    def test_with_all_fields(self) -> None:
        node = KGNodeResponse(
            id=_uuid(),
            node_type="Property",
            label="melting_point",
            aliases=["mp", "Tm"],
            properties={"unit": "K", "value": 3138},
            confidence=0.95,
        )
        assert len(node.aliases) == 2
        assert node.confidence == 0.95


class TestKGEdgeResponse:
    def test_minimal(self) -> None:
        edge = KGEdgeResponse(
            id=_uuid(),
            source_node_id=_uuid(),
            target_node_id=_uuid(),
            relation_type="hasProperty",
        )
        assert edge.confidence == 1.0
        assert edge.properties == {}


class TestPropertyQueryRequest:
    def test_defaults(self) -> None:
        req = PropertyQueryRequest()
        assert req.node_type is None
        assert req.label is None
        assert req.fuzzy is False
        assert req.limit == 20
        assert req.offset == 0

    def test_custom_values(self) -> None:
        req = PropertyQueryRequest(
            node_type="Material",
            label="UO2",
            fuzzy=True,
            limit=50,
            offset=10,
        )
        assert req.node_type == "Material"
        assert req.fuzzy is True
        assert req.limit == 50

    def test_limit_bounds(self) -> None:
        with pytest.raises(Exception):
            PropertyQueryRequest(limit=0)
        with pytest.raises(Exception):
            PropertyQueryRequest(limit=101)

    def test_offset_negative_rejected(self) -> None:
        with pytest.raises(Exception):
            PropertyQueryRequest(offset=-1)


class TestPropertyQueryResponse:
    def test_empty(self) -> None:
        resp = PropertyQueryResponse()
        assert resp.nodes == []
        assert resp.total == 0


class TestRelationQueryRequest:
    def test_defaults(self) -> None:
        req = RelationQueryRequest()
        assert req.source_node_id is None
        assert req.direction == "outgoing"
        assert req.limit == 20

    def test_all_directions(self) -> None:
        for d in ("outgoing", "incoming", "both"):
            req = RelationQueryRequest(direction=d)
            assert req.direction == d

    def test_invalid_direction_rejected(self) -> None:
        with pytest.raises(Exception):
            RelationQueryRequest(direction="sideways")


class TestPathQueryRequest:
    def test_minimal(self) -> None:
        req = PathQueryRequest(source_node_id=_uuid(), target_node_id=_uuid())
        assert req.max_depth == 3
        assert req.limit == 10
        assert req.relation_types is None

    def test_relation_types_as_string(self) -> None:
        req = PathQueryRequest(
            source_node_id=_uuid(),
            target_node_id=_uuid(),
            relation_types="hasProperty,measuredIn",
        )
        assert req.relation_types == ["hasProperty", "measuredIn"]

    def test_relation_types_as_list(self) -> None:
        req = PathQueryRequest(
            source_node_id=_uuid(),
            target_node_id=_uuid(),
            relation_types=["hasProperty"],
        )
        assert req.relation_types == ["hasProperty"]

    def test_max_depth_exceeds_max_rejected(self) -> None:
        with pytest.raises(Exception):
            PathQueryRequest(source_node_id=_uuid(), target_node_id=_uuid(), max_depth=5)

    def test_max_depth_minimum(self) -> None:
        with pytest.raises(Exception):
            PathQueryRequest(source_node_id=_uuid(), target_node_id=_uuid(), max_depth=0)


class TestPathResult:
    def test_construction(self) -> None:
        nodes = [KGNodeResponse(id=_uuid(), node_type="M", label="A")]
        edges = [PathEdge(source_node_id=_uuid(), target_node_id=_uuid(), relation_type="hasProperty")]
        pr = PathResult(nodes=nodes, edges=edges, length=1)
        assert pr.length == 1
        assert len(pr.nodes) == 1


class TestPathQueryResponse:
    def test_empty(self) -> None:
        resp = PathQueryResponse()
        assert resp.paths == []
        assert resp.total == 0