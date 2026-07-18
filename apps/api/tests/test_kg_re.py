"""Tests for KG relation extraction and GraphBuilder service (NFM-984).

Focuses on the _fire_lightrag_ingest wiring added in NFM-1222 (NFM-1247),
and the UUID-before-consumers regression (NFM-1499 → NFM-1500).

Verifies that GraphBuilder correctly:
- Triggers the LightRAG auto-ingest hook when new KG nodes/edges are created
- Assigns concrete UUIDs to nodes and edges BEFORE any downstream consumer
  (review queue, AGE sync, edge FK) reads the ID, so transactions don't
  fail with NOT NULL / IntegrityError on the final flush.

External side effects are mocked where required; the UUID-assignment
regression uses the real ``db_session`` fixture (SQLite in-memory with FK
enforcement enabled) and patches only EntityLinker, AGE sync, and LightRAG.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, KGReviewQueue
from nfm_db.services.kg_re import ExtractedEntity, ExtractedRelation, GraphBuilder

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


# ---------------------------------------------------------------------------
# UUID-before-consumers regression (NFM-1499 → NFM-1500)
# ---------------------------------------------------------------------------


class TestGraphBuilderUUIDAssignment:
    """Regression for NFM-1499: GraphBuilder must assign concrete UUIDs to
    newly created nodes and edges BEFORE any downstream consumer reads
    ``.id``.

    Pre-fix behaviour: ``KGNode``/``KGEdge`` rely on the SQLAlchemy
    ``default=uuid.uuid4`` Python-side default, which only fires at flush
    time. ``_create_node`` adds the new node to the session and returns it
    immediately. Downstream consumers (``_queue_for_review``, ``sync_node``,
    and ``_create_edge`` reading ``source_node.id``/``target_node.id``) all
    see ``None`` until the final flush, which then raises ``IntegrityError``
    on the ``NOT NULL`` constraints of ``kg_review_queue.item_id`` and
    ``kg_edges.source_node_id`` / ``target_node_id``.

    Post-fix behaviour: ``_create_node`` and ``_create_edge`` assign a
    concrete ``uuid.uuid4()`` to ``id`` at construction time, so the
    downstream consumers always read a real UUID and the final flush
    succeeds.
    """

    @pytest.mark.asyncio
    async def test_create_node_returns_node_with_concrete_uuid(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Unit-level guard: ``_create_node`` must return a node whose
        ``id`` is a real UUID, NOT ``None``, before any flush."""
        builder = GraphBuilder(
            session=db_session,
            corpus_id="test-corpus",
            sync_to_age=False,
        )
        entity = ExtractedEntity(
            label="UO2",
            entity_type="Material",
            confidence=0.95,
        )
        node = await builder._create_node(entity)

        # Pre-flush invariant: the returned node MUST carry a concrete UUID
        # so downstream consumers (_queue_for_review, sync_node, _create_edge
        # reading source_node.id/target_node.id) never see None.
        assert node.id is not None, (
            "_create_node returned a node with id=None; downstream "
            "consumers would persist None foreign keys and the final "
            "flush would fail with NOT NULL / IntegrityError."
        )
        assert isinstance(node.id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_create_edge_returns_edge_with_concrete_uuid(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Unit-level guard: ``_create_edge`` must return an edge whose
        own ``id`` is a real UUID, NOT ``None``, before any flush."""
        builder = GraphBuilder(
            session=db_session,
            corpus_id="test-corpus",
            sync_to_age=False,
        )
        relation = ExtractedRelation(
            source_label="UO2",
            source_type="Material",
            target_label="thermal_conductivity",
            target_type="Property",
            relation_type="hasProperty",
            confidence=0.9,
        )
        # Use manually assigned source/target ids so this test isolates the
        # edge's own id assignment from the node id assignment.
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        edge = await builder._create_edge(relation, source_id, target_id)

        assert edge.id is not None, (
            "_create_edge returned an edge with id=None; downstream "
            "consumers (_queue_for_review for low-confidence relations, "
            "sync_edge) would persist None."
        )
        assert isinstance(edge.id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_build_uo2_multi_property_persists_with_concrete_uuids(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Integration regression for NFM-1499.

        With a real ``db_session`` (FK enforcement enabled), build a
        UO2 multi-property payload that produces ≥1 ``hasProperty`` edge
        and at least one low-confidence entity routed to the review queue.

        The build MUST:

        - Complete without IntegrityError on the final flush.
        - Produce ``BuildResult.edges_created >= 1``.
        - Persist every node and edge with a non-null UUID.
        - Persist every edge FK referencing a node present in the same
          transaction.
        - Persist every review queue entry with a non-null ``item_id``
          that resolves to a node or edge in the same transaction.
        """
        builder = GraphBuilder(
            session=db_session,
            corpus_id="test-corpus",
            sync_to_age=False,  # patch external AGE side effect only
        )

        # 3 entities: 1 Material + 2 Property (one low-confidence).
        # Pair rule (Material, Property) → hasProperty fires twice,
        # yielding 2 edges.
        extracted = [
            {
                "material_name": "UO2",
                "property": "thermal_conductivity",
                "confidence": 0.95,
            },
            {
                "material_name": "UO2",
                "property": "thermal_expansion",
                "confidence": 0.4,  # below 0.6 → routes entity to review queue
            },
        ]

        # Patch only the linker to return None without executing any DB
        # query. This guarantees no autoflush during the node-creation
        # loop, which is exactly the production scenario where the
        # pre-fix code reads None from .id (last node in the loop never
        # gets a chance to flush before the next consumer reads it).
        with patch.object(
            builder._linker,
            "find_matching_node",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # Pre-fix: this raises IntegrityError on the final flush.
            result = await builder.build_from_extraction(extracted)

        # --- count invariants (NFM-1500 acceptance criteria) ---
        assert result.edges_created >= 1, (
            f"Expected ≥1 edge for UO2 multi-property, got {result.edges_created}"
        )
        assert result.nodes_created >= 2
        assert result.review_queue_items >= 1, (
            "Expected ≥1 review queue item for the low-confidence thermal_expansion entity."
        )

        # --- node UUID invariants ---
        nodes = (await db_session.execute(select(KGNode))).scalars().all()
        assert len(nodes) >= 2
        for n in nodes:
            assert n.id is not None, "Persisted KGNode has null id"
            assert isinstance(n.id, uuid.UUID)

        # --- edge UUID + FK invariants ---
        edges = (await db_session.execute(select(KGEdge))).scalars().all()
        assert len(edges) >= 1
        node_ids = {n.id for n in nodes}
        for e in edges:
            assert e.id is not None, "Persisted KGEdge has null id"
            assert isinstance(e.id, uuid.UUID)
            assert e.source_node_id in node_ids, (
                f"Edge {e.id} source_node_id={e.source_node_id} not in "
                f"node set {sorted(str(x) for x in node_ids)}"
            )
            assert e.target_node_id in node_ids, (
                f"Edge {e.id} target_node_id={e.target_node_id} not in "
                f"node set {sorted(str(x) for x in node_ids)}"
            )

        # --- review queue item_id invariants ---
        queue = (await db_session.execute(select(KGReviewQueue))).scalars().all()
        assert len(queue) >= 1
        all_ids = node_ids | {e.id for e in edges}
        for q in queue:
            assert q.item_id is not None, (
                "Persisted KGReviewQueue entry has null item_id; pre-fix "
                "code would have inserted None here from a pre-flush node."
            )
            assert q.item_id in all_ids, (
                f"Review queue item_id={q.item_id} does not resolve to "
                f"any node or edge in the same transaction"
            )

    @pytest.mark.asyncio
    async def test_fail_fast_when_source_or_target_node_id_is_none(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Invariant guard: if a resolved source or target node object has
        a ``None`` id (regression / programmer error), ``build_from_extraction``
        must raise a ``RuntimeError`` naming both labels, NOT silently
        persist a None FK and blow up on flush."""
        builder = GraphBuilder(
            session=db_session,
            corpus_id="test-corpus",
            sync_to_age=False,
        )

        # Build two lightweight fake-node stand-ins with id=None to
        # simulate the regression (e.g. someone forgot to assign id at
        # construction). The linker "finds" these so node_map contains
        # them, but their ids are None.
        fake_source = MagicMock(spec=KGNode)
        fake_source.id = None
        fake_source.label = "FAKE_SOURCE"
        fake_source.node_type = "Material"

        fake_target = MagicMock(spec=KGNode)
        fake_target.id = None
        fake_target.label = "FAKE_TARGET"
        fake_target.node_type = "Property"

        extracted = [
            {
                "material_name": "FAKE_SOURCE",
                "property": "FAKE_TARGET",
                "confidence": 0.95,
            },
        ]

        async def _linker_side_effect(*_args, **_kwargs):
            # Return the two fake nodes in turn so node_map contains them
            # with id=None. Use a list to alternate.
            _linker_side_effect.calls += 1
            return fake_source if _linker_side_effect.calls == 1 else fake_target

        _linker_side_effect.calls = 0

        with patch.object(
            builder._linker,
            "find_matching_node",
            side_effect=_linker_side_effect,
        ):
            with pytest.raises(RuntimeError) as excinfo:
                await builder.build_from_extraction(extracted)

        msg = str(excinfo.value)
        assert "FAKE_SOURCE" in msg, f"Fail-fast error must name the source label, got: {msg!r}"
        assert "FAKE_TARGET" in msg, f"Fail-fast error must name the target label, got: {msg!r}"
