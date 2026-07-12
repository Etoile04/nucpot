"""Rate-limit + standard response header tests (T5: RED phase)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.main import app
from nfm_db.services.rate_limit import (
    InProcessRateLimiter,
    make_rate_limit_dependency,
    ontology_rate_limit,
)
from tests.ontology_seed import seed_corpus

_CORPUS = "smirnov2014"


@pytest.mark.asyncio
async def test_rate_limit_returns_429_with_retry_after(
    async_client,
    db_session: AsyncSession,
) -> None:
    """After the limit, the next request is 429 with a Retry-After header."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )
    # Override with a tight, fresh limiter for a deterministic 429.
    tight = InProcessRateLimiter(max_requests=2, window_seconds=60)
    app.dependency_overrides[ontology_rate_limit] = make_rate_limit_dependency(tight)
    try:
        url = f"/api/v1/ontology/corpora/{_CORPUS}/graph"
        first = await async_client.get(url)
        second = await async_client.get(url)
        third = await async_client.get(url)
        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429, third.text
        assert "retry-after" in {k.lower() for k in third.headers}
    finally:
        app.dependency_overrides.pop(ontology_rate_limit, None)


@pytest.mark.asyncio
async def test_response_has_cache_control_etag_and_last_modified(
    async_client,
    db_session: AsyncSession,
) -> None:
    """A successful response carries Cache-Control, ETag, Last-Modified."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )
    response = await async_client.get(
        f"/api/v1/ontology/corpora/{_CORPUS}/graph",
    )
    assert response.status_code == 200, response.text
    headers = {k.lower(): v for k, v in response.headers.items()}
    assert "cache-control" in headers
    assert "etag" in headers
    assert "last-modified" in headers


@pytest.mark.asyncio
async def test_etag_is_content_addressed_and_stable(
    async_client,
    db_session: AsyncSession,
) -> None:
    """ETag is derived from the corpus content (same corpus → same ETag)."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )
    r1 = await async_client.get(f"/api/v1/ontology/corpora/{_CORPUS}/graph")
    r2 = await async_client.get(f"/api/v1/ontology/corpora/{_CORPUS}/graph")
    etag1 = r1.headers.get("etag")
    etag2 = r2.headers.get("etag")
    assert etag1 and etag2
    assert etag1 == etag2
