"""PDF page splitter service (NFM-850).

Converts PDF documents into per-page PIL Images for downstream
layout analysis and figure detection.

Uses PyMuPDF (fitz) for fast, reliable PDF rendering with
configurable DPI and optional page range selection.

Configuration via environment variables:
  PAGE_SPLITTER_DPI - render DPI (default: 200)
  PAGE_SPLITTER_MAX_PAGES - cap on pages processed (default: 0 = unlimited)
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from typing import BinaryIO

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DPI = 200
_DEFAULT_MAX_PAGES = 0  # 0 = no limit


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageImage:
    """A single rendered page image with metadata."""

    index: int
    image: Image.Image
    width: int
    height: int

    @property
    def size(self) -> tuple[int, int]:
        """Return (width, height) tuple."""
        return (self.width, self.height)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PageSplitterError(Exception):
    """Raised when PDF page splitting fails."""


# ---------------------------------------------------------------------------
# PageSplitter
# ---------------------------------------------------------------------------


class PageSplitter:
    """Split a PDF document into per-page images.

    Usage::

        splitter = PageSplitter()
        pages = splitter.split(pdf_bytes)
        for page in pages:
            # page.image is a PIL Image
            # page.index is zero-based page number
            ...
    """

    def __init__(
        self,
        *,
        dpi: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        self.dpi = dpi or int(os.environ.get("PAGE_SPLITTER_DPI", _DEFAULT_DPI))
        self.max_pages = max_pages if max_pages is not None else int(
            os.environ.get("PAGE_SPLITTER_MAX_PAGES", _DEFAULT_MAX_PAGES)
        )

        if self.dpi < 72:
            raise ValueError(f"DPI must be >= 72, got {self.dpi}")
        if self.max_pages < 0:
            raise ValueError(f"max_pages must be >= 0, got {self.max_pages}")

    def split(
        self,
        pdf_source: bytes | BinaryIO | str,
        *,
        page_range: tuple[int, int] | None = None,
    ) -> list[PageImage]:
        """Split a PDF into per-page PIL Images.

        Args:
            pdf_source: PDF data as raw bytes, a file-like object, or a
                        file path string.
            page_range: Optional (start, end) tuple of zero-based page
                        indices to extract (inclusive start, exclusive end).
                        If None, all pages are extracted.

        Returns:
            List of PageImage objects in page order.

        Raises:
            PageSplitterError: If the PDF cannot be opened or rendered.
        """
        doc = _open_document(pdf_source)

        try:
            total_pages = doc.page_count
            start, end = _resolve_range(page_range, total_pages)

            effective_end = min(end, start + self.max_pages) if self.max_pages > 0 else end
            effective_end = min(effective_end, total_pages)

            logger.info(
                "Splitting PDF: total_pages=%d, range=(%d, %d), dpi=%d",
                total_pages,
                start,
                effective_end,
                self.dpi,
            )

            pages: list[PageImage] = []
            zoom = self.dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)

            for page_idx in range(start, effective_end):
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(matrix=matrix)

                img_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes))
                image.load()  # Force load into memory so BytesIO can be GC'd

                pages.append(
                    PageImage(
                        index=page_idx,
                        image=image,
                        width=pix.width,
                        height=pix.height,
                    )
                )

            logger.info(
                "PDF split complete: %d pages rendered out of %d total",
                len(pages),
                total_pages,
            )
            return pages

        except Exception as exc:
            raise PageSplitterError(
                f"Failed to render PDF pages: {exc}"
            ) from exc
        finally:
            doc.close()

    def page_count(self, pdf_source: bytes | BinaryIO | str) -> int:
        """Return the total number of pages in the PDF without rendering.

        Args:
            pdf_source: PDF data as raw bytes, a file-like object, or a
                        file path string.

        Returns:
            Number of pages in the document.

        Raises:
            PageSplitterError: If the PDF cannot be opened.
        """
        doc = _open_document(pdf_source)
        try:
            return doc.page_count
        finally:
            doc.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_document(source: bytes | BinaryIO | str) -> fitz.Document:
    """Open a PDF document from bytes, file-like object, or path."""
    try:
        if isinstance(source, str):
            return fitz.open(source)
        if isinstance(source, (bytes, bytearray)):
            return fitz.open("pdf", source)
        if hasattr(source, "read"):
            return fitz.open("pdf", source.read())
        raise PageSplitterError(f"Unsupported PDF source type: {type(source)}")
    except Exception as exc:
        raise PageSplitterError(
            f"Cannot open PDF document: {exc}"
        ) from exc


def _resolve_range(
    page_range: tuple[int, int] | None,
    total_pages: int,
) -> tuple[int, int]:
    """Resolve page range to (start, end) tuple.

    Returns:
        Tuple of (start_inclusive, end_exclusive) page indices.
    """
    if page_range is None:
        return (0, total_pages)

    start, end = page_range
    if start < 0:
        start = max(0, total_pages + start)
    if end < 0:
        end = total_pages + end

    start = max(0, min(start, total_pages))
    end = max(start, min(end, total_pages))

    return (start, end)
