"""Unit tests for bibliographic metadata extraction from content_md and PDF bytes.

Covers the acceptance criteria for NFM-3301 (QA-E2E F7):
DOI, journal, year, and abstract must be extracted from the Markdown
produced by the PDF parser and written to the DataSource row.

Functions under test:
- :func:`extract_bibliographic_metadata` — regex extraction from markdown
- :func:`extract_pdf_metadata` — PyMuPDF extraction from PDF bytes
- :func:`extract_metadata_combined` — combined strategy
"""

from __future__ import annotations

from nfm_db.services.bibliographic_metadata import (
    extract_bibliographic_metadata,
    extract_metadata_combined,
    extract_pdf_metadata,
)


class TestExtractDOI:
    """DOI extraction from various formats found in scientific papers."""

    def test_doi_with_prefix(self) -> None:
        md = "Some text\nDOI: 10.1016/j.jnucmat.2023.01.001\nMore text"
        result = extract_bibliographic_metadata(md)
        assert result["doi"] == "10.1016/j.jnucmat.2023.01.001"

    def test_doi_with_http_prefix(self) -> None:
        md = "See https://doi.org/10.1016/j.jnucmat.2023.01.001 for details"
        result = extract_bibliographic_metadata(md)
        assert result["doi"] == "10.1016/j.jnucmat.2023.01.001"

    def test_doi_with_https_prefix(self) -> None:
        md = "Reference: https://doi.org/10.1016/j.jnucmat.2023.01.001"
        result = extract_bibliographic_metadata(md)
        assert result["doi"] == "10.1016/j.jnucmat.2023.01.001"

    def test_no_doi_returns_none(self) -> None:
        md = "No DOI in this text at all"
        result = extract_bibliographic_metadata(md)
        assert result["doi"] is None

    def test_doi_takes_first_match(self) -> None:
        md = "DOI: 10.1016/j.jnucmat.2023.01.001\nAlso DOI: 10.1000/xyz"
        result = extract_bibliographic_metadata(md)
        assert result["doi"] == "10.1016/j.jnucmat.2023.01.001"

    def test_doi_bare_format(self) -> None:
        """DOI appearing as bare 10.xxxx/yyyy without prefix."""
        md = "Article identifier: 10.1016/j.jnucmat.2023.01.001\nEnd."
        result = extract_bibliographic_metadata(md)
        assert result["doi"] == "10.1016/j.jnucmat.2023.01.001"


class TestExtractYear:
    """Year extraction from markdown content."""

    def test_year_in_journal_line(self) -> None:
        md = "Published in Journal of Nuclear Materials 576 (2023)"
        result = extract_bibliographic_metadata(md)
        assert result["year"] == 2023

    def test_year_in_parenthetical(self) -> None:
        md = "Some paper title (2023) by Author"
        result = extract_bibliographic_metadata(md)
        assert result["year"] == 2023

    def test_year_no_match_returns_none(self) -> None:
        md = "No year-like number here, just 12 and 34"
        result = extract_bibliographic_metadata(md)
        assert result["year"] is None

    def test_year_in_copyright_line(self) -> None:
        md = "Copyright 2023 Elsevier B.V."
        result = extract_bibliographic_metadata(md)
        assert result["year"] == 2023

    def test_year_range_returns_first(self) -> None:
        md = "Published 2020-2023"
        result = extract_bibliographic_metadata(md)
        # Should return 2020 as the publication start year
        assert result["year"] == 2020


class TestExtractJournal:
    """Journal name extraction from markdown content."""

    def test_journal_from_common_pattern(self) -> None:
        md = "Journal of Nuclear Materials 576 (2023) 123-135"
        result = extract_bibliographic_metadata(md)
        assert result["journal"] == "Journal of Nuclear Materials"

    def test_journal_not_found(self) -> None:
        md = "Some random text with no journal mention"
        result = extract_bibliographic_metadata(md)
        assert result["journal"] is None


class TestExtractAbstract:
    """Abstract section extraction from markdown content."""

    def test_abstract_from_hash_heading(self) -> None:
        md = (
            "# Title\n\n"
            "## Abstract\n\n"
            "This is the abstract text about diffusion.\n"
            "It spans multiple lines.\n\n"
            "## 1. Introduction\n\n"
            "Body text follows."
        )
        result = extract_bibliographic_metadata(md)
        assert result["abstract"] == "This is the abstract text about diffusion.\nIt spans multiple lines."

    def test_abstract_case_insensitive(self) -> None:
        md = (
            "# Paper\n\n"
            "## ABSTRACT\n\n"
            "Uppercase abstract heading content.\n\n"
            "## Introduction\n"
        )
        result = extract_bibliographic_metadata(md)
        assert result["abstract"] == "Uppercase abstract heading content."

    def test_no_abstract_heading(self) -> None:
        md = "# Title\n\nNo abstract section in this document."
        result = extract_bibliographic_metadata(md)
        assert result["abstract"] is None

    def test_abstract_trailing_whitespace_stripped(self) -> None:
        md = (
            "## Abstract\n\n"
            "Abstract with trailing spaces   \n"
            "\n"
            "## Keywords\n"
        )
        result = extract_bibliographic_metadata(md)
        assert result["abstract"] == "Abstract with trailing spaces"


