"""API integration tests for POST /literature/from-doi (NFM-1488).

Covers acceptance criteria:
- AC #6: Valid DOI returns {literature_id, status:'parsed'}.
- AC #7: Malformed DOI returns 400.
- AC #8: DOI that fails doi_fetcher returns 502.
- AC #9: Already-ingested DOI returns original literature_id (idempotent).
"""

from __future__ import annotations

import os
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.source import DataSource

# ---------------------------------------------------------------------------
# Test data — valid DOIs from seed_dois.json
# ---------------------------------------------------------------------------

VALID_DOI = "10.1016/j.jnucmat.2020.152307"
VALID_DOI_2 = "10.1016/j.jnucmat.2019.07.004"

MOCK_MARKDOWN_CONTENT = (
    "# Nuclear Fuel Performance Under Irradiation\n\n"
    "This paper investigates the behavior of UO2 fuel under high burnup conditions.\n"
)

# ---------------------------------------------------------------------------
# Shared mock context — patches Celery dispatcher and storage backend
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_literature_env(tmp_path: Path):
    """Set LITERATURE_STORAGE_ROOT to a temp dir so LocalDiskStorage works."""
    os.environ["LITERATURE_STORAGE_ROOT"] = str(tmp_path / "uploads" / "literature")
    yield
    os.environ.pop("LITERATURE_STORAGE_ROOT", None)


def _doi_happy_context():
    """Patch fetcher + dispatcher for happy-path DOI tests."""
    cm = ExitStack()
    cm.enter_context(
        patch(
            "nfm_db.services.doi_fetcher.fetch_paper_content",
            return_value=MOCK_MARKDOWN_CONTENT,
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="doi-task-id"),
        ),
    )
    return cm


def _doi_failure_context():
    """Patch fetcher to raise and dispatcher for 502 test."""
    cm = ExitStack()
    cm.enter_context(
        patch(
            "nfm_db.services.doi_fetcher.fetch_paper_content",
            side_effect=Exception("API rate limit exceeded"),
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="x"),
        ),
    )
    return cm


# ---------------------------------------------------------------------------
# AC #4 corollary: 401 — unauthenticated
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_from_doi_returns_401_without_auth(async_client) -> None:
    """AC #4 corollary: Unauthenticated requests must be rejected with 401."""
    response = await async_client.post(
        "/api/v1/literature/from-doi",
        json={"doi": VALID_DOI},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC #6: 200 — happy path with valid DOI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_doi_valid_doi_returns_parsed_status(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """AC #6: Valid DOI returns {literature_id, status:'parsed'}."""
    with _doi_happy_context():
        response = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "parsed"

    literature_id = uuid.UUID(body["data"]["literature_id"])

    source = await db_session.get(DataSource, literature_id)
    assert source is not None
    assert source.doi == VALID_DOI
    assert source.parse_status == "parsed"
    assert source.content_md == MOCK_MARKDOWN_CONTENT
    assert source.file_hash is not None
    assert source.original_filename == f"{VALID_DOI}.md"


@pytest.mark.asyncio
async def test_from_doi_dispatches_celery_task(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """AC #6 corollary: from-doi must call schedule_literature_processing."""
    with _doi_happy_context():
        response = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC #7: 400 — malformed DOI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_doi_malformed_returns_400(
    async_client,
    db_session: AsyncSession,
) -> None:
    """AC #7: Malformed DOI returns 400."""
    malformed_dois = [
        "not-a-doi",
        "10.123",
        "",
        "ftp://invalid",
        "10./missing-suffix",
    ]
    for bad_doi in malformed_dois:
        response = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": bad_doi},
        )
        assert response.status_code == 400, f"Expected 400 for DOI: {bad_doi!r}"


# ---------------------------------------------------------------------------
# AC #8: 502 — DOI fetch failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_doi_fetch_failure_returns_502(
    async_client,
    db_session: AsyncSession,
) -> None:
    """AC #8: DOI that fails doi_fetcher returns 502."""
    with _doi_failure_context():
        response = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )

    assert response.status_code == 502
    assert "DOI fetch failed" in response.json()["detail"]


# ---------------------------------------------------------------------------
# AC #9: Idempotent re-DOI (same DOI → same literature_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_doi_idempotent_returns_original_id(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """AC #9: Already-ingested DOI returns original literature_id."""
    with _doi_happy_context():
        resp1 = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )

    assert resp1.status_code == 200
    first_id = uuid.UUID(resp1.json()["data"]["literature_id"])

    with _doi_happy_context():
        resp2 = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )

    assert resp2.status_code == 200
    second_id = uuid.UUID(resp2.json()["data"]["literature_id"])

    # Same DOI → same literature_id (idempotent).
    assert first_id == second_id


# ---------------------------------------------------------------------------
# NFM-4089 E2E regression: fetch_paper_content() must NOT be called on a
# duplicate DOI.  The prior revision moved the dedup probe below
# ``fetch_paper_content()``, so a known DOI re-fetched from Semantic Scholar
# and 502'd when that call failed (baseline was 200/4.8 ms no-network).
# The early-return dedup probe (by DOI) must short-circuit before any
# outbound HTTP happens.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_doi_duplicate_does_not_call_fetch_paper_content(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """NFM-4089 ordering regression: duplicate DOI must skip Semantic Scholar."""
    # Seed first so we already have a row with the matching DOI.
    with _doi_happy_context():
        resp1 = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )
    assert resp1.status_code == 200

    # Second DOI submit — patch fetch_paper_content to RAISE.  If the early-
    # return probe is working, the fetcher is never reached and the second
    # response is idempotent 200 with the original literature_id.
    cm = ExitStack()
    cm.enter_context(
        patch(
            "nfm_db.services.doi_fetcher.fetch_paper_content",
            side_effect=Exception("Semantic Scholar unreachable"),
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="x"),
        ),
    )
    with cm:
        resp2 = await async_client.post(
            "/api/v1/literature/from-doi",
            json={"doi": VALID_DOI},
        )

    # Without the early-return probe this would have been 502 because
    # fetch_paper_content would have been called and raised.  With the
    # probe in place the fetcher is bypassed entirely.
    assert resp2.status_code == 200, (
        f"Duplicate DOI regressed to {resp2.status_code} — the dedup probe "
        f"must short-circuit BEFORE fetch_paper_content (NFM-4089 ordering "
        f"regression). Body: {resp2.text}"
    )
    first_id = uuid.UUID(resp1.json()["data"]["literature_id"])
    second_id = uuid.UUID(resp2.json()["data"]["literature_id"])
    assert first_id == second_id
