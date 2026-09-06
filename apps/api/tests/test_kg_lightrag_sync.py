"""Tests for KG → LightRAG entity serialization and auto-ingest (NFM-1222).

All external services are mocked:
  - LightRAG sidecar → mock LightRAGClient / LightRAGProvider
  - PostgreSQL → mock AsyncSession
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_lightrag_sync import (
    fire_ingest_to_lightrag,
    ingest_kg_to_lightrag,
    serialize_build_result,
    serialize_kg_edge,
    serialize_kg_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    *,
    label: str = "UO2",
    node_type: str = "Material",
    properties: dict | None = None,
    aliases: list[str] | None = None,
    confidence: float = 0.9,
) -> KGNode:
    """Create a mock KGNode for testing."""
    node = MagicMock(spec=KGNode)
    node.id = uuid.uuid4()
    node.node_type = node_type
    node.label = label
    node.aliases = json.dumps(aliases) if aliases else None
    node.properties = properties or {}
    node.confidence = confidence
    return node


def _make_edge(
    *,
    source_node_id: uuid.UUID | None = None,
    target_node_id: uuid.UUID | None = None,
    relation_type: str = "relatedTo",
    properties: dict | None = None,
    confidence: float = 0.85,
) -> KGEdge:
    """Create a mock KGEdge for testing."""
    edge = MagicMock(spec=KGEdge)
    edge.id = uuid.uuid4()
    edge.source_node_id = source_node_id or uuid.uuid4()
    edge.target_node_id = target_node_id or uuid.uuid4()
    edge.relation_type = relation_type
    edge.properties = properties or {}
    edge.confidence = confidence
    return edge


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_module_importable() -> None:
    """The kg_lightrag_sync module should be importable."""
    assert serialize_kg_node is not None
    assert serialize_kg_edge is not None
    assert serialize_build_result is not None
    assert ingest_kg_to_lightrag is not None
    assert fire_ingest_to_lightrag is not None


# ---------------------------------------------------------------------------
# serialize_kg_node
# ---------------------------------------------------------------------------


class TestSerializeKGNode:
    """Tests for the KGNode serialization."""

    def test_basic_material_node(self) -> None:
        node = _make_node(
            label="UO2",
            node_type="Material",
            properties={"crystal_structure": "Fluorite", "density": "10.97 g/cm³"},
        )
        text = serialize_kg_node(node)

        assert "[Material] UO2" in text
        assert "crystal_structure: Fluorite" in text
        assert "density: 10.97 g/cm³" in text
        assert "confidence: 0.90" in text

    def test_node_with_aliases(self) -> None:
        node = _make_node(
            label="UO2",
            aliases=["Uranium Dioxide", "UO2 fuel"],
        )
        text = serialize_kg_node(node)

        assert "aliases: Uranium Dioxide, UO2 fuel" in text

    def test_node_without_properties(self) -> None:
        node = _make_node(properties={})
        text = serialize_kg_node(node)

        assert "[Material] UO2" in text
        assert "confidence: 0.90" in text
        # Only header + confidence lines
        lines = text.strip().split("\n")
        assert len(lines) == 2

    def test_node_without_aliases(self) -> None:
        node = _make_node(aliases=None)
        text = serialize_kg_node(node)
        assert "aliases:" not in text

    def test_property_entity(self) -> None:
        node = _make_node(
            label="thermal_conductivity",
            node_type="Property",
            properties={"unit": "W/m·K", "value": "8.0"},
        )
        text = serialize_kg_node(node)

        assert "[Property] thermal_conductivity" in text
        assert "unit: W/m·K" in text

    def test_confidence_formatting(self) -> None:
        node = _make_node(confidence=0.6)
        text = serialize_kg_node(node)
        assert "confidence: 0.60" in text

    def test_experiment_entity(self) -> None:
        node = _make_node(
            label="TEM analysis",
            node_type="Experiment",
            confidence=0.75,
        )
        text = serialize_kg_node(node)
        assert "[Experiment] TEM analysis" in text
        assert "confidence: 0.75" in text


# ---------------------------------------------------------------------------
# serialize_kg_edge
# ---------------------------------------------------------------------------


class TestSerializeKGEdge:
    """Tests for the KGEdge serialization."""

    def test_basic_edge(self) -> None:
        edge = _make_edge(relation_type="relatedTo")
        text = serialize_kg_edge(edge, "UO2", "ZrO2")

        assert "[relatedTo] UO2 -> ZrO2" in text
        assert "confidence: 0.85" in text

    def test_edge_with_properties(self) -> None:
        edge = _make_edge(
            relation_type="hasProperty",
            properties={"extraction_method": "heuristic_type_pair"},
        )
        text = serialize_kg_edge(edge, "UO2", "melting_point")

        assert "[hasProperty] UO2 -> melting_point" in text
        assert "extraction_method: heuristic_type_pair" in text

    def test_measured_in_edge(self) -> None:
        edge = _make_edge(relation_type="measuredIn")
        text = serialize_kg_edge(edge, "XRD scan", "UO2")
        assert "[measuredIn] XRD scan -> UO2" in text


# ---------------------------------------------------------------------------
# serialize_build_result
# ---------------------------------------------------------------------------


class TestSerializeBuildResult:
    """Tests for the combined build result serialization."""

    def test_nodes_and_edges(self) -> None:
        node = _make_node(label="UO2")
        edge = _make_edge(
            source_node_id=node.id,
            target_node_id=uuid.uuid4(),
            relation_type="relatedTo",
        )
        node_labels = {node.id: "UO2"}

        text = serialize_build_result([node], [edge], node_labels)

        assert "[Material] UO2" in text
        assert "[relatedTo] UO2 ->" in text

    def test_empty_result(self) -> None:
        text = serialize_build_result([], [], {})
        assert text == ""

    def test_multiple_nodes_separated_by_blank_lines(self) -> None:
        n1 = _make_node(label="UO2")
        n2 = _make_node(label="ZrO2")
        text = serialize_build_result([n1, n2], [], {})

        assert "[Material] UO2" in text
        assert "[Material] ZrO2" in text
        # Blank line between sections
        assert "\n\n" in text

    def test_uuid_fallback_for_missing_labels(self) -> None:
        node = _make_node(label="UO2")
        unknown_id = uuid.uuid4()
        edge = _make_edge(
            source_node_id=node.id,
            target_node_id=unknown_id,
        )
        node_labels = {node.id: "UO2"}

        text = serialize_build_result([node], [edge], node_labels)
        assert str(unknown_id) in text


# ---------------------------------------------------------------------------
# ingest_kg_to_lightrag
# ---------------------------------------------------------------------------


class TestIngestKGToLightRAG:
    """Tests for the fire-and-forget ingest function."""

    @pytest.mark.asyncio
    async def test_skips_when_not_configured(self) -> None:
        """Should skip ingestion when LightRAG is not configured."""
        with patch(
            "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
            return_value=False,
        ):
            node = _make_node()
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
            )

    @pytest.mark.asyncio
    async def test_skips_when_no_data(self) -> None:
        """Should skip when no nodes or edges to ingest."""
        with patch(
            "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
            return_value=True,
        ):
            await ingest_kg_to_lightrag(nodes=[], edges=[], node_labels={})

    @pytest.mark.asyncio
    async def test_calls_provider_ingest_on_success(self) -> None:
        """Should serialize and call provider.ingest() when configured."""
        mock_provider = AsyncMock()
        node = _make_node(label="UO2")

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
        ):
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
            )

            mock_provider.ingest.assert_called_once()
            call_kwargs = mock_provider.ingest.call_args
            assert "[Material] UO2" in call_kwargs.kwargs["text"]
            assert call_kwargs.kwargs["source"] == "kg_pipeline"

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self) -> None:
        """Should catch and log exceptions without propagating."""
        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                side_effect=RuntimeError("connection refused"),
            ),
        ):
            node = _make_node()
            # Should NOT raise
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
            )


# ---------------------------------------------------------------------------
# track_id persistence (NFM-2881 AC-2)
# ---------------------------------------------------------------------------


class TestTrackIdPersistence:
    """Verify the LightRAG ``track_id`` returned by ``ingest()`` is
    persisted to the corresponding ``ExtractionJob`` row (NFM-2881 AC-2)
    and that the persistence path is fully backward-compatible
    (AC-3 — no track_id ⇒ no DB write).
    """

    @staticmethod
    def _mock_session_factory() -> MagicMock:
        """Build an async-context-manager mock for ``async_session_factory``."""
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock())
        session.commit = AsyncMock(return_value=None)

        factory = MagicMock()
        # ``async with async_session_factory() as session`` — return
        # an object whose ``__aenter__`` yields ``session``.
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        factory.return_value = cm
        return factory

    @pytest.mark.asyncio
    async def test_persists_track_id_when_extraction_job_id_provided(self) -> None:
        """Should UPDATE extraction_jobs.track_id with the provider's return value."""
        job_id = uuid.uuid4()
        expected_track_id = "lightrag-track-abc123"
        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(return_value=expected_track_id)
        session_factory = self._mock_session_factory()

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
            patch(
                "nfm_db.database.get_session_factory",
                return_value=session_factory,
            ),
        ):
            node = _make_node(label="UO2")
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
                extraction_job_id=job_id,
            )

            mock_provider.ingest.assert_awaited_once()
            session_factory.assert_called_once()
            inner_session = session_factory.return_value.__aenter__.return_value
            inner_session.execute.assert_awaited_once()
            inner_session.commit.assert_awaited_once()

            # Verify the UPDATE statement carried the track_id we got back.
            update_stmt = inner_session.execute.await_args.args[0]
            compiled = update_stmt.compile(
                dialect=update_stmt.bind.dialect
                if hasattr(update_stmt, "bind") and update_stmt.bind is not None
                else None
            )
            # The compiled params expose the values dict that was bound to
            # the ``values(track_id=...)`` clause.
            params = compiled.params if hasattr(compiled, "params") else {}
            assert params.get("track_id") == expected_track_id

    @pytest.mark.asyncio
    async def test_skips_persistence_when_extraction_job_id_is_none(self) -> None:
        """Backward compat (AC-3): no ``extraction_job_id`` ⇒ no DB write.

        Existing callers that do not thread ``extraction_job_id`` through
        must keep working unchanged.  ``ingest()`` still runs, but no
        ``async_session_factory`` is opened.
        """
        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(return_value="track-xyz")
        session_factory = self._mock_session_factory()

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
            patch(
                "nfm_db.database.get_session_factory",
                return_value=session_factory,
            ),
        ):
            node = _make_node()
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
                # extraction_job_id defaults to None
            )

            session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_persistence_when_track_id_is_none(self) -> None:
        """Fallback path: provider returned ``None`` ⇒ no DB write.

        ``RuleBasedFallbackProvider`` and the LightRAG→fallback selector
        both return ``None`` from ``ingest()``.  No point opening a DB
        session to write ``track_id=NULL`` — leave it untouched.
        """
        job_id = uuid.uuid4()
        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(return_value=None)
        session_factory = self._mock_session_factory()

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
            patch(
                "nfm_db.database.get_session_factory",
                return_value=session_factory,
            ),
        ):
            node = _make_node()
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
                extraction_job_id=job_id,
            )

            mock_provider.ingest.assert_awaited_once()
            session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_db_failure_gracefully(self) -> None:
        """DB write failures must not propagate — fire-and-forget contract."""
        job_id = uuid.uuid4()
        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(return_value="track-dbfail")
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=RuntimeError("database connection lost")
        )
        session.commit = AsyncMock(return_value=None)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=cm)

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
            patch(
                "nfm_db.database.get_session_factory",
                return_value=session_factory,
            ),
        ):
            node = _make_node()
            # Must NOT raise — DB failure is a degraded-mode event, not an
            # error that breaks the extraction pipeline.
            await ingest_kg_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
                extraction_job_id=job_id,
            )


