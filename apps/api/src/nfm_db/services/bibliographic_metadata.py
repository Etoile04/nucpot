"""Bibliographic metadata extraction from PDF bytes and Markdown content.

Two extraction strategies are used:

1. **PDF metadata** (fast): reads ``fitz.Document.metadata`` directly
   from the raw PDF bytes — this gives us title and DOI from the PDF's
   XMP/stream metadata with zero parsing cost.

2. **Markdown regex** (fallback): parses the ``content_md`` field of a
   :class:`DataSource` row to extract structured bibliographic fields
   (DOI, title, journal, year, abstract) that the PDF→Markdown parser
   produces.

Both strategies are deliberately conservative: they prefer to return
``None`` over a wrong value.  Callers should only write non-None fields
to the database, preserving any existing values.

NFM-3301 (QA-E2E F7): PDF metadata fields null after upload.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DOI extraction
# ---------------------------------------------------------------------------

_DOI_PREFIX_PATTERNS = [
    # https://doi.org/10.xxxx/yyyy or http://doi.org/10.xxxx/yyyy
    re.compile(r"https?://doi\.org/(10\.\d{4,9}/[^\s]+)", re.IGNORECASE),
    # DOI: 10.xxxx/yyyy or doi: 10.xxxx/yyyy
    re.compile(r"(?:DOI|doi):\s*(10\.\d{4,9}/[^\s]+)", re.IGNORECASE),
    # Bare DOI: 10.xxxx/yyyy (at word boundary to avoid matching in URLs)
    re.compile(r"(?<![/\w])(10\.\d{4,9}/[^\s,;]+)"),
]


def _extract_doi(text: str) -> str | None:
    """Return the first DOI found in *text*, or ``None``."""
    for pattern in _DOI_PREFIX_PATTERNS:
        match = pattern.search(text)
        if match:
            doi = match.group(1)
            # Strip trailing punctuation that commonly trails a DOI in prose.
            return doi.rstrip(".,;:)")
    return None


# ---------------------------------------------------------------------------
# Title extraction (first H1 heading)
# ---------------------------------------------------------------------------

_H1_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _extract_title(text: str) -> str | None:
    """Return the text of the first ``#`` heading, or ``None``."""
    match = _H1_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Year extraction
# ---------------------------------------------------------------------------

_YEAR_PATTERNS = [
    # "Journal Name volume (YEAR)" — common citation line
    re.compile(r"\((\d{4})\)\s*$", re.MULTILINE),
    # "(YEAR)" standalone parenthetical
    re.compile(r"\((\d{4})\)", re.MULTILINE),
    # "Copyright YEAR" or "© YEAR"
    re.compile(r"(?:Copyright|©)\s+(\d{4})", re.IGNORECASE),
    # "Published YYYY" or similar
    re.compile(r"(?:Published|published)\s+(\d{4})", re.IGNORECASE),
    # "YYYY-YYYY" range -- capture first year
    re.compile(r"\b(\d{4})\s*[-]\s*\d{4}\b"),
]


def _extract_year(text: str) -> int | None:
    """Return the first plausible publication year (1900-2099), or ``None``."""
    for pattern in _YEAR_PATTERNS:
        match = pattern.search(text)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2099:
                return year
    return None


# ---------------------------------------------------------------------------
# Journal name extraction
# ---------------------------------------------------------------------------

_JOURNAL_PATTERN = re.compile(
    r"((?:Journal|J\.|J\s)[\w\s&]+?(?:Materials|Science|Physics|Chemistry|"
    r"Engineering|Energy|Nuclear|Applied|Computational|Solid State|"
    r"Acta|Letters|Communications|Reviews|Progress|Reports|"
    r"Transactions|Proceedings|Advances)[\w\s&]*?)"
    r"\s+\d+",
    re.IGNORECASE,
)


def _extract_journal(text: str) -> str | None:
    """Return a probable journal name from *text*, or ``None``."""
    match = _JOURNAL_PATTERN.search(text)
    if match:
        name = match.group(1).strip()
        return name if name else None
    return None


# ---------------------------------------------------------------------------
# Abstract extraction (## Abstract / ## ABSTRACT section)
# ---------------------------------------------------------------------------

_ABSTRACT_HEADING = re.compile(
    r"^##\s*(?:Abstract|ABSTRACT|abstract)\s*$",
    re.MULTILINE,
)
_NEXT_HEADING = re.compile(r"^#{1,3}\s+", re.MULTILINE)


