"""Tests for DFTFillPath (NFM-2649)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.dft_calculation import DFTCalculation
from nfm_db.services.paths.dft_fill import (
    DEFAULT_CUTOFF_EV,
    DEFAULT_FUNCTIONAL,
    HANDLED_PREFERENCES,
    DFTFillPath,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    source_preference: str = "dft",
    entity_type: str = "NuclearMaterial",
    property: str = "bulk_modulus",
    material_system: str = "UO2",
    urgency: int = 1,
) -> DataCollectionRequest:
    """Build a DataCollectionRequest for testing (not persisted)."""
    return DataCollectionRequest(
        id=uuid.uuid4(),
        ontology_version_id=uuid.uuid4(),
        entity_type=entity_type,
        property=property,
        material_system=material_system,
        urgency=urgency,
        source_preference=source_preference,
        status="open",
        requested_at=datetime.now(UTC),
        metadata_=None,
    )


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pref", ["dft", "any"])
def test_can_handle_accepts_supported_preferences(pref: str) -> None:
    """can_handle returns True for dft and any."""
    handler = DFTFillPath(session=None)  # type: ignore[arg-type]
    assert handler.can_handle(pref) is True


@pytest.mark.parametrize("pref", ["literature", "external_db", "unknown", ""])
def test_can_handle_rejects_unsupported_preferences(pref: str) -> None:
    """can_handle returns False for non-dft preferences."""
    handler = DFTFillPath(session=None)  # type: ignore[arg-type]
    assert handler.can_handle(pref) is False


def test_handled_preferences_constant() -> None:
    """HANDLED_PREFERENCES is the canonical set."""
    assert frozenset({"dft", "any"}) == HANDLED_PREFERENCES


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_dft_calculation(db_session) -> None:
    """execute persists a DFTCalculation row."""
    request = _make_request()
    handler = DFTFillPath(session=db_session)

    result = await handler.execute(request)

    assert result.success is True
    assert result.path == "dft"
    assert result.data_found is False
    assert result.error is None
    uuid.UUID(result.reference)  # DFT calc id is a valid UUID


@pytest.mark.asyncio
async def test_execute_calculation_id_prefix(db_session) -> None:
    """calculation_id is prefixed with 'gap-' for traceability."""
    request = _make_request()
    handler = DFTFillPath(session=db_session)

    result = await handler.execute(request)

    assert result.metadata["calculation_id"] == f"gap-{request.id}"


@pytest.mark.asyncio
async def test_execute_marks_placeholder(db_session) -> None:
    """Result metadata and computation_metadata flag placeholder=True."""
    request = _make_request()
    handler = DFTFillPath(session=db_session)

    result = await handler.execute(request)

    # Result metadata
    assert result.metadata["placeholder"] is True
    assert result.metadata["status"] == "pending"

    # Persisted computation_metadata
    row = (await db_session.execute(select(DFTCalculation))).scalars().one()
    assert row.computation_metadata is not None
    assert row.computation_metadata["placeholder"] is True
    assert row.computation_metadata["data_collection_request_id"] == str(
        request.id,
    )
    assert row.computation_metadata["entity_type"] == request.entity_type
    assert row.computation_metadata["property"] == request.property
    assert row.computation_metadata["material_system"] == request.material_system


@pytest.mark.asyncio
async def test_execute_uses_default_functional_and_cutoff(db_session) -> None:
    """Defaults match the dispatch-service defaults."""
    request = _make_request()
    handler = DFTFillPath(session=db_session)

    await handler.execute(request)

    row = (await db_session.execute(select(DFTCalculation))).scalars().one()
    assert row.functional == DEFAULT_FUNCTIONAL
    assert float(row.cutoff_energy) == pytest.approx(DEFAULT_CUTOFF_EV)
    assert row.status == "pending"
    assert row.source == "gap_dispatch"


@pytest.mark.asyncio
async def test_execute_notes_mention_request(db_session) -> None:
    """Notes text names the entity/property/material triple."""
    request = _make_request(
        entity_type="Isotope",
        property="half_life",
        material_system="U-235",
    )
    handler = DFTFillPath(session=db_session)

    await handler.execute(request)

    row = (await db_session.execute(select(DFTCalculation))).scalars().one()
    assert row.notes is not None
    assert "Isotope" in row.notes
    assert "half_life" in row.notes
    assert "U-235" in row.notes


@pytest.mark.asyncio
async def test_execute_persists_urgency(db_session) -> None:
    """computation_metadata carries the request urgency."""
    request = _make_request(urgency=7)
    handler = DFTFillPath(session=db_session)

    await handler.execute(request)

    row = (await db_session.execute(select(DFTCalculation))).scalars().one()
    assert row.computation_metadata is not None
    assert row.computation_metadata["urgency"] == 7


@pytest.mark.asyncio
async def test_execute_reference_is_dft_id(db_session) -> None:
    """DispatchResult.reference is the DFTCalculation UUID."""
    request = _make_request()
    handler = DFTFillPath(session=db_session)

    result = await handler.execute(request)

    assert result.metadata["dft_calculation_id"] == result.reference
    # Reference must be retrievable via db_session.get
    calc = await db_session.get(DFTCalculation, uuid.UUID(result.reference))
    assert calc is not None
