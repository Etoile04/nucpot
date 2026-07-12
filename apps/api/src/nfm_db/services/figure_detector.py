"""Figure detection pipeline service (NFM-850).

Detects figures, tables, plots, and microstructure images in PDF pages
using VLM-based layout analysis.

Two-stage pipeline per ADR-NFM-817-1:
  1. Layout detection — VLM identifies figure regions with bounding boxes
  2. Type classification — VLM classifies each detected region

Uses the existing VisionClient for VLM API calls and PageSplitter
for PDF-to-image conversion.

Configuration via environment variables:
  FIGURE_DETECTION_MIN_CONFIDENCE - minimum confidence threshold (default: 0.5)
  FIGURE_DETECTION_MIN_AREA - minimum figure area in pixels^2 (default: 1000)
  FIGURE_DETECTION_OVERLAP_THRESHOLD - IoU threshold for NMS (default: 0.5)
"""

from __future__ import annotations

import io
import logging
import os
import time
from typing import Any

from PIL import Image

from nfm_db.schemas.figure import (
    BoundingBox,
    DetectedFigure,
    FigureDetectionResult,
    FigureType,
    PageDetectionResult,
)
from nfm_db.services.page_splitter import PageImage, PageSplitter, PageSplitterError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MIN_CONFIDENCE = 0.5
_DEFAULT_MIN_AREA = 1000
_DEFAULT_OVERLAP_THRESHOLD = 0.5

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FigureDetectorError(Exception):
    """Raised when figure detection fails."""


# ---------------------------------------------------------------------------
# Detection prompt
# ---------------------------------------------------------------------------


def _build_detection_prompt() -> str:
    """Build the VLM prompt for figure region detection.

    Instructs the VLM to identify all figure/table/plot regions
    and return bounding boxes with confidence scores.
    """
    return (
        "You are a scientific document layout analyzer for nuclear materials "
        "research papers. Analyze this page image and identify ALL figure and "
        "table regions.\n\n"
        "For each detected region, return:\n"
        "1. \"type\": one of \"plot\", \"table\", \"microstructure\", \"diagram\"\n"
        "2. \"bbox\": [x, y, width, height] in pixels (top-left origin)\n"
        "3. \"confidence\": 0.0 to 1.0\n"
        "4. \"caption\": any caption or label text near the figure\n\n"
        "Return ONLY a JSON object with key \"figures\" containing a list of "
        "these objects. If no figures are found, return {\"figures\": []}.\n\n"
        "Important:\n"
        "- Include ALL figures, tables, charts, and microstructure images\n"
        "- Bounding boxes must be within the page dimensions\n"
        "- Width and height must be positive integers\n"
        "- No markdown, no explanation — raw JSON only"
    )


def _build_classification_prompt(figure_type: str) -> str:
    """Build a refinement prompt for ambiguous figure types."""
    return (
        f"You classified a figure as \"{figure_type}\". "
        "Confirm or correct the classification. "
        "Choose from: plot, table, microstructure, diagram. "
        "Return ONLY the type string, nothing else."
    )


# ---------------------------------------------------------------------------
# FigureDetector
# ---------------------------------------------------------------------------


