"""Integration tests for multi-source fusion pipeline (NFM-839 B3.2).

Covers:
  - detect_conflicts() — pure function, no DB
  - FusionPipeline.run() — async, DB-backed conflict detection + auto-resolution
  - FusionPipeline.get_conflicts() — async, filtered conflict queries
  - FusionPipeline.resolve_conflict() — async, manual resolution

NOTE: Skipped due to duplicate conflict_records table registration
between conflict.py (full model) and conflict_record.py (stub). Needs
model unification. (NFM-1211)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.skip(
    reason="Duplicate conflict_records table (model unification needed) (NFM-1211)",
)
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from nfm_db.models.conflict import ConflictRecord, ConflictStatus  # noqa: E402
from nfm_db.models.material import Material  # noqa: E402
from nfm_db.services.fusion_pipeline import (  # noqa: E402
    ConflictGroup,
    ExtractedProperty,
    FusionPipeline,
    detect_conflicts,
)

# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------

_SEED_MATERIAL_ID = uuid.UUID("b0000000-0000-0000-0000-000000000001")


async def _seed_material(session: AsyncSession) -> uuid.UUID:
    """Create a minimal Material record for FK references."""
    material = await session.get(Material, _SEED_MATERIAL_ID)
    if material is None:
        session.add(Material(
            id=_SEED_MATERIAL_ID,
            name="UO2-Test",
            formula="UO2",
        ))
        await session.flush()
    return _SEED_MATERIAL_ID


# ---------------------------------------------------------------------------
# detect_conflicts() — pure function tests (no DB needed)
# ---------------------------------------------------------------------------

class TestDetectConflicts:
    """Tests for the synchronous detect_conflicts() function."""

    def test_no_properties_returns_empty(self) -> None:
        result = detect_conflicts([])
        assert result == []

    def test_single_source_no_conflict(self) -> None:
        props = [
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.5,
                source_id="src-a",
            ),
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.5,
                source_id="src-a",
            ),
        ]
        result = detect_conflicts(props)
        assert result == []

    def test_same_value_different_sources_no_conflict(self) -> None:
        props = [
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.5,
                source_id="src-a",
            ),
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.5,
                source_id="src-b",
            ),
        ]
        result = detect_conflicts(props)
        assert result == []

    def test_different_values_different_sources_detects_conflict(self) -> None:
        props = [
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.5,
                source_id="src-a",
                confidence=0.9,
            ),
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=11.2,
                source_id="src-b",
                confidence=0.8,
            ),
        ]
        result = detect_conflicts(props)
        assert len(result) == 1
        group = result[0]
        assert isinstance(group, ConflictGroup)
        assert group.material_id == str(_SEED_MATERIAL_ID)
        assert group.property_type == "density"
        assert len(group.values) == 2

    def test_multiple_materials_detects_independently(self) -> None:
        mat_a = str(uuid.uuid4())
        mat_b = str(uuid.uuid4())
        props = [
            ExtractedProperty(
                material_id=mat_a, property_type="density",
                value=10.0, source_id="s1",
            ),
            ExtractedProperty(
                material_id=mat_a, property_type="density",
                value=11.0, source_id="s2",
            ),
            ExtractedProperty(
                material_id=mat_b, property_type="melting_point",
                value=2800.0, source_id="s1",
            ),
            ExtractedProperty(
                material_id=mat_b, property_type="melting_point",
                value=2850.0, source_id="s3",
            ),
        ]
        result = detect_conflicts(props)
        assert len(result) == 2
        prop_types = {g.property_type for g in result}
        assert prop_types == {"density", "melting_point"}

    def test_three_sources_two_distinct_values(self) -> None:
        props = [
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.0,
                source_id="src-a",
            ),
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.0,
                source_id="src-b",
            ),
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=11.0,
                source_id="src-c",
            ),
        ]
        result = detect_conflicts(props)
        assert len(result) == 1
        assert len(result[0].values) == 3

    def test_extracts_confidence_and_timestamp(self) -> None:
        now = datetime.now(UTC)
        props = [
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=10.5,
                source_id="src-a",
                confidence=0.95,
                extracted_at=now - timedelta(days=1),
            ),
            ExtractedProperty(
                material_id=str(_SEED_MATERIAL_ID),
                property_type="density",
                value=11.0,
                source_id="src-b",
                confidence=0.7,
                extracted_at=now,
            ),
        ]
        result = detect_conflicts(props)
        assert len(result) == 1
        values = result[0].values
        assert values[0]["confidence"] == 0.95
        assert values[1]["confidence"] == 0.7


# ---------------------------------------------------------------------------
# FusionPipeline.run() — async DB-backed tests
# ---------------------------------------------------------------------------

class TestFusionPipelineRun:
    """Tests for FusionPipeline.run() with in-memory SQLite."""

    @pytest.fixture
    async def material_id(self, db_session: AsyncSession) -> uuid.UUID:
        return await _seed_material(db_session)

    @pytest.mark.asyncio
    async def test_run_creates_conflict_record(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
    ) -> None:
        props = [
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=10.5,
                source_id="src-a",
                confidence=0.9,
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=11.2,
                source_id="src-b",
                confidence=0.8,
            ),
        ]
        pipeline = FusionPipeline(db_session)
        results = await pipeline.run(props)

        assert len(results) == 1
        fr = results[0]
        assert fr.conflict_detected is True
        assert fr.material_id == str(material_id)
        assert fr.property_type == "density"
        assert fr.conflict_id is not None
        assert fr.resolved_value is not None
        assert fr.strategy_used == "confidence"

    @pytest.mark.asyncio
    async def test_run_auto_resolves_with_confidence(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
    ) -> None:
        props = [
            ExtractedProperty(
                material_id=str(material_id),
                property_type="thermal_conductivity",
                value=5.0,
                source_id="src-a",
                confidence=0.6,
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="thermal_conductivity",
                value=7.0,
                source_id="src-b",
                confidence=0.95,
            ),
        ]
        pipeline = FusionPipeline(db_session)
        results = await pipeline.run(props, strategy="confidence")

        assert len(results) == 1
        fr = results[0]
        assert fr.resolved_value["value"] == 7.0
        assert fr.resolved_value["source_id"] == "src-b"

    @pytest.mark.asyncio
    async def test_run_newest_strategy(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
    ) -> None:
        now = datetime.now(UTC)
        props = [
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=10.0,
                source_id="old-src",
                confidence=0.9,
                extracted_at=now - timedelta(days=5),
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=11.0,
                source_id="new-src",
                confidence=0.7,
                extracted_at=now,
            ),
        ]
        pipeline = FusionPipeline(db_session)
        results = await pipeline.run(props, strategy="newest")

        assert len(results) == 1
        assert results[0].resolved_value["source_id"] == "new-src"

    @pytest.mark.asyncio
    async def test_run_manual_strategy_escapes(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
    ) -> None:
        props = [
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=10.0,
                source_id="src-a",
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=11.0,
                source_id="src-b",
            ),
        ]
        pipeline = FusionPipeline(db_session)
        results = await pipeline.run(props, strategy="manual", auto_resolve=True)

        assert len(results) == 1
        fr = results[0]
        assert fr.conflict_detected is True
        assert fr.conflict_id is not None
        assert fr.resolved_value is None

        query_result = await pipeline.get_conflicts(
            material_id=str(material_id),
            property_type="density",
        )
        assert len(query_result) == 1
        assert query_result[0].status == ConflictStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_run_no_conflict_same_values(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
    ) -> None:
        props = [
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=10.5,
                source_id="src-a",
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=10.5,
                source_id="src-b",
            ),
        ]
        pipeline = FusionPipeline(db_session)
        results = await pipeline.run(props)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_run_multiple_conflict_groups(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
    ) -> None:
        props = [
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=10.0,
                source_id="s1",
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="density",
                value=11.0,
                source_id="s2",
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="melting_point",
                value=2800.0,
                source_id="s1",
            ),
            ExtractedProperty(
                material_id=str(material_id),
                property_type="melting_point",
                value=2850.0,
                source_id="s3",
            ),
        ]
        pipeline = FusionPipeline(db_session)
        results = await pipeline.run(props)

        assert len(results) == 2
        prop_types = {r.property_type for r in results}
        assert prop_types == {"density", "melting_point"}


# ---------------------------------------------------------------------------
# FusionPipeline.get_conflicts() — async filtered query tests
# ---------------------------------------------------------------------------

class TestFusionPipelineGetConflicts:
    """Tests for FusionPipeline.get_conflicts() filtered queries."""

    @pytest.fixture
    async def seeded_conflicts(
        self,
        db_session: AsyncSession,
    ) -> tuple[uuid.UUID, list[ConflictRecord]]:
        """Seed material + 2 conflict records for query tests."""
        material_id = await _seed_material(db_session)

        conflicts: list[ConflictRecord] = []
        for i, (prop_type, status) in enumerate([
            ("density", ConflictStatus.PENDING),
            ("melting_point", ConflictStatus.AUTO_RESOLVED),
        ]):
            record = ConflictRecord(
                material_id=material_id,
                property_type=prop_type,
                status=status,
                resolution_strategy="confidence",
                conflicting_values=[
                    {"value": 10.0 + i, "source_id": f"s{i}"},
                    {"value": 11.0 + i, "source_id": f"s{i+1}"},
                ],
            )
            db_session.add(record)
            conflicts.append(record)
        await db_session.flush()
        return material_id, conflicts

    @pytest.mark.asyncio
    async def test_get_all_conflicts(
        self,
        db_session: AsyncSession,
        seeded_conflicts: tuple[uuid.UUID, list[ConflictRecord]],
    ) -> None:
        material_id, _ = seeded_conflicts
        pipeline = FusionPipeline(db_session)
        results = await pipeline.get_conflicts()

        assert len(results) >= 2

    @pytest.mark.asyncio
    async def test_filter_by_material_id(
        self,
        db_session: AsyncSession,
        seeded_conflicts: tuple[uuid.UUID, list[ConflictRecord]],
    ) -> None:
        material_id, _ = seeded_conflicts
        pipeline = FusionPipeline(db_session)
        results = await pipeline.get_conflicts(
            material_id=str(material_id),
        )

        assert len(results) >= 2
        for r in results:
            assert r.material_id == material_id

    @pytest.mark.asyncio
    async def test_filter_by_property_type(
        self,
        db_session: AsyncSession,
        seeded_conflicts: tuple[uuid.UUID, list[ConflictRecord]],
    ) -> None:
        material_id, _ = seeded_conflicts
        pipeline = FusionPipeline(db_session)
        results = await pipeline.get_conflicts(
            material_id=str(material_id),
            property_type="density",
        )

        assert len(results) >= 1
        for r in results:
            assert r.property_type == "density"

    @pytest.mark.asyncio
    async def test_filter_by_status(
        self,
        db_session: AsyncSession,
        seeded_conflicts: tuple[uuid.UUID, list[ConflictRecord]],
    ) -> None:
        material_id, _ = seeded_conflicts
        pipeline = FusionPipeline(db_session)
        results = await pipeline.get_conflicts(
            material_id=str(material_id),
            status=ConflictStatus.PENDING,
        )

        assert len(results) >= 1
        for r in results:
            assert r.status == ConflictStatus.PENDING

    @pytest.mark.asyncio
    async def test_filter_by_nonexistent_material(
        self,
        db_session: AsyncSession,
    ) -> None:
        pipeline = FusionPipeline(db_session)
        results = await pipeline.get_conflicts(
            material_id=str(uuid.uuid4()),
        )
        assert results == []


# ---------------------------------------------------------------------------
# FusionPipeline.resolve_conflict() — async manual resolution tests
# ---------------------------------------------------------------------------

class TestFusionPipelineResolveConflict:
    """Tests for FusionPipeline.resolve_conflict() manual resolution."""

    @pytest.fixture
    async def pending_conflict(
        self,
        db_session: AsyncSession,
    ) -> ConflictRecord:
        """Create a pending ConflictRecord for resolution tests."""
        material_id = await _seed_material(db_session)
        record = ConflictRecord(
            material_id=material_id,
            property_type="density",
            status=ConflictStatus.PENDING,
            resolution_strategy="manual",
            conflicting_values=[
                {"value": 10.0, "source_id": "src-a"},
                {"value": 11.0, "source_id": "src-b"},
            ],
        )
        db_session.add(record)
        await db_session.flush()
        return record

    @pytest.mark.asyncio
    async def test_resolve_pending_conflict(
        self,
        db_session: AsyncSession,
        pending_conflict: ConflictRecord,
    ) -> None:
        pipeline = FusionPipeline(db_session)
        resolved = await pipeline.resolve_conflict(
            conflict_id=str(pending_conflict.id),
            resolved_value={"value": 11.0, "source_id": "src-b"},
            resolution_reason="Expert review selected src-b value",
            resolved_by=str(uuid.uuid4()),
        )

        assert resolved.status == ConflictStatus.MANUALLY_RESOLVED
        assert resolved.resolved_value["value"] == 11.0
        assert resolved.resolution_reason == "Expert review selected src-b value"
        assert resolved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_raises(
        self,
        db_session: AsyncSession,
    ) -> None:
        pipeline = FusionPipeline(db_session)
        with pytest.raises(ValueError, match="not found"):
            await pipeline.resolve_conflict(
                conflict_id=str(uuid.uuid4()),
                resolved_value={"value": 10.0},
                resolution_reason="test",
            )

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_raises(
        self,
        db_session: AsyncSession,
        pending_conflict: ConflictRecord,
    ) -> None:
        pipeline = FusionPipeline(db_session)

        await pipeline.resolve_conflict(
            conflict_id=str(pending_conflict.id),
            resolved_value={"value": 10.0},
            resolution_reason="first",
        )

        with pytest.raises(ValueError, match="already resolved"):
            await pipeline.resolve_conflict(
                conflict_id=str(pending_conflict.id),
                resolved_value={"value": 11.0},
                resolution_reason="second",
            )

    @pytest.mark.asyncio
    async def test_resolve_without_resolver_id(
        self,
        db_session: AsyncSession,
        pending_conflict: ConflictRecord,
    ) -> None:
        pipeline = FusionPipeline(db_session)
        resolved = await pipeline.resolve_conflict(
            conflict_id=str(pending_conflict.id),
            resolved_value={"value": 10.0},
            resolution_reason="system auto-resolve",
        )

        assert resolved.resolved_by is None
        assert resolved.status == ConflictStatus.MANUALLY_RESOLVED
