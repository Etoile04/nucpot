"""Tests for the EntityMergeLog ORM model (NFM-1391, B3.1.1).

Covers the 7-column dedup merge decision log table that records when
a duplicate material is merged into a canonical material via the
entity dedup engine (B3.1).

Mirrors the layout used in test_phase1_models.py for consistency.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import EntityMergeLog, MatchMethod
from nfm_db.models.entity_merge import EntityMergeLog as DirectImport
from nfm_db.models.material import Material, MaterialCategory


class TestMatchMethodEnum:
    """MatchMethod StrEnum contract."""

    def test_exact_value(self) -> None:
        assert MatchMethod.EXACT.value == "exact"

    def test_fuzzy_value(self) -> None:
        assert MatchMethod.FUZZY.value == "fuzzy"

    def test_semantic_value(self) -> None:
        assert MatchMethod.SEMANTIC.value == "semantic"

    def test_inherits_from_str(self) -> None:
        # StrEnum: each value can be used as its underlying string
        assert MatchMethod.EXACT == "exact"
        assert MatchMethod.FUZZY == "fuzzy"
        assert MatchMethod.SEMANTIC == "semantic"

    def test_member_count(self) -> None:
        assert len(MatchMethod) == 3


class TestEntityMergeLogMetadata:
    """Static metadata (tablename, columns)."""

    def test_tablename(self) -> None:
        assert EntityMergeLog.__tablename__ == "entity_merge_log"

    def test_direct_module_import(self) -> None:
        # AC: Model exported from nfm_db.models.__init__.py
        # Re-export from module must be the same class
        assert DirectImport is EntityMergeLog

    def test_seven_columns(self) -> None:
        columns = {c.name for c in EntityMergeLog.__table__.columns}
        expected = {
            "id",
            "canonical_id",
            "merged_id",
            "match_score",
            "match_method",
            "merged_at",
            "details",
        }
        assert columns == expected


class TestEntityMergeLogCreation:
    """EntityMergeLog insertion & persistence behavior."""

    @pytest.mark.asyncio
    async def test_create_minimal_merge_log(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A merge log can be persisted with required columns."""
        category = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(category)
        await db_session.flush()

        canonical = Material(name="UO2", formula="UO2", category_id=category.id)
        merged = Material(name="Uranium dioxide", formula="UO2", category_id=category.id)
        db_session.add_all([canonical, merged])
        await db_session.flush()

        log = EntityMergeLog(
            canonical_id=canonical.id,
            merged_id=merged.id,
            match_score=0.97,
            match_method=MatchMethod.FUZZY,
        )
        db_session.add(log)
        await db_session.commit()
        await db_session.refresh(log)

        assert log.id is not None
        assert isinstance(log.id, uuid.UUID)
        assert log.canonical_id == canonical.id
        assert log.merged_id == merged.id
        assert log.match_score == 0.97
        assert log.match_method == MatchMethod.FUZZY
        assert log.merged_at is not None
        assert log.details is None

    @pytest.mark.asyncio
    async def test_create_with_details_jsonb(
        self,
        db_session: AsyncSession,
    ) -> None:
        """details (JSONB) accepts a free-form dict payload."""
        category = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(category)
        await db_session.flush()

        canonical = Material(name="UO2", formula="UO2", category_id=category.id)
        merged = Material(name="UO2 dup", formula="UO2", category_id=category.id)
        db_session.add_all([canonical, merged])
        await db_session.flush()

        payload = {
            "matched_aliases": ["Urania", "Uranium oxide"],
            "edit_distance": 2,
            "rule_version": "v1",
        }
        log = EntityMergeLog(
            canonical_id=canonical.id,
            merged_id=merged.id,
            match_score=0.88,
            match_method=MatchMethod.SEMANTIC,
            details=payload,
        )
        db_session.add(log)
        await db_session.commit()
        await db_session.refresh(log)

        assert log.details == payload

    @pytest.mark.asyncio
    async def test_fk_canonical_required(
        self,
        db_session: AsyncSession,
    ) -> None:
        """canonical_id is NOT NULL — empty UUID must fail at flush time."""
        bogus_id = uuid.uuid4()
        log = EntityMergeLog(
            canonical_id=bogus_id,
            merged_id=bogus_id,
            match_score=0.5,
            match_method=MatchMethod.EXACT,
        )
        db_session.add(log)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_fk_merged_required(
        self,
        db_session: AsyncSession,
    ) -> None:
        """merged_id is NOT NULL FK to materials.id."""
        category = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(category)
        await db_session.flush()

        canonical = Material(name="UO2", formula="UO2", category_id=category.id)
        db_session.add(canonical)
        await db_session.flush()

        bogus_merged = uuid.uuid4()
        log = EntityMergeLog(
            canonical_id=canonical.id,
            merged_id=bogus_merged,
            match_score=0.5,
            match_method=MatchMethod.EXACT,
        )
        db_session.add(log)
        with pytest.raises(IntegrityError):
            await db_session.flush()
