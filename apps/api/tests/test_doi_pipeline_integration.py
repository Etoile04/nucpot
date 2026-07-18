"""Integration tests for DOI branch in ontofuel_extract (NFM-1482 / NFM-1475-B2).

Verifies that ``source_type == "doi"`` calls ``fetch_paper_content`` while
non-DOI sources still use ``_load_source_content``. Also verifies the
DOI-specific early-return stubs were removed/simplified so DOI is a real
content source.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from nfm_db.services.extraction_pipeline import ontofuel_extract

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_DOI = "10.1234/sample.paper.2026"
SAMPLE_DOI_MD = "# Source: doi:10.1234/sample.paper.2026\n\nSample extracted PDF text."


# ---------------------------------------------------------------------------
# DOI branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doi_source_calls_fetch_paper_content() -> None:
    """``source_type='doi'`` must call ``await fetch_paper_content(doi)``."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
            return_value=SAMPLE_DOI_MD,
        ) as fetch_mock,
        patch(
            "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
            return_value="prompt",
        ),
        patch(
            "nfm_db.services.extraction_pipeline.call_llm",
            new_callable=AsyncMock,
            return_value=[{"property_name": "lattice_constant", "value": 5.47}],
        ),
    ):
        results = await ontofuel_extract(SAMPLE_DOI, "doi")

    fetch_mock.assert_awaited_once_with(SAMPLE_DOI)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_doi_source_does_not_call_load_source_content() -> None:
    """``source_type='doi'`` must NOT call ``_load_source_content``."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
            return_value=SAMPLE_DOI_MD,
        ),
        patch(
            "nfm_db.services.extraction_pipeline._load_source_content",
            return_value="FILE_CONTENT_SHOULD_NOT_BE_USED",
        ) as load_mock,
        patch(
            "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
            return_value="prompt",
        ),
        patch(
            "nfm_db.services.extraction_pipeline.call_llm",
            new_callable=AsyncMock,
            return_value=[{"property_name": "density", "value": 10.0}],
        ),
    ):
        await ontofuel_extract(SAMPLE_DOI, "doi")

    load_mock.assert_not_called()


@pytest.mark.asyncio
async def test_doi_source_passes_fetched_md_to_llm() -> None:
    """The LLM user_message must contain the MD returned by fetch_paper_content."""
    captured: dict = {}

    async def fake_call_llm(system_prompt, user_message):
        captured["user_message"] = user_message
        return [{"property_name": "x", "value": 1.0}]

    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
            return_value=SAMPLE_DOI_MD,
        ),
        patch(
            "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
            return_value="prompt",
        ),
        patch(
            "nfm_db.services.extraction_pipeline.call_llm",
            new=fake_call_llm,
        ),
    ):
        await ontofuel_extract(SAMPLE_DOI, "doi")

    assert SAMPLE_DOI_MD in captured["user_message"]


# ---------------------------------------------------------------------------
# Non-DOI fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_source_uses_load_source_content() -> None:
    """``source_type='file'`` must continue to use ``_load_source_content``."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
        ) as fetch_mock,
        patch(
            "nfm_db.services.extraction_pipeline._load_source_content",
            return_value="file content",
        ) as load_mock,
        patch(
            "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
            return_value="prompt",
        ),
        patch(
            "nfm_db.services.extraction_pipeline.call_llm",
            new_callable=AsyncMock,
            return_value=[{"property_name": "x", "value": 1.0}],
        ),
    ):
        await ontofuel_extract("docs/source.md", "file")

    load_mock.assert_called_once_with("docs/source.md")
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_path_source_uses_load_source_content() -> None:
    """``source_type='path'`` must continue to use ``_load_source_content``."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
        ) as fetch_mock,
        patch(
            "nfm_db.services.extraction_pipeline._load_source_content",
            return_value="file content",
        ) as load_mock,
        patch(
            "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
            return_value="prompt",
        ),
        patch(
            "nfm_db.services.extraction_pipeline.call_llm",
            new_callable=AsyncMock,
            return_value=[{"property_name": "x", "value": 1.0}],
        ),
    ):
        await ontofuel_extract("/abs/path.md", "path")

    load_mock.assert_called_once_with("/abs/path.md")
    fetch_mock.assert_not_called()


# ---------------------------------------------------------------------------
# DOI error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doi_fetch_error_returns_empty_list() -> None:
    """When ``fetch_paper_content`` raises ValueError, ontofuel_extract
    must surface it (caught by the existing exception handler) and return []."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
            side_effect=ValueError("No open-access PDF for 10.x/y"),
        ),
    ):
        results = await ontofuel_extract(SAMPLE_DOI, "doi")

    assert results == []


@pytest.mark.asyncio
async def test_doi_runtime_error_returns_empty_list() -> None:
    """When ``fetch_paper_content`` raises RuntimeError (>50MB), ontofuel_extract
    must surface it (caught by the existing exception handler) and return []."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PDF exceeds 52428800 bytes"),
        ),
    ):
        results = await ontofuel_extract(SAMPLE_DOI, "doi")

    assert results == []


# ---------------------------------------------------------------------------
# Stub mode: DOI is no longer special-cased to empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_mode_doi_no_longer_returns_empty_early() -> None:
    """After B2 simplification, stub mode + DOI must NOT short-circuit to
    ``return []`` for DOI specifically. It should follow the generic
    stub-mode path and return demo data (same as any other source in stub
    mode)."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
            return_value=SAMPLE_DOI_MD,
        ) as fetch_mock,
        patch(
            "nfm_db.services.extraction_pipeline._load_source_content",
            return_value="ignored",
        ) as load_mock,
    ):
        results = await ontofuel_extract(SAMPLE_DOI, "doi")

    # DOI must NOT be special-cased to [].
    assert results != []
    # Stub mode short-circuits before the loader runs — neither loader is consulted.
    fetch_mock.assert_not_called()
    load_mock.assert_not_called()


@pytest.mark.asyncio
async def test_stub_mode_file_still_returns_stub_results() -> None:
    """Non-DOI stub mode behavior unchanged: returns demo stub results."""
    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
        patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        patch(
            "nfm_db.services.extraction_pipeline.fetch_paper_content",
            new_callable=AsyncMock,
        ) as fetch_mock,
    ):
        results = await ontofuel_extract("source.md", "file")

    fetch_mock.assert_not_called()
    # Stub mode returns demo data — must be non-empty.
    assert len(results) >= 1
