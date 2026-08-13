"""Tests for the V2 pipeline feature-flag helper (NFM-2680).

Strangler-fig routing is gated on ``EXTRACTION_PIPELINE_V2`` (config field
``extraction_v2_enabled``).  The dispatcher must:
1. Default OFF (legacy unchanged).
2. Honour the ``NFM_EXTRACTION_V2_ENABLED`` env var (pydantic Settings
   ``env_prefix="NFM_"``).
3. Expose a cheap cached helper so hot-path call-sites don't re-parse
   settings every invocation.
4. Route to the legacy ``trigger_extraction()`` when OFF.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_is_extraction_v2_enabled_defaults_on():
    """No env var set → True (NFM-2869-T2 flip)."""
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("NFM_EXTRACTION_V2_ENABLED", None)
        from nfm_db.services.extraction_pipeline_dispatch import (
            is_extraction_v2_enabled,
        )
        try:
            is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        assert is_extraction_v2_enabled() is True


def test_is_extraction_v2_enabled_honours_env_var():
    """Setting NFM_EXTRACTION_V2_ENABLED=1 flips the helper to True."""
    with patch.dict("os.environ", {"NFM_EXTRACTION_V2_ENABLED": "1"}):
        from nfm_db.services.extraction_pipeline_dispatch import (
            is_extraction_v2_enabled,
        )
        try:
            is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        assert is_extraction_v2_enabled() is True


def test_trigger_extraction_pipeline_off_routes_to_legacy(monkeypatch):
    """Flag OFF → the dispatcher must call the legacy trigger_extraction."""
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    # Force OFF regardless of host env.
    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: False)

    called = {"legacy": 0}

    from nfm_db.models.extraction_job import ExtractionJob as OrmExtractionJob
    from nfm_db.services.extraction_pipeline import JobStatus

    fake_job = OrmExtractionJob(
        job_id="fake-job-id",
        source_reference="foo.md",
        source_type="file",
        status=JobStatus.COMPLETED,
    )

    async def fake_legacy(*args, **kwargs):
        called["legacy"] += 1
        return fake_job

    # Stub AsyncSession instance so the typeguard in the dispatch accepts it.
    from sqlalchemy.ext.asyncio import AsyncSession
    fake_legacy_session = AsyncSession()

    monkeypatch.setattr(
        "nfm_db.services.extraction_pipeline_dispatch.trigger_extraction",
        fake_legacy,
    )

    import asyncio

    result = asyncio.run(
        dispatch_mod.trigger_extraction_pipeline(
            source_reference="foo.md",
            source_type="file",
            session=fake_legacy_session,
        )
    )
    assert called["legacy"] == 1
    assert result["status"] == "completed"
    assert result["job_id"] == "fake-job-id"


def test_trigger_extraction_pipeline_on_routes_to_v2(monkeypatch):
    """Flag ON → the dispatcher must route to the V2 orchestrator path."""
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: True)

    called = {"v2": 0}

    async def fake_v2(*args, **kwargs):
        called["v2"] += 1
        return {"routed": "v2"}

    # The dispatcher should consult this symbol when ON.
    monkeypatch.setattr(dispatch_mod, "_run_v2_pipeline", fake_v2)

    import asyncio

    result = asyncio.run(
        dispatch_mod.trigger_extraction_pipeline(
            source_reference="foo.md",
            source_type="file",
        )
    )
    assert called["v2"] == 1
    assert result["routed"] == "v2"


def test_is_extraction_v2_enabled_is_cached():
    """Repeated calls within one process must not re-parse settings."""
    from nfm_db.services.extraction_pipeline_dispatch import (
        is_extraction_v2_enabled,
    )
    # If @lru_cache is applied, .cache_info / .cache_clear exist.
    assert hasattr(is_extraction_v2_enabled, "cache_info")


@pytest.mark.asyncio
async def test_trigger_extraction_pipeline_v2_runs_orchestrator(
    monkeypatch, tmp_path, db_session,
):
    """V2 path runs the full 5-step orchestrator and returns the
    canonical 24-key dict (NFM-2686).

    Creates a temporary source file, calls the dispatcher with the V2
    flag forced ON, and asserts the response has the expected shape
    and the orchestrator persisted chunks to the DB.
    """
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    # Force V2 flag ON.
    dispatch_mod.is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: True)

    # Create a source file with markdown content.
    source_file = tmp_path / "test_material.md"
    source_file.write_text(
        "# UO2 Properties\n\n"
        "## Introduction\n"
        "UO2 is a nuclear fuel material.\n\n"
        "## Thermophysical Data\n"
        "Lattice constant: 5.47 angstrom\n"
        "Melting point: 3100 K\n"
        "Thermal conductivity: 8.0 W/(m-K)\n",
        encoding="utf-8",
    )

    result = await dispatch_mod.trigger_extraction_pipeline(
        source_reference=str(source_file),
        source_type="file",
        session=db_session,
    )

    # Response shape: canonical 24-key dict.
    assert isinstance(result, dict)
    assert result["status"] == "completed"
    assert result["source_reference"] == str(source_file)
    assert result["source_type"] == "file"
    assert result["job_id"] is not None
    assert result["error_message"] is None
    assert result["created_at"] is not None
    assert result["completed_at"] is not None

    # Verify chunks were persisted via the orchestrator.
    import uuid

    from sqlalchemy import select

    from nfm_db.models.extraction_chunk import ExtractionChunk as ORMChunk

    job_uuid = uuid.UUID(result["job_id"])
    job_chunks = (
        await db_session.execute(
            select(ORMChunk).where(ORMChunk.job_id == job_uuid)
        )
    ).scalars().all()

    assert len(job_chunks) >= 1, "V2 orchestrator should persist at least one chunk"


@pytest.mark.asyncio
async def test_trigger_extraction_pipeline_legacy_returns_normalized_dict(monkeypatch):
    """Legacy path returns the NFM-2743 / D3 canonical 24-key dict."""
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: False)

    from unittest.mock import AsyncMock, patch

    from nfm_db.models.extraction_job import ExtractionJob as OrmExtractionJob
    from nfm_db.services.extraction_pipeline import JobStatus

    fake_job = OrmExtractionJob(
        job_id="test-job-123",
        source_reference="foo.md",
        source_type="file",
        status=JobStatus.QUEUED,
    )

    with patch(
        "nfm_db.services.extraction_pipeline_dispatch.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ):
        from sqlalchemy.ext.asyncio import AsyncSession

        fake_session = AsyncSession()
        result = await dispatch_mod.trigger_extraction_pipeline(
            source_reference="foo.md",
            source_type="file",
            session=fake_session,
        )

    assert isinstance(result, dict)
    assert result["status"] == "queued"
    assert result["job_id"] == "test-job-123"
    assert result["created_at"] is not None
    assert result["error_message"] is None
    # D3 seam — the helper exposes the full canonical key set, not just
    # the four ad-hoc keys the previous inline normalization emitted.
    # (NFM-2743 AC: ``assert set(from_dataclass) == set(from_orm)``.)
    assert len(result) == 24, (
        f"Expected 24 canonical keys, got {len(result)}: {set(result)!r}"
    )
    for required_key in (
        "job_id", "source_reference", "source_type", "status",
        "error_message", "created_at", "started_at", "completed_at",
        "fill_batch_id", "extracted_count", "staged_count", "rejected_count",
        "element_systems", "cache_level", "max_confidence",
        "conflict_strategy", "figures", "tables",
        "extract_figures", "extract_tables", "confidence_threshold",
        "figure_types", "ontology_version_id", "ontology_version_str",
    ):
        assert required_key in result, f"Missing canonical key: {required_key}"
