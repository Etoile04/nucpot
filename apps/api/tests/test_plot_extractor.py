"""Tests for VLM-based plot data extractor (NFM-851, B1.2).

Covers: extraction flow, schema validation, confidence mapping,
fallback behavior, error handling. Uses mocked VLM client.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.schemas.vision_extraction import (
    VisionExtractionResult,
)
from nfm_db.services.ocr_fallback import OCRResult
from nfm_db.services.plot_extractor import PlotExtractor, extract_plot_data
from nfm_db.services.vision_client import VisionClientError

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
    """Build a mock VLM response for plot extraction."""
    base: dict[str, Any] = {
        "title": "UO2 Thermal Conductivity vs Temperature",
        "plot_type": "line",
        "x_axis": {
            "label": "Temperature",
            "unit": "K",
            "values": [300, 400, 500, 600, 700, 800, 900, 1000],
            "scale": "linear",
        },
        "y_axis": {
            "label": "Thermal Conductivity",
            "unit": "W/m·K",
            "values": [10.5, 8.2, 6.8, 5.5, 4.5, 3.8, 3.2, 2.8],
            "scale": "linear",
        },
        "series": [
            {
                "name": "UO2 (stoichiometric)",
                "values": [10.5, 8.2, 6.8, 5.5, 4.5, 3.8, 3.2, 2.8],
                "color": "black",
                "marker_style": "circle",
            }
        ],
        "legend_entries": ["UO2 (stoichiometric)"],
        "annotations": [],
        "confidence": 0.85,
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
        "usage": {"total_tokens": 300},
    }


# ---------------------------------------------------------------------------
# Tests: PlotExtractor initialization
# ---------------------------------------------------------------------------


class TestPlotExtractorInit:
    """Tests for PlotExtractor configuration."""

    def test_default_provider_is_openai(self) -> None:
        """Should default to OpenAI provider."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = PlotExtractor()
            assert extractor.client.provider == "openai"

    def test_custom_client(self) -> None:
        """Should accept a custom VisionClient instance."""
        mock_client = MagicMock()
        mock_client.provider = "ollama"
        mock_client.model = "llava"
        extractor = PlotExtractor(client=mock_client)
        assert extractor.client is mock_client


# ---------------------------------------------------------------------------
# Tests: extract_plot_data function
# ---------------------------------------------------------------------------


