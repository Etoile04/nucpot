"""DOI content fetcher (NFM-1488 / NFM-1485-3).

Fetches the full text of a paper identified by its DOI and returns it as
Markdown.  The implementation uses the Semantic Scholar public API to
retrieve metadata and open-access content, falling back to the abstract.

The public function :func:`fetch_paper_content` is designed to be mockable
in tests via ``unittest.mock.patch``.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Maximum number of characters for the returned Markdown content.
MAX_CONTENT_LENGTH = 2_000_000

#: Timeout for HTTP requests (seconds).
REQUEST_TIMEOUT = 30

#: Basic DOI format validation regex.
DOI_REGEX = re.compile(r"^10\.\d{4,9}/[^\s]+$")

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DOIFetcherBackend(Protocol):
    """Abstraction for DOI content retrieval backends."""

    def fetch(self, doi: str) -> str:
        """Fetch paper content for *doi* and return Markdown."""
        ...


# ---------------------------------------------------------------------------
# Default implementation — Semantic Scholar API
# ---------------------------------------------------------------------------


class SemanticScholarFetcher:
    """Fetch paper content via the Semantic Scholar public API.

    Uses the ``DOI`` lookup endpoint to get metadata, then attempts to
    retrieve the full-text PDF URL (open access) or falls back to the
    abstract formatted as Markdown.
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, timeout: int = REQUEST_TIMEOUT) -> None:
        self._timeout = timeout

    def fetch(self, doi: str) -> str:
        """Fetch paper content for *doi*.

        Returns Markdown-formatted content (full text when available,
        abstract otherwise).

        Raises:
            DOIFetchError: if the fetch fails for any reason.
        """
        try:
            fields = "title,abstract,externalIds,openAccessPdf"
            url = f"{self.BASE_URL}/paper/DOI:{doi}"
            resp = httpx.get(url, params={"fields": fields}, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()

            title = data.get("title", "")
            abstract = data.get("abstract", "")
            pdf_url = None
            oa = data.get("openAccessPdf")
            if isinstance(oa, dict):
                pdf_url = oa.get("url")

            # If open-access PDF is available, fetch and convert.
            if pdf_url:
                try:
                    pdf_resp = httpx.get(
                        pdf_url,
                        timeout=self._timeout,
                        follow_redirects=True,
                    )
                    pdf_resp.raise_for_status()
                    return _pdf_bytes_to_markdown(pdf_resp.content, title=title)
                except Exception:
                    logger.warning(
                        "DOI fetch: PDF download failed for doi=%s, falling back to abstract",
                        doi,
                        exc_info=True,
                    )

            # Fall back to abstract.
            if abstract:
                return f"# {title}\n\n{abstract}" if title else abstract

            raise DOIFetchError(f"No content available for DOI: {doi}")

        except DOIFetchError:
            raise
        except Exception as exc:
            raise DOIFetchError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Helper — minimal PDF to Markdown (text extraction)
# ---------------------------------------------------------------------------


def _pdf_bytes_to_markdown(pdf_bytes: bytes, *, title: str = "") -> str:
    """Convert PDF bytes to Markdown text via PyMuPDF."""
    import fitz  # type: ignore[import-untyped]

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text())
        md = "\n\n".join(parts)
        if title:
            md = f"# {title}\n\n{md}"
        return md[:MAX_CONTENT_LENGTH]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Error class
# ---------------------------------------------------------------------------


class DOIFetchError(Exception):
    """Raised when a DOI fetch fails for any reason."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_default_fetcher: DOIFetcherBackend | None = None


def get_doi_fetcher() -> DOIFetcherBackend:
    """Return the configured DOI fetcher backend.

    Returns a module-level singleton of :class:`SemanticScholarFetcher`
    so tests can patch ``nfm_db.services.doi_fetcher._default_fetcher``.
    """
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = SemanticScholarFetcher()
    return _default_fetcher


def fetch_paper_content(doi: str) -> str:
    """Fetch paper content for *doi* and return Markdown.

    Delegates to the configured backend.  Raises :class:`DOIFetchError`
    on failure so callers can surface an appropriate HTTP error.

    Returns:
        Markdown string (may be abstract-only if full text is unavailable).
    """
    fetcher = get_doi_fetcher()
    result = fetcher.fetch(doi)
    if not result or not result.strip():
        raise DOIFetchError(f"Empty content returned for DOI: {doi}")
    return result[:MAX_CONTENT_LENGTH]


def validate_doi_format(doi: str) -> bool:
    """Return True if *doi* matches the basic DOI pattern."""
    return bool(DOI_REGEX.match(doi))


__all__ = [
    "DOI_REGEX",
    "MAX_CONTENT_LENGTH",
    "REQUEST_TIMEOUT",
    "DOIFetchError",
    "DOIFetcherBackend",
    "SemanticScholarFetcher",
    "fetch_paper_content",
    "get_doi_fetcher",
    "validate_doi_format",
]