def _extract_abstract(text: str) -> str | None:
    """Return the text between ``## Abstract`` and the next heading, or ``None``."""
    heading_match = _ABSTRACT_HEADING.search(text)
    if heading_match is None:
        return None

    # Find the content start (line after the heading)
    content_start = heading_match.end()
    # Find the next heading after the abstract heading
    next_heading = _NEXT_HEADING.search(text, content_start)
    if next_heading is not None:
        abstract_text = text[content_start:next_heading.start()]
    else:
        abstract_text = text[content_start:]

    # Strip leading/trailing whitespace and collapse internal newlines
    lines = abstract_text.strip().splitlines()
    cleaned = "\n".join(line.rstrip() for line in lines if line.strip())
    return cleaned or None


# ---------------------------------------------------------------------------
# PDF metadata extraction (fast, from raw PDF bytes)
# ---------------------------------------------------------------------------


def extract_pdf_metadata(pdf_bytes: bytes) -> dict[str, Any]:
    """Extract bibliographic metadata directly from PDF binary metadata.

    Uses PyMuPDF (fitz) to read ``Document.metadata`` which typically
    contains title, author, subject, and keywords.  Also checks for
    a DOI in the metadata fields.

    Returns a dict with keys ``title``, ``doi``, ``year``, ``journal``,
    and ``abstract``.  Each value is ``None`` when the field could not
    be reliably detected.  ``year``, ``journal``, and ``abstract`` are
    always ``None`` from this strategy (they require content analysis).
    """
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("extract_pdf_metadata: PyMuPDF not available")
        return _empty_metadata()

    if not pdf_bytes:
        return _empty_metadata()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        logger.debug("extract_pdf_metadata: PyMuPDF could not open bytes", exc_info=True)
        return _empty_metadata()
    try:
        meta = doc.metadata or {}
        title = meta.get("title", "").strip() or None
        doi = _extract_doi_from_pdf_meta(meta)
        return {
            "title": title,
            "doi": doi,
            "year": None,
            "journal": None,
            "abstract": None,
        }
    finally:
        doc.close()


def _extract_doi_from_pdf_meta(meta: dict[str, str]) -> str | None:
    """Look for a DOI in PDF metadata fields (keywords, subject, etc.)."""
    for key in ("keywords", "subject", "doi"):
        value = meta.get(key, "")
        if value:
            doi = _extract_doi(value)
            if doi:
                return doi
    return None


# ---------------------------------------------------------------------------
# Markdown metadata extraction (regex-based, from content_md)
# ---------------------------------------------------------------------------

def extract_bibliographic_metadata(content_md: str) -> dict[str, Any]:
    """Extract bibliographic metadata from a Markdown document.

    Returns a dict with keys ``title``, ``doi``, ``year``, ``journal``,
    and ``abstract``.  Each value is ``None`` when the field could not
    be reliably detected.

    The function is deliberately conservative: it prefers to return
    ``None`` over a wrong value.  Callers should only write non-None
    fields to the database, preserving any existing values.
    """
    if not content_md:
        return _empty_metadata()

    return {
        "title": _extract_title(content_md),
        "doi": _extract_doi(content_md),
        "year": _extract_year(content_md),
        "journal": _extract_journal(content_md),
        "abstract": _extract_abstract(content_md),
    }


def extract_metadata_combined(
    pdf_bytes: bytes | None,
    content_md: str | None,
) -> dict[str, Any]:
    """Extract metadata using both strategies, preferring PDF metadata
    for title/DOI and markdown regex for journal/year/abstract.

    This is the recommended entry point for ``process_literature()``.
    """
    result = _empty_metadata()

    # Strategy 1: PDF binary metadata (fast, reliable for title/DOI)
    if pdf_bytes:
        try:
            pdf_meta = extract_pdf_metadata(pdf_bytes)
            for key in ("title", "doi"):
                if pdf_meta[key] is not None:
                    result[key] = pdf_meta[key]
        except Exception:
            logger.debug("extract_metadata_combined: PDF metadata extraction failed", exc_info=True)

    # Strategy 2: Markdown regex (broader coverage: journal, year, abstract)
    if content_md:
        try:
            md_meta = extract_bibliographic_metadata(content_md)
            for key in ("title", "doi", "year", "journal", "abstract"):
                # PDF metadata takes priority for title/DOI
                if result[key] is None and md_meta[key] is not None:
                    result[key] = md_meta[key]
        except Exception:
            logger.debug("extract_metadata_combined: markdown extraction failed", exc_info=True)

    return result


def _empty_metadata() -> dict[str, Any]:
    """Return a metadata dict with all fields set to ``None``."""
    return {
        "title": None,
        "doi": None,
        "year": None,
        "journal": None,
        "abstract": None,
    }


__all__ = [
    "extract_bibliographic_metadata",
    "extract_metadata_combined",
    "extract_pdf_metadata",
]
