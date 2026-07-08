"""Tests for V4 multimodal data wiring (NFM-924).

Covers:
- POST /submit passes multimodal options through to pipeline
- GET /result returns figures[] and tables[] with VLM-extracted data
- GET /result supports include_figures / include_tables query params
- GET /status shows multimodal extraction steps in progress tracker
- Backward compatibility: requests without multimodal options return empty figures/tables
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app
from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _job_store,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def v4_client(db_session):
    """Async test client for v4 extraction endpoints."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides.clear()
    from nfm_db.database import get_db

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


def _make_completed_job_with_multimodal(
    job_id: str = "test-multimodal-job",
) -> None:
    """Insert a completed job with figures and tables into the in-memory store."""
    _job_store[job_id] = ExtractionJob(
        job_id=job_id,
        source_reference="10.1016/test",
        source_type="doi",
        status=JobStatus.COMPLETED,
        extracted_count=5,
        staged_count=4,
        rejected_count=1,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        extract_figures=True,
        extract_tables=True,
        figure_types=["plot", "diagram"],
        confidence_threshold=0.7,
        conflict_strategy="prefer_vlm",
        figures=[
            {
                "page_number": 3,
                "source_file": "10.1016/test.pdf",
                "vision_result": {
                    "figure_type": "plot",
                    "plot_data": None,
                    "table_data": None,
                    "source_image_path": "fig1.png",
                    "provider": "openai",
                },
            },
            {
                "page_number": 5,
                "source_file": "10.1016/test.pdf",
                "vision_result": {
                    "figure_type": "diagram",
                    "plot_data": None,
                    "table_data": None,
                    "source_image_path": "fig2.png",
                    "provider": "openai",
                },
            },
        ],
        tables=[
            {
                "page_number": 2,
                "source_file": "10.1016/test.pdf",
                "table_data": {
                    "title": "Thermal conductivity",
                    "headers": {"columns": ["T (K)", "k (W/mK)"]},
                    "rows": [],
                    "num_columns": 2,
                    "num_rows": 5,
                    "has_merged_cells": False,
                    "notes": [],
                },
            },
        ],
    )