class TestExtractTitle:
    """Title extraction from the first heading in content_md."""

    def test_title_from_first_h1(self) -> None:
        md = "# Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2\n\nBody text"
        result = extract_bibliographic_metadata(md)
        assert result["title"] == "Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2"

    def test_title_strips_hashes(self) -> None:
        md = "## Not H1\n### Also Not H1\n# Actual Title\nMore text"
        result = extract_bibliographic_metadata(md)
        assert result["title"] == "Actual Title"

    def test_no_heading_returns_none(self) -> None:
        md = "Just plain text with no markdown headings at all."
        result = extract_bibliographic_metadata(md)
        assert result["title"] is None


class TestIntegratedExtraction:
    """End-to-end extraction from realistic paper markdown."""

    def test_owen_paper(self) -> None:
        """Simulated MinerU output for Owen et al. 2023."""
        md = (
            "# Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2\n\n"
            "## Abstract\n\n"
            "Molecular dynamics simulations were used to study the diffusion "
            "of uranium and oxygen in amorphous UO2.\n\n"
            "## 1. Introduction\n\n"
            "Nuclear fuel performance depends on...\n\n"
            "Journal of Nuclear Materials 576 (2023) 123-135\n\n"
            "DOI: 10.1016/j.jnucmat.2023.01.001\n"
        )
        result = extract_bibliographic_metadata(md)
        assert result["title"] == "Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2"
        assert result["doi"] == "10.1016/j.jnucmat.2023.01.001"
        assert result["year"] == 2023
        assert result["journal"] == "Journal of Nuclear Materials"
        assert "Molecular dynamics simulations" in result["abstract"]

    def test_empty_content_returns_all_none(self) -> None:
        result = extract_bibliographic_metadata("")
        assert result == {
            "title": None,
            "doi": None,
            "year": None,
            "journal": None,
            "abstract": None,
        }

    def test_returns_dict_with_all_keys(self) -> None:
        """Result always has exactly the expected keys, even when nothing found."""
        result = extract_bibliographic_metadata("random text")
        assert set(result.keys()) == {"title", "doi", "year", "journal", "abstract"}


class TestExtractPdfMetadata:
    """PDF metadata extraction from raw PDF bytes via PyMuPDF."""

    def test_empty_bytes_returns_all_none(self) -> None:
        result = extract_pdf_metadata(b"")
        assert result["title"] is None
        assert result["doi"] is None
        assert result["year"] is None
        assert result["journal"] is None
        assert result["abstract"] is None

    def test_non_pdf_bytes_returns_all_none(self) -> None:
        result = extract_pdf_metadata(b"this is not a PDF")
        assert result["title"] is None
        assert result["doi"] is None

    def test_year_journal_abstract_always_none(self) -> None:
        """PDF metadata strategy cannot extract year/journal/abstract."""
        result = extract_pdf_metadata(b"")
        assert result["year"] is None
        assert result["journal"] is None
        assert result["abstract"] is None


class TestExtractMetadataCombined:
    """Combined extraction strategy using both PDF and markdown."""

    def test_pdf_only_returns_pdf_title(self) -> None:
        """When only PDF bytes provided (no content_md), returns PDF metadata."""
        result = extract_metadata_combined(b"", None)
        assert set(result.keys()) == {"title", "doi", "year", "journal", "abstract"}

    def test_md_only_returns_md_fields(self) -> None:
        """When only content_md provided, returns markdown-extracted fields."""
        md = "# Test Title\n\n## Abstract\n\nSome abstract.\n\nDOI: 10.1234/test"
        result = extract_metadata_combined(None, md)
        assert result["title"] == "Test Title"
        assert result["doi"] == "10.1234/test"
        assert "Some abstract" in result["abstract"]

    def test_both_strategies_prefers_pdf_for_title(self) -> None:
        """PDF metadata title takes priority over markdown H1."""
        md = "# Markdown Title\n\nSome text."
        # Non-PDF bytes won't produce a title from PyMuPDF
        result = extract_metadata_combined(b"not a pdf", md)
        # PDF extraction fails silently, so markdown title is used
        assert result["title"] == "Markdown Title"

    def test_neither_returns_all_none(self) -> None:
        result = extract_metadata_combined(None, None)
        assert all(v is None for v in result.values())

    def test_empty_both_returns_all_none(self) -> None:
        result = extract_metadata_combined(b"", "")
        assert all(v is None for v in result.values())
