"""Tests for ExternalDBFillPath handler (NFM-2645)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.external_data_sources import ExternalDataSource
from nfm_db.services.paths.external_db_fill import (
    _HANDLER_NAME,
    ExternalDBFillPath,
)


def _make_request(
    source_preference: str = "external_db",
    property_name: str = "thermal_conductivity",
    material_system: str = "UO2",
    entity_type: str = "NuclearMaterial",
) -> DataCollectionRequest:
    req = MagicMock(spec=DataCollectionRequest)
    req.id = uuid.uuid4()
    req.source_preference = source_preference
    req.property = property_name
    req.material_system = material_system
    req.entity_type = entity_type
    return req


class TestExternalDBCanHandle:

    @pytest.mark.asyncio
    async def test_accepts_external_db(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request(source_preference="external_db")
        assert await handler.can_handle(req) is True

    @pytest.mark.asyncio
    async def test_accepts_any(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request(source_preference="any")
        assert await handler.can_handle(req) is True

    @pytest.mark.asyncio
    async def test_rejects_literature(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request(source_preference="literature")
        assert await handler.can_handle(req) is False

    @pytest.mark.asyncio
    async def test_rejects_dft(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request(source_preference="dft")
        assert await handler.can_handle(req) is False


class TestExternalDBExecute:

    @pytest.mark.asyncio
    async def test_returns_data_found_when_hit(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request()
        mock_result = {"value": 3.5, "unit": "W/m/K"}
        handler._query_sources = AsyncMock(
            return_value=("nist_ipr", mock_result)
        )
        result = await handler.execute(req)
        assert result.success is True
        assert result.path == _HANDLER_NAME
        assert result.data_found is True
        assert "nist_ipr" in result.reference
        assert "thermal_conductivity" in result.reference

    @pytest.mark.asyncio
    async def test_returns_no_data_when_miss(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request()
        handler._query_sources = AsyncMock(return_value=None)
        result = await handler.execute(req)
        assert result.success is True
        assert result.path == _HANDLER_NAME
        assert result.data_found is False
        assert result.reference is None

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self) -> None:
        handler = ExternalDBFillPath(session=AsyncMock())
        req = _make_request()
        handler._query_sources = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        result = await handler.execute(req)
        assert result.success is False
        assert result.path == _HANDLER_NAME
        assert "timeout" in result.error


class TestQuerySingleSource:

    @pytest.mark.asyncio
    async def test_dispatches_nist_ipr(self) -> None:
        mock_client = AsyncMock()
        mock_client.query_nist_ipr = AsyncMock(return_value={"data": True})
        mock_client.query_openkim = AsyncMock(return_value=None)
        mock_client.query_materials_project = AsyncMock(return_value=None)
        result = await ExternalDBFillPath._query_single_source(
            mock_client, ExternalDataSource.NIST_IPR, "UO2", "density"
        )
        assert result == {"data": True}
        mock_client.query_nist_ipr.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_openkim(self) -> None:
        mock_client = AsyncMock()
        mock_client.query_openkim = AsyncMock(return_value={"data": True})
        result = await ExternalDBFillPath._query_single_source(
            mock_client, ExternalDataSource.OPENKIM, "UO2", "potential"
        )
        assert result == {"data": True}
        mock_client.query_openkim.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_materials_project(self) -> None:
        mock_client = AsyncMock()
        mock_client.query_materials_project = AsyncMock(
            return_value={"data": True}
        )
        result = await ExternalDBFillPath._query_single_source(
            mock_client,
            ExternalDataSource.MATERIALS_PROJECT,
            "UO2",
            "formation_energy",
        )
        assert result == {"data": True}
        mock_client.query_materials_project.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_for_unmatched_source(self) -> None:
        mock_client = AsyncMock()
        # Use a MagicMock to simulate an unrecognized enum-like object
        # without triggering Enum's ValueError for invalid values.
        unknown = MagicMock(spec=ExternalDataSource)
        unknown.value = "unknown"
        result = await ExternalDBFillPath._query_single_source(
            mock_client, unknown, "UO2", "density"
        )
        assert result is None
