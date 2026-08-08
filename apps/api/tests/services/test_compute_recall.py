"""Tests for compute_recall() (NFM-2614 / NFM-2575-T4).

Recall rate computation: reads ontology schema + gap records to produce
aggregated coverage metrics per ontology version.

Covers:
1. All gaps open → recall_rate reflects all missing.
2. All gaps filled → recall_rate = 1.0.
3. Mixed gap statuses → recall_rate = (total - open - filling) / total.
4. Wont_fix gaps count as covered (not subtracted).
5. Filling gaps count as uncovered (subtracted like open).
6. 0 expected properties → recall_rate = 1.0.
7. Ontology version not found → ValueError.
8. No gaps at all → recall_rate = 1.0.
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import ExtractionGap, OntologyVersion
from nfm_db.services.gap_scanner import (
    compute_recall,
    extract_entity_types,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")

_ONTOLOGY_DATA_2_PROPS = {
    "entity_types": [
        {
            "name": "NuclearMaterial",
            "properties": [
                {"name": "density", "datatype": "float"},
                {"name": "melting_point", "datatype": "float"},
            ],
        },
    ],
    "relation_types": [],
}

_ONTOLOGY_DATA_0_PROPS = {
    "entity_types": [],
    "relation_types": [],
}

_ONTOLOGY_DATA_4_PROPS = {
    "entity_types": [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "melting_point"],
        },
        {
            "name": "Isotope",
            "properties": [
                {"name": "half_life", "datatype": "float"},
                {"name": "decay_mode", "datatype": "string"},
            ],
        },
    ],
    "relation_types": [],
}


async def _seed_version(
    session,
    *,
    ontology_data: dict | None = None,
    version_id: uuid.UUID | None = None,
) -> OntologyVersion:
    """Create a fresh OntologyVersion row."""
    version = OntologyVersion(
        id=version_id or uuid.uuid4(),
        version="1.0.0",
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data=ontology_data or _ONTOLOGY_DATA_2_PROPS,
    )
    session.add(version)
    await session.flush()
    await session.refresh(version)
    return version


async def _seed_gap(
    session,
    *,
    ontology_version_id: uuid.UUID,
    entity_type: str = "NuclearMaterial",
    property_name: str = "density",
    gap_status: str = "open",
) -> ExtractionGap:
    """Insert a minimal ExtractionGap row."""
    gap = ExtractionGap(
        id=uuid.uuid4(),
        ontology_version_id=ontology_version_id,
        entity_type=entity_type,
        property=property_name,
        gap_status=gap_status,
    )
    session.add(gap)
    await session.flush()
    return gap


# ---------------------------------------------------------------------------
# Unit tests — compute_recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_gaps_open_recall_reflects_missing(db_session) -> None:
    """2 expected props, 2 open gaps → recall = 0.0."""
    ov = await _seed_version(db_session)
    await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="melting_point",
        gap_status="open",
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.total_expected == 2
    assert metrics.total_gaps == 2
    assert metrics.open_gaps == 2
    assert metrics.filled_gaps == 0
    assert metrics.wont_fix_gaps == 0
    assert metrics.recall_rate == 0.0


@pytest.mark.asyncio
async def test_all_gaps_filled_recall_is_one(db_session) -> None:
    """2 expected props, 2 filled gaps → recall = 1.0."""
    ov = await _seed_version(db_session)
    await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filled",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="melting_point",
        gap_status="filled",
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.total_expected == 2
    assert metrics.filled_gaps == 2
    assert metrics.open_gaps == 0
    assert metrics.recall_rate == 1.0


@pytest.mark.asyncio
async def test_mixed_statuses_recall_correct(db_session) -> None:
    """4 expected, 1 open + 1 filling + 1 filled + 1 wont_fix → recall=0.5."""
    ov = await _seed_version(
        db_session, ontology_data=_ONTOLOGY_DATA_4_PROPS,
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="density",
        gap_status="open",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="melting_point",
        gap_status="filling",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        entity_type="Isotope",
        property_name="half_life",
        gap_status="filled",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        entity_type="Isotope",
        property_name="decay_mode",
        gap_status="wont_fix",
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.total_expected == 4
    assert metrics.total_gaps == 4
    assert metrics.open_gaps == 1
    assert metrics.filled_gaps == 1
    assert metrics.wont_fix_gaps == 1
    # recall = (4 - 1 open - 1 filling) / 4 = 0.5
    assert metrics.recall_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_wont_fix_counts_as_covered(db_session) -> None:
    """wont_fix gaps should NOT be subtracted from recall numerator."""
    ov = await _seed_version(db_session)
    await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="wont_fix",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="melting_point",
        gap_status="wont_fix",
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.wont_fix_gaps == 2
    assert metrics.recall_rate == 1.0


@pytest.mark.asyncio
async def test_filling_counts_as_uncovered(db_session) -> None:
    """filling gaps should be subtracted like open gaps."""
    ov = await _seed_version(db_session)
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        gap_status="filling",
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.open_gaps == 0
    assert metrics.recall_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_zero_expected_properties_recall_is_one(db_session) -> None:
    """Edge case: empty ontology → recall_rate = 1.0."""
    ov = await _seed_version(
        db_session, ontology_data=_ONTOLOGY_DATA_0_PROPS,
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.total_expected == 0
    assert metrics.total_gaps == 0
    assert metrics.recall_rate == 1.0


@pytest.mark.asyncio
async def test_ontology_version_not_found_raises_valueerror(
    db_session,
) -> None:
    """Unknown ontology_version_id → ValueError."""
    with pytest.raises(ValueError, match="OntologyVersion not found"):
        await compute_recall(db_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_no_gaps_at_all_recall_is_one(db_session) -> None:
    """2 expected props, 0 gaps → recall = 1.0."""
    ov = await _seed_version(db_session)

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.total_expected == 2
    assert metrics.total_gaps == 0
    assert metrics.open_gaps == 0
    assert metrics.recall_rate == 1.0


@pytest.mark.asyncio
async def test_metrics_contains_computed_at_timestamp(db_session) -> None:
    """computed_at should be a recent datetime."""
    ov = await _seed_version(db_session)

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.computed_at is not None
    assert metrics.ontology_version_id == ov.id


@pytest.mark.asyncio
async def test_partial_gaps_only_some_properties_have_gaps(
    db_session,
) -> None:
    """4 expected props, 1 open gap → recall = 0.75."""
    ov = await _seed_version(
        db_session, ontology_data=_ONTOLOGY_DATA_4_PROPS,
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="density",
        gap_status="open",
    )

    metrics = await compute_recall(db_session, ov.id)

    assert metrics.total_expected == 4
    assert metrics.total_gaps == 1
    assert metrics.open_gaps == 1
    assert metrics.recall_rate == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Unit tests — extract_entity_types helper
# ---------------------------------------------------------------------------


def test_extract_entity_types_normal() -> None:
    ov = OntologyVersion(
        version="1.0",
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data=_ONTOLOGY_DATA_2_PROPS,
    )
    entity_types = extract_entity_types(ov)
    assert len(entity_types) == 1
    assert entity_types[0]["name"] == "NuclearMaterial"


def test_extract_entity_types_none_data() -> None:
    ov = OntologyVersion(
        version="1.0",
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data=None,
    )
    assert extract_entity_types(ov) == []


def test_extract_entity_types_empty_list() -> None:
    ov = OntologyVersion(
        version="1.0",
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data={"entity_types": [], "relation_types": []},
    )
    assert extract_entity_types(ov) == []
