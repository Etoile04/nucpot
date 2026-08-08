"""Tests for DFTFillPath handler (NFM-2645)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.paths.dft_fill import (
    _HANDLER_NAME,
    _PLACEHOLDER_MARKER,
    DFTFillPath,
)


def _make_request(
    source_preference: str = "dft",
    property_name: str = "formation_energy",
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


class TestDFTCanHandle:

    @pytest.mark.asyncio
    async def test_accepts_dft(self) -> None:
        handler = DFTFillPath(session=AsyncMock())
        req = _make_request(source_preference="dft")
        assert await handler.can_handle(req) is True

    @pytest.mark.asyncio
    async def test_accepts_any(self) -> None:
        handler = DFTFillPath(session=AsyncMock())
        req = _make_request(source_preference="any")
        assert await handler.can_handle(req) is True

    @pytest.mark.asyncio
    async def test_rejects_literature(self) -> None:
        handler = DFTFillPath(session=AsyncMock())
        req = _make_request(source_preference="literature")
        assert await handler.can_handle(req) is False

    @pytest.mark.asyncio
    async def test_rejects_external_db(self) -> None:
        handler = DFTFillPath(session=AsyncMock())
        req = _make_request(source_preference="external_db")
        assert await handler.can_handle(req) is False


class TestDFTExecute:

    @pytest.mark.asyncio
    async def test_creates_stub(self) -> None:
        session = AsyncMock()
        handler = DFTFillPath(session=session)
        req = _make_request()
        mock_calc = MagicMock()
        mock_calc.id = uuid.uuid4()
        session.add = MagicMock()
        session.flush = AsyncMock()
        with patch(
            "nfm_db.services.paths.dft_fill.DFTCalculation",
            return_value=mock_calc,
        ):
            result = await handler.execute(req)
        assert result.success is True
        assert result.path == _HANDLER_NAME
        assert result.reference == str(mock_calc.id)
        assert result.data_found is False
        assert result.error is None

    @pytest.mark.asyncio
    async def test_stub_has_placeholder_metadata(self) -> None:
        session = AsyncMock()
        handler = DFTFillPath(session=session)
        req = _make_request(
            property_name="band_gap",
            material_system="SiC",
        )
        mock_calc = MagicMock()
        mock_calc.id = uuid.uuid4()
        session.flush = AsyncMock()
        with patch(
            "nfm_db.services.paths.dft_fill.DFTCalculation",
            return_value=mock_calc,
        ) as MockCalc:
            await handler.execute(req)
            call_kwargs = MockCalc.call_args[1]
            assert call_kwargs["status"] == "pending"
            assert call_kwargs["functional"] == "PBE"
            assert call_kwargs["computation_metadata"]["placeholder"] == _PLACEHOLDER_MARKER
            assert call_kwargs["computation_metadata"]["property"] == "band_gap"
            assert call_kwargs["computation_metadata"]["material_system"] == "SiC"
            assert call_kwargs["computation_metadata"]["entity_type"] == "NuclearMaterial"
            assert "collection_request_id" in call_kwargs["computation_metadata"]

    @pytest.mark.asyncio
    async def test_stub_calculation_id_contains_request_id(self) -> None:
        session = AsyncMock()
        handler = DFTFillPath(session=session)
        req = _make_request()
        mock_calc = MagicMock()
        mock_calc.id = uuid.uuid4()
        session.flush = AsyncMock()
        with patch(
            "nfm_db.services.paths.dft_fill.DFTCalculation",
            return_value=mock_calc,
        ) as MockCalc:
            await handler.execute(req)
            call_kwargs = MockCalc.call_args[1]
            assert str(req.id) in call_kwargs["calculation_id"]
            assert call_kwargs["calculation_id"].startswith("stub-")

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self) -> None:
        session = AsyncMock()
        session.flush = AsyncMock(side_effect=RuntimeError("insert failed"))
        handler = DFTFillPath(session=session)
        req = _make_request()
        with patch(
            "nfm_db.services.paths.dft_fill.DFTCalculation",
            return_value=MagicMock(),
        ):
            result = await handler.execute(req)
        assert result.success is False
        assert result.path == _HANDLER_NAME
        assert "insert failed" in result.error
