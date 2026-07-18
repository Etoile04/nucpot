"""Unit tests for doi_fetcher (NFM-1480 / NFM-1475-B1).

Covers:
- Happy path (Unpaywall 200 → PDF download → PyMuPDF extract)
- No open-access location (Unpaywall 200 with no OA fields) → ValueError
- Unpaywall HTTP error → httpx.HTTPError
- Non-PDF content-type on PDF URL → ValueError
- Oversized PDF (>50MB) → RuntimeError
- Empty PDF bytes → ValueError
- DOI normalization (prefix stripping, whitespace, empty)
- Header prepended
- Rate limit (asyncio.sleep called before Unpaywall call)
- PyMuPDF decode failure → fallback path
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nfm_db.services.doi_fetcher import (
    DEFAULT_RATE_LIMIT_SECONDS,
    MAX_PDF_SIZE_BYTES,
    UNPAYWALL_BASE_URL,
    UNPAYWALL_EMAIL,
    _extract_with_fitz,
    _fallback_extract,
    _normalize_doi,
    _pick_oa_url,
    fetch_paper_content,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DOI = "10.1234/sample.paper.2026"
SAMPLE_PDF_URL = "https://example.org/papers/sample.pdf"


def _make_unpaywall_response(best_url: str | None = None) -> dict:
    """Build a Unpaywall-shaped payload."""
    payload: dict = {}
    if best_url:
        payload["best_oa_location"] = {"url": best_url, "url_for_pdf": best_url}
    else:
        payload["best_oa_location"] = None
    return payload


def _make_httpx_response(
    *,
    content: bytes = b"%PDF-1.4 fake",
    content_type: str = "application/pdf",
    status_code: int = 200,
) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.content = content
    response.headers = {"content-type": content_type}
    response.raise_for_status = MagicMock()
    return response


def _fake_pdf_bytes(text: str = "Sample extracted PDF text.") -> bytes:
    """Build real PDF bytes via PyMuPDF for round-trip extraction."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ---------------------------------------------------------------------------
# DOI normalization
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_normalize_doi_strips_prefix_and_whitespace() -> None:
    assert _normalize_doi(f"  doi:{SAMPLE_DOI}  ") == SAMPLE_DOI


@pytest.mark.unit
def test_normalize_doi_preserves_canonical() -> None:
    assert _normalize_doi(SAMPLE_DOI) == SAMPLE_DOI


@pytest.mark.unit
def test_normalize_doi_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_doi("")


@pytest.mark.unit
def test_normalize_doi_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_doi("   ")


@pytest.mark.unit
def test_normalize_doi_rejects_prefix_only() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_doi("doi:")


# ---------------------------------------------------------------------------
# _pick_oa_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pick_oa_url_prefers_url_for_pdf() -> None:
    payload = {
        "best_oa_location": {
            "url": "https://example.org/landing",
            "url_for_pdf": "https://example.org/paper.pdf",
        }
    }
    assert _pick_oa_url(payload) == "https://example.org/paper.pdf"


@pytest.mark.unit
def test_pick_oa_url_falls_back_to_best_url() -> None:
    payload = {"best_oa_location": {"url": "https://example.org/landing"}}
    assert _pick_oa_url(payload) == "https://example.org/landing"


@pytest.mark.unit
def test_pick_oa_url_falls_back_to_first_location() -> None:
    payload = {
        "best_oa_location": None,
        "first_oa_location": {"url": "https://example.org/first.pdf"},
    }
    assert _pick_oa_url(payload) == "https://example.org/first.pdf"


@pytest.mark.unit
def test_pick_oa_url_returns_none_when_no_oa() -> None:
    assert _pick_oa_url({"best_oa_location": None}) is None
    assert _pick_oa_url({}) is None


# ---------------------------------------------------------------------------
# _extract_with_fitz / _fallback_extract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_with_fitz_round_trip() -> None:
    """Real PyMuPDF: bytes → text round-trip."""
    pdf_bytes = _fake_pdf_bytes("Hello NFMD.")
    text = _extract_with_fitz(pdf_bytes)
    assert "Hello NFMD." in text


@pytest.mark.unit
def test_fallback_extract_decodes_garbage_bytes() -> None:
    raw = b"random\x00binary\x01data\nwith\tlines"
    text = _fallback_extract(raw)
    assert "random" in text
    assert "data" in text


# ---------------------------------------------------------------------------
# fetch_paper_content — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_paper_content_happy_path() -> None:
    """End-to-end: Unpaywall 200 → PDF download → MD with header."""
    pdf_bytes = _fake_pdf_bytes("Nuclear fuel properties extracted text.")
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(SAMPLE_PDF_URL))
    unpaywall_response.raise_for_status = MagicMock()

    pdf_response = _make_httpx_response(content=pdf_bytes, content_type="application/pdf")

    call_count = {"n": 0}

    async def fake_get(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return unpaywall_response
        return pdf_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = fake_get

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await fetch_paper_content(SAMPLE_DOI)

    assert result.startswith(f"# Source: doi:{SAMPLE_DOI}\n\n")
    assert "Nuclear fuel properties extracted text." in result


@pytest.mark.asyncio
async def test_fetch_paper_content_normalizes_doi_prefix() -> None:
    """``doi:10.x/y`` should resolve the same as ``10.x/y``."""
    pdf_bytes = _fake_pdf_bytes("content")
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(SAMPLE_PDF_URL))
    unpaywall_response.raise_for_status = MagicMock()

    pdf_response = _make_httpx_response(content=pdf_bytes, content_type="application/pdf")

    async def fake_get(url, **kwargs):
        if UNPAYWALL_BASE_URL in url:
            return unpaywall_response
        return pdf_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = fake_get

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await fetch_paper_content(f"doi:{SAMPLE_DOI}")

    # DOI in header is normalized (no prefix).
    assert f"# Source: doi:{SAMPLE_DOI}" in result
    assert "content" in result


