"""Tests for _get_job_properties() bridging staging DB to v4 result endpoint.

NFM-634: Verifies that _get_job_properties queries ref_gap_fill_staging
by fill_batch_id and returns property dicts compatible with _to_v4_property().
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.main import app
from nfm_db.models.ref_gap_fill import (
    CacheLevel,
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _job_store,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_client(db_session: AsyncSession):
    """Async test client for v4 extraction endpoints with DB session."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides.clear()
    from nfm_db.database import get_db

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


def _make_completed_job(
    job_id: str,
    fill_batch_id: str | None = None,
) -> ExtractionJob:
    """Create a completed ExtractionJob in the in-memory store."""
    job = ExtractionJob(
        job_id=job_id,
        source_reference="10.1016/j.jnucmat.2023.01.001",
        source_type="doi",
        status=JobStatus.COMPLETED,
        fill_batch_id=fill_batch_id,
        extracted_count=3,
        staged_count=3,
        rejected_count=0,
    )
    _job_store[job_id] = job
    return job


def _make_staging_record(
    fill_batch_id: uuid.UUID,
    *,
    element_system: str = "UO2",
    phase: str | None = "alpha",
    property_name: str = "thermal_conductivity",
    value: float = 8.5,
    unit: str = "W/(m·K)",
    method: str | None = "measurement",
    source: str = "Test Paper 2023",
    source_doi: str | None = "10.1016/test",
    uncertainty: float | None = 0.1,
    temperature: float | None = 300.0,
    source_file: str | None = "paper.pdf",
    composition: str | None = "UO2",
    element: str | None = "U",
    property_category: str | None = "thermal",
    context: str | None = "in-pile",
    confidence: Confidence = Confidence.HIGH,
    status: StagingStatus = StagingStatus.PENDING,
    cache_level: CacheLevel | None = CacheLevel.L2,
) -> RefGapFillStaging:
    """Create a RefGapFillStaging record for testing."""
    return RefGapFillStaging(
        id=uuid.uuid4(),
        element_system=element_system,
        phase=phase,
        property_name=property_name,
        value=value,
        unit=unit,
        method=method,
        source=source,
        source_doi=source_doi,
        uncertainty=uncertainty,
        temperature=temperature,
        source_file=source_file,
        composition=composition,
        element=element,
        property_category=property_category,
        context=context,
        confidence=confidence,
        dedup_hash=f"hash-{uuid.uuid4()}",
        status=status,
        cache_level=cache_level,
        fill_batch_id=fill_batch_id,
    )


# ---------------------------------------------------------------------------
# Test: result endpoint returns staging properties
# ---------------------------------------------------------------------------


