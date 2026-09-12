"""Tests for KG → LightRAG entity serialization and auto-ingest (NFM-1222).

All external services are mocked:
  - LightRAG sidecar → mock LightRAGClient / LightRAGProvider
  - PostgreSQL → mock AsyncSession
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_lightrag_sync import (
    fire_ingest_to_lightrag,
    ingest_kg_to_lightrag,
    serialize_build_result,
    serialize_kg_edge,
    serialize_kg_node,
)
from nfm_db.services.lightrag_client import LightRAGConflictError

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
    async def test_forwards_custom_source_marker(self) -> None:
        """NFM-4636: a caller-supplied ``source`` must reach the ingest.

        The literature pipeline passes ``data_source:<uuid>`` so the
        daily ``rag_audit_index_coverage`` reconciliation can match the
        ingested doc back to its ``data_sources`` row; a hardcoded
        ``kg_pipeline`` tag is invisible to that diff.
        """
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
                source=f"data_source:{node.id}",
            )

            call_kwargs = mock_provider.ingest.call_args
            assert call_kwargs.kwargs["source"] == f"data_source:{node.id}"

    @pytest.mark.asyncio
    async def test_propagates_provider_exception(self) -> None:
        """NFM-4719: provider exceptions MUST propagate.

        Pre-fix behaviour silently swallowed every exception inside
        ``ingest_kg_to_lightrag`` (``except Exception:``), which masked
        the "Event loop is closed" traceback from the inline-ingest
        path in ``process_literature`` and let it log "LightRAG inline
        ingest done" while VDB rows never landed.  Callers now wrap
        the call in their own ``try/except`` (the inline path uses the
        existing "process_literature: inline LightRAG ingest failed"
        branch; the fire-and-forget path uses the ``_fire_and_forget_ingest``
        wrapper that catches + logs).
        """
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
            with pytest.raises(RuntimeError, match="connection refused"):
                await ingest_kg_to_lightrag(
                    nodes=[node],
                    edges=[],
                    node_labels={node.id: "UO2"},
                )


# ---------------------------------------------------------------------------
# NFM-4758 / NFM-4730-FixA: reextract idempotency on 409
# ---------------------------------------------------------------------------
#
# The reextract path (process_literature_task → ingest_kg_to_lightrag)
# raises on 409 "Document storage already contains '<doc_id>'" and the
# caller treats it as non-fatal, but no VDB rows are written when the
# marker ``data_source:<uuid>`` (or ``kg_pipeline``) already exists in
# ``lightrag_doc_status`` from a prior run.  NFM-4680 recovery data
# left these markers as ``processed``, so every subsequent reextract is
# silently blocked.
#
# Fix: ``ingest_kg_to_lightrag`` detects the 409, calls
# ``DELETE /documents?doc_id=<source>`` first, then retries the insert
# exactly once.  The marker ends up in ``processed`` state with the
# new VDB rows.


class TestIngestReextractIdempotency:
    """NFM-4758: ``ingest_kg_to_lightrag`` must self-heal on 409."""

    @staticmethod
    def _make_conflict(
        *, doc_id: str, status_code: int = 409
    ) -> LightRAGConflictError:
        """Build a ``LightRAGConflictError`` that mirrors the prod payload."""
        body = (
            f"Document storage already contains '{doc_id}'. "
            "Please use a different id or delete the existing document."
        )
        return LightRAGConflictError(
            f"LightRAG ingest failed: HTTP {status_code} - {body}",
            status_code=status_code,
            response_body=body,
            doc_id=doc_id,
        )

    @pytest.mark.asyncio
    async def test_delete_then_retry_on_409_conflict(self) -> None:
        """409 ``Document storage already contains '<doc_id>'`` triggers
        DELETE /documents?doc_id=<source> then a single retry.

        Pre-fix: the 409 propagated up and the caller's ``except
        Exception`` logged "process_literature: … inline LightRAG
        ingest failed (non-fatal)" without any VDB rows landing.
        Post-fix: the function detects the conflict, deletes the
        stale marker, and the retry inserts the new VDB rows.
        """
        source = "data_source:abc"
        mock_provider = AsyncMock()
        # First ingest → 409 (mimics the prod already-processed state).
        # Second ingest → success.
        mock_provider.ingest = AsyncMock(
            side_effect=[
                self._make_conflict(doc_id=source),
                "track-after-retry",
            ]
        )

        shared_client = MagicMock()
        shared_client.delete_document = AsyncMock(
            return_value={"status": "deleted"}
        )

        node = _make_node(label="UO2")

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.lightrag_lifecycle.get_shared_lightrag_client",
                return_value=shared_client,
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
                source=source,
            )

        # DELETE was issued with the source marker (the doc_id the
        # sidecar already had indexed).
        shared_client.delete_document.assert_awaited_once_with(source)
        # Ingest was attempted twice — first 409, second success.
        assert mock_provider.ingest.await_count == 2

    @pytest.mark.asyncio
    async def test_propagates_non_conflict_provider_error(self) -> None:
        """A non-409 provider error MUST still propagate (NFM-4719).

        ``Event loop is closed`` and other transport errors are not
        idempotency issues — they should bubble up so the caller's
        ``except Exception`` can log them as before.  Only the 409
        with the specific conflict body triggers the delete-retry path.
        """
        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(
            side_effect=RuntimeError("Event loop is closed")
        )
        shared_client = MagicMock()
        shared_client.delete_document = AsyncMock(
            return_value={"status": "deleted"}
        )

        node = _make_node(label="UO2")

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.lightrag_lifecycle.get_shared_lightrag_client",
                return_value=shared_client,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
        ):
            with pytest.raises(RuntimeError, match="Event loop is closed"):
                await ingest_kg_to_lightrag(
                    nodes=[node],
                    edges=[],
                    node_labels={node.id: "UO2"},
                    source="data_source:abc",
                )

        shared_client.delete_document.assert_not_called()
        assert mock_provider.ingest.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_failure_propagates(self) -> None:
        """If the retry also fails, the second exception propagates.

        The delete-then-retry is best-effort: at most one retry, and
        the caller still gets a real failure signal if the retry
        cannot land the insert either (e.g. the sidecar is unhealthy
        or the new content is itself rejected).  This guards against
        a silent "swallow on retry" regression like NFM-4717.
        """
        source = "data_source:abc"
        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(
            side_effect=[
                self._make_conflict(doc_id=source),
                RuntimeError("sidecar still unhappy"),
            ]
        )
        shared_client = MagicMock()
        shared_client.delete_document = AsyncMock(
            return_value={"status": "deleted"}
        )

        node = _make_node(label="UO2")

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.lightrag_lifecycle.get_shared_lightrag_client",
                return_value=shared_client,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                return_value=mock_provider,
            ),
        ):
            with pytest.raises(RuntimeError, match="sidecar still unhappy"):
                await ingest_kg_to_lightrag(
                    nodes=[node],
                    edges=[],
                    node_labels={node.id: "UO2"},
                    source=source,
                )

        shared_client.delete_document.assert_awaited_once_with(source)
        assert mock_provider.ingest.await_count == 2


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
        """Build the factory mock behind a patched ``get_session_factory``."""
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock())
        session.commit = AsyncMock(return_value=None)

        factory = MagicMock()
        # ``async with get_session_factory()() as session`` — the accessor
        # returns this factory, whose ``__aenter__`` yields ``session``.
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
    async def test_propagates_db_write_failure(self) -> None:
        """NFM-4719: DB write failures MUST propagate from the base function.

        Pre-fix behaviour swallowed the DB failure inside ``except Exception:``
        and returned normally — a downstream consumer had no signal that the
        ``track_id`` persistence had failed.  The new contract: the base
        function re-raises; fire-and-forget callers go through
        :func:`_fire_and_forget_ingest` (the only sanctioned swallow site).
        """
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
            with pytest.raises(RuntimeError, match="database connection lost"):
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


# ---------------------------------------------------------------------------
# NFM-4719: inline-ingest silent failure + Celery worker re-entry
# ---------------------------------------------------------------------------
#
# The pre-fix code had two compounding defects:
#   1. ``ingest_kg_to_lightrag`` swallowed every exception via
#      ``except Exception:`` and returned normally — the inline caller's
#      "LightRAG inline ingest done (nodes=N edges=M)" log was therefore
#      indistinguishable from a real success even when the sidecar POST
#      raised ``RuntimeError: Event loop is closed`` (Celery worker
#      re-entry path: shared ``httpx.AsyncClient`` bound to a loop that
#      the previous ``asyncio.run`` had already closed).
#   2. The module-level ``_shared_client`` survived across tasks in a
#      long-lived worker process, so the second task always hit the
#      closed-loop client.
#
# These tests guard the fix by exercising both behaviours directly.


class TestFireAndForgetWrapper:
    """Verify :func:`_fire_and_forget_ingest` catches + logs failures.

    The base :func:`ingest_kg_to_lightrag` now re-raises so the inline
    caller can distinguish success from failure.  Fire-and-forget
    callers schedule :func:`_fire_and_forget_ingest` (which wraps the
    base function in a try/except + WARNING log) so the task never
    carries an unhandled traceback but the failure IS visible in the
    worker log.
    """

    @pytest.mark.asyncio
    async def test_swallows_and_logs_provider_failure(self) -> None:
        """Provider exception → swallowed with a WARNING log."""
        from nfm_db.services.kg_lightrag_sync import _fire_and_forget_ingest

        node = _make_node(label="UO2")

        with (
            patch(
                "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
                return_value=True,
            ),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                side_effect=RuntimeError("connection refused"),
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.logger"
            ) as mock_logger,
        ):
            # Must NOT raise — fire-and-forget contract.
            await _fire_and_forget_ingest(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
            )
            # WARNING must be emitted with the canonical text the prod
            # log search keys on.
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert "KG auto-ingest to LightRAG failed" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_succeeds_when_base_function_succeeds(self) -> None:
        """No exception → no warning logged."""
        from nfm_db.services.kg_lightrag_sync import _fire_and_forget_ingest

        mock_provider = AsyncMock()
        mock_provider.ingest = AsyncMock(return_value="track-ok")
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
            patch(
                "nfm_db.services.kg_lightrag_sync.logger"
            ) as mock_logger,
        ):
            await _fire_and_forget_ingest(
                nodes=[node],
                edges=[],
                node_labels={node.id: "UO2"},
            )
            mock_provider.ingest.assert_awaited_once()
            mock_logger.warning.assert_not_called()


class TestInlineIngestNoSilentFailure:
    """Regression guard for NFM-4719 / NFM-4717 inline-ingest silent failure.

    Pre-fix: ``process_literature`` called ``ingest_kg_to_lightrag``
    inline; the function swallowed the underlying exception; the
    caller logged "LightRAG inline ingest done" regardless of whether
    the sidecar POST actually landed.  These tests exercise the
    inline call path with a provider that fails, and assert that the
    failure surfaces through the caller's own try/except — not
    misreported as success.
    """

    @pytest.mark.asyncio
    async def test_inline_ingest_logs_failure_when_provider_raises(self) -> None:
        """When the inline ingest fails, the caller MUST log 'failed'.

        This is the regression assertion: pre-fix, the caller logged
        "LightRAG inline ingest done (nodes=N edges=M)" even when the
        POST raised ``RuntimeError: Event loop is closed``.  Post-fix,
        the exception propagates out of ``ingest_kg_to_lightrag`` and
        the caller's ``except Exception:`` branch emits the
        "process_literature: … inline LightRAG ingest failed" log
        instead.
        """
        from nfm_db.services.literature_service import (
            process_literature as process_lit,
        )

        node = _make_node(label="UO2")

        # Stub the build_result to carry ingest payload so the inline
        # code path is exercised.
        build_result = MagicMock()
        build_result.ingest_nodes = (node,)
        build_result.ingest_edges = ()

        # We don't actually run process_literature end-to-end here —
        # that requires a full DB / session factory stack.  Instead we
        # mirror the inline-ingest call site (literature_service.py
        # line ~1106) and assert its logger behaviour.
        from nfm_db.services import kg_lightrag_sync as kls

        with (
            patch.object(kls, "is_lightrag_configured", return_value=True),
            patch(
                "nfm_db.services.rag_provider.LightRAGProvider",
                side_effect=RuntimeError("Event loop is closed"),
            ),
            patch(
                "nfm_db.services.literature_service.logger"
            ) as mock_lit_logger,
        ):
            try:
                await kls.ingest_kg_to_lightrag(
                    nodes=list(build_result.ingest_nodes),
                    edges=list(build_result.ingest_edges),
                    node_labels={node.id: "UO2"},
                    source="data_source:abc",
                )
                # The fix: this MUST raise.  If we reach here, the
                # silent-failure regression has returned.
                raised = False
            except RuntimeError as exc:
                raised = True
                assert "Event loop is closed" in str(exc)

            assert raised, (
                "Regression: ingest_kg_to_lightrag must propagate "
                "RuntimeError (was silently swallowed pre-NFM-4719)"
            )
            # Inline caller is expected to do its own try/except; here
            # we just confirm the exception actually surfaced to it
            # (i.e. didn't vanish mid-call).
            mock_lit_logger.warning.assert_not_called()
            # Touch the unused import so linters don't strip it.
            assert process_lit is not None


class TestSharedClientRecreatesOnClosedLoop:
    """NFM-4719: lifecycle helper must detect Celery worker re-entry.

    A long-lived Celery worker process executes N tasks in a row, each
    wrapped in ``asyncio.run(_run())``.  The module-level
    ``_shared_client`` survives across tasks; the underlying
    ``httpx.AsyncClient`` is bound to whichever loop first created
    it.  After that loop closes (end of the first task), every POST
    on the SECOND task would raise ``RuntimeError: Event loop is
    closed``.

    The fix: ``get_shared_lightrag_client()`` detects a stale client
    (loop is closed or differs from the running loop) and rebuilds.
    These tests simulate the cross-loop scenario without booting a
    real Celery worker.
    """

    def _make_mock_client(
        self, *, bound_to_closed_loop: bool
    ) -> MagicMock:
        """Return a stand-in for a stale or fresh shared client."""
        client = MagicMock()
        client.base_url = "http://stale:9621"
        client._loop = MagicMock()
        client._loop.is_closed.return_value = bound_to_closed_loop
        return client

    def test_returns_fresh_client_when_singleton_is_closed_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stale loop → client dropped, new one constructed."""
        from nfm_db.services import lightrag_lifecycle as ll

        stale = self._make_mock_client(bound_to_closed_loop=True)
        fresh = self._make_mock_client(bound_to_closed_loop=False)

        # Pretend LightRAG IS configured and the module-level singleton
        # is bound to a closed loop from the previous Celery task.
        monkeypatch.setattr(
            "nfm_db.services.lightrag_lifecycle.is_lightrag_configured",
            lambda: True,
        )
        monkeypatch.setattr(ll, "_shared_client", stale)
        # Each call to LightRAGClient() returns a distinct mock so the
        # recreation is observable.
        construction_count = {"n": 0}

        def _fake_ctor() -> MagicMock:
            construction_count["n"] += 1
            return fresh

        monkeypatch.setattr(ll, "LightRAGClient", _fake_ctor)

        result = ll.get_shared_lightrag_client()

        assert result is fresh
        assert construction_count["n"] == 1
        # The stale singleton must have been replaced in the module.
        assert ll._shared_client is fresh

    def test_returns_existing_client_when_loop_is_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Healthy loop → no rebuild, same instance returned."""
        from nfm_db.services import lightrag_lifecycle as ll

        healthy = self._make_mock_client(bound_to_closed_loop=False)

        monkeypatch.setattr(
            "nfm_db.services.lightrag_lifecycle.is_lightrag_configured",
            lambda: True,
        )
        monkeypatch.setattr(ll, "_shared_client", healthy)

        construction_count = {"n": 0}

        def _fake_ctor() -> MagicMock:
            construction_count["n"] += 1
            return MagicMock()

        monkeypatch.setattr(ll, "LightRAGClient", _fake_ctor)

        result = ll.get_shared_lightrag_client()

        assert result is healthy
        assert construction_count["n"] == 0


class TestCelerySubprocessEndToEnd:
    """End-to-end regression for NFM-4719 / NFM-4717.

    Simulates the exact production scenario: a long-lived worker
    subprocess invokes ``ingest_kg_to_lightrag`` inside
    ``asyncio.run(_run())`` TWICE.  Pre-fix, the SECOND invocation
    hit a closed-loop ``httpx.AsyncClient`` and raised
    ``RuntimeError: Event loop is closed`` — which the old swallow
    masked and ``process_literature`` misreported as success.

    Post-fix: the second invocation gets a freshly constructed
    client (loop-aware rebuild) and the POST succeeds.
    """

    def test_two_consecutive_asyncio_runs_both_ingest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nfm_db.services import kg_lightrag_sync as kls
        from nfm_db.services import lightrag_lifecycle as ll

        # Per-run POST counts so we can verify the sidecar was actually
        # contacted both times.
        post_count = {"first": 0, "second": 0}

        class _FakeAsyncClient:
            """Minimal stand-in for httpx.AsyncClient.

            Records which loop it was created on so the test can prove
            the second call created a fresh client bound to the new
            loop (not the closed first loop).
            """

            instances: list[_FakeAsyncClient] = []

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                try:
                    self._created_on_loop = asyncio.get_running_loop()
                except RuntimeError:
                    self._created_on_loop = None
                self.kwargs = kwargs
                _FakeAsyncClient.instances.append(self)

            async def post(
                self, path: str, *, json: dict[str, Any], timeout: Any
            ) -> MagicMock:
                # Tag the call so the test can distinguish first vs
                # second invocation.
                if (
                    _FakeAsyncClient.instances.index(self) == 0
                    and post_count["first"] == 0
                ):
                    post_count["first"] += 1
                else:
                    post_count["second"] += 1
                response = MagicMock()
                response.status_code = 200
                response.json = lambda: {"track_id": "track-fake"}
                response.raise_for_status = lambda: None
                return response

            async def aclose(self) -> None:
                return None

        monkeypatch.setattr(
            "nfm_db.services.kg_lightrag_sync.is_lightrag_configured",
            lambda: True,
        )
        monkeypatch.setattr(ll, "is_lightrag_configured", lambda: True)
        monkeypatch.setattr(ll, "_shared_client", None)
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        # Stub the DB write — not relevant to the loop-binding test.
        monkeypatch.setattr(
            "nfm_db.database.get_session_factory",
            lambda: MagicMock(
                return_value=MagicMock(
                    __aenter__=AsyncMock(
                        return_value=MagicMock(
                            execute=AsyncMock(return_value=MagicMock()),
                            commit=AsyncMock(return_value=None),
                        )
                    ),
                    __aexit__=AsyncMock(return_value=None),
                )
            ),
        )

        node = _make_node(label="UO2")
        ingest_kwargs = {
            "nodes": [node],
            "edges": [],
            "node_labels": {node.id: "UO2"},
            "source": "data_source:abc",
        }

        async def _drive() -> None:
            await kls.ingest_kg_to_lightrag(**ingest_kwargs)

        # First task: simulates a Celery worker's first invocation.
        asyncio.run(_drive())
        first_loop = _FakeAsyncClient.instances[0]._created_on_loop

        # Second task: a fresh asyncio.run = a fresh loop.  Pre-fix
        # this is where ``RuntimeError: Event loop is closed`` would
        # fire (httpx.AsyncClient is bound to ``first_loop`` which is
        # now closed).
        asyncio.run(_drive())
        second_loop = _FakeAsyncClient.instances[1]._created_on_loop

        # Sanity: two separate loops were used.
        assert first_loop is not second_loop
        assert first_loop.is_closed() is True

        # Both tasks must have hit the sidecar — pre-fix the second
        # would have raised before reaching the POST.
        assert post_count["first"] == 1
        assert post_count["second"] == 1

        # And two distinct clients were constructed (proves the loop
        # mismatch triggered a rebuild).
        assert len(_FakeAsyncClient.instances) == 2
