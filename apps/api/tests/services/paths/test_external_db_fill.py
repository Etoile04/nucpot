"""Tests for ExternalDBFillPath (NFM-2649)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.paths.external_db_fill import (
    HANDLED_PREFERENCES,
    ExternalDBFillPath,
    _is_meaningful_result,
    _reference_for,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    source_preference: str = "external_db",
    entity_type: str = "NuclearMaterial",
    property: str = "thermal_conductivity",
    material_system: str = "UO2",
) -> DataCollectionRequest:
    """Build a DataCollectionRequest for testing (not persisted)."""
    return DataCollectionRequest(
        id=uuid.uuid4(),
        ontology_version_id=uuid.uuid4(),
        entity_type=entity_type,
        property=property,
        material_system=material_system,
        urgency=1,
        source_preference=source_preference,
        status="open",
        requested_at=datetime.now(UTC),
        metadata_=None,
    )


def _fake_client(
    *,
    nist: object = None,
    openkim: object = None,
    mp: object = None,
) -> MagicMock:
    """Build a mock ExternalDataSourceClient with the three query methods."""
    client = MagicMock()
    client.query_nist_ipr = AsyncMock(return_value=nist)
    client.query_openkim = AsyncMock(return_value=openkim)
    client.query_materials_project = AsyncMock(return_value=mp)
    client.close = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pref", ["external_db", "any"])
def test_can_handle_accepts_supported_preferences(pref: str) -> None:
    """can_handle returns True for external_db and any."""
    handler = ExternalDBFillPath(session=None)  # type: ignore[arg-type]
    assert handler.can_handle(pref) is True


@pytest.mark.parametrize("pref", ["literature", "dft", "unknown", ""])
def test_can_handle_rejects_unsupported_preferences(pref: str) -> None:
    """can_handle returns False for non-external_db preferences."""
    handler = ExternalDBFillPath(session=None)  # type: ignore[arg-type]
    assert handler.can_handle(pref) is False


def test_handled_preferences_constant() -> None:
    """HANDLED_PREFERENCES is the canonical set."""
    assert frozenset({"external_db", "any"}) == HANDLED_PREFERENCES


# ---------------------------------------------------------------------------
# _reference_for helper
# ---------------------------------------------------------------------------


def test_reference_for_combines_source_and_query() -> None:
    """Reference is 'source:query_id'."""
    assert _reference_for("nist_ipr", "abc-123") == "nist_ipr:abc-123"


def test_reference_for_uses_none_prefix() -> None:
    """None source maps to 'none:' prefix."""
    assert _reference_for("none", "abc-123") == "none:abc-123"


# ---------------------------------------------------------------------------
# _is_meaningful_result
# ---------------------------------------------------------------------------


def test_is_meaningful_result_none() -> None:
    """None is not meaningful."""
    assert _is_meaningful_result(None) is False


def test_is_meaningful_result_dict() -> None:
    """Any non-None value is meaningful."""
    assert _is_meaningful_result({"any": "thing"}) is True
    assert _is_meaningful_result([]) is True
    assert _is_meaningful_result(0) is True
    assert _is_meaningful_result("") is True


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_queries_all_three_sources(db_session) -> None:
    """execute calls all three external DB sources."""
    request = _make_request()
    client = _fake_client(
        nist={"values": []},
        openkim={"potentials": []},
        mp={"materials": []},
    )

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        await handler.execute(request)

    client.query_nist_ipr.assert_awaited_once()
    client.query_openkim.assert_awaited_once()
    client.query_materials_project.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_uses_request_formula_and_property(db_session) -> None:
    """All three queries receive material_system and property."""
    request = _make_request(material_system="Zr", property="density")
    client = _fake_client()

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        await handler.execute(request)

    # NIST + MP use formula; OpenKIM uses species (same value).
    nist_kwargs = client.query_nist_ipr.await_args.kwargs
    mp_kwargs = client.query_materials_project.await_args.kwargs
    ok_kwargs = client.query_openkim.await_args.kwargs

    assert nist_kwargs["formula"] == "Zr"
    assert nist_kwargs["property_name"] == "density"
    assert mp_kwargs["formula"] == "Zr"
    assert mp_kwargs["property_name"] == "density"
    assert ok_kwargs["species"] == "Zr"
    assert ok_kwargs["property_name"] == "density"


@pytest.mark.asyncio
async def test_execute_data_found_when_any_source_returns(db_session) -> None:
    """data_found is True when at least one backend returns data."""
    request = _make_request()
    client = _fake_client(
        nist={"values": []},  # placeholder with empty list
        openkim=None,  # no result
        mp=None,
    )

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        result = await handler.execute(request)

    assert result.data_found is True
    assert result.success is True
    assert result.path == "external_db"


@pytest.mark.asyncio
async def test_execute_no_data_found_when_all_null(db_session) -> None:
    """data_found is False when all three backends return None."""
    request = _make_request()
    client = _fake_client(nist=None, openkim=None, mp=None)

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        result = await handler.execute(request)

    assert result.data_found is False
    assert result.success is True
    assert result.metadata["source_count"] == 0


@pytest.mark.asyncio
async def test_execute_reference_uses_first_source(db_session) -> None:
    """Reference is built from the first source that returned data."""
    request = _make_request()
    client = _fake_client(
        nist=None,
        openkim={"potentials": ["a"]},
        mp={"materials": ["b"]},
    )

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        result = await handler.execute(request)

    assert result.reference == f"openkim:{request.id}"


@pytest.mark.asyncio
async def test_execute_reference_none_when_no_data(db_session) -> None:
    """Reference is 'none:<id>' when no source returned data."""
    request = _make_request()
    client = _fake_client(nist=None, openkim=None, mp=None)

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        result = await handler.execute(request)

    assert result.reference == f"none:{request.id}"


@pytest.mark.asyncio
async def test_execute_includes_all_results_in_metadata(db_session) -> None:
    """The metadata dict exposes external_results to callers."""
    request = _make_request()
    nist_payload = {"values": [1, 2]}
    mp_payload = {"materials": ["Fe"]}
    client = _fake_client(nist=nist_payload, openkim=None, mp=mp_payload)

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        result = await handler.execute(request)

    assert result.metadata["external_results"] == {
        "nist_ipr": nist_payload,
        "materials_project": mp_payload,
    }
    assert result.metadata["source_count"] == 2
    assert result.metadata["queried_sources"] == 3
    assert result.metadata["material_system"] == request.material_system
    assert result.metadata["property"] == request.property


@pytest.mark.asyncio
async def test_execute_closes_client_even_on_error(db_session) -> None:
    """Client is closed even if a query raises."""
    request = _make_request()
    client = _fake_client()
    client.query_materials_project = AsyncMock(
        side_effect=RuntimeError("boom"),
    )

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        with pytest.raises(RuntimeError):
            await handler.execute(request)

    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_aggregates_source_count(db_session) -> None:
    """source_count matches len(results)."""
    request = _make_request()
    client = _fake_client(
        nist={"values": []},
        openkim={"potentials": []},
        mp=None,
    )

    with patch(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        return_value=client,
    ):
        handler = ExternalDBFillPath(session=db_session)
        result = await handler.execute(request)

    assert result.metadata["source_count"] == 2
    assert result.data_found is True
