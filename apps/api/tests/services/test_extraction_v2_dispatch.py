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


# ---------------------------------------------------------------------------
# Strict side-effect tests (NFM-2680 / [[flag-default-off-blinds-tests]])
#
# Mock-based tests above prove the wrapper's routing logic exists. The
# tests below prove the wrapper ACTUALLY routes to the right real code
# path by asserting observable per-branch side-effects:
#   - legacy branch MUST populate the module-level ``_job_store``
#   - V2 branch MUST ``session.add()`` an ``ORMExtractionJob``, MUST
#     instantiate ``ExtractionOrchestrator``, and MUST NOT touch
#     ``_job_store``.
# ---------------------------------------------------------------------------


class _RecordingOrchestrator:
    """Stub orchestrator class that records every instantiation.

    Replaces :class:`nfm_db.services.extraction_orchestrator.ExtractionOrchestrator`
    during V2-branch strict tests. Records the session + orm_job it was
    constructed with so the test can assert the V2 branch did the
    right thing.
    """

    instances: list[_RecordingOrchestrator] = []

    def __init__(self, session: object, orm_job: object) -> None:
        self.session = session
        self.orm_job = orm_job
        type(self).instances.append(self)

    async def run(self, **_kwargs: object) -> object:
        return self.orm_job


@pytest.fixture
def _reset_recorder() -> None:
    """Reset recording orchestrator class state between tests."""
    _RecordingOrchestrator.instances.clear()


@pytest.mark.asyncio
async def test_dispatch_wrapper_legacy_branch_populates_job_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict side-effect: legacy branch MUST populate ``_job_store``.

    Per [[flag-default-off-blinds-tests]] discipline: assert the real
    side-effect of the legacy branch, not just that a mock was called.
    Patches ``ontofuel_extract`` to raise so the legacy branch fails
    fast AFTER populating ``_job_store`` (the legacy branch writes the
    dataclass ``ExtractionJob`` into ``_job_store`` BEFORE invoking
    ``ontofuel_extract``; see extraction_pipeline.py).
    """
    monkeypatch.setenv("NFM_EXTRACTION_V2_ENABLED", "false")
    from nfm_db.config import is_extraction_v2_enabled

    is_extraction_v2_enabled.cache_clear()

    from nfm_db.services import extraction_pipeline as legacy_module

    sentinel_key = "doi:10.1234/dispatch-legacy-strict"
    legacy_module._job_store.pop(sentinel_key, None)
    pre_count = len(legacy_module._job_store)

    # session.execute and session.commit must be awaitable because the
    # legacy path queries the latest published ontology (calls
    # ``await session.execute(stmt)``) and commits the FAILED job
    # status when ``ontofuel_extract`` raises.
    session = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = None
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    with patch(
        "nfm_db.services.extraction_pipeline.ontofuel_extract",
        new_callable=AsyncMock,
        side_effect=RuntimeError("forced: legacy branch hit"),
    ):
        await legacy_module.trigger_extraction_dispatch(
            session,
            source_reference=sentinel_key,
            source_type="doi",
        )

    assert len(legacy_module._job_store) == pre_count + 1, (
        "Legacy branch must populate _job_store before calling ontofuel_extract"
    )
    stored_entries = [
        job
        for job in legacy_module._job_store.values()
        if getattr(job, "source_reference", None) == sentinel_key
    ]
    assert len(stored_entries) == 1, (
        "Legacy branch must write the ExtractionJob to _job_store"
    )
    assert getattr(stored_entries[0], "fill_batch_id", None) is not None, (
        "Legacy branch stores dataclass ExtractionJob with fill_batch_id"
    )


@pytest.mark.asyncio
async def test_dispatch_wrapper_v2_branch_persists_orm_job_and_skips_job_store(
    monkeypatch: pytest.MonkeyPatch,
    _reset_recorder: None,
) -> None:
    """Strict side-effects: V2 branch MUST ``session.add()`` an
    ``ORMExtractionJob``, MUST instantiate ``ExtractionOrchestrator``,
    and MUST NOT touch the legacy ``_job_store``.

    Per [[flag-default-off-blinds-tests]] discipline: avoid mock-only
    tests by asserting real per-branch side-effects.
    """
    monkeypatch.setenv("NFM_EXTRACTION_V2_ENABLED", "true")
    from nfm_db.config import is_extraction_v2_enabled

    is_extraction_v2_enabled.cache_clear()

    from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
    from nfm_db.services import extraction_pipeline as legacy_module

    sentinel_key = "doi:10.1234/dispatch-v2-strict"
    legacy_module._job_store.pop(sentinel_key, None)
    pre_count = len(legacy_module._job_store)

    session = MagicMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    # session.execute returns no published ontology version (so the
    # V2 branch's ontology discovery is a no-op).
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    )

    with patch(
        "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
        _RecordingOrchestrator,
    ):
        job = await legacy_module.trigger_extraction_dispatch(
            session,
            source_reference=sentinel_key,
            source_type="doi",
        )

    assert len(_RecordingOrchestrator.instances) == 1, (
        "V2 branch must construct exactly one ExtractionOrchestrator"
    )
    recorded = _RecordingOrchestrator.instances[0]
    assert recorded.session is session

    assert session.add.called, "V2 branch must session.add() the ORM job"
    added_objects = [call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(obj, ORMExtractionJob) for obj in added_objects), (
        f"V2 branch must add ORMExtractionJob, got: {added_objects!r}"
    )

    # Legacy branch's _job_store MUST NOT be touched.
    assert sentinel_key not in legacy_module._job_store
    assert len(legacy_module._job_store) == pre_count, (
        "V2 branch must not write to legacy _job_store"
    )
    assert getattr(job, "fill_batch_id", None) is None, (
        "V2 branch returns ORMExtractionJob which has no fill_batch_id"
    )
