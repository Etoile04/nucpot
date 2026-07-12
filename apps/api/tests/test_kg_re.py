"""Tests for KG relation extraction and GraphBuilder service (NFM-984).

Focuses on the _fire_lightrag_ingest wiring added in NFM-1222 (NFM-1247).
Verifies that GraphBuilder correctly triggers the LightRAG auto-ingest
hook when new KG nodes and/or edges are created.

All external services are mocked:
  - PostgreSQL → mock AsyncSession
  - AGE graph sync → mock ontology_sync
  - LightRAG sidecar → mock kg_lightrag_sync
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.kg import KGNode
from nfm_db.services.kg_re import GraphBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session() -> AsyncMock:
    """Create a mock SQLAlchemy AsyncSession."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _make_fake_node(
    *,
    label: str = "UO2",
    node_type: str = "Material",
) -> KGNode:
    """Create a lightweight KGNode-like object without ORM machinery."""
    node = MagicMock(spec=KGNode)
    node.id = uuid.uuid4()
    node.node_type = node_type
    node.label = label
    return node


# ---------------------------------------------------------------------------
# _fire_lightrag_ingest wiring tests
# ---------------------------------------------------------------------------


class TestGraphBuilderFireLightRAGIngest:
    """Verify that GraphBuilder calls _fire_lightrag_ingest after
    creating new nodes and/or edges (NFM-1247, finding 4b).

    The fire-and-forget function is mocked to avoid real LightRAG calls.
    """

    @pytest.mark.asyncio
    async def test_fire_lightrag_ingest_called_when_nodes_created(
        self,
    ) -> None:
        """_fire_lightrag_ingest should be called when new KGNodes are created.

        Scenario: extraction properties produce a new Material entity that
        does not match any existing node, so a new KGNode is created and
        the ingest hook fires.
        """
        session = _make_mock_session()
        builder = GraphBuilder(
            session=session,  # type: ignore[arg-type]
            corpus_id="test-corpus",
            sync_to_age=False,
        )

        extracted = [
            {
                "material_name": "UO2",
                "confidence": 0.9,
                "source_file": "paper.pdf",
            },
        ]

        with (
            patch.object(
                builder._linker,
                "find_matching_node",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                builder,
                "_fire_lightrag_ingest",
            ) as mock_fire,
        ):
            result = await builder.build_from_extraction(extracted)

        assert result.nodes_created == 1

        mock_fire.assert_called_once()
        nodes_arg, edges_arg = mock_fire.call_args[0]
        assert len(nodes_arg) == 1
        assert len(edges_arg) == 0  # single entity, no edges

    @pytest.mark.asyncio
    async def test_fire_lightrag_ingest_called_when_edges_created(
        self,
    ) -> None:
        """_fire_lightrag_ingest should be called when new KGEdges are created.

        Scenario: two co-occurring Material entities produce a relation,
        resulting in an edge being created and the ingest hook firing.
        """
        session = _make_mock_session()
        builder = GraphBuilder(
            session=session,  # type: ignore[arg-type]
            corpus_id="test-corpus",
            sync_to_age=False,
        )

        # Two materials -> Material-Material relations.
        # The RelationExtractor checks both (Material, Material) forward
        # and reverse; for same-type pairs this yields 2 edges.
        extracted = [
            {
                "material_name": "UO2",
                "confidence": 0.9,
                "source_file": "paper.pdf",
            },
            {
                "material_name": "ZrO2",
                "confidence": 0.9,
                "source_file": "paper.pdf",
            },
        ]

        with (
            patch.object(
                builder._linker,
                "find_matching_node",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                builder,
                "_fire_lightrag_ingest",
            ) as mock_fire,
        ):
            result = await builder.build_from_extraction(extracted)

        assert result.nodes_created == 2
        assert result.edges_created >= 1

        mock_fire.assert_called_once()
        nodes_arg, edges_arg = mock_fire.call_args[0]
        assert len(nodes_arg) == 2
        assert len(edges_arg) >= 1

    @pytest.mark.asyncio
    async def test_fire_lightrag_ingest_not_called_when_no_new_data(
        self,
    ) -> None:
        """_fire_lightrag_ingest should NOT be called when all entities
        match existing nodes and no new edges are produced.
        """
        session = _make_mock_session()
        builder = GraphBuilder(
            session=session,  # type: ignore[arg-type]
            corpus_id="test-corpus",
            sync_to_age=False,
        )

        existing_node = _make_fake_node(label="UO2")

        extracted = [
            {
                "material_name": "UO2",
                "confidence": 0.9,
                "source_file": "paper.pdf",
            },
        ]

        with (
            patch.object(
                builder._linker,
                "find_matching_node",
                new_callable=AsyncMock,
                return_value=existing_node,
            ),
            patch.object(
                builder,
                "_fire_lightrag_ingest",
            ) as mock_fire,
        ):
            result = await builder.build_from_extraction(extracted)

        assert result.nodes_created == 0
        assert result.edges_created == 0

        mock_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_fire_lightrag_ingest_survives_import_error(
        self,
    ) -> None:
        """_fire_lightrag_ingest should not raise even if kg_lightrag_sync
        import fails — failures are caught and logged."""
        session = _make_mock_session()
        builder = GraphBuilder(
            session=session,  # type: ignore[arg-type]
            corpus_id="test-corpus",
            sync_to_age=False,
        )

        extracted = [
            {
                "material_name": "UO2",
                "confidence": 0.9,
            },
        ]

        with (
            patch.object(
                builder._linker,
                "find_matching_node",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.fire_ingest_to_lightrag",
                side_effect=ImportError("module not installed"),
            ),
        ):
            result = await builder.build_from_extraction(extracted)
            assert result.nodes_created == 1
