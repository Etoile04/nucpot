"""Tests for NFM-2013 AC-4 + NFM-2032: DB-enforced cross-request dedup.

E2E QA v2 (comment 821ebee9) found that ``POST /api/v1/extraction/ingest``
twice with the *same* payload created two duplicate rows in
``property_measurements`` — the in-memory ``seen_measurement_keys: set``
in ``extraction_to_db_mapper.py`` is per-call only and resets to empty
between requests.

NFM-2032 was merged on a sibling branch (commit 8ef0034) to fix this
defect by making the 5-tuple a DB invariant:

  * New ``method VARCHAR(100) NOT NULL DEFAULT ''`` column on
    ``property_measurements``.
  * Backfilled ``conditions_hash`` from joined ``MeasurementCondition``
    rows.
  * Deduplicated existing rows before adding the unique index.
  * ``UNIQUE INDEX uq_pm_dedup`` on
    ``(dataset_id, property_type_id, conditions_hash, method)``.
  * ``UNIQUE INDEX uq_datasets_source_material`` on
    ``(source_id, material_id)``.

These tests verify the same invariants in our branch (without the
in-memory-only dedup fallback that masked the bug for cross-request
cases).

NFM-2013 E2E QA reproduction (comment 821ebee9):
    submitted identical payload twice → property_measurements went
    21→23→25 (+2 each time, not 0).  Schema check: no UNIQUE constraint
    on (dataset_id, property_type_id, conditions_hash, method).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    PropertyCategory,
    PropertyMeasurement,
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
    source_reference: str = "10.1234/dedup",
) -> dict[str, Any]:
    return {
        "source_reference": source_reference,
        "source_type": "doi",
        "corpus_id": corpus_id,
        "properties": properties or [_sample_property()],
    }


# --- Fixtures ---


@pytest.fixture
async def service_account(db_session: AsyncSession) -> User:
    user = User(
        username="ontofuel_svc_dedup",
        email="ontofuel_dedup@svc.nucpot.local",
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


class TestCrossRequestDedup:
    """AC-4: identical POST must not double-insert property_measurements."""

    @pytest.mark.asyncio
    async def test_identical_post_twice_yields_single_measurement(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """Same payload, same source, same material, same property, same
        conditions, same method — POST twice → 1 row, not 2.

        Before NFM-2032/NFM-2013 fix: count went +2 (the in-memory set
        was per-call only and the DB had no UNIQUE constraint).
        """
        before_count = (await db_session.execute(
            select(func.count()).select_from(PropertyMeasurement)
        )).scalar_one()

        payload = _ingest_payload(corpus_id="dedup-cross")

        # First POST
        resp1 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp1.status_code == 202, resp1.text
        data1 = resp1.json()["data"]
        assert data1["created_measurements"] == 1

        # Second POST (identical payload)
        resp2 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp2.status_code == 202, resp2.text
        data2 = resp2.json()["data"]

        # AC-4: cross-request dedup.  Either the DB UNIQUE blocked the
        # insert (counts return to 0) or the mapper's find-by-key
        # returned the existing row (counts split as reused/skipped).
        # What MUST NOT happen: created_measurements increments.
        assert data2["created_measurements"] == 0, (
            f"AC-4 FAIL: identical POST created a NEW measurement "
            f"({data2['created_measurements']}). Cross-request dedup "
            f"is missing. Second POST should reuse the existing row."
        )
        # And the rejected skip counter must reflect the duplicate.
        assert (
            data2["skipped_duplicate_measurements"] >= 1
            or data2["reused_entities"] >= 1
            or data2["skipped_duplicates"] >= 1
        ), (
            f"AC-4 FAIL: second POST didn't report the duplicate under "
            f"any counter. Got: created={data2['created_measurements']} "
            f"skipped_dup={data2['skipped_duplicate_measurements']} "
            f"reused={data2['reused_entities']} "
            f"skipped_total={data2['skipped_duplicates']}"
        )

        # And the DB must reflect exactly +1 row total.
        after_count = (await db_session.execute(
            select(func.count()).select_from(PropertyMeasurement)
        )).scalar_one()
        assert after_count == before_count + 1, (
            f"AC-4 FAIL: property_measurements count incremented by "
            f"{after_count - before_count} across two identical POSTs. "
            f"Expected exactly 1 new row."
        )


class TestDifferentConditionsTwoMeasurements:
    """Method-tuple dedup must distinguish scientifically different
    measurements.  Different method → 2 rows, not 1.
    """

    @pytest.mark.asyncio
    async def test_different_method_yields_two_measurements(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """Same material/source/property/conditions but different
        measurement method → both rows must persist.

        NFM-2032 CR Finding #1: omitting ``method`` from the 5-tuple
        collapsed two scientifically distinct measurements into one.
        """
        before = (await db_session.execute(
            select(func.count()).select_from(PropertyMeasurement)
        )).scalar_one()

        # First POST: tensile method
        resp1 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                properties=[
                    _sample_property({"method": "tensile"}),
                ],
                corpus_id="method-distinct",
                source_reference="10.1234/method-distinct",
            ),
            headers=svc_headers,
        )
        assert resp1.status_code == 202, resp1.text

        # Second POST: same source but different method
        resp2 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                properties=[
                    _sample_property({"method": "nanoindentation"}),
                ],
                corpus_id="method-distinct",
                source_reference="10.1234/method-distinct",
            ),
            headers=svc_headers,
        )
        assert resp2.status_code == 202, resp2.text

        after = (await db_session.execute(
            select(func.count()).select_from(PropertyMeasurement)
        )).scalar_one()
        # Different method → 2 rows.
        assert after == before + 2, (
            f"Method-distinct POSTs should produce 2 distinct rows; "
            f"got delta={after - before}. Without method in the dedup "
            f"5-tuple, the second row collides with the first."
        )