def _make_completed_job_without_multimodal(
    job_id: str = "test-no-multimodal-job",
) -> None:
    """Insert a completed job with NO multimodal data (backward compat)."""
    _job_store[job_id] = ExtractionJob(
        job_id=job_id,
        source_reference="10.1016/classic",
        source_type="doi",
        status=JobStatus.COMPLETED,
        extracted_count=3,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# POST /submit — multimodal options pass-through
# ---------------------------------------------------------------------------


class TestSubmitMultimodalOptions:
    """POST /api/v4/extraction/submit passes multimodal options to pipeline."""

    @pytest.mark.asyncio
    async def test_submit_with_multimodal_options_returns_202(
        self, v4_client: AsyncClient
    ):
        payload = {
            "source_reference": "10.1016/test",
            "source_type": "doi",
            "extract_figures": True,
            "extract_tables": True,
            "figure_types": ["plot", "diagram"],
            "confidence_threshold": 0.7,
            "conflict_strategy": "prefer_vlm",
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert "job_id" in data

        # Verify the job was stored with multimodal options
        job = _job_store.get(data["job_id"])
        assert job is not None
        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["plot", "diagram"]
        assert job.confidence_threshold == 0.7
        assert job.conflict_strategy == "prefer_vlm"

    @pytest.mark.asyncio
    async def test_submit_without_multimodal_defaults_false(
        self, v4_client: AsyncClient
    ):
        """Backward compat: omitting multimodal fields defaults to disabled."""
        payload = {
            "source_reference": "10.1016/classic",
            "source_type": "doi",
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 202
        job_id = response.json()["data"]["job_id"]
        job = _job_store.get(job_id)
        assert job is not None
        assert job.extract_figures is False
        assert job.extract_tables is False
        assert job.figure_types is None

    @pytest.mark.asyncio
    async def test_submit_defaults_conflict_strategy(
        self, v4_client: AsyncClient
    ):
        """conflict_strategy defaults to prefer_vlm when not specified."""
        payload = {
            "source_reference": "10.1016/test",
            "source_type": "doi",
            "extract_figures": True,
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 202
        job_id = response.json()["data"]["job_id"]
        job = _job_store.get(job_id)
        assert job.conflict_strategy == "prefer_vlm"


# ---------------------------------------------------------------------------
# GET /result — figures and tables
# ---------------------------------------------------------------------------


class TestResultMultimodalData:
    """GET /api/v4/extraction/{job_id}/result returns figures and tables."""

    @pytest.mark.asyncio
    async def test_result_includes_figures_and_tables(
        self, v4_client: AsyncClient
    ):
        _make_completed_job_with_multimodal("result-mm-test")
        try:
            response = await v4_client.get("/api/v4/extraction/result-mm-test/result")
            assert response.status_code == 200
            body = response.json()
            data = body["data"]

            assert "figures" in data
            assert "tables" in data
            assert len(data["figures"]) == 2
            assert len(data["tables"]) == 1

            # Verify figure structure
            fig = data["figures"][0]
            assert fig["page_number"] == 3
            assert fig["source_file"] == "10.1016/test.pdf"
            assert "vision_result" in fig

            # Verify table structure
            tbl = data["tables"][0]
            assert tbl["page_number"] == 2
            assert tbl["source_file"] == "10.1016/test.pdf"
            assert "table_data" in tbl
        finally:
            _job_store.pop("result-mm-test", None)

    @pytest.mark.asyncio
    async def test_result_empty_figures_tables_by_default(
        self, v4_client: AsyncClient
    ):
        """Backward compat: job without multimodal data returns empty arrays."""
        _make_completed_job_without_multimodal("result-compat-test")
        try:
            response = await v4_client.get(
                "/api/v4/extraction/result-compat-test/result"
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["figures"] == []
            assert data["tables"] == []
        finally:
            _job_store.pop("result-compat-test", None)

    @pytest.mark.asyncio
    async def test_result_include_figures_false(
        self, v4_client: AsyncClient
    ):
        """include_figures=false excludes figures from response."""
        _make_completed_job_with_multimodal("result-fig-off")
        try:
            response = await v4_client.get(
                "/api/v4/extraction/result-fig-off/result",
                params={"include_figures": "false"},
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["figures"] == []
            assert len(data["tables"]) == 1  # tables still included
            meta = response.json()["meta"]
            assert meta["include_figures"] is False
        finally:
            _job_store.pop("result-fig-off", None)

    @pytest.mark.asyncio
    async def test_result_include_tables_false(
        self, v4_client: AsyncClient
    ):
        """include_tables=false excludes tables from response."""
        _make_completed_job_with_multimodal("result-tbl-off")
        try:
            response = await v4_client.get(
                "/api/v4/extraction/result-tbl-off/result",
                params={"include_tables": "false"},
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert len(data["figures"]) == 2  # figures still included
            assert data["tables"] == []
            meta = response.json()["meta"]
            assert meta["include_tables"] is False
        finally:
            _job_store.pop("result-tbl-off", None)

    @pytest.mark.asyncio
    async def test_result_both_include_false(
        self, v4_client: AsyncClient
    ):
        """Both include_figures=false and include_tables=false."""
        _make_completed_job_with_multimodal("result-both-off")
        try:
            response = await v4_client.get(
                "/api/v4/extraction/result-both-off/result",
                params={"include_figures": "false", "include_tables": "false"},
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["figures"] == []
            assert data["tables"] == []
        finally:
            _job_store.pop("result-both-off", None)

    @pytest.mark.asyncio
    async def test_result_meta_includes_multimodal_flags(
        self, v4_client: AsyncClient
    ):
        """Meta object includes include_figures and include_tables."""
        _make_completed_job_with_multimodal("result-meta-test")
        try:
            response = await v4_client.get(
                "/api/v4/extraction/result-meta-test/result",
                params={"include_figures": "true", "include_tables": "true"},
            )
            meta = response.json()["meta"]
            assert meta["include_figures"] is True
            assert meta["include_tables"] is True
        finally:
            _job_store.pop("result-meta-test", None)


# ---------------------------------------------------------------------------
# GET /status — multimodal steps in progress tracker
# ---------------------------------------------------------------------------


class TestStatusMultimodalSteps:
    """GET /status shows multimodal extraction steps."""

    @pytest.mark.asyncio
    async def test_ordered_steps_includes_multimodal(self):
        """_ORDERED_STEPS should contain extracting_figures and extracting_tables."""
        from nfm_db.api.v4.extraction import _ORDERED_STEPS

        assert "extracting_figures" in _ORDERED_STEPS
        assert "extracting_tables" in _ORDERED_STEPS
        # Verify ordering: after "extracting", before "mapping"
        extracting_idx = _ORDERED_STEPS.index("extracting")
        fig_idx = _ORDERED_STEPS.index("extracting_figures")
        tbl_idx = _ORDERED_STEPS.index("extracting_tables")
        mapping_idx = _ORDERED_STEPS.index("mapping")
        assert extracting_idx < fig_idx < tbl_idx < mapping_idx

    @pytest.mark.asyncio
    async def test_status_progress_includes_multimodal_steps(
        self, v4_client: AsyncClient
    ):
        """Progress object should reflect multimodal steps in remaining."""
        job_id = "status-mm-steps"
        _job_store[job_id] = ExtractionJob(
            job_id=job_id,
            source_reference="10.1016/test",
            source_type="doi",
            status=JobStatus.EXTRACTING,
        )
        try:
            response = await v4_client.get(f"/api/v4/extraction/{job_id}/status")
            assert response.status_code == 200
            progress = response.json()["data"]["progress"]
            assert "extracting_figures" in progress["steps_remaining"]
            assert "extracting_tables" in progress["steps_remaining"]
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_status_at_mapping_shows_multimodal_steps_completed(
        self, v4_client: AsyncClient
    ):
        """When status is mapping, both multimodal steps should be completed."""
        job_id = "status-mm-completed"
        _job_store[job_id] = ExtractionJob(
            job_id=job_id,
            source_reference="10.1016/test",
            source_type="doi",
            status=JobStatus.MAPPING,
        )
        try:
            response = await v4_client.get(f"/api/v4/extraction/{job_id}/status")
            assert response.status_code == 200
            progress = response.json()["data"]["progress"]
            assert progress["current_step"] == "mapping"
            # extracting_figures and extracting_tables should be completed
            # (they come before mapping in ordered steps)
            from nfm_db.api.v4.extraction import _ORDERED_STEPS

            assert "extracting_figures" in progress["steps_completed"]
            assert "extracting_tables" in progress["steps_completed"]
        finally:
            _job_store.pop(job_id, None)
