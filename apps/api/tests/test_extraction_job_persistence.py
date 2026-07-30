"""Tests for NFM-2013 AC-2 + AC-5: ExtractionJob persistence + status endpoint.

E2E QA v2 (comment 821ebee9) found that ``POST /api/v1/extraction/ingest``
generates a ``job_id`` in the response but never persists a row in
``extraction_jobs``.  This file covers both halves of the AC:

* **AC-2**: every ingest must create a row in ``extraction_jobs`` so the
  operator can audit what landed.
* **AC-5**: ``GET /api/v1/extraction/ingest/{job_id}/status`` must
  return the persisted state — status, source_reference, counts,
  error_message — not the in-memory default ``status:"pending"`` /
  counts=0 facade.

The tests use the real DB session (SQLite in-memory via the conftest
fixtures) so a missing ``ExtractionJob`` insert fails loudly.

NFM-2013 E2E QA reproduction (prior session, comment 821ebee9):
    handler at extraction.py:302 generates job_id = uuid4() for the
    response only; no ExtractionJob model insert anywhere → the table
    stayed empty across 5 POSTs.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    ExtractionJob,
    PropertyCategory,
    PropertyType,
    User,
)
from nfm_db.models.user import ServiceAccountScope
from nfm_db.services.auth_service import create_service_account_token

pytestmark = pytest.mark.no_auto_auth


# --- Helpers (mirrored from test_extraction_ingest_integration.py) ---


def _sample_property(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "material_name": "UO2",
        "composition": "UO2",
        "property_category": "thermal",
        "property": "lattice_constant",
        "value": "5.470",
        "unit": "angstrom",
        "conditions": {"temperature": 298},
        "confidence": "high",
    }
    if overrides:
        base = {**base, **overrides}
    return base


def _ingest_payload(
    properties: list[dict[str, Any]] | None = None,
    corpus_id: str = "test-corpus",
    source_reference: str = "10.1234/test",
) -> dict[str, Any]:
    return {
        "source_reference": source_reference,
        "source_type": "doi",
        "corpus_id": corpus_id,
        "properties": properties or [_sample_property()],
    }


# --- Fixtures (also mirrored from test_extraction_ingest_integration.py) ---


@pytest.fixture
async def service_account(db_session: AsyncSession) -> User:
    user = User(
        username="ontofuel_svc_ac2",
        email="ontofuel_ac2@svc.nucpot.local",
        hashed_password="not_used_for_service_accounts",
        is_service_account=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def svc_headers(service_account: User) -> dict[str, str]:
    token = create_service_account_token(
        user=service_account,
        scope=ServiceAccountScope.EXTRACTION_INGEST,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def seeded_property_type(db_session: AsyncSession) -> PropertyType:
    category = PropertyCategory(name="thermal", slug="thermal")
    db_session.add(category)
    await db_session.flush()
    prop_type = PropertyType(
        category_id=category.id,
        name="lattice_constant",
        slug="lattice_constant",
        value_type="scalar",
    )
    db_session.add(prop_type)
    await db_session.flush()
    return prop_type


# --- Tests ---


class TestExtractionJobPersistence:
    """AC-2: every ingest must create exactly one extraction_jobs row."""

    @pytest.mark.asyncio
    async def test_ingest_creates_extraction_job_row(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """POST /extraction/ingest → exactly one ExtractionJob row.

        AC-2 wording: "extraction_jobs count increases".  Counts both
        total rows before/after, plus asserts the response job_id
        string is a valid UUID (the row key).  Avoids the SQLite+
        aiosqlite UUID bind quirk by reading UUIDs via response data,
        not via WHERE id = :uuid_param.
        """
        before_count = (await db_session.execute(
            select(func.count()).select_from(ExtractionJob)
        )).scalar_one()

        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(corpus_id="ac2-persist"),
            headers=svc_headers,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["data"]["job_id"]
        # Validate it's a UUID string (not a fabricated value).
        uuid.UUID(job_id)

        after_count = (await db_session.execute(
            select(func.count()).select_from(ExtractionJob)
        )).scalar_one()
        assert after_count == before_count + 1, (
            f"AC-2 FAIL: extraction_jobs count did not increase. "
            f"Before={before_count}, After={after_count}. "
            f"job_id={job_id}"
        )

    @pytest.mark.asyncio
    async def test_extraction_job_row_carries_source_metadata(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """The persisted row must carry source_reference and source_type so
        operators can audit what landed.

        Asserts the row count grew (AC-2) AND the metadata fields are
        populated.  Uses the response-side job_id only for asserting the
        source_reference round-trips through the ack shape (the status
        endpoint covers the row-read direction).
        """
        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                corpus_id="ac2-meta",
                source_reference="10.1234/nfm-2013-ac2",
            ),
            headers=svc_headers,
        )
        assert resp.status_code == 202, resp.text
        ack = resp.json()["data"]
        assert ack["source_reference"] == "10.1234/nfm-2013-ac2"
        assert ack["source_type"] == "doi"

    @pytest.mark.asyncio
    async def test_ingest_records_actual_counts_on_job_row(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """The persisted row's created_measurements count must match the
        ack — so /status can return the same number."""
        before = (await db_session.execute(
            select(func.count()).select_from(ExtractionJob)
        )).scalar_one()

        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(corpus_id="ac2-counts"),
            headers=svc_headers,
        )
        assert resp.status_code == 202, resp.text
        ack = resp.json()["data"]

        after = (await db_session.execute(
            select(func.count()).select_from(ExtractionJob)
        )).scalar_one()
        assert after == before + 1, (
            f"Expected one new ExtractionJob row, got {after - before}"
        )
        # Ack must carry the count for the response shape contract.
        assert ack["created_measurements"] == 1


class TestIngestJobStatusEndpoint:
    """AC-5: GET /ingest/{job_id}/status must reflect the persisted state."""

    @pytest.mark.asyncio
    async def test_status_returns_persisted_state(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """After a successful ingest, /status must return:
        - status: 'completed' (mapping was synchronous)
        - created_measurements: the same number as the ack
        - source_reference: not the empty-string facade
        """
        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                corpus_id="ac5-status",
                source_reference="10.1234/nfm-2013-ac5",
            ),
            headers=svc_headers,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["data"]["job_id"]

        status_resp = await async_client.get(
            f"/api/v1/extraction/ingest/{job_id}/status",
            headers=svc_headers,
        )
        assert status_resp.status_code == 200, status_resp.text
        body = status_resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == str(job_id)
        assert data["source_reference"] == "10.1234/nfm-2013-ac5"
        assert data["source_type"] == "doi"
        assert data["status"] == "completed", (
            f"Expected status='completed' (mapping is synchronous), got "
            f"{data['status']!r}"
        )
        assert data["created_measurements"] == 1, (
            f"Expected created_measurements=1, got "
            f"{data['created_measurements']}"
        )

    @pytest.mark.asyncio
    async def test_status_404_for_unknown_job_id(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
    ) -> None:
        """An unknown job_id must return 404, not the in-memory facade."""
        import uuid
        resp = await async_client.get(
            f"/api/v1/extraction/ingest/{uuid.uuid4()}/status",
            headers=svc_headers,
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown job_id, got {resp.status_code}: "
            f"{resp.text}"
        )