class TestGetJobPropertiesFromStaging:
    """Tests for _get_job_properties() querying ref_gap_fill_staging."""

    @pytest.mark.asyncio
    async def test_result_returns_staging_properties(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Result endpoint should return properties from staging DB."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-with-staging"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            # Insert two staging records with the same fill_batch_id
            batch_uuid = uuid.UUID(fill_batch_id)
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="thermal_conductivity",
                    value=8.5,
                    confidence=Confidence.HIGH,
                )
            )
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="density",
                    value=10.97,
                    confidence=Confidence.MEDIUM,
                    property_category="mechanical",
                )
            )
            await db_session.commit()

            # Call result endpoint
            response = await db_client.get(f"/api/v4/extraction/{job_id}/result")
            assert response.status_code == 200
            body = response.json()
            assert body["success"] is True

            properties = body["data"]["properties"]
            assert len(properties) == 2

            # Verify first property fields
            prop_names = {p["property"] for p in properties}
            assert "thermal_conductivity" in prop_names
            assert "density" in prop_names

            # Verify confidence filtering is available via meta
            assert body["meta"]["total"] == 2
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_property_fields_match_staging(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Each property dict should contain all fields from staging."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-field-mapping"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            batch_uuid = uuid.UUID(fill_batch_id)
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    element_system="Zr-Nb",
                    phase="beta",
                    property_name="yield_strength",
                    value=450.0,
                    unit="MPa",
                    method="tensile_test",
                    source="Smith et al. 2024",
                    source_doi="10.1016/j.msea.2024.111",
                    uncertainty=5.0,
                    temperature=600.0,
                    source_file="smith2024.pdf",
                    composition="Zr-2.5Nb",
                    element="Zr",
                    property_category="mechanical",
                    context="creep regime",
                    confidence=Confidence.HIGH,
                    status=StagingStatus.APPROVED,
                    cache_level=CacheLevel.L1,
                )
            )
            await db_session.commit()

            response = await db_client.get(f"/api/v4/extraction/{job_id}/result")
            assert response.status_code == 200
            properties = response.json()["data"]["properties"]
            assert len(properties) == 1

            prop = properties[0]
            assert prop["material_name"] == "Zr-Nb"
            assert prop["property"] == "yield_strength"
            assert prop["value"] == "450.0"
            assert prop["unit"] == "MPa"
            assert prop["confidence"] == "high"
            assert prop["staging_status"] == "approved"
            assert prop["cache_level"] == "L1"
            assert prop["reference"] == "Smith et al. 2024"
            assert prop["source_file"] == "smith2024.pdf"
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_empty_when_no_staging_records(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Result should return empty properties when no staging records exist."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-no-staging"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            response = await db_client.get(f"/api/v4/extraction/{job_id}/result")
            assert response.status_code == 200
            properties = response.json()["data"]["properties"]
            assert properties == []
            assert response.json()["meta"]["total"] == 0
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_empty_when_fill_batch_id_is_none(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Result should return empty properties when fill_batch_id is None."""
        job_id = "test-job-no-batch-id"

        _make_completed_job(job_id, fill_batch_id=None)
        try:
            response = await db_client.get(f"/api/v4/extraction/{job_id}/result")
            assert response.status_code == 200
            properties = response.json()["data"]["properties"]
            assert properties == []
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_empty_when_job_not_found(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Result should return empty properties when job is unknown."""
        response = await db_client.get("/api/v4/extraction/unknown-job/result")
        # Job not found returns 404, not empty properties
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_result_confidence_filter_works_with_staging(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Confidence filter should work on staging-sourced properties."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-confidence-filter"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            batch_uuid = uuid.UUID(fill_batch_id)
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="prop_high",
                    confidence=Confidence.HIGH,
                )
            )
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="prop_low",
                    confidence=Confidence.LOW,
                )
            )
            await db_session.commit()

            # Filter by confidence=high
            response = await db_client.get(
                f"/api/v4/extraction/{job_id}/result",
                params={"confidence": "high"},
            )
            assert response.status_code == 200
            properties = response.json()["data"]["properties"]
            assert len(properties) == 1
            assert properties[0]["property"] == "prop_high"
            assert response.json()["meta"]["total"] == 1
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_pagination_works_with_staging(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Pagination (limit/offset) should work on staging-sourced properties."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-pagination"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            batch_uuid = uuid.UUID(fill_batch_id)
            for i in range(5):
                db_session.add(
                    _make_staging_record(
                        batch_uuid,
                        property_name=f"prop_{i}",
                    )
                )
            await db_session.commit()

            # Page 1, limit 2
            response = await db_client.get(
                f"/api/v4/extraction/{job_id}/result",
                params={"page": 1, "limit": 2},
            )
            assert response.status_code == 200
            properties = response.json()["data"]["properties"]
            assert len(properties) == 2
            assert response.json()["meta"]["total"] == 5
            assert response.json()["meta"]["page"] == 1
            assert response.json()["meta"]["limit"] == 2

            # Page 2, limit 2
            response = await db_client.get(
                f"/api/v4/extraction/{job_id}/result",
                params={"page": 2, "limit": 2},
            )
            properties = response.json()["data"]["properties"]
            assert len(properties) == 2
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_validate_uses_staging_properties(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Validate endpoint should count staging-sourced properties."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-validate-staging"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            batch_uuid = uuid.UUID(fill_batch_id)
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="prop_high",
                    confidence=Confidence.HIGH,
                )
            )
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="prop_medium",
                    confidence=Confidence.MEDIUM,
                )
            )
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    property_name="prop_low",
                    confidence=Confidence.LOW,
                )
            )
            await db_session.commit()

            response = await db_client.post(
                f"/api/v4/extraction/{job_id}/validate",
                json={"auto_approve": True},
            )
            assert response.status_code == 202
            data = response.json()["data"]
            assert data["total_properties"] == 3
            assert data["auto_approved"] == 1
            assert data["sent_to_review"] == 1
            assert data["flagged"] == 1
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_cache_level_nullable(
        self, db_client: AsyncClient, db_session: AsyncSession
    ):
        """Properties with null cache_level should have cache_level=None."""
        fill_batch_id = str(uuid.uuid4())
        job_id = "test-job-null-cache-level"

        _make_completed_job(job_id, fill_batch_id=fill_batch_id)
        try:
            batch_uuid = uuid.UUID(fill_batch_id)
            db_session.add(
                _make_staging_record(
                    batch_uuid,
                    cache_level=None,
                )
            )
            await db_session.commit()

            response = await db_client.get(f"/api/v4/extraction/{job_id}/result")
            assert response.status_code == 200
            prop = response.json()["data"]["properties"][0]
            assert prop["cache_level"] is None
        finally:
            _job_store.pop(job_id, None)
