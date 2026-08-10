"""Tests for LiteratureFillPath (NFM-2649)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.source import DataSource
from nfm_db.services.paths.literature_fill import (
    HANDLED_PREFERENCES,
    LiteratureFillPath,
    _build_search_keywords,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    source_preference: str = "literature",
    entity_type: str = "NuclearMaterial",
    property: str = "thermal_conductivity",
    material_system: str = "UO2",
    ontology_version_id: uuid.UUID | None = None,
) -> DataCollectionRequest:
    """Build a DataCollectionRequest for testing (not persisted)."""
    return DataCollectionRequest(
        id=uuid.uuid4(),
        ontology_version_id=ontology_version_id or uuid.uuid4(),
        entity_type=entity_type,
        property=property,
        material_system=material_system,
        urgency=1,
        source_preference=source_preference,
        status="open",
        requested_at=datetime.now(UTC),
        metadata_=None,
    )


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pref", ["literature", "any"])
def test_can_handle_accepts_supported_preferences(pref: str) -> None:
    """can_handle returns True for literature and any."""
    handler = LiteratureFillPath(session=None)  # type: ignore[arg-type]
    assert handler.can_handle(pref) is True


@pytest.mark.parametrize("pref", ["dft", "external_db", "unknown", ""])
def test_can_handle_rejects_unsupported_preferences(pref: str) -> None:
    """can_handle returns False for non-literature preferences."""
    handler = LiteratureFillPath(session=None)  # type: ignore[arg-type]
    assert handler.can_handle(pref) is False


def test_handled_preferences_constant() -> None:
    """HANDLED_PREFERENCES is the canonical set."""
    assert frozenset({"literature", "any"}) == HANDLED_PREFERENCES


# ---------------------------------------------------------------------------
# _build_search_keywords
# ---------------------------------------------------------------------------


def test_build_search_keywords_includes_material() -> None:
    """Bare material_system is always included."""
    keywords = _build_search_keywords(
        entity_type="NuclearMaterial",
        property_name="thermal_conductivity",
        material_system="UO2",
    )
    assert "UO2" in keywords


def test_build_search_keywords_includes_combinations() -> None:
    """All four combinations are produced."""
    keywords = _build_search_keywords(
        entity_type="NuclearMaterial",
        property_name="thermal_conductivity",
        material_system="UO2",
    )
    assert "UO2 thermal_conductivity" in keywords
    assert "NuclearMaterial thermal_conductivity" in keywords
    assert "thermal_conductivity UO2" in keywords


def test_build_search_keywords_dedupes() -> None:
    """Duplicate keywords are filtered out."""
    keywords = _build_search_keywords(
        entity_type="U",
        property_name="U",
        material_system="U",
    )
    assert len(keywords) == len(set(keywords))


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_placeholder_datasource(db_session) -> None:
    """execute persists a DataSource placeholder."""
    request = _make_request()
    handler = LiteratureFillPath(session=db_session)

    result = await handler.execute(request)

    assert result.success is True
    assert result.path == "literature"
    assert result.data_found is False
    assert result.error is None
    # Reference is the new DataSource UUID.
    assert result.reference == result.metadata["data_source_id"]
    uuid.UUID(result.reference)  # valid UUID


@pytest.mark.asyncio
async def test_execute_placeholder_metadata_includes_keywords(db_session) -> None:
    """Persisted DataSource has search_keywords in metadata_."""
    request = _make_request()
    handler = LiteratureFillPath(session=db_session)

    await handler.execute(request)

    rows = (await db_session.execute(select(DataSource))).scalars().all()
    assert len(rows) == 1
    ds = rows[0]
    assert ds.source_type == "placeholder"
    assert ds.metadata_ is not None
    assert ds.metadata_["kind"] == "literature_search_placeholder"
    assert ds.metadata_["data_collection_request_id"] == str(request.id)
    assert "search_keywords" in ds.metadata_
    assert isinstance(ds.metadata_["search_keywords"], list)
    assert len(ds.metadata_["search_keywords"]) > 0


@pytest.mark.asyncio
async def test_execute_placeholder_title_includes_triple(db_session) -> None:
    """DataSource title identifies the gap triple."""
    request = _make_request(
        entity_type="Isotope",
        property="half_life",
        material_system="U-235",
    )
    handler = LiteratureFillPath(session=db_session)

    await handler.execute(request)

    row = (await db_session.execute(select(DataSource))).scalars().one()
    assert "Isotope" in row.title
    assert "half_life" in row.title
    assert "U-235" in row.title


@pytest.mark.asyncio
async def test_execute_uses_session_for_persistence(db_session) -> None:
    """The handler calls session.add + flush, leaving the row in the session."""
    request = _make_request()
    handler = LiteratureFillPath(session=db_session)

    result = await handler.execute(request)

    # Same UUID must be retrievable from the session.
    ds = await db_session.get(DataSource, uuid.UUID(result.reference))
    assert ds is not None
    assert ds.source_type == "placeholder"


@pytest.mark.asyncio
async def test_execute_metadata_round_trip(db_session) -> None:
    """Handler returns metadata dict with the request triple + keywords."""
    request = _make_request(
        entity_type="NuclearMaterial",
        property="density",
        material_system="Zr",
    )
    handler = LiteratureFillPath(session=db_session)

    result = await handler.execute(request)

    assert result.metadata["entity_type"] == "NuclearMaterial"
    assert result.metadata["property"] == "density"
    assert result.metadata["material_system"] == "Zr"
    assert result.metadata["search_keywords"] == _build_search_keywords(
        entity_type="NuclearMaterial",
        property_name="density",
        material_system="Zr",
    )