class TestExtractPlotData:
    """Tests for the main extraction entry point."""

    @pytest.mark.asyncio
    async def test_returns_vision_extraction_result(self) -> None:
        """Should return a VisionExtractionResult with plot_data populated."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = PlotExtractor()

        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(
                image_data=_sample_png_bytes(),
                source_path="/data/fig1.png",
            )

        assert isinstance(result, VisionExtractionResult)
        assert result.figure_type == "plot"
        assert result.source_image_path == "/data/fig1.png"
        assert result.plot_data is not None
        assert result.fallback_used is False

    @pytest.mark.asyncio
    async def test_populates_plot_data_fields(self) -> None:
        """Should correctly populate PlotData schema fields."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = PlotExtractor()

        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(
                image_data=_sample_png_bytes(),
            )

        plot = result.plot_data
        assert plot is not None
        assert plot.title == "UO2 Thermal Conductivity vs Temperature"
        assert plot.plot_type == "line"
        assert plot.x_axis.label == "Temperature"
        assert plot.x_axis.unit == "K"
        assert len(plot.x_axis.values) == 8
        assert plot.y_axis.label == "Thermal Conductivity"
        assert len(plot.series) == 1
        assert plot.series[0].name == "UO2 (stoichiometric)"

    @pytest.mark.asyncio
    async def test_handles_minimal_response(self) -> None:
        """Should handle VLM response with minimal fields."""
        minimal = {"title": "", "plot_type": "unknown", "confidence": 0.3}
        mock_response = _mock_vlm_chat_response(minimal)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = PlotExtractor()

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.plot_data is not None
        assert result.plot_data.confidence == 0.3

    @pytest.mark.asyncio
    async def test_records_extraction_metadata(self) -> None:
        """Should record provider, model, and extraction time."""
        vlm_data = _mock_vlm_response()
        mock_response = _mock_vlm_chat_response(vlm_data)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = PlotExtractor()

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
            extractor = PlotExtractor()

        with patch.object(
            extractor.client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.plot_data is not None
        assert result.plot_data.raw_response == vlm_data


# ---------------------------------------------------------------------------
# Tests: convenience function
# ---------------------------------------------------------------------------


class TestConvenienceFunction:
    """Tests for the module-level extract_plot_data function."""

    @pytest.mark.asyncio
    async def test_convenience_function_works(self) -> None:
        """extract_plot_data should be a working shortcut."""
        vlm_data = _mock_vlm_response()

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            with patch("nfm_db.services.plot_extractor.VisionClient") as MockClient:
                mock_instance = MockClient.return_value
                mock_instance.provider = "openai"
                mock_instance.model = "gpt-4o"
                mock_instance.extract = AsyncMock(return_value=vlm_data)

                result = await extract_plot_data(
                    image_data=_sample_png_bytes(),
                    source_path="/test/fig2.png",
                )

        assert isinstance(result, VisionExtractionResult)
        assert result.figure_type == "plot"


# ---------------------------------------------------------------------------
# Tests: OCR fallback integration
# ---------------------------------------------------------------------------


class TestPlotExtractorOcrFallback:
    """Tests for OCR fallback when VLM is unavailable."""

    @pytest.mark.asyncio
    async def test_falls_back_on_vlm_error(self) -> None:
        """Should activate OCR fallback when VLM raises VisionClientError."""
        mock_client = MagicMock()
        mock_client.provider = "openai"
        mock_client.model = "gpt-4o"
        mock_client.extract = AsyncMock(
            side_effect=VisionClientError("VLM request failed after 3 retries")
        )

        mock_ocr = MagicMock()
        mock_ocr.extract_text = AsyncMock(
            return_value=OCRResult(
                text="Temperature (K)\nConductivity (W/m·K)",
                confidence=0.3,
                method="stub",
            )
        )

        extractor = PlotExtractor(client=mock_client, ocr_fallback=mock_ocr)
        result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.figure_type == "plot"
        assert result.plot_data is not None
        assert result.fallback_used is True
        assert result.provider == "ocr"

    @pytest.mark.asyncio
    async def test_fallback_preserves_source_path(self) -> None:
        """Should pass source_path through to OCR fallback result."""
        mock_client = MagicMock()
        mock_client.provider = "openai"
        mock_client.model = "gpt-4o"
        mock_client.extract = AsyncMock(
            side_effect=VisionClientError("Connection refused")
        )

        mock_ocr = MagicMock()
        mock_ocr.extract_text = AsyncMock(
            return_value=OCRResult(text="data", confidence=0.2, method="stub")
        )

        extractor = PlotExtractor(client=mock_client, ocr_fallback=mock_ocr)
        result = await extractor.extract(
            image_data=_sample_png_bytes(),
            source_path="/data/fig1.png",
        )

        assert result.source_image_path == "/data/fig1.png"

    @pytest.mark.asyncio
    async def test_no_fallback_when_vlm_succeeds(self) -> None:
        """Should not use OCR fallback when VLM succeeds."""
        vlm_data = _mock_vlm_response()

        mock_client = MagicMock()
        mock_client.provider = "openai"
        mock_client.model = "gpt-4o"
        mock_client.extract = AsyncMock(return_value=vlm_data)

        mock_ocr = MagicMock()
        mock_ocr.extract_text = AsyncMock()

        extractor = PlotExtractor(client=mock_client, ocr_fallback=mock_ocr)
        # Patch build_prompt to avoid needing _call_provider mock chain
        with patch(
            "nfm_db.services.plot_extractor.build_plot_extraction_prompt",
            return_value="system",
        ):
            result = await extractor.extract(image_data=_sample_png_bytes())

        assert result.fallback_used is False
        mock_ocr.extract_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_ocr_fallback_created(self) -> None:
        """Should create OcrFallback automatically if none provided."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            extractor = PlotExtractor()
            assert extractor.ocr_fallback is not None
