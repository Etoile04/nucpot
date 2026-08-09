"""NFM-2680 [NFM-2677-B1]: tests for the EXTRACTION_PIPELINE_V2 feature flag.

Verifies the four acceptance criteria:

1. The ``is_extraction_v2_enabled()`` helper defaults to ``False`` (flag
   defaults OFF — strangler-fig: legacy path remains default).
2. The helper reads ``NFM_EXTRACTION_V2_ENABLED`` from the environment
   and returns ``True`` when set.
3. The helper is cached — repeated calls do not re-read the environment.
4. ``trigger_extraction_dispatch()`` routes to legacy ``trigger_extraction()``
   when the flag is ``False``.
5. ``trigger_extraction_dispatch()`` routes to ``ExtractionOrchestrator``
   (NOT legacy) when the flag is ``True``.

The autouse ``_reset_flag_cache`` fixture clears the helper's cache
between tests so each test sees a fresh environment view.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_flag_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the cached flag value before and after each test for isolation."""
    # Avoid leaking NFM_EXTRACTION_V2_ENABLED into unrelated tests.
    monkeypatch.delenv("NFM_EXTRACTION_V2_ENABLED", raising=False)
    try:
        from nfm_db.config import is_extraction_v2_enabled

        is_extraction_v2_enabled.cache_clear()
    except (ImportError, AttributeError):
        pass  # Helper does not exist yet (RED phase).
    yield
    try:
        from nfm_db.config import is_extraction_v2_enabled

        is_extraction_v2_enabled.cache_clear()
    except (ImportError, AttributeError):
        pass


def test_is_extraction_v2_enabled_default_false() -> None:
    """AC1: Flag defaults to False when the env var is unset."""
    from nfm_db.config import is_extraction_v2_enabled

    assert is_extraction_v2_enabled() is False


def test_is_extraction_v2_enabled_returns_true_when_env_set() -> None:
    """AC2: Flag returns True when NFM_EXTRACTION_V2_ENABLED is truthy."""
    from nfm_db.config import is_extraction_v2_enabled

    # Pydantic-settings coerces standard boolean strings. The helper
    # just exposes the field, so we only test the strings it accepts.
    for truthy in ("true", "True", "1"):
        os.environ["NFM_EXTRACTION_V2_ENABLED"] = truthy
        is_extraction_v2_enabled.cache_clear()
        assert is_extraction_v2_enabled() is True, (
            f"env={truthy!r} should yield True"
        )

    for falsy in ("false", "False", "0"):
        os.environ["NFM_EXTRACTION_V2_ENABLED"] = falsy
        is_extraction_v2_enabled.cache_clear()
        assert is_extraction_v2_enabled() is False, (
            f"env={falsy!r} should yield False"
        )


def test_is_extraction_v2_enabled_is_cached() -> None:
    """AC3: Repeated calls do not re-read env (cheap lookup)."""
    from nfm_db.config import is_extraction_v2_enabled

    os.environ["NFM_EXTRACTION_V2_ENABLED"] = "true"
    first = is_extraction_v2_enabled()
    assert first is True

    # Flip the env var AFTER first read; cache must keep the first value.
    os.environ["NFM_EXTRACTION_V2_ENABLED"] = "false"
    second = is_extraction_v2_enabled()
    assert second is True, "second call must return the cached value"


@pytest.mark.asyncio
async def test_dispatch_wrapper_routes_to_legacy_when_flag_false() -> None:
    """AC4a: flag=False → dispatch wrapper invokes legacy trigger_extraction."""
    os.environ["NFM_EXTRACTION_V2_ENABLED"] = "false"

    mock_legacy_job = MagicMock()
    mock_legacy_job.job_id = "legacy-job-id"

    with patch(
        "nfm_db.services.extraction_pipeline.trigger_extraction",
        new_callable=AsyncMock,
        return_value=mock_legacy_job,
    ) as mock_legacy:
        from nfm_db.services.extraction_pipeline import (
            trigger_extraction_dispatch,
        )

        session = MagicMock()
        job = await trigger_extraction_dispatch(
            session=session,
            source_reference="doi:10.1234/dispatch-legacy",
            source_type="doi",
        )

    assert job is mock_legacy_job
    assert job.job_id == "legacy-job-id"
    mock_legacy.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_wrapper_routes_to_orchestrator_when_flag_true() -> None:
    """AC4b: flag=True → dispatch wrapper invokes ExtractionOrchestrator.

    Legacy trigger_extraction must NOT be called when the flag is True.
    """
    os.environ["NFM_EXTRACTION_V2_ENABLED"] = "true"

    mock_orch_job = MagicMock()
    mock_orch_job.id = "orch-job-uuid"
    mock_orch_job.job_id = "orch-job-id"
    mock_orch_instance = MagicMock()
    mock_orch_instance.run = AsyncMock(return_value=mock_orch_job)

    session = MagicMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    )

    with (
        patch(
            "nfm_db.services.extraction_pipeline.trigger_extraction",
            new_callable=AsyncMock,
        ) as mock_legacy,
        patch(
            "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
            return_value=mock_orch_instance,
        ) as mock_orch_cls,
    ):
        from nfm_db.services.extraction_pipeline import (
            trigger_extraction_dispatch,
        )

        job = await trigger_extraction_dispatch(
            session=session,
            source_reference="doi:10.1234/dispatch-orchestrator",
            source_type="doi",
        )

    mock_orch_cls.assert_called_once()
    mock_orch_instance.run.assert_awaited_once()
    mock_legacy.assert_not_called()
    assert job is mock_orch_job


@pytest.mark.asyncio
async def test_dispatch_wrapper_default_flag_is_legacy() -> None:
    """AC4c: No env override → dispatch wrapper defaults to legacy.

    This is the strangler-fig safety net: any caller that does not
    explicitly opt in to V2 keeps the legacy path.
    """
    # Ensure the env var is unset so the helper's default (False) wins.
    os.environ.pop("NFM_EXTRACTION_V2_ENABLED", None)

    mock_legacy_job = MagicMock()
    mock_legacy_job.job_id = "legacy-default-job"

    with patch(
        "nfm_db.services.extraction_pipeline.trigger_extraction",
        new_callable=AsyncMock,
        return_value=mock_legacy_job,
    ) as mock_legacy:
        from nfm_db.services.extraction_pipeline import (
            trigger_extraction_dispatch,
        )

        session = MagicMock()
        job = await trigger_extraction_dispatch(
            session=session,
            source_reference="doi:10.1234/dispatch-default",
            source_type="doi",
        )

    assert job.job_id == "legacy-default-job"
    mock_legacy.assert_awaited_once()