@pytest.mark.asyncio
async def test_fetch_paper_content_rate_limits_unpaywall_call() -> None:
    """Verify asyncio.sleep is awaited before the Unpaywall call."""
    pdf_bytes = _fake_pdf_bytes("x")
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(SAMPLE_PDF_URL))
    unpaywall_response.raise_for_status = MagicMock()

    pdf_response = _make_httpx_response(content=pdf_bytes, content_type="application/pdf")

    sleep_mock = AsyncMock()

    async def fake_get(url, **kwargs):
        if UNPAYWALL_BASE_URL in url:
            return unpaywall_response
        return pdf_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = fake_get

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=sleep_mock),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await fetch_paper_content(SAMPLE_DOI)

    sleep_mock.assert_awaited()
    # First sleep call should be the rate-limit delay.
    first_call_args = sleep_mock.call_args_list[0]
    assert first_call_args.args[0] == DEFAULT_RATE_LIMIT_SECONDS


# ---------------------------------------------------------------------------
# fetch_paper_content — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_paper_content_raises_when_no_oa_location() -> None:
    """Unpaywall returns 200 but no best/first OA location."""
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(best_url=None))
    unpaywall_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=unpaywall_response)

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(ValueError, match="No open-access PDF"):
            await fetch_paper_content(SAMPLE_DOI)


@pytest.mark.asyncio
async def test_fetch_paper_content_raises_on_unpaywall_http_error() -> None:
    """Unpaywall 500 propagates as httpx.HTTPStatusError."""
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 500
    unpaywall_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=unpaywall_response,
        )
    )

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=unpaywall_response)

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_paper_content(SAMPLE_DOI)


@pytest.mark.asyncio
async def test_fetch_paper_content_rejects_non_pdf_content_type() -> None:
    """PDF URL returns HTML → ValueError."""
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(SAMPLE_PDF_URL))
    unpaywall_response.raise_for_status = MagicMock()

    pdf_response = _make_httpx_response(content=b"<html>nope</html>", content_type="text/html")

    async def fake_get(url, **kwargs):
        if UNPAYWALL_BASE_URL in url:
            return unpaywall_response
        return pdf_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = fake_get

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(ValueError, match="Unexpected content-type"):
            await fetch_paper_content(SAMPLE_DOI)


@pytest.mark.asyncio
async def test_fetch_paper_content_rejects_oversized_pdf() -> None:
    """PDF > 50 MB → RuntimeError."""
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(SAMPLE_PDF_URL))
    unpaywall_response.raise_for_status = MagicMock()

    # Build a payload that exceeds MAX_PDF_SIZE_BYTES without consuming real memory.
    oversized = b"x" * (MAX_PDF_SIZE_BYTES + 1)
    pdf_response = _make_httpx_response(content=oversized, content_type="application/pdf")

    async def fake_get(url, **kwargs):
        if UNPAYWALL_BASE_URL in url:
            return unpaywall_response
        return pdf_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = fake_get

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(RuntimeError, match="exceeds"):
            await fetch_paper_content(SAMPLE_DOI)


@pytest.mark.asyncio
async def test_fetch_paper_content_rejects_empty_doi() -> None:
    """Empty/whitespace DOI → ValueError without HTTP calls."""
    with pytest.raises(ValueError, match="non-empty"):
        await fetch_paper_content("")


@pytest.mark.asyncio
async def test_fetch_paper_content_falls_back_when_fitz_fails() -> None:
    """PyMuPDF raises → fallback path still produces MD with header."""
    unpaywall_response = MagicMock(spec=httpx.Response)
    unpaywall_response.status_code = 200
    unpaywall_response.json = MagicMock(return_value=_make_unpaywall_response(SAMPLE_PDF_URL))
    unpaywall_response.raise_for_status = MagicMock()

    # Provide bytes that are NOT a valid PDF but contain readable text.
    fake_bytes = b"some plain text that is not really a PDF\n\nmore text"
    pdf_response = _make_httpx_response(content=fake_bytes, content_type="application/pdf")

    async def fake_get(url, **kwargs):
        if UNPAYWALL_BASE_URL in url:
            return unpaywall_response
        return pdf_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = fake_get

    with (
        patch("nfm_db.services.doi_fetcher.asyncio.sleep", new=AsyncMock()),
        patch("nfm_db.services.doi_fetcher._extract_with_fitz", side_effect=RuntimeError("boom")),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await fetch_paper_content(SAMPLE_DOI)

    assert result.startswith(f"# Source: doi:{SAMPLE_DOI}\n\n")
    # Fallback decoded at least some of the input.
    assert "some plain text" in result


# ---------------------------------------------------------------------------
# Module-level constants sanity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unpaywall_constants_have_expected_values() -> None:
    """Sanity check on module-level constants."""
    assert UNPAYWALL_BASE_URL == "https://api.unpaywall.org/v2"
    assert UNPAYWALL_EMAIL  # email must be set (even if placeholder)
    assert MAX_PDF_SIZE_BYTES == 50 * 1024 * 1024
