"""OCR fallback service for degraded VLM extraction (NFM-851).

Provides a text-only extraction fallback when the VLM provider is
unavailable or returns errors. Uses pytesseract (optional) for
basic OCR, with a pure-text stub mode for CI/testing environments.

Activation: when ``VisionClientError`` is raised or
``VLM_STUB_MODE=true`` is set.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from nfm_db.schemas.vision_extraction import (
    AxisInfo,
    PlotData,
    TableData,
    VisionExtractionResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OCR result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OCRResult:
    """Result from OCR fallback extraction."""

    text: str
    confidence: float
    method: str  # "tesseract", "stub"


# ---------------------------------------------------------------------------
# OCR Fallback Service
# ---------------------------------------------------------------------------


class OcrFallback:
    """Fallback text extraction when VLM is unavailable.

    Attempts pytesseract OCR if available; falls back to a stub
    mode that returns an empty result with zero confidence.

    Usage::

        fallback = OcrFallback()
        result = await fallback.extract_text(image_data=png_bytes)
    """

    def __init__(self, *, use_tesseract: bool | None = None) -> None:
        self.use_tesseract = (
            use_tesseract
            if use_tesseract is not None
            else os.environ.get("VLM_OCR_TESSERACT", "false").lower() == "true"
        )
        self._tesseract_available: bool | None = None

    def is_available(self) -> bool:
        """Check if any OCR backend is available."""
        if self.use_tesseract:
            return self._check_tesseract()
        return True  # stub mode always available

    def _check_tesseract(self) -> bool:
        """Lazy-check for pytesseract availability."""
        if self._tesseract_available is None:
            try:
                import pytesseract  # noqa: F401

                self._tesseract_available = True
            except ImportError:
                logger.warning("pytesseract not installed; OCR fallback will use stub mode")
                self._tesseract_available = False
        return self._tesseract_available

    async def extract_text(self, *, image_data: bytes) -> OCRResult:
        """Extract text from an image using OCR.

        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.).

        Returns:
            OCRResult with extracted text and confidence.
        """
        if self.use_tesseract and self._check_tesseract():
            return await self._extract_tesseract(image_data=image_data)
        return OCRResult(text="", confidence=0.0, method="stub")

    async def _extract_tesseract(self, *, image_data: bytes) -> OCRResult:
        """Extract text using pytesseract."""
        try:
            import io

            import pytesseract
            from PIL import Image

            img = Image.open(io.BytesIO(image_data))
            text = pytesseract.image_to_string(img).strip()
            confidence = 0.3 if text else 0.0
            return OCRResult(text=text, confidence=confidence, method="tesseract")
        except Exception as exc:
            logger.error("OCR extraction failed: %s", exc)
            return OCRResult(text="", confidence=0.0, method="stub")


# ---------------------------------------------------------------------------
# Fallback extraction for plot/table
# ---------------------------------------------------------------------------


def ocr_fallback_plot_result(
    *,
    ocr_result: OCRResult,
    source_path: str | None = None,
) -> VisionExtractionResult:
    """Create a minimal PlotData result from OCR text.

    Attempts to extract axis labels and title from raw OCR text
    using simple heuristics. Confidence is low (0.1-0.3).
    """
    text = ocr_result.text

    title = _extract_first_line(text)
    axis_labels = _guess_axis_labels(text)

    plot_data = PlotData(
        title=title,
        plot_type="unknown",
        x_axis=_build_axis_info(axis_labels.get("x")),
        y_axis=_build_axis_info(axis_labels.get("y")),
        confidence=min(ocr_result.confidence, 0.3),
    )

    return VisionExtractionResult(
        figure_type="plot",
        plot_data=plot_data,
        table_data=None,
        source_image_path=source_path,
        provider="ocr",
        model="tesseract" if ocr_result.method == "tesseract" else "stub",
        fallback_used=True,
    )


def ocr_fallback_table_result(
    *,
    ocr_result: OCRResult,
    source_path: str | None = None,
) -> VisionExtractionResult:
    """Create a minimal TableData result from OCR text.

    Splits OCR text into rows/columns by newlines and whitespace.
    Low confidence (0.1-0.3).
    """
    from nfm_db.schemas.vision_extraction import TableCell, TableHeader

    text = ocr_result.text
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    rows: list[list[TableCell]] = []
    for line in lines:
        cells = re.split(r"\s{2,}", line)
        row = [TableCell(value=c, confidence=0.2) for c in cells if c]
        if row:
            rows.append(row)

    headers = TableHeader()
    if rows:
        headers = TableHeader(
            columns=[c.value for c in rows[0]],
        )
        rows = rows[1:] if len(rows) > 1 else []

    table_data = TableData(
        title=_extract_first_line(text),
        headers=headers,
        rows=rows,
        num_columns=len(headers.columns),
        num_rows=len(rows),
        confidence=min(ocr_result.confidence, 0.3),
    )

    return VisionExtractionResult(
        figure_type="table",
        plot_data=None,
        table_data=table_data,
        source_image_path=source_path,
        provider="ocr",
        model="tesseract" if ocr_result.method == "tesseract" else "stub",
        fallback_used=True,
    )


# ---------------------------------------------------------------------------
# Text heuristics
# ---------------------------------------------------------------------------


def _extract_first_line(text: str) -> str:
    """Extract the first non-empty line as a potential title."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _build_axis_info(label_dict: dict[str, str] | None) -> AxisInfo:
    """Construct an AxisInfo from a label dict returned by _guess_axis_labels."""
    if not label_dict:
        return AxisInfo()
    return AxisInfo(
        label=label_dict.get("label", ""),
        unit=label_dict.get("unit", ""),
    )


def _guess_axis_labels(text: str) -> dict[str, dict[str, str]]:
    """Attempt to guess axis labels from OCR text.

    Returns a dict with optional 'x' and 'y' keys, each containing
    a dict with 'label' and optionally 'unit'.
    """
    lines = text.split("\n")
    result: dict[str, dict[str, str]] = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "temperature" in lower or "temp" in lower:
            # Extract up to the end of the label word including trailing text
            match = re.search(r"Temperature[\w\s]*", stripped, re.IGNORECASE)
            if match:
                result.setdefault("x", {})["label"] = match.group(0).strip()
        elif "conductivity" in lower or "stress" in lower or "strain" in lower:
            match = re.search(r"(?:Conductivity|Stress|Strain)[\w\s]*", stripped, re.IGNORECASE)
            if match:
                result.setdefault("y", {})["label"] = match.group(0).strip()

    return result
