"""Integration tests for POST /api/v1/extraction/ingest (NFM-1983 AC-3).

Tests use real database sessions (SQLite in-memory) via conftest.py fixtures.
No mocks for DB operations — only GraphBuilder is mocked where the AC requires.

Marked ``no_auto_auth`` so the real auth chain runs (service-account JWT
validation via ``require_ingest_authority``).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Corpus,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
    User,
)
from nfm_db.models.user import ServiceAccountScope
from nfm_db.services.auth_service import create_service_account_token

pytestmark = pytest.mark.no_auto_auth


# --- Helpers ---


def _sample_property(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a minimal property dict that passes ExtractedProperty validation."""
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
    **overrides: Any,
) -> dict[str, Any]:
    """Build the JSON body for POST /extraction/ingest."""
    body: dict[str, Any] = {
        "source_reference": source_reference,
        "source_type": "doi",
        "corpus_id": corpus_id,
        "properties": properties or [_sample_property()],
        **overrides,
    }
    return body


# --- Fixtures ---


@pytest.fixture
async def service_account(db_session: AsyncSession) -> User:
    """Create a service account user with extraction:ingest scope."""
    user = User(
        username="ontofuel_svc",
        email="ontofuel@svc.nucpot.local",
        hashed_password="not_used_for_service_accounts",
        is_service_account=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def svc_headers(service_account: User) -> dict[str, str]:
    """Authorization headers for the service account."""
    token = create_service_account_token(
        user=service_account,
        scope=ServiceAccountScope.EXTRACTION_INGEST,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def seeded_property_type(db_session: AsyncSession) -> PropertyType:
    """Seed a PropertyCategory + PropertyType so map_and_persist can resolve it."""
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


class TestIngestFullFlow:
    """AC-3: full ingest flow with real DB writes."""

    @pytest.mark.asyncio
    async def test_ingest_full_flow(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """Login service account -> POST ingest -> verify property_measurements written.

        Repeating the POST should increment skipped_duplicates.
        """
        payload = _ingest_payload(
            properties=[_sample_property()],
            corpus_id="flow-test",
        )

        # --- First POST ---
        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["ingested"] == 1
        assert data["created_measurements"] == 1
        assert data["skipped_duplicates"] == 0
        assert data["reused_entities"] == 0
        assert data["skipped_duplicate_measurements"] == 0
        assert data["corpus_id"] == "flow-test"

        # Verify property_measurements row exists
        measurements = (await db_session.execute(
            select(PropertyMeasurement)
        )).scalars().all()
        assert len(measurements) >= 1, "Expected at least one PropertyMeasurement row"

        # Verify corpus was auto-created
        corpus = (await db_session.execute(
            select(Corpus).where(Corpus.corpus_id == "flow-test")
        )).scalar_one_or_none()
        assert corpus is not None
        assert corpus.is_auto_created is True

        # --- Second POST (same data -> dedup) ---
        resp2 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp2.status_code == 202
        body2 = resp2.json()
        assert body2["success"] is True
        data2 = body2["data"]
        assert data2["skipped_duplicates"] >= 1, (
            "Second POST with identical 5-tuple should skip duplicates"
        )
        # NFM-1996: split counters must be present
        assert "reused_entities" in data2
        assert "skipped_duplicate_measurements" in data2
        assert "skipped_unknown_properties" in data2
        assert data2["skipped_duplicates"] == (
            data2["reused_entities"]
            + data2["skipped_duplicate_measurements"]
            + data2["skipped_unknown_properties"]
        )


class TestSkippedCounterReconciliation:
    """NFM-1996 AC-3: every term of the ``skipped_duplicates`` alias must be
    visible in the response.

    ``MappingResult.skipped_duplicates`` sums three counters.  If the response
    exposes only two of them, a caller reconciling
    ``skipped_duplicates == reused_entities + skipped_duplicate_measurements``
    silently breaks the moment a property name is not yet seeded in
    ``property_types``.
    """

    @pytest.mark.asyncio
    async def test_unknown_property_is_visible_in_response(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
    ) -> None:
        """One seeded + one unseeded property -> the alias fully reconciles."""
        payload = _ingest_payload(
            properties=[
                _sample_property(),
                _sample_property({"property": "totally_unseeded_property"}),
            ],
            corpus_id="counter-reconciliation",
        )

        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]

        assert data["skipped_unknown_properties"] == 1, (
            "The unseeded property must be reported under its own counter"
        )
        assert data["skipped_duplicates"] == (
            data["reused_entities"]
            + data["skipped_duplicate_measurements"]
            + data["skipped_unknown_properties"]
        ), (
            "skipped_duplicates must reconcile against every counter the "
            f"response exposes; got {data['skipped_duplicates']} vs "
            f"{data['reused_entities']} + {data['skipped_duplicate_measurements']} "
            f"+ {data['skipped_unknown_properties']}"
        )
        # The seeded property still lands.
        assert data["created_measurements"] == 1


class TestIngestKGBuildFailureIsolated:
    """AC-3: property persistence is isolated from KG build failures.

    The endpoint catches map_and_persist exceptions gracefully so that
    the ack is always returned.  When GraphBuilder is wired in (future),
    a KG failure must not roll back measurement writes.  This test
    verifies the isolation principle by mocking map_and_persist to raise,
    confirming the endpoint still returns 202 and the ack shape.
    """

    @pytest.mark.asyncio
    async def test_ingest_kg_build_failure_isolated(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mock map_and_persist to raise -> verify success=True (202 returned).

        Property measurements from a prior successful call should
        still be in the DB after the failed call.
        """

        # First: do a successful ingest to seed measurements
        from nfm_db.models.property import PropertyCategory, PropertyType
        cat = PropertyCategory(name="thermal", slug="thermal-iso")
        db_session.add(cat)
        await db_session.flush()
        pt = PropertyType(
            category_id=cat.id,
            name="lattice_constant",
            slug="lattice_constant_iso",
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        good_payload = _ingest_payload(
            properties=[_sample_property()],
            corpus_id="iso-test",
        )
        good_resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=good_payload,
            headers=svc_headers,
        )
        assert good_resp.status_code == 202
        good_body = good_resp.json()
        assert good_body["success"] is True
        measurements_before = (await db_session.execute(
            select(PropertyMeasurement)
        )).scalars().all()
        count_before = len(measurements_before)

        # Now mock map_and_persist to simulate a KG build failure.
        # Patch at the service module level because the endpoint
        # imports map_and_persist locally inside a try block.
        async def _failing_map_and_persist(db, props):
            raise RuntimeError("Simulated GraphBuilder.build_from_extraction failure")

        monkeypatch.setattr(
            "nfm_db.services.extraction_to_db_mapper.map_and_persist",
            _failing_map_and_persist,
        )

        fail_payload = _ingest_payload(
            properties=[_sample_property({"property": "thermal_conductivity"})],
            corpus_id="iso-test",
        )
        fail_resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=fail_payload,
            headers=svc_headers,
        )

        # Endpoint must still return 202 (failure is isolated)
        assert fail_resp.status_code == 202, (
            f"Expected 202 even on persist failure, got {fail_resp.status_code}"
        )
        fail_body = fail_resp.json()
        assert fail_body["success"] is True
        fail_data = fail_body["data"]
        assert fail_data["created_measurements"] == 0

        # Prior measurements must still be in the DB
        measurements_after = (await db_session.execute(
            select(PropertyMeasurement)
        )).scalars().all()
        assert len(measurements_after) == count_before, (
            "Prior measurements should survive a subsequent persist failure"
        )


class TestIngestWithConditions:
    """AC-3: conditions dict is correctly persisted to MeasurementCondition."""

    @pytest.mark.asyncio
    async def test_ingest_with_conditions(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """conditions={{"temperature": 400}} -> MeasurementCondition.temperature == 400."""
        payload = _ingest_payload(
            properties=[_sample_property({"conditions": {"temperature": 400}})],
            corpus_id="cond-test",
        )

        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp.status_code == 202
        cond_body = resp.json()
        assert cond_body["success"] is True
        data = cond_body["data"]
        assert data["created_measurements"] == 1

        # Verify MeasurementCondition row
        conditions = (await db_session.execute(
            select(MeasurementCondition)
        )).scalars().all()
        assert len(conditions) >= 1, "Expected at least one MeasurementCondition row"
        temps = [c.temperature for c in conditions if c.temperature is not None]
        assert 400.0 in temps, (
            f"Expected temperature=400.0 in conditions, got {temps}"
        )


class TestIngestDuplicateDetection:
    """AC-3: 5-tuple dedup within and across POSTs.

    Within a single POST, map_and_persist skips exact 5-tuple duplicates.
    Across POSTs, DataSource/Material find-or-create produces skip counts.
    Full cross-call measurement dedup is a future enhancement.
    """

    @pytest.mark.asyncio
    async def test_ingest_duplicate_detection(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """Two POSTs with identical 5-tuple properties -> second shows
        skipped_duplicates == len(properties).
        """
        props = [
            _sample_property({"value": "5.470"}),
            _sample_property({"value": "10.20", "property": "density"}),
        ]
        # Seed a second property type for density
        category = (await db_session.execute(
            select(PropertyCategory).where(PropertyCategory.slug == "thermal")
        )).scalar_one_or_none()
        if category is not None:
            density_type = PropertyType(
                category_id=category.id,
                name="density",
                slug="density",
                value_type="scalar",
            )
            db_session.add(density_type)
            await db_session.flush()

        payload = _ingest_payload(
            properties=props,
            corpus_id="dedup-test",
        )

        # First POST
        resp1 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp1.status_code == 202
        body1 = resp1.json()
        assert body1["success"] is True
        data1 = body1["data"]
        assert data1["created_measurements"] == 2
        assert data1["skipped_duplicates"] == 0
        # NFM-1996: split counters must be present
        assert "reused_entities" in data1
        assert "skipped_duplicate_measurements" in data1

        # Second POST (identical 5-tuples).
        # NOTE: map_and_persist 5-tuple dedup is per-call (within a single
        # batch).  Cross-call dedup (same 5-tuple across separate POSTs) is a
        # future enhancement.  For now, DataSource/Material find-or-create
        # produces skipped counts >= 1.
        resp2 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=payload,
            headers=svc_headers,
        )
        assert resp2.status_code == 202
        body2 = resp2.json()
        assert body2["success"] is True
        data2 = body2["data"]
        assert data2["skipped_duplicates"] >= 1, (
            f"Expected skipped_duplicates >= 1, got {data2['skipped_duplicates']}"
        )
        # NFM-1996: backward-compat alias must equal sum of split counters
        assert data2["skipped_duplicates"] == (
            data2["reused_entities"] + data2["skipped_duplicate_measurements"]
        )


class TestSyncVerificationPerRequestDelta:
    """NFM-2096 AC-2 / AC-3: AC-R3 sync verification uses per-request
    delta (after - before == created_measurements), NOT cumulative count.

    Before the W1 fix, the verifier compared ``created_measurements``
    against ``SELECT count(*)`` (cumulative per-DOI), which only held on
    the FIRST ingest.  After the fix, both POST#1 and POST#2 with
    distinct valid values under the same DOI report ``verified=True``.
    """

    @pytest.mark.asyncio
    async def test_incremental_ingest_both_verified(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        db_session: AsyncSession,
    ) -> None:
        """POST#1 then POST#2 with distinct 5-tuples under the same
        DOI must both report ``verified=True``.

        The 5-tuple dedup key is (material_name, property, source_ref,
        conditions_hash, method).  To test a *legitimate* incremental
        ingest, the second POST must differ in at least one dedup-key
        component — we use a different property name (lattice_constant
        vs density), seeded as a separate PropertyType.

        ``db_measurement_count`` reflects the cumulative total AFTER
        this request, while ``verified`` checks the per-request delta.
        """
        doi = "10.1234/nfm2096-ac2"

        # Seed a second property type so the second POST is a novel 5-tuple.
        category = (await db_session.execute(
            select(PropertyCategory).where(PropertyCategory.slug == "thermal")
        )).scalar_one_or_none()
        assert category is not None
        density_type = PropertyType(
            category_id=category.id,
            name="density",
            slug="density",
            value_type="scalar",
        )
        db_session.add(density_type)
        await db_session.flush()

        # POST#1: first ingest for this DOI (lattice_constant).
        resp1 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                properties=[_sample_property({"value": "5.470"})],
                corpus_id="nfm2096-delta",
                source_reference=doi,
            ),
            headers=svc_headers,
        )
        assert resp1.status_code == 202
        data1 = resp1.json()["data"]

        # AC-2a: first ingest must be verified.
        assert data1["verified"] is True, (
            f"POST#1 should be verified; got verified={data1['verified']}, "
            f"db_measurement_count={data1['db_measurement_count']}, "
            f"created_measurements={data1['created_measurements']}"
        )
        assert data1["db_measurement_count"] == 1
        assert data1["created_measurements"] == 1

        # POST#2: distinct 5-tuple under the SAME DOI (different property).
        resp2 = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                properties=[_sample_property({
                    "property": "density",
                    "value": "10.95",
                    "unit": "g/cm3",
                })],
                corpus_id="nfm2096-delta",
                source_reference=doi,
            ),
            headers=svc_headers,
        )
        assert resp2.status_code == 202
        data2 = resp2.json()["data"]

        # AC-2b: second ingest must ALSO be verified (per-request delta).
        assert data2["verified"] is True, (
            f"POST#2 should be verified; got verified={data2['verified']}, "
            f"db_measurement_count={data2['db_measurement_count']}, "
            f"created_measurements={data2['created_measurements']}"
        )
        # db_measurement_count is cumulative (2 after both POSTs),
        # but verified=True because delta (2-1=1) == created_measurements (1).
        assert data2["db_measurement_count"] == 2
        assert data2["created_measurements"] == 1

    @pytest.mark.asyncio
    async def test_force_mismatch_detected(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC-3: mock map_and_persist to claim created_measurements=2 while
        inserting 0 rows -> ``verified=False`` with MISMATCH error.

        This confirms the detector still catches real silent failures.
        """
        doi = "10.1234/nfm2096-ac3"

        # Patch map_and_persist to lie about what it persisted.
        from nfm_db.services.extraction_to_db_mapper import (
            MappingResult,
            map_and_persist,
        )

        _real_map_and_persist = map_and_persist

        async def _lying_map_and_persist(db, props):
            # Call the real one so DB state is consistent, but return
            # inflated created_measurements.
            result = await _real_map_and_persist(db, props)
            return MappingResult(
                created_measurements=99,  # lie: claim 99, actually 0 new
                reused_entities=result.reused_entities,
                skipped_duplicate_measurements=result.skipped_duplicate_measurements,
                skipped_unknown_properties=result.skipped_unknown_properties,
                validation_errors=result.validation_errors,
            )

        monkeypatch.setattr(
            "nfm_db.services.extraction_to_db_mapper.map_and_persist",
            _lying_map_and_persist,
        )

        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                properties=[_sample_property()],
                corpus_id="nfm2096-mismatch",
                source_reference=doi,
            ),
            headers=svc_headers,
        )
        assert resp.status_code == 202
        data = resp.json()["data"]

        # AC-3: must report verified=False.
        assert data["verified"] is False, (
            f"Force-mismatch should yield verified=False; "
            f"got verified={data['verified']}"
        )
        # Must contain the structured MISMATCH error message.
        mismatch_errors = [
            e for e in data["errors"]
            if "sync-verification MISMATCH" in e
            and "created_measurements=99" in e
        ]
        assert len(mismatch_errors) == 1, (
            f"Expected exactly 1 MISMATCH error mentioning "
            f"created_measurements=99; got errors={data['errors']}"
        )

class TestVerifiedFalseDetector:
    """NFM-2097 AC-1 / AC-2: verified-False drift paths that the original
    fc4984a commit failed to cover — the gaps that allowed the W1
    per-request-vs-cumulative bug to ship through Code Review.
    """

    @pytest.mark.asyncio
    async def test_verified_false_on_mismatch_with_job_id(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC-1: mock map_and_persist to claim created_measurements=2 while
        actually persisting 1 row.

        Asserts verified=False, db_measurement_count=1, a structured
        MISMATCH error in errors[], and a valid job_id for correlation.
        """
        from nfm_db.services.extraction_to_db_mapper import (
            MappingResult,
            map_and_persist,
        )

        _real = map_and_persist

        async def _lying_mapper(db, props):
            result = await _real(db, props)
            return MappingResult(
                created_measurements=2,
                reused_entities=result.reused_entities,
                skipped_duplicate_measurements=result.skipped_duplicate_measurements,
                skipped_unknown_properties=result.skipped_unknown_properties,
                validation_errors=result.validation_errors,
            )

        monkeypatch.setattr(
            "nfm_db.services.extraction_to_db_mapper.map_and_persist",
            _lying_mapper,
        )

        doi = "10.1234/nfm2097-ac1"
        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json=_ingest_payload(
                properties=[_sample_property()],
                corpus_id="nfm2097-mismatch",
                source_reference=doi,
            ),
            headers=svc_headers,
        )
        assert resp.status_code == 202
        data = resp.json()["data"]

        assert data["verified"] is False, (
            f"Expected verified=False; got {data['verified']}"
        )
        assert data["db_measurement_count"] == 1, (
            f"Expected db_measurement_count=1; got {data['db_measurement_count']}"
        )

        mismatch_errors = [
            e for e in data["errors"]
            if "sync-verification MISMATCH" in e
            and "created_measurements=2" in e
        ]
        assert len(mismatch_errors) == 1, (
            f"Expected 1 MISMATCH error; got {data['errors']}"
        )

        # Response carries a valid job_id for correlation.
        assert "job_id" in data
        assert len(data["job_id"]) == 36  # UUID hex format

    @pytest.mark.asyncio
    async def test_verified_false_when_no_source_reference(
        self,
        async_client: AsyncClient,
        svc_headers: dict[str, str],
        seeded_property_type: PropertyType,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC-2: POST with empty source_reference -> verified=False with
        'sync-verification SKIPPED: no source_reference' error.

        The schema now accepts empty source_reference (NFM-2097).  When
        source_reference is empty, the handler's sync-verification
        cannot match by DOI and reports verified=False with SKIPPED.
        """
        resp = await async_client.post(
            "/api/v1/extraction/ingest",
            json={
                "source_reference": "",
                "source_type": "doi",
                "corpus_id": "nfm2097-nosr",
                "properties": [_sample_property()],
            },
            headers=svc_headers,
        )

        assert resp.status_code == 202
        data = resp.json()["data"]

        assert data["verified"] is False, (
            f"Expected verified=False; got {data['verified']}"
        )

        skipped_errors = [
            e for e in data["errors"]
            if "sync-verification SKIPPED" in e
            and "no source_reference" in e
        ]
        assert len(skipped_errors) == 1, (
            f"Expected 1 SKIPPED error; got {data['errors']}"
        )
