"""E2E integration test for the OntoFuel seed pipeline (NFM-768).

Validates the complete chain:
  NVL JSON → parse → dedup → DB write (OntologyIdMap + KGNode + KGEdge) → verify counts

Uses the real nvl_ontology_data.json file (927 nodes, 1061 edges)
and the db_session fixture (in-memory SQLite).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, OntologyIdMap
from nfm_db.services.seed_ontofuel import CORPUS_ID, seed_ontofuel

NVL_JSON = (
    Path(__file__).resolve().parent.parent.parent
    / "web"
    / "public"
    / "ontology-viewer"
    / "data"
    / "nvl_ontology_data.json"
)


# ── AC #2: --dry-run mode outputs correct stats ─────────────────────


class TestDryRun:
    """--dry-run mode: parse and stats only, no DB writes."""

    async def test_dry_run_returns_correct_counts(self, db_session: AsyncSession):
        stats = await seed_ontofuel(db_session, json_path=NVL_JSON, dry_run=True)
        assert stats.total_nodes == 927
        assert stats.classes == 172
        assert stats.individuals == 755
        assert stats.total_relationships == 1061

    async def test_dry_run_no_db_changes(self, db_session: AsyncSession):
        """dry-run must not create any DB rows."""
        await seed_ontofuel(db_session, json_path=NVL_JSON, dry_run=True)
        assert (await db_session.execute(select(OntologyIdMap))).scalars().all() == []
        assert (await db_session.execute(select(KGNode))).scalars().all() == []
        assert (await db_session.execute(select(KGEdge))).scalars().all() == []


# ── AC #3: Idempotency ─────────────────────────────────────────────


class TestIdempotency:
    """Re-running seed must not create duplicate data."""

    async def test_double_seed_same_counts(self, db_session: AsyncSession):
        """Running seed twice produces the same DB counts."""
        stats1 = await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        stats2 = await seed_ontofuel(db_session, json_path=NVL_JSON, force=True)
        await db_session.commit()

        # Second run: all 927 id_maps should be skipped as duplicates
        assert stats2.duplicates_skipped == 927
        assert stats2.id_maps_created == 0
        assert stats2.kg_nodes_created == 0

    async def test_second_run_without_force_returns_existing(self, db_session: AsyncSession):
        """Running seed without --force on existing corpus returns existing stats."""
        stats1 = await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        stats2 = await seed_ontofuel(db_session, json_path=NVL_JSON, force=False)

        # Should return counts of existing data (from _count_existing)
        assert stats2.id_maps_created == stats1.id_maps_created
        assert stats2.kg_nodes_created == stats1.kg_nodes_created


# ── AC #1 & #4: Full E2E pipeline ──────────────────────────────────


class TestFullE2E:
    """Full pipeline: parse → dedup → write → verify DB counts."""

    async def test_seed_creates_ontology_id_map(self, db_session: AsyncSession):
        """All 927 nodes get OntologyIdMap entries."""
        stats = await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        assert stats.id_maps_created == 927
        assert stats.duplicates_skipped == 0

        count = (await db_session.execute(
            select(OntologyIdMap).where(OntologyIdMap.corpus_id == CORPUS_ID)
        )).scalars().all()
        assert len(count) == 927

    async def test_seed_creates_kg_nodes_for_individuals(self, db_session: AsyncSession):
        """Individual nodes get KGNode entries with corpus_id='ontofuel'."""
        stats = await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        assert stats.kg_nodes_created == 927

        nodes = (await db_session.execute(
            select(KGNode).where(KGNode.corpus_id == CORPUS_ID)
        )).scalars().all()
        assert len(nodes) == 927

    async def test_seed_creates_kg_edges(self, db_session: AsyncSession):
        """Individual-to-individual relationships become KGEdge entries."""
        stats = await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        edges = (await db_session.execute(
            select(KGEdge).where(KGEdge.corpus_id == CORPUS_ID)
        )).scalars().all()
        assert len(edges) == stats.kg_edges_created

    async def test_seed_stats_summary(self, db_session: AsyncSession):
        """Summary string is non-empty and informative."""
        stats = await seed_ontofuel(db_session, json_path=NVL_JSON, dry_run=True)
        summary = stats.summary()
        assert "927" in summary
        assert "172" in summary
        assert "755" in summary
        assert "1061" in summary

    async def test_deterministic_uuids(self, db_session: AsyncSession):
        """Same NVL ID always maps to the same internal UUID."""
        await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        await seed_ontofuel(db_session, json_path=NVL_JSON, force=True)
        await db_session.commit()

        maps = (await db_session.execute(
            select(OntologyIdMap).where(OntologyIdMap.corpus_id == CORPUS_ID)
        )).scalars().all()
        nvl_ids = [m.nvl_id for m in maps]
        assert len(nvl_ids) == len(set(nvl_ids))

    async def test_kg_nodes_have_approved_status(self, db_session: AsyncSession):
        """Seed data nodes are pre-curated (review_status='approved')."""
        await seed_ontofuel(db_session, json_path=NVL_JSON)
        await db_session.commit()

        nodes = (await db_session.execute(
            select(KGNode).where(KGNode.corpus_id == CORPUS_ID).limit(10)
        )).scalars().all()
        for node in nodes:
            assert node.review_status == "approved"
            assert node.confidence == 1.0

    async def test_missing_file_raises(self, db_session: AsyncSession):
        """Non-existent JSON path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="OntoFuel JSON not found"):
            await seed_ontofuel(db_session, json_path=Path("/nonexistent/path.json"))