class FigureDetector:
    """Detect and classify figures in PDF pages using VLM-based analysis.

    Two-stage pipeline:
      1. Split PDF into page images (via PageSplitter)
      2. For each page, call VLM to detect figure regions
      3. Post-process: filter by confidence/area, apply NMS

    Usage::

        detector = FigureDetector()
        result = await detector.detect(pdf_bytes, source_path="paper.pdf")
        for page_result in result.pages:
            for fig in page_result.figures:
                print(f"Page {fig.page_index}: {fig.figure_type} "
                      f"at ({fig.bounding_box.x}, {fig.bounding_box.y})")
    """

    def __init__(
        self,
        *,
        vision_client: Any | None = None,
        page_splitter: PageSplitter | None = None,
        min_confidence: float | None = None,
        min_area: int | None = None,
        overlap_threshold: float | None = None,
    ) -> None:
        self.min_confidence = min_confidence or float(
            os.environ.get("FIGURE_DETECTION_MIN_CONFIDENCE", _DEFAULT_MIN_CONFIDENCE)
        )
        self.min_area = min_area or int(
            os.environ.get("FIGURE_DETECTION_MIN_AREA", _DEFAULT_MIN_AREA)
        )
        self.overlap_threshold = overlap_threshold or float(
            os.environ.get(
                "FIGURE_DETECTION_OVERLAP_THRESHOLD", _DEFAULT_OVERLAP_THRESHOLD
            )
        )
        self._vision_client = vision_client
        self._page_splitter = page_splitter or PageSplitter()

    async def detect(
        self,
        pdf_source: bytes,
        *,
        source_path: str = "",
    ) -> FigureDetectionResult:
        """Detect figures in a PDF document.

        Args:
            pdf_source: Raw PDF bytes.
            source_path: Optional file path for metadata.

        Returns:
            FigureDetectionResult with per-page figure detections.

        Raises:
            FigureDetectorError: If detection fails.
        """
        start_time = time.monotonic()

        try:
            page_images = self._page_splitter.split(pdf_source)
        except PageSplitterError as exc:
            raise FigureDetectorError(
                f"Failed to split PDF for detection: {exc}"
            ) from exc

        if not page_images:
            return FigureDetectionResult(
                source_path=source_path,
                total_pages=0,
                total_figures=0,
                pages=[],
                provider="vlm",
            )

        pages: list[PageDetectionResult] = []
        total_figures = 0

        for page_img in page_images:
            page_result = await self._detect_page(page_img)
            pages.append(page_result)
            total_figures += len(page_result.figures)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "Figure detection complete: %d pages, %d figures, %.0fms",
            len(pages),
            total_figures,
            elapsed_ms,
        )

        return FigureDetectionResult(
            source_path=source_path,
            total_pages=len(page_images),
            total_figures=total_figures,
            pages=pages,
            provider="vlm",
            processing_time_ms=elapsed_ms,
        )

    async def detect_page(self, page_image: PageImage) -> PageDetectionResult:
        """Detect figures on a single page image.

        Convenience method for processing individual pages.

        Args:
            page_image: A PageImage from PageSplitter.

        Returns:
            PageDetectionResult for the page.
        """
        return await self._detect_page(page_image)

    # ---------------------------------------------------------------------------
    # Internal methods
    # ---------------------------------------------------------------------------

    async def _detect_page(self, page: PageImage) -> PageDetectionResult:
        """Run detection on a single page image."""
        raw_figures = await self._call_vlm_detection(page)

        detected = [
            self._parse_detected_figure(raw_fig, page.index)
            for raw_fig in raw_figures
        ]

        filtered = self._filter_figures(detected, page.width, page.height)
        deduplicated = self._non_max_suppression(filtered)

        return PageDetectionResult(
            page_index=page.index,
            page_width=page.width,
            page_height=page.height,
            figures=deduplicated,
        )

    async def _call_vlm_detection(
        self, page: PageImage
    ) -> list[dict[str, Any]]:
        """Call VLM to detect figure regions on a page image.

        Returns a list of raw figure dicts from the VLM response.
        Falls back to empty list on failure.
        """
        if self._vision_client is None:
            logger.warning(
                "No vision client configured, returning empty detection for page %d",
                page.index,
            )
            return []

        try:
            img_bytes = _image_to_bytes(page.image)
            result = await self._vision_client.extract(
                image_data=img_bytes,
                prompt=_build_detection_prompt(),
                temperature=0.0,
            )

            figures = result.get("figures", [])
            if not isinstance(figures, list):
                logger.warning(
                    "VLM returned non-list figures on page %d: %s",
                    page.index,
                    type(figures),
                )
                return []

            return figures

        except Exception as exc:
            logger.error(
                "VLM detection failed on page %d: %s",
                page.index,
                exc,
            )
            return []

    def _parse_detected_figure(
        self,
        raw: dict[str, Any],
        page_index: int,
    ) -> DetectedFigure:
        """Parse a raw VLM figure dict into a DetectedFigure."""
        bbox_raw = raw.get("bbox", [0, 0, 0, 0])
        figure_type_str = raw.get("type", "unknown")
        confidence = float(raw.get("confidence", 0.0))
        caption = str(raw.get("caption", ""))

        bbox = _parse_bounding_box(bbox_raw)

        try:
            figure_type = FigureType(figure_type_str)
        except ValueError:
            figure_type = FigureType.UNKNOWN

        return DetectedFigure(
            figure_type=figure_type,
            bounding_box=bbox,
            confidence=max(0.0, min(1.0, confidence)),
            caption=caption,
            page_index=page_index,
        )

    def _filter_figures(
        self,
        figures: list[DetectedFigure],
        page_width: int,
        page_height: int,
    ) -> list[DetectedFigure]:
        """Filter figures by confidence and area thresholds."""
        result: list[DetectedFigure] = []

        for fig in figures:
            bbox = fig.bounding_box

            if fig.confidence < self.min_confidence:
                continue

            area = bbox.width * bbox.height
            if area < self.min_area:
                continue

            clamped = _clamp_bbox(bbox, page_width, page_height)
            result.append(
                DetectedFigure(
                    figure_type=fig.figure_type,
                    bounding_box=clamped,
                    confidence=fig.confidence,
                    caption=fig.caption,
                    page_index=fig.page_index,
                )
            )

        return result

    def _non_max_suppression(
        self,
        figures: list[DetectedFigure],
    ) -> list[DetectedFigure]:
        """Remove overlapping detections using greedy NMS."""
        if not figures:
            return []

        sorted_figures = sorted(figures, key=lambda f: f.confidence, reverse=True)
        kept: list[DetectedFigure] = []

        for candidate in sorted_figures:
            is_suppressed = False
            for existing in kept:
                iou = _compute_iou(
                    candidate.bounding_box,
                    existing.bounding_box,
                )
                if iou > self.overlap_threshold:
                    is_suppressed = True
                    break

            if not is_suppressed:
                kept.append(candidate)

        return kept


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _image_to_bytes(image: Image.Image) -> bytes:
    """Convert a PIL Image to PNG bytes for VLM transmission."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _parse_bounding_box(raw: Any) -> BoundingBox:
    """Parse a bounding box from VLM response.

    Accepts list [x, y, width, height] or dict with matching keys.
    Falls back to a minimal 1x1 box at origin.
    """
    try:
        if isinstance(raw, (list, tuple)) and len(raw) >= 4:
            return BoundingBox(
                x=max(0, int(raw[0])),
                y=max(0, int(raw[1])),
                width=max(1, int(raw[2])),
                height=max(1, int(raw[3])),
            )
        if isinstance(raw, dict):
            return BoundingBox(
                x=max(0, int(raw.get("x", 0))),
                y=max(0, int(raw.get("y", 0))),
                width=max(1, int(raw.get("width", 1))),
                height=max(1, int(raw.get("height", 1))),
            )
    except (ValueError, TypeError):
        logger.warning("Failed to parse bounding box from: %s", raw)

    return BoundingBox(x=0, y=0, width=1, height=1)


def _clamp_bbox(bbox: BoundingBox, max_width: int, max_height: int) -> BoundingBox:
    """Clamp a bounding box to fit within page dimensions."""
    x = min(bbox.x, max(0, max_width - 1))
    y = min(bbox.y, max(0, max_height - 1))
    width = min(bbox.width, max(1, max_width - x))
    height = min(bbox.height, max(1, max_height - y))
    return BoundingBox(x=x, y=y, width=width, height=height)


def _compute_iou(a: BoundingBox, b: BoundingBox) -> float:
    """Compute Intersection over Union of two bounding boxes."""
    x_left = max(a.x, b.x)
    y_top = max(a.y, b.y)
    x_right = min(a.x + a.width, b.x + b.width)
    y_bottom = min(a.y + a.height, b.y + b.height)

    if x_right <= x_left or y_bottom <= y_top:
        return 0.0

    intersection_area = (x_right - x_left) * (y_bottom - y_top)
    a_area = a.width * a.height
    b_area = b.width * b.height
    union_area = a_area + b_area - intersection_area

    if union_area <= 0:
        return 0.0

    return intersection_area / union_area
