"""Tests for VLM-based table structure extractor (NFM-851, B1.3).

Covers: extraction flow, schema validation, cell parsing,
merged cell handling, error handling. Uses mocked VLM client.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.schemas.vision_extraction import (
    VisionExtractionResult,
)
from nfm_db.services.table_extractor import TableExtractor, extract_table_data

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_png_bytes() -> bytes:
    """Minimal valid PNG bytes (1x1 transparent pixel)."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n\xb4\x15\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _mock_vlm_response(**overrides: Any) -> dict[str, Any]:
    """Build a mock VLM response for table extraction."""
    base: dict[str, Any] = {
        "title": "Thermal Properties of UO2 Fuel",
        "headers": {
            "columns": ["Material", "Temperature (K)", "Conductivity (W/m·K)", "Density (g/cm³)"],
            "sub_headers": None,
        },
        "rows": [
            [
                {"value": "UO2", "confidence": 0.95},
                {"value": "300", "confidence": 0.95},
                {"value": "10.5", "confidence": 0.90},
                {"value": "10.97", "confidence": 0.90},
            ],
            [
                {"value": "UO2", "confidence": 0.95},
                {"value": "600", "confidence": 0.95},
                {"value": "5.5", "confidence": 0.90},
                {"value": "10.85", "confidence": 0.90},
            ],
            [
                {"value": "UO2", "confidence": 0.95},
                {"value": "1200", "confidence": 0.95},
                {"value": "2.8", "confidence": 0.85},
                {"value": "10.50", "confidence": 0.85},
            ],
        ],
        "num_columns": 4,
        "num_rows": 3,
        "has_merged_cells": False,
        "notes": ["* Measured at atmospheric pressure"],
        "confidence": 0.88,
    }
    base.update(overrides)
    return base


def _mock_vlm_chat_response(vlm_data: dict[str, Any]) -> dict[str, Any]:
    """Wrap VLM data in a chat completion response."""
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(vlm_data),
                },
            }
        ],
        "usage": {"total_tokens": 400},
    }


# ---------------------------------------------------------------------------
# Tests: TableExtractor initialization
# ---------------------------------------------------------------------------


class TestTableExtractorInit:
    """Tests for TableExtractor configuration."""

    def test_default_provider_is_openai(self) -> None:
        """Should default to OpenAI provider."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()
            assert extractor.client.provider == "openai"

    def test_custom_client(self) -> None:
        """Should accept a custom VisionClient instance."""
        mock_client = MagicMock()
        mock_client.provider = "ollama"
        extractor = TableExtractor(client=mock_client)
        assert extractor.client is mock_client


# ---------------------------------------------------------------------------
# Tests: extract method
# ---------------------------------------------------------------------------


class TestExtractTableData:
    """Tests for the main table extraction entry point."""

    @pytest.mark.asyncio
    async def test_returns_vision_extraction_result(self) -> None:
        """Should return a VisionExtractionResult with table_data populated."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(
                image_data=_sample_png_bytes(),
                source_path="/data/table1.png",
            )

        assert isinstance(result, VisionExtractionResult)
        assert result.figure_type == "table"
        assert result.source_image_path == "/data/table1.png"
        assert result.table_data is not None
        assert result.fallback_used is False

    @pytest.mark.asyncio
    async def test_populates_table_data_fields(self) -> None:
        """Should correctly populate TableData schema fields."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        table = result.table_data
        assert table is not None
        assert table.title == "Thermal Properties of UO2 Fuel"
        assert table.num_columns == 4
        assert table.num_rows == 3
        assert len(table.headers.columns) == 4
        assert table.headers.columns[0] == "Material"
        assert len(table.rows) == 3

    @pytest.mark.asyncio
    async def test_cell_values_preserved(self) -> None:
        """Should preserve exact cell text values."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        table = result.table_data
        assert table is not None
        assert table.rows[0][0].value == "UO2"
        assert table.rows[0][1].value == "300"
        assert table.rows[0][2].value == "10.5"

    @pytest.mark.asyncio
    async def test_handles_merged_cells(self) -> None:
        """Should detect and handle merged cells."""
        merged_data = _mock_vlm_response(
            has_merged_cells=True,
            rows=[
                [
                    {"value": "UO2", "row_span": 2, "col_span": 1, "confidence": 0.95},
                    {"value": "300", "confidence": 0.95},
                    {"value": "10.5", "confidence": 0.90},
                    {"value": "10.97", "confidence": 0.90},
                ],
                [
                    {"value": "", "row_span": 1, "col_span": 1, "confidence": 0.5},
                    {"value": "600", "confidence": 0.95},
                    {"value": "5.5", "confidence": 0.90},
                    {"value": "10.85", "confidence": 0.90},
                ],
            ],
        )
        mock_response = _mock_vlm_chat_response(merged_data)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        table = result.table_data
        assert table is not None
        assert table.has_merged_cells is True
        assert table.rows[0][0].row_span == 2

    @pytest.mark.asyncio
    async def test_handles_minimal_response(self) -> None:
        """Should handle VLM response with minimal fields."""
        minimal = {"title": "", "confidence": 0.2}
        mock_response = _mock_vlm_chat_response(minimal)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.table_data is not None
        assert result.table_data.confidence == 0.2

    @pytest.mark.asyncio
    async def test_records_extraction_metadata(self) -> None:
        """Should record provider, model, and extraction time."""
        vlm_data = _mock_vlm_response()
        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        assert result.extraction_time_ms >= 0

    @pytest.mark.asyncio
    async def test_stores_raw_vlm_response(self) -> None:
        """Should store raw VLM response for debugging."""
        vlm_data = _mock_vlm_response()
        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = TableExtractor()

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.table_data is not None
        assert result.table_data.raw_response == vlm_data


# ---------------------------------------------------------------------------
# Tests: convenience function
# ---------------------------------------------------------------------------


class TestConvenienceFunction:
    """Tests for the module-level extract_table_data function."""

    @pytest.mark.asyncio
    async def test_convenience_function_works(self) -> None:
        """extract_table_data should be a working shortcut."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            with patch("nfm_db.services.table_extractor.VisionClient") as MockClient:
                mock_instance = MockClient.return_value
                mock_instance.provider = "openai"
                mock_instance.model = "gpt-4o"
                mock_instance.extract = AsyncMock(return_value=vlm_data)

                result = await extract_table_data(
                    image_data=_sample_png_bytes(),
                    source_path="/test/table2.png",
                )

        assert isinstance(result, VisionExtractionResult)
        assert result.figure_type == "table"
