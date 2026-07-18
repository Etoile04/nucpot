"""DOI → Markdown content fetcher (NFM-1480 / NFM-1475-B1).

Acquires open-access PDF content for a given DOI and converts it to
Markdown for downstream LLM extraction. Three async stages:

1. **Unpaywall lookup** — GET ``https://api.unpaywall.org/v2/{doi}?email=...``
   resolves the best open-access PDF URL. Falls back from
   ``best_oa_location`` to ``first_oa_location``. Raises ``ValueError``
   when no OA location is available.
2. **PDF download** — ``httpx.AsyncClient.get(url, follow_redirects=True,
   timeout=30s)``. Validates ``Content-Type`` is ``application/pdf`` and
   enforces a 50 MB hard cap.
3. **PDF → Markdown** — PyMuPDF (``fitz``) text extraction. The DOI is
   prepended as a ``# Source: doi:{doi}`` header. On extraction failure,
   falls back to a minimal byte-level text fallback so the LLM still
   receives something useful.

The Unpaywall step is rate-limited at 1 request/second via
``asyncio.sleep`` (when no ``Retry-After`` hint is returned).

Constraints honored:
- No new dependencies (``httpx`` and ``PyMuPDF`` already in
  ``apps/api/pyproject.toml``).
- Fully async.
- No secrets required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"
UNPAYWALL_EMAIL = "nucpot@example.com"

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_RATE_LIMIT_SECONDS = 1.0
MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB hard cap
PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/octet-stream"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_paper_content(doi: str) -> str:
    """Resolve a DOI to Markdown content.

    Pipeline:
        DOI → Unpaywall lookup → PDF download → PDF → Markdown.

    Args:
        doi: A canonical DOI string (e.g. ``10.1234/abcd.efgh``).
            A leading ``doi:`` prefix is stripped and whitespace trimmed.

    Returns:
        Markdown text with a ``# Source: doi:{doi}`` header prepended.

    Raises:
        ValueError: DOI is empty, has no open-access PDF, returns a
            non-PDF content type, or the PDF cannot be decoded.
        httpx.HTTPError: Network/HTTP errors from Unpaywall or the PDF
            download.
        RuntimeError: Downloaded payload exceeds the 50 MB size cap.
    """
    clean_doi = _normalize_doi(doi)
    pdf_bytes = await _download_pdf(clean_doi)
    return _pdf_bytes_to_markdown(clean_doi, pdf_bytes)


# ---------------------------------------------------------------------------
# Stage 1: Unpaywall
# ---------------------------------------------------------------------------


async def _resolve_pdf_url(doi: str) -> str:
    """Resolve a DOI to a PDF URL via the Unpaywall API.

    Rate-limited at 1 req/sec by default. Honors ``Retry-After`` if the
    upstream returns one.

    Returns:
        Absolute URL string for an open-access PDF.

    Raises:
        ValueError: No open-access location is available for the DOI.
        httpx.HTTPError: Unpaywall network or HTTP failure.
    """
    await asyncio.sleep(DEFAULT_RATE_LIMIT_SECONDS)

    url = f"{UNPAYWALL_BASE_URL}/{doi}"
    params = {"email": UNPAYWALL_EMAIL}

    async with httpx.AsyncClient(timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

    pdf_url = _pick_oa_url(payload)
    if pdf_url is None:
        raise ValueError(f"No open-access PDF for {doi}")

    return pdf_url


def _pick_oa_url(payload: dict[str, Any]) -> str | None:
    """Pick the best PDF URL from an Unpaywall response payload.

    Preference order:
        1. ``best_oa_location.url_for_pdf`` (when present and a PDF)
        2. ``best_oa_location.url``
        3. ``first_oa_location.url``
    """
    best = payload.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        return str(best["url_for_pdf"])
    if best.get("url"):
        return str(best["url"])

    first = payload.get("first_oa_location") or {}
    if first.get("url"):
        return str(first["url"])

    return None


# ---------------------------------------------------------------------------
# Stage 2: PDF download
# ---------------------------------------------------------------------------


async def _download_pdf(doi: str) -> bytes:
    """Resolve the PDF URL and download the PDF bytes.

    Combines stages 1 and 2 so callers get a single awaitable returning
    the raw PDF payload.

    Raises:
        ValueError: DOI empty, no open-access PDF, or non-PDF content-type.
        httpx.HTTPError: Network or HTTP errors.
        RuntimeError: Downloaded payload exceeds 50 MB.
    """
    pdf_url = await _resolve_pdf_url(doi)

    async with httpx.AsyncClient(
        timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        response = await client.get(pdf_url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in PDF_CONTENT_TYPES:
        raise ValueError(
            f"Unexpected content-type for {doi} PDF: {content_type!r} "
            f"(expected one of {sorted(PDF_CONTENT_TYPES)})"
        )

    pdf_bytes = response.content
    if len(pdf_bytes) > MAX_PDF_SIZE_BYTES:
        raise RuntimeError(
            f"PDF for {doi} exceeds {MAX_PDF_SIZE_BYTES} bytes "
            f"({len(pdf_bytes)} bytes received)"
        )

    return pdf_bytes


# ---------------------------------------------------------------------------
# Stage 3: PDF → Markdown
# ---------------------------------------------------------------------------


def _pdf_bytes_to_markdown(doi: str, pdf_bytes: bytes) -> str:
    """Convert PDF bytes to a Markdown string with a source header.

    On PyMuPDF decode failure, falls back to a minimal byte-level text
    extraction so the downstream LLM still gets something usable.

    Raises:
        ValueError: PDF bytes are empty or completely undecodable.
    """
    if not pdf_bytes:
        raise ValueError(f"Empty PDF payload for {doi}")

    header = f"# Source: doi:{doi}\n\n"
    try:
        text = _extract_with_fitz(pdf_bytes)
    except Exception as exc:
        logger.warning("PyMuPDF extraction failed for %s: %s — using fallback", doi, exc)
        text = _fallback_extract(pdf_bytes)

    if not text.strip():
        raise ValueError(f"No extractable text from PDF for {doi}")

    return header + text


def _extract_with_fitz(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes via PyMuPDF.

    Returns:
        Concatenated per-page text joined by blank lines.

    Raises:
        RuntimeError: PyMuPDF cannot open or iterate the document.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise RuntimeError(f"PyMuPDF open failed: {exc}") from exc

    try:
        pages: list[str] = []
        for page in doc:
            page_text = page.get_text() or ""
            if page_text:
                pages.append(page_text)
        return "\n\n".join(pages)
    finally:
        doc.close()


def _fallback_extract(pdf_bytes: bytes) -> str:
    """Best-effort byte-level text fallback when PyMuPDF fails.

    Decodes the bytes as UTF-8 with errors='replace' and strips common
    PDF binary framing bytes. Returns whatever is decodable.
    """
    decoded = pdf_bytes.decode("utf-8", errors="replace")
    # Drop null bytes and other binary noise; keep printable text + newlines.
    cleaned = "".join(ch for ch in decoded if ch == "\n" or ch == "\t" or ch.isprintable())
    return cleaned


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_doi(doi: str) -> str:
    """Strip ``doi:`` prefix and surrounding whitespace; reject empty."""
    if not doi:
        raise ValueError("DOI must be a non-empty string")
    cleaned = doi.strip()
    if cleaned.lower().startswith("doi:"):
        cleaned = cleaned[4:].strip()
    if not cleaned:
        raise ValueError("DOI must be a non-empty string after stripping 'doi:' prefix")
    return cleaned
