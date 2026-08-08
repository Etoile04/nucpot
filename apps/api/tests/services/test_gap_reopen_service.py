"""Tests for gap auto-reopen service (NFM-2582).

Covers:
1. Gap with wont_fix status gets reopened when matching extraction succeeds.
2. Gap stays wont_fix when extraction still finds no data for it.
3. Gaps without matching extraction are unaffected.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from nfm_db.models.knowledge_gap import GapStatus, KnowledgeGap
from nfm_db.services.gap_reopen_service import (
    _build_target_key,
    check_and_reopen_wont_fix_gaps,
)

# ---------------------------------------------------------------------------
# _build_target_key unit tests
# ---------------------------------------------------------------------------


class TestBuildTargetKey:
    """Unit tests for the target key builder helper."""

    def test_property_key(self) -> None:
        key = _build_target_key("property", {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "thermal_conductivity",
        })
        assert key == "UO2/FCC/thermal_conductivity"

    def test_property_key_none_phase(self) -> None:
        key = _build_target_key("property", {
            "element_system": "Zr",
            "phase": None,
            "property_name": "bulk_modulus",
        })
        assert key == "Zr//bulk_modulus"

    def test_property_key_fallback_to_property(self) -> None:
        key = _build_target_key("property", {
            "element_system": "U",
            "phase": "BCC",
            "property": "lattice_constant",
        })
        assert key == "U/BCC/lattice_constant"

    def test_entity_key(self) -> None:
        key = _build_target_key("entity", {
            "entity_name": "Uranium",
        })
        assert key == "entity:Uranium"

    def test_entity_key_fallback_to_name(self) -> None:
        key = _build_target_key("entity", {
            "name": "Plutonium",
        })
        assert key == "entity:Plutonium"

    def test_relation_key(self) -> None:
        key = _build_target_key("relation", {
            "source": "UO2",
            "relation_type": "HAS_PROPERTY",
            "target": "thermal_conductivity",
        })
        assert key == "UO2:HAS_PROPERTY:thermal_conductivity"

    def test_unknown_type_returns_empty(self) -> None:
        key = _build_target_key("unknown_type", {})
        assert key == ""


# ---------------------------------------------------------------------------
# check_and_reopen_wont_fix_gaps integration tests
# ---------------------------------------------------------------------------


async def _seed_wont_fix_gap(
    gap_type: str,
    target_key: str,
    *,
    session,
) -> KnowledgeGap:
    """Create and return a wont_fix gap."""
    gap = KnowledgeGap(
        gap_type=gap_type,
        target_key=target_key,
        status=GapStatus.WONT_FIX.value,
        ontology_version_id=None,
        audit_note="Original wont_fix marking",
    )
    session.add(gap)
    await session.flush()
    return gap


async def _seed_open_gap(
    gap_type: str,
    target_key: str,
    *,
    session,
) -> KnowledgeGap:
    """Create and return an open gap (should not be affected)."""
    gap = KnowledgeGap(
        gap_type=gap_type,
        target_key=target_key,
        status=GapStatus.OPEN.value,
        ontology_version_id=None,
    )
    session.add(gap)
    await session.flush()
    return gap


@pytest.mark.asyncio
async def test_reopen_matching_wont_fix_gap(db_session) -> None:
    """AC: Gap with wont_fix status gets reopened when matching extraction succeeds."""
    gap = await _seed_wont_fix_gap(
        "property",
        "UO2/FCC/thermal_conductivity",
        session=db_session,
    )

    ontology_version_id = uuid.uuid4()
    extraction_results = [
        {
            "item_type": "property",
            "item_data": {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "thermal_conductivity",
            },
        },
    ]

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=ontology_version_id,
        extraction_results=extraction_results,
    )

    assert result.gaps_checked == 1
    assert result.gaps_reopened == 1
    assert "UO2/FCC/thermal_conductivity" in result.reopened_keys

    await db_session.refresh(gap)
    assert gap.status == GapStatus.OPEN.value
    assert gap.audit_note is not None
    assert "Auto-reopened" in gap.audit_note
    assert str(ontology_version_id) in gap.audit_note
    assert gap.resolved_at is None
    assert gap.resolved_by is None


@pytest.mark.asyncio
async def test_stays_wont_fix_when_no_matching_extraction(db_session) -> None:
    """AC: Gap stays wont_fix when extraction still finds no data."""
    gap_match = await _seed_wont_fix_gap(
        "property",
        "UO2/FCC/lattice_constant",
        session=db_session,
    )
    gap_no_match = await _seed_wont_fix_gap(
        "property",
        "Zr/HCP/thermal_conductivity",
        session=db_session,
    )

    extraction_results = [
        {
            "item_type": "property",
            "item_data": {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "lattice_constant",
            },
        },
    ]

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=uuid.uuid4(),
        extraction_results=extraction_results,
    )

    assert result.gaps_checked == 2
    assert result.gaps_reopened == 1
    assert "UO2/FCC/lattice_constant" in result.reopened_keys
    assert "Zr/HCP/thermal_conductivity" not in result.reopened_keys

    await db_session.refresh(gap_match)
    assert gap_match.status == GapStatus.OPEN.value

    await db_session.refresh(gap_no_match)
    assert gap_no_match.status == GapStatus.WONT_FIX.value
    assert gap_no_match.audit_note == "Original wont_fix marking"


@pytest.mark.asyncio
async def test_unaffected_gaps_unchanged(db_session) -> None:
    """AC: Gaps without matching extraction are unaffected."""
    wont_fix = await _seed_wont_fix_gap(
        "property",
        "U/BCC/thermal_conductivity",
        session=db_session,
    )
    open_gap = await _seed_open_gap(
        "property",
        "UO2/FCC/bulk_modulus",
        session=db_session,
    )

    extraction_results = [
        {
            "item_type": "property",
            "item_data": {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "density",
            },
        },
    ]

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=uuid.uuid4(),
        extraction_results=extraction_results,
    )

    assert result.gaps_checked == 1
    assert result.gaps_reopened == 0
    assert result.reopened_keys == ()

    await db_session.refresh(wont_fix)
    assert wont_fix.status == GapStatus.WONT_FIX.value
    assert wont_fix.audit_note == "Original wont_fix marking"

    await db_session.refresh(open_gap)
    assert open_gap.status == GapStatus.OPEN.value


@pytest.mark.asyncio
async def test_empty_extraction_results(db_session) -> None:
    """Empty extraction results → no gaps checked, no reopens."""
    await _seed_wont_fix_gap(
        "property",
        "UO2/FCC/thermal_conductivity",
        session=db_session,
    )

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=uuid.uuid4(),
        extraction_results=[],
    )

    assert result.gaps_checked == 0
    assert result.gaps_reopened == 0


@pytest.mark.asyncio
async def test_multiple_reopens(db_session) -> None:
    """Multiple wont_fix gaps with matching extraction are all reopened."""
    await _seed_wont_fix_gap("property", "UO2/FCC/lattice_constant", session=db_session)
    await _seed_wont_fix_gap("property", "UO2/FCC/bulk_modulus", session=db_session)
    await _seed_wont_fix_gap("property", "Zr/HCP/lattice_constant", session=db_session)

    extraction_results = [
        {
            "item_type": "property",
            "item_data": {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "lattice_constant",
            },
        },
        {
            "item_type": "property",
            "item_data": {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "bulk_modulus",
            },
        },
    ]

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=uuid.uuid4(),
        extraction_results=extraction_results,
    )

    assert result.gaps_checked == 3
    assert result.gaps_reopened == 2

    stmt = select(KnowledgeGap).where(
        KnowledgeGap.status == GapStatus.OPEN.value,
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_entity_type_gap_reopen(db_session) -> None:
    """Entity-type wont_fix gap is reopened by matching entity extraction."""
    await _seed_wont_fix_gap("entity", "entity:Uranium", session=db_session)

    extraction_results = [
        {
            "item_type": "entity",
            "item_data": {
                "entity_name": "Uranium",
            },
        },
    ]

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=uuid.uuid4(),
        extraction_results=extraction_results,
    )

    assert result.gaps_reopened == 1
    assert "entity:Uranium" in result.reopened_keys


@pytest.mark.asyncio
async def test_relation_type_gap_reopen(db_session) -> None:
    """Relation-type wont_fix gap is reopened by matching relation extraction."""
    await _seed_wont_fix_gap(
        "relation",
        "UO2:HAS_PROPERTY:thermal_conductivity",
        session=db_session,
    )

    extraction_results = [
        {
            "item_type": "relation",
            "item_data": {
                "source": "UO2",
                "relation_type": "HAS_PROPERTY",
                "target": "thermal_conductivity",
            },
        },
    ]

    result = await check_and_reopen_wont_fix_gaps(
        db_session,
        new_ontology_version_id=uuid.uuid4(),
        extraction_results=extraction_results,
    )

    assert result.gaps_reopened == 1
    assert "UO2:HAS_PROPERTY:thermal_conductivity" in result.reopened_keys
