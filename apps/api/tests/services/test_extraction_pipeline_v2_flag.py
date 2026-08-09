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


def test_is_extraction_v2_enabled_defaults_off():
    """No env var set → False."""
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
        assert is_extraction_v2_enabled() is False


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

    from datetime import UTC, datetime

    class FakeJob:
        status = type("S", (), {"value": "completed"})()
        job_id = "fake-job-id"
        created_at = datetime.now(UTC)
        error_message = None

    async def fake_legacy(*args, **kwargs):
        called["legacy"] += 1
        return FakeJob()

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
async def test_trigger_extraction_pipeline_v2_raises_not_implemented(monkeypatch):
    """V2 path raises NotImplementedError until content loading is wired.

    The flag-default-off guard prevents accidental zero-result runs in
    production.  Once RawTextLoader gains production document-fetch
    wiring this test is replaced with a real round-trip.
    """
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: True)
    try:
        dispatch_mod.is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
    except AttributeError:
        pass

    with pytest.raises(NotImplementedError, match="content loading not yet implemented"):
        await dispatch_mod.trigger_extraction_pipeline(
            source_reference="10.5555/not-implemented-test",
            source_type="doi",
        )


@pytest.mark.asyncio
async def test_trigger_extraction_pipeline_legacy_returns_normalized_dict(monkeypatch):
    """Legacy path returns a normalized dict with consistent keys."""
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: False)

    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    fake_job = type(
        "Job",
        (),
        {
            "status": type("S", (), {"value": "queued"})(),
            "job_id": "test-job-123",
            "created_at": datetime.now(UTC),
            "error_message": None,
        },
    )()

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
