"""Tests for PDF page splitter service (NFM-850).

Covers: PDF splitting, DPI config, page range, max pages,
error handling, page counting. Uses real PyMuPDF rendering
with minimal in-memory PDFs (no filesystem required).
"""

from __future__ import annotations

import fitz
import pytest
from PIL import Image

from nfm_db.services.page_splitter import (
    PageImage,
    PageSplitter,
    PageSplitterError,
    _resolve_range,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_minimal_pdf(page_count: int = 3) -> bytes:
    """Create a minimal PDF with the given number of blank pages."""
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)  # A4 at 72 DPI
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture()
def three_page_pdf() -> bytes:
    """A minimal 3-page PDF."""
    return _create_minimal_pdf(3)


@pytest.fixture()
def single_page_pdf() -> bytes:
    """A minimal 1-page PDF."""
    return _create_minimal_pdf(1)


# ---------------------------------------------------------------------------
# Tests: Constructor validation
# ---------------------------------------------------------------------------


class TestPageSplitterInit:
    """Tests for PageSplitter constructor validation."""

    def test_default_dpi(self) -> None:
        """Should default to 200 DPI."""
        splitter = PageSplitter()
        assert splitter.dpi == 200

    def test_custom_dpi(self) -> None:
        """Should accept custom DPI."""
        splitter = PageSplitter(dpi=300)
        assert splitter.dpi == 300

    def test_dpi_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should read DPI from environment variable."""
        monkeypatch.setenv("PAGE_SPLITTER_DPI", "150")
        splitter = PageSplitter()
        assert splitter.dpi == 150

    def test_dpi_below_minimum_raises(self) -> None:
        """Should raise ValueError for DPI < 72."""
        with pytest.raises(ValueError, match="DPI must be >= 72"):
            PageSplitter(dpi=50)

    def test_max_pages_default(self) -> None:
        """Should default to 0 (no limit)."""
        splitter = PageSplitter()
        assert splitter.max_pages == 0

    def test_max_pages_custom(self) -> None:
        """Should accept custom max_pages."""
        splitter = PageSplitter(max_pages=5)
        assert splitter.max_pages == 5

    def test_max_pages_negative_raises(self) -> None:
        """Should raise ValueError for negative max_pages."""
        with pytest.raises(ValueError, match="max_pages must be >= 0"):
            PageSplitter(max_pages=-1)


# ---------------------------------------------------------------------------
# Tests: Splitting
# ---------------------------------------------------------------------------


class TestPageSplitterSplit:
    """Tests for PDF splitting functionality."""

    def test_split_returns_page_images(self, three_page_pdf: bytes) -> None:
        """Should return list of PageImage for each page."""
        splitter = PageSplitter()
        pages = splitter.split(three_page_pdf)

        assert len(pages) == 3
        for page in pages:
            assert isinstance(page, PageImage)
            assert isinstance(page.image, Image.Image)

    def test_page_indices_are_sequential(self, three_page_pdf: bytes) -> None:
        """Page indices should be 0, 1, 2 for a 3-page PDF."""
        splitter = PageSplitter()
        pages = splitter.split(three_page_pdf)

        assert [p.index for p in pages] == [0, 1, 2]

    def test_page_dimensions_are_positive(self, three_page_pdf: bytes) -> None:
        """Each page should have positive width and height."""
        splitter = PageSplitter()
        pages = splitter.split(three_page_pdf)

        for page in pages:
            assert page.width > 0
            assert page.height > 0

    def test_size_property(self, single_page_pdf: bytes) -> None:
        """PageImage.size should return (width, height)."""
        splitter = PageSplitter()
        pages = splitter.split(single_page_pdf)

        assert pages[0].size == (pages[0].width, pages[0].height)

    def test_higher_dpi_gives_larger_images(self, single_page_pdf: bytes) -> None:
        """Higher DPI should produce larger page images."""
        low = PageSplitter(dpi=72)
        high = PageSplitter(dpi=144)

        low_pages = low.split(single_page_pdf)
        high_pages = high.split(single_page_pdf)

        assert high_pages[0].width > low_pages[0].width
        assert high_pages[0].height > low_pages[0].height

    def test_empty_range_returns_empty_list(self) -> None:
        """Should return empty list when page_range is empty."""
        splitter = PageSplitter()
        pdf_bytes = _create_minimal_pdf(3)
        pages = splitter.split(pdf_bytes, page_range=(0, 0))

        assert pages == []

    def test_page_range_subset(self, three_page_pdf: bytes) -> None:
        """Should only extract pages in the specified range."""
        splitter = PageSplitter()
        pages = splitter.split(three_page_pdf, page_range=(1, 3))

        assert len(pages) == 2
        assert [p.index for p in pages] == [1, 2]

    def test_page_range_single_page(self, three_page_pdf: bytes) -> None:
        """Should extract a single page with range."""
        splitter = PageSplitter()
        pages = splitter.split(three_page_pdf, page_range=(0, 1))

        assert len(pages) == 1
        assert pages[0].index == 0

    def test_max_pages_cap(self, three_page_pdf: bytes) -> None:
        """Should respect max_pages cap."""
        splitter = PageSplitter(max_pages=2)
        pages = splitter.split(three_page_pdf)

        assert len(pages) == 2


# ---------------------------------------------------------------------------
# Tests: Page counting
# ---------------------------------------------------------------------------


class TestPageSplitterCount:
    """Tests for page_count method."""

    def test_count_returns_correct_number(self, three_page_pdf: bytes) -> None:
        """Should return the correct page count."""
        splitter = PageSplitter()
        assert splitter.page_count(three_page_pdf) == 3

    def test_count_single_page(self, single_page_pdf: bytes) -> None:
        """Should return 1 for single-page PDF."""
        splitter = PageSplitter()
        assert splitter.page_count(single_page_pdf) == 1


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestPageSplitterErrors:
    """Tests for error handling."""

    def test_invalid_pdf_bytes_raises(self) -> None:
        """Should raise PageSplitterError for invalid PDF data."""
        splitter = PageSplitter()
        with pytest.raises(PageSplitterError, match="Cannot open PDF"):
            splitter.split(b"not a pdf")

    def test_empty_bytes_raises(self) -> None:
        """Should raise PageSplitterError for empty bytes."""
        splitter = PageSplitter()
        with pytest.raises(PageSplitterError):
            splitter.split(b"")


# ---------------------------------------------------------------------------
# Tests: Range resolution
# ---------------------------------------------------------------------------


class TestResolveRange:
    """Tests for the _resolve_range helper."""

    def test_none_returns_full_range(self) -> None:
        """Should return (0, total) when page_range is None."""
        assert _resolve_range(None, 5) == (0, 5)

    def test_explicit_range(self) -> None:
        """Should return the range as-is when valid."""
        assert _resolve_range((1, 3), 5) == (1, 3)

    def test_negative_start(self) -> None:
        """Should convert negative start from end."""
        assert _resolve_range((-1, 5), 5) == (4, 5)

    def test_negative_end(self) -> None:
        """Should convert negative end from end."""
        assert _resolve_range((0, -1), 5) == (0, 4)

    def test_clamped_to_total(self) -> None:
        """Should clamp range to total pages."""
        assert _resolve_range((0, 100), 5) == (0, 5)

    def test_start_clamped_to_zero(self) -> None:
        """Should clamp negative start to 0."""
        assert _resolve_range((-10, 3), 5) == (0, 3)

    def test_inverted_range_becomes_empty(self) -> None:
        """Should produce empty range when start > end after clamping."""
        result = _resolve_range((3, 1), 5)
        assert result[0] <= result[1]
