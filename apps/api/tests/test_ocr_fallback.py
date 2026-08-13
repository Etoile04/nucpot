"""Tests for OCR fallback service (NFM-851).

Covers: stub mode, availability checks, text heuristic parsing,
plot/table fallback result generation.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from nfm_db.schemas.vision_extraction import (
    VisionExtractionResult,
)
from nfm_db.services.ocr_fallback import (
    OcrFallback,
    OCRResult,
    _extract_first_line,
    _guess_axis_labels,
    ocr_fallback_plot_result,
    ocr_fallback_table_result,
)

# ---------------------------------------------------------------------------
# Tests: OCRResult
# ---------------------------------------------------------------------------


class TestOCRResult:
    """Tests for the OCRResult data class."""

    def test_is_frozen(self) -> None:
        """OCRResult should be immutable."""
        result = OCRResult(text="hello", confidence=0.5, method="tesseract")
        with pytest.raises(AttributeError):
            result.text = "changed"  # type: ignore[misc]

    def test_fields(self) -> None:
        result = OCRResult(text="UO2 data", confidence=0.8, method="stub")
        assert result.text == "UO2 data"
        assert result.confidence == 0.8
        assert result.method == "stub"


# ---------------------------------------------------------------------------
# Tests: OcrFallback
# ---------------------------------------------------------------------------


class TestOcrFallback:
    """Tests for the OcrFallback service."""

    def test_stub_mode_is_available(self) -> None:
        """Stub mode should always report as available."""
        with patch.dict("os.environ", {"VLM_OCR_TESSERACT": "false"}):
            fallback = OcrFallback(use_tesseract=False)
            assert fallback.is_available() is True

    def test_tesseract_mode_without_install(self) -> None:
        """Tesseract mode without pytesseract should still be available (stub fallback)."""
        with patch.dict("os.environ", {}, clear=True):
            fallback = OcrFallback(use_tesseract=False)
            assert fallback.is_available() is True

    @pytest.mark.asyncio
    async def test_stub_mode_returns_empty(self) -> None:
        """Stub mode should return empty OCR result."""
        fallback = OcrFallback(use_tesseract=False)
        result = await fallback.extract_text(image_data=b"\x89PNG")
        assert result.text == ""
        assert result.confidence == 0.0
        assert result.method == "stub"

    @pytest.mark.asyncio
    async def test_tesseract_mode_fallback_to_stub(self) -> None:
        """Tesseract mode without pytesseract should fall back to stub."""
        with patch.dict("os.environ", {}, clear=True):
            fallback = OcrFallback(use_tesseract=True)
            # Patch to simulate pytesseract missing
            fallback._tesseract_available = False
            result = await fallback.extract_text(image_data=b"\x89PNG")
            assert result.method == "stub"

    def test_env_var_enables_tesseract(self) -> None:
        """VLM_OCR_TESSERACT=true should enable tesseract mode."""
        with patch.dict("os.environ", {"VLM_OCR_TESSERACT": "true"}):
            fallback = OcrFallback()
            assert fallback.use_tesseract is True

    @pytest.mark.asyncio
    async def test_tesseract_mode_with_pytesseract(self) -> None:
        """Should call pytesseract when available and return text."""
        mock_pil_module = MagicMock()
        mock_pil_image_instance = MagicMock()
        mock_pil_module.Image.open.return_value = mock_pil_image_instance
        mock_pil_module.io = MagicMock()

        mock_tesseract_module = MagicMock()
        mock_tesseract_module.image_to_string.return_value = "Temperature (K)\n300  10.5"

        # Inject mocks into sys.modules so local imports resolve
        original_pil = sys.modules.get("PIL")
        original_pil_image = sys.modules.get("PIL.Image")
        original_tesseract = sys.modules.get("pytesseract")
        sys.modules["pytesseract"] = mock_tesseract_module
        sys.modules["PIL"] = mock_pil_module
        sys.modules["PIL.Image"] = mock_pil_module.Image

        try:
            # Reset the cached availability so _check_tesseract re-runs
            fallback = OcrFallback(use_tesseract=True)
            fallback._tesseract_available = None  # force re-check

            result = await fallback.extract_text(image_data=b"\x89PNG")
            assert result.method == "tesseract"
            assert result.confidence > 0.0
            assert "Temperature" in result.text
        finally:
            # Restore original modules
            if original_tesseract is not None:
                sys.modules["pytesseract"] = original_tesseract
            else:
                sys.modules.pop("pytesseract", None)
            if original_pil is not None:
                sys.modules["PIL"] = original_pil
            else:
                sys.modules.pop("PIL", None)
            if original_pil_image is not None:
                sys.modules["PIL.Image"] = original_pil_image
            else:
                sys.modules.pop("PIL.Image", None)

    @pytest.mark.asyncio
    async def test_tesseract_returns_empty_on_error(self) -> None:
        """Should fall back to stub result on pytesseract exception."""
        mock_pil_module = MagicMock()
        mock_pil_module.Image.open.side_effect = RuntimeError("PIL error")
        mock_pil_module.io = MagicMock()

        mock_tesseract_module = MagicMock()
        mock_tesseract_module.image_to_string.return_value = ""

        original_pil = sys.modules.get("PIL")
        original_pil_image = sys.modules.get("PIL.Image")
        original_tesseract = sys.modules.get("pytesseract")
        sys.modules["pytesseract"] = mock_tesseract_module
        sys.modules["PIL"] = mock_pil_module
        sys.modules["PIL.Image"] = mock_pil_module.Image

        try:
            fallback = OcrFallback(use_tesseract=True)
            fallback._tesseract_available = None

            result = await fallback.extract_text(image_data=b"\x89PNG")
            assert result.method == "stub"
            assert result.text == ""
            assert result.confidence == 0.0
        finally:
            if original_tesseract is not None:
                sys.modules["pytesseract"] = original_tesseract
            else:
                sys.modules.pop("pytesseract", None)
            if original_pil is not None:
                sys.modules["PIL"] = original_pil
            else:
                sys.modules.pop("PIL", None)
            if original_pil_image is not None:
                sys.modules["PIL.Image"] = original_pil_image
            else:
                sys.modules.pop("PIL.Image", None)

    def test_is_available_with_tesseract_enabled_and_available(self) -> None:
        """Should return True when tesseract is enabled and pytesseract is importable."""
        mock_tesseract = MagicMock()

        original = sys.modules.get("pytesseract")
        sys.modules["pytesseract"] = mock_tesseract

        try:
            fallback = OcrFallback(use_tesseract=True)
            fallback._tesseract_available = None  # force re-check
            assert fallback.is_available() is True
        finally:
            if original is not None:
                sys.modules["pytesseract"] = original
            else:
                sys.modules.pop("pytesseract", None)


# ---------------------------------------------------------------------------
# Tests: Text heuristics
# ---------------------------------------------------------------------------


class TestTextHeuristics:
    """Tests for OCR text parsing helpers."""

    def test_extract_first_line(self) -> None:
        """Should return first non-empty line."""
        text = "Title Here\nSome data\nMore data"
        assert _extract_first_line(text) == "Title Here"

    def test_extract_first_line_empty(self) -> None:
        """Should return empty string for empty text."""
        assert _extract_first_line("") == ""
        assert _extract_first_line("\n\n") == ""

    def test_guess_axis_labels_temperature(self) -> None:
        """Should detect temperature as x-axis candidate."""
        text = "Temperature (K)\nConductivity (W/m·K)\n300  10.5"
        result = _guess_axis_labels(text)
        assert "x" in result
        assert "Temperature" in result["x"]["label"]

    def test_guess_axis_labels_conductivity(self) -> None:
        """Should detect conductivity as y-axis candidate."""
        text = "Temperature (K)\nConductivity (W/m·K)\n300  10.5"
        result = _guess_axis_labels(text)
        assert "y" in result
        assert "Conductivity" in result["y"]["label"]

    def test_guess_axis_labels_empty(self) -> None:
        """Should return empty dict for unrelated text."""
        text = "Hello World\nNo axes here"
        result = _guess_axis_labels(text)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: Fallback result builders
# ---------------------------------------------------------------------------


class TestFallbackResultBuilders:
    """Tests for ocr_fallback_plot_result and ocr_fallback_table_result."""

    def test_plot_fallback_result_structure(self) -> None:
        """Should produce valid VisionExtractionResult with plot_data."""
        ocr = OCRResult(text="Temperature (K)\n10.5", confidence=0.3, method="stub")
        result = ocr_fallback_plot_result(ocr_result=ocr, source_path="/fig.png")
        assert isinstance(result, VisionExtractionResult)
        assert result.figure_type == "plot"
        assert result.plot_data is not None
        assert result.fallback_used is True
        assert result.source_image_path == "/fig.png"

    def test_plot_fallback_captures_title(self) -> None:
        """Should extract first line as title."""
        ocr = OCRResult(
            text="UO2 Conductivity Plot\nTemperature (K)\n10.5",
            confidence=0.3,
            method="stub",
        )
        result = ocr_fallback_plot_result(ocr_result=ocr)
        assert result.plot_data is not None
        assert result.plot_data.title == "UO2 Conductivity Plot"

    def test_plot_fallback_low_confidence(self) -> None:
        """Should cap confidence at 0.3."""
        ocr = OCRResult(text="data", confidence=0.8, method="stub")
        result = ocr_fallback_plot_result(ocr_result=ocr)
        assert result.plot_data.confidence <= 0.3

    def test_table_fallback_result_structure(self) -> None:
        """Should produce valid VisionExtractionResult with table_data."""
        ocr = OCRResult(
            text="Material  Temp  Value\nUO2     300   10.5\nUO2     600   5.5",
            confidence=0.3,
            method="stub",
        )
        result = ocr_fallback_table_result(ocr_result=ocr)
        assert isinstance(result, VisionExtractionResult)
        assert result.figure_type == "table"
        assert result.table_data is not None
        assert result.fallback_used is True

    def test_table_fallback_parses_rows(self) -> None:
        """Should split text into rows and cells."""
        ocr = OCRResult(
            text="Material  Temp  Value\nUO2     300   10.5\nUO2     600   5.5",
            confidence=0.3,
            method="stub",
        )
        result = ocr_fallback_table_result(ocr_result=ocr)
        assert result.table_data is not None
        assert result.table_data.num_rows == 2
        assert result.table_data.num_columns == 3
        assert result.table_data.rows[0][0].value == "UO2"

    def test_table_fallback_empty_text(self) -> None:
        """Should handle empty OCR text gracefully."""
        ocr = OCRResult(text="", confidence=0.0, method="stub")
        result = ocr_fallback_table_result(ocr_result=ocr)
        assert result.table_data is not None
        assert result.table_data.num_rows == 0
        assert result.table_data.confidence == 0.0
