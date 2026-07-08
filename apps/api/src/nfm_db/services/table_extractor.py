"""VLM-based table structure extractor (NFM-851, B1.3).

Extracts structured data from scientific table images using
a Vision Language Model (VLM). Produces a ``TableData`` schema
with headers, rows, merged cells, and confidence information.

When VLM is unavailable, automatically falls back to OCR-based
extraction via ``OcrFallback``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nfm_db.schemas.vision_extraction import (
    TableCell,
    TableData,
    TableHeader,
    VisionExtractionResult,
)
from nfm_db.services.ocr_fallback import (
    OcrFallback,
    ocr_fallback_table_result,
)
from nfm_db.services.vision_client import (
    VisionClient,
    VisionClientError,
    build_table_extraction_prompt,
)

logger = logging.getLogger(__name__)


class TableExtractor:
    """Extracts structured data from table images via VLM.

    Wraps ``VisionClient`` with table-specific prompt construction
    and result parsing into ``TableData`` schemas.

    Usage::

        extractor = TableExtractor()
        result = await extractor.extract(
            image_data=png_bytes,
            source_path="figures/table1.png",
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
        """Extract table data from an image using VLM.

        Falls back to OCR-based extraction when the VLM is unavailable
        or returns an error.

        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.).
            source_path: Optional path for provenance tracking.

        Returns:
            VisionExtractionResult with table_data populated. Uses
            ``fallback_used=True`` when OCR fallback was activated.
        """
        try:
            return await self._extract_vlm(
                image_data=image_data,
                source_path=source_path,
            )
        except VisionClientError as exc:
            logger.warning(
                "VLM table extraction failed, falling back to OCR: %s", exc
            )
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
        system_prompt = build_table_extraction_prompt()
        user_prompt = (
            "Extract all table data from this scientific figure. "
            "Include column headers, all row values, merged cell spans, "
            "and footnotes as structured JSON."
        )

        raw_data = await self.client.extract(
            image_data=image_data,
            prompt=user_prompt,
            system_prompt=system_prompt,
        )

        elapsed_ms = (time.monotonic() - start) * 1000

        table_data = self._parse_table_response(raw_data)

        return VisionExtractionResult(
            figure_type="table",
            plot_data=None,
            table_data=table_data,
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
        return ocr_fallback_table_result(
            ocr_result=ocr_result,
            source_path=source_path,
        )

    @staticmethod
    def _parse_table_response(raw: dict[str, Any]) -> TableData:
        """Parse raw VLM dict into a TableData schema.

        Handles missing or malformed fields gracefully with defaults.
        """
        # Parse headers
        headers_raw = raw.get("headers", {})
        if isinstance(headers_raw, dict):
            headers = TableHeader(
                columns=headers_raw.get("columns", []),
                sub_headers=headers_raw.get("sub_headers"),
            )
        elif isinstance(headers_raw, list):
            headers = TableHeader(columns=headers_raw)
        else:
            headers = TableHeader()

        # Parse rows
        rows_raw = raw.get("rows", [])
        rows: list[list[TableCell]] = []
        for row_data in rows_raw:
            if not isinstance(row_data, list):
                continue
            row: list[TableCell] = []
            for cell_data in row_data:
                if isinstance(cell_data, dict):
                    row.append(TableCell(
                        value=cell_data.get("value", ""),
                        row_span=cell_data.get("row_span", 1),
                        col_span=cell_data.get("col_span", 1),
                        is_header=cell_data.get("is_header", False),
                        confidence=cell_data.get("confidence", 1.0),
                    ))
                elif isinstance(cell_data, str):
                    row.append(TableCell(value=cell_data))
            rows.append(row)

        return TableData(
            title=raw.get("title", ""),
            headers=headers,
            rows=rows,
            num_columns=raw.get("num_columns", len(headers.columns)),
            num_rows=raw.get("num_rows", len(rows)),
            has_merged_cells=raw.get("has_merged_cells", False),
            notes=raw.get("notes", []),
            confidence=raw.get("confidence", 0.0),
            raw_response=raw,
        )


async def extract_table_data(
    *,
    image_data: bytes,
    source_path: str | None = None,
) -> VisionExtractionResult:
    """Convenience function for one-off table extraction.

    Creates a default ``TableExtractor`` and extracts data from the
    provided image.

    Args:
        image_data: Raw image bytes.
        source_path: Optional path for provenance tracking.

    Returns:
        VisionExtractionResult with table_data populated.
    """
    extractor = TableExtractor()
    return await extractor.extract(
        image_data=image_data,
        source_path=source_path,
    )
