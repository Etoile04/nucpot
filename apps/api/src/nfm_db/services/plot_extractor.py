"""VLM-based plot/chart data extractor (NFM-851, B1.2).

Extracts structured data from scientific plot and chart images using
a Vision Language Model (VLM). Produces a ``PlotData`` schema
with axes, series, legend, and confidence information.

When VLM is unavailable, automatically falls back to OCR-based
extraction via ``OcrFallback``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nfm_db.schemas.vision_extraction import (
    AxisInfo,
    PlotData,
    SeriesData,
    VisionExtractionResult,
)
from nfm_db.services.ocr_fallback import (
    OcrFallback,
    ocr_fallback_plot_result,
)
from nfm_db.services.vision_client import (
    VisionClient,
    VisionClientError,
    build_plot_extraction_prompt,
)

logger = logging.getLogger(__name__)


class PlotExtractor:
    """Extracts structured data from plot/chart images via VLM.

    Wraps ``VisionClient`` with plot-specific prompt construction
    and result parsing into ``PlotData`` schemas.

    Usage::

        extractor = PlotExtractor()
        result = await extractor.extract(
            image_data=png_bytes,
            source_path="figures/fig1.png",
        )
    """

    def __init__(
        self,
        *,
        client: VisionClient | None = None,
        ocr_fallback: OcrFallback | None = None,
    ) -> None:
        self.client = client or VisionClient()
        self.ocr_fallback = ocr_fallback or OcrFallback()

    async def extract(
        self,
        *,
        image_data: bytes,
        source_path: str | None = None,
    ) -> VisionExtractionResult:
        """Extract plot data from an image using VLM.

        Falls back to OCR-based extraction when the VLM is unavailable
        or returns an error.

        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.).
            source_path: Optional path for provenance tracking.

        Returns:
            VisionExtractionResult with plot_data populated. Uses
            ``fallback_used=True`` when OCR fallback was activated.
        """
        try:
            return await self._extract_vlm(
                image_data=image_data,
                source_path=source_path,
            )
        except VisionClientError as exc:
            logger.warning("VLM plot extraction failed, falling back to OCR: %s", exc)
            return await self._extract_ocr_fallback(
                image_data=image_data,
                source_path=source_path,
            )

    async def _extract_vlm(
        self,
        *,
        image_data: bytes,
        source_path: str | None = None,
    ) -> VisionExtractionResult:
        """Attempt VLM-based extraction (primary path)."""
        start = time.monotonic()
        system_prompt = build_plot_extraction_prompt()
        user_prompt = (
            "Extract all plot/chart data from this scientific figure. "
            "Include axis labels, units, tick values, data series, "
            "and legend entries as structured JSON."
        )

        raw_data = await self.client.extract(
            image_data=image_data,
            prompt=user_prompt,
            system_prompt=system_prompt,
        )

        elapsed_ms = (time.monotonic() - start) * 1000

        plot_data = self._parse_plot_response(raw_data)

        return VisionExtractionResult(
            figure_type="plot",
            plot_data=plot_data,
            table_data=None,
            source_image_path=source_path,
            provider=self.client.provider,
            model=self.client.model,
            extraction_time_ms=elapsed_ms,
            fallback_used=False,
        )

    async def _extract_ocr_fallback(
        self,
        *,
        image_data: bytes,
        source_path: str | None = None,
    ) -> VisionExtractionResult:
        """Attempt OCR-based extraction (degraded path)."""
        ocr_result = await self.ocr_fallback.extract_text(image_data=image_data)
        return ocr_fallback_plot_result(
            ocr_result=ocr_result,
            source_path=source_path,
        )

    @staticmethod
    def _parse_plot_response(raw: dict[str, Any]) -> PlotData:
        """Parse raw VLM dict into a PlotData schema.

        Handles missing or malformed fields gracefully with defaults.
        """
        x_axis_raw = raw.get("x_axis", {})
        y_axis_raw = raw.get("y_axis", {})
        y2_axis_raw = raw.get("y2_axis")

        x_axis = AxisInfo(
            label=x_axis_raw.get("label", ""),
            unit=x_axis_raw.get("unit", ""),
            values=x_axis_raw.get("values", []),
            scale=x_axis_raw.get("scale", "linear"),
        )

        y_axis = AxisInfo(
            label=y_axis_raw.get("label", ""),
            unit=y_axis_raw.get("unit", ""),
            values=y_axis_raw.get("values", []),
            scale=y_axis_raw.get("scale", "linear"),
        )

        y2_axis = None
        if y2_axis_raw and isinstance(y2_axis_raw, dict):
            y2_axis = AxisInfo(
                label=y2_axis_raw.get("label", ""),
                unit=y2_axis_raw.get("unit", ""),
                values=y2_axis_raw.get("values", []),
                scale=y2_axis_raw.get("scale", "linear"),
            )

        series_raw = raw.get("series", [])
        series = [
            SeriesData(
                name=s.get("name", ""),
                values=s.get("values", []),
                color=s.get("color", ""),
                marker_style=s.get("marker_style", ""),
            )
            for s in series_raw
            if isinstance(s, dict)
        ]

        return PlotData(
            title=raw.get("title", ""),
            plot_type=raw.get("plot_type", "unknown"),
            x_axis=x_axis,
            y_axis=y_axis,
            y2_axis=y2_axis,
            series=series,
            legend_entries=raw.get("legend_entries", []),
            annotations=raw.get("annotations", []),
            confidence=raw.get("confidence", 0.0),
            raw_response=raw,
        )


async def extract_plot_data(
    *,
    image_data: bytes,
    source_path: str | None = None,
) -> VisionExtractionResult:
    """Convenience function for one-off plot extraction.

    Creates a default ``PlotExtractor`` and extracts data from the
    provided image.

    Args:
        image_data: Raw image bytes.
        source_path: Optional path for provenance tracking.

    Returns:
        VisionExtractionResult with plot_data populated.
    """
    extractor = PlotExtractor()
    return await extractor.extract(
        image_data=image_data,
        source_path=source_path,
    )
