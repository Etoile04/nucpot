"""Tests for LiteratureFillPath handler (NFM-2645)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.paths.literature_fill import (
    _HANDLER_NAME,
    LiteratureFillPath,
)


def _make_request(
    source_preference: str = "literature",
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


class TestLiteratureCanHandle:

    @pytest.mark.asyncio
    async def test_accepts_literature(self) -> None:
        handler = LiteratureFillPath(session=AsyncMock())
        req = _make_request(source_preference="literature")
        assert await handler.can_handle(req) is True

    @pytest.mark.asyncio
    async def test_accepts_any(self) -> None:
        handler = LiteratureFillPath(session=AsyncMock())
        req = _make_request(source_preference="any")
        assert await handler.can_handle(req) is True

    @pytest.mark.asyncio
    async def test_rejects_dft(self) -> None:
        handler = LiteratureFillPath(session=AsyncMock())
        req = _make_request(source_preference="dft")
        assert await handler.can_handle(req) is False

    @pytest.mark.asyncio
    async def test_rejects_external_db(self) -> None:
        handler = LiteratureFillPath(session=AsyncMock())
        req = _make_request(source_preference="external_db")
        assert await handler.can_handle(req) is False

    @pytest.mark.asyncio
    async def test_rejects_unknown(self) -> None:
        handler = LiteratureFillPath(session=AsyncMock())
        req = _make_request(source_preference="unknown")
        assert await handler.can_handle(req) is False


class TestLiteratureExecute:

    @pytest.mark.asyncio
    async def test_creates_placeholder(self) -> None:
        session = AsyncMock()
        handler = LiteratureFillPath(session=session)
        req = _make_request()
        mock_source = MagicMock()
        mock_source.id = uuid.uuid4()
        session.add = MagicMock()
        session.flush = AsyncMock()
        with patch(
            "nfm_db.services.paths.literature_fill.DataSource",
            return_value=mock_source,
        ):
            result = await handler.execute(req)
        assert result.success is True
        assert result.path == _HANDLER_NAME
        assert result.reference == str(mock_source.id)
        assert result.data_found is False
        assert result.error is None
        session.add.assert_called_once_with(mock_source)
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_placeholder_title_contains_keywords(self) -> None:
        session = AsyncMock()
        handler = LiteratureFillPath(session=session)
        req = _make_request(property_name="density", material_system="Zr")
        mock_source = MagicMock()
        mock_source.id = uuid.uuid4()
        session.flush = AsyncMock()
        with patch(
            "nfm_db.services.paths.literature_fill.DataSource",
            return_value=mock_source,
        ) as MockDataSource:
            await handler.execute(req)
            call_kwargs = MockDataSource.call_args[1]
            assert "density" in call_kwargs["title"]
            assert "Zr" in call_kwargs["title"]
            assert call_kwargs["source_type"] == "literature_placeholder"
            assert call_kwargs["parse_status"] == "placeholder"

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self) -> None:
        session = AsyncMock()
        session.flush = AsyncMock(side_effect=RuntimeError("DB error"))
        handler = LiteratureFillPath(session=session)
        req = _make_request()
        with patch(
            "nfm_db.services.paths.literature_fill.DataSource",
            return_value=MagicMock(),
        ):
            result = await handler.execute(req)
        assert result.success is False
        assert result.path == _HANDLER_NAME
        assert "DB error" in result.error


class TestBuildSearchKeywords:

    def test_format(self) -> None:
        req = _make_request(
            property_name="thermal_conductivity",
            material_system="UO2",
        )
        keywords = LiteratureFillPath._build_search_keywords(req)
        assert keywords == "[PLACEHOLDER] thermal_conductivity - UO2"

    def test_different_property(self) -> None:
        req = _make_request(property_name="melting_point", material_system="Fe")
        keywords = LiteratureFillPath._build_search_keywords(req)
        assert keywords == "[PLACEHOLDER] melting_point - Fe"