# ---------------------------------------------------------------------------
# ExtractionJob model — track_id column (NFM-2881 AC-1, AC-3)
# ---------------------------------------------------------------------------


class TestExtractionJobTrackIdColumn:
    """Verify the ORM column exists, is nullable, and round-trips a value.

    These tests exercise the model definition directly (no DB session)
    so they stay in the fast feedback loop.
    """

    def test_track_id_column_is_nullable(self) -> None:
        """AC-3: legacy rows must remain valid — ``track_id`` is nullable."""
        from sqlalchemy import inspect

        from nfm_db.models.extraction_job import ExtractionJob

        mapper = inspect(ExtractionJob)
        col = mapper.columns["track_id"]
        assert col.nullable is True
        assert str(col.type.length) == "255"

    def test_track_id_column_default_is_none(self) -> None:
        """New rows without an explicit value should default to ``None``."""
        from nfm_db.models.extraction_job import ExtractionJob

        job = ExtractionJob()
        assert job.track_id is None


# ---------------------------------------------------------------------------
# fire_ingest_to_lightrag
# ---------------------------------------------------------------------------


class TestFireIngestToLightRAG:
    """Tests for the fire-and-forget scheduling function."""

    def test_skips_when_not_configured(self) -> None:
        """Should skip when LightRAG is not configured."""
        with patch(
            "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
            return_value=False,
        ):
            fire_ingest_to_lightrag(nodes=[], edges=[], node_labels={})

    def test_skips_when_no_running_loop(self) -> None:
        """Should not raise when there is no running event loop."""
        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.asyncio.get_running_loop",
                side_effect=RuntimeError("no running loop"),
            ),
        ):
            fire_ingest_to_lightrag(nodes=[], edges=[], node_labels={})

    def test_creates_task_when_loop_available(self) -> None:
        """Should create an asyncio.Task when loop is running."""
        mock_loop = MagicMock()
        mock_task = MagicMock()

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.asyncio.get_running_loop",
                return_value=mock_loop,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.asyncio.create_task",
                return_value=mock_task,
            ),
        ):
            node = _make_node()
            fire_ingest_to_lightrag(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
            )
            mock_loop.create_task.assert_called_once()
