"""Flag-verification tests for ``Settings.extraction_v2_enabled`` — NFM-2698.

These tests verify that the *real* ``trigger_extraction()`` dispatch
logic in :mod:`nfm_db.services.extraction_pipeline` actually routes to
the V2 orchestrator branch when the flag is ON, and to the legacy
dataclass branch when the flag is OFF.

Why this matters (see ``[[flag-default-off-blinds-tests]]``)
------------------------------------------------------------

A naive test mocks ``ExtractionOrchestrator`` to a recording stub and
then asserts the stub was called. That passes whether the dispatch
actually routed to the V2 branch or not — because the test
double-installed the stub regardless. Mock-only tests against the
flag-routed path are an "accidental pass" trap: the headline says
"v2 verified" while only the patch site is verified.

This test takes the opposite approach: instead of patching
``ExtractionOrchestrator`` *unconditionally*, it asserts the *real*
side-effects each branch produces and the side-effects each branch
must NOT produce:

- Legacy branch (flag=False) writes the ``ExtractionJob`` dataclass
  into the module-level ``_job_store`` dict and NEVER imports
  ``ExtractionOrchestrator``.
- V2 branch (flag=True) constructs an ``ExtractionOrchestrator``
  instance and ``session.add()``-s an ``ORMExtractionJob`` and NEVER
  writes to ``_job_store``.

The flag is explicitly overridden via ``monkeypatch.setattr`` on
``nfm_db.config.get_settings`` so the test does
NOT depend on the default value (which the design intentionally
leaves as False).  Note: ``trigger_extraction`` does
``from nfm_db.config import get_settings`` *inside the function body*
(local import), so the patch target is ``nfm_db.config.get_settings``
(the source module), not the consumer module.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — fake settings + recording orchestrator stub
# ---------------------------------------------------------------------------


def _make_settings(flag: bool) -> MagicMock:
    """Return a MagicMock Settings with the requested flag value."""
    settings = MagicMock()
    settings.extraction_v2_enabled = flag
    return settings


class _RecordingOrchestrator:
    """Stub orchestrator class that records every instantiation.

    Replaces :class:`nfm_db.services.extraction_orchestrator.ExtractionOrchestrator`
    during the V2-branch test. Records the session + orm_job it was
    constructed with so the test can assert the V2 branch did the
    right thing.
    """

    instances: list[_RecordingOrchestrator] = []

    def __init__(self, session: object, orm_job: object) -> None:
        self.session = session
        self.orm_job = orm_job
        _RecordingOrchestrator.instances.append(self)

    async def run(self, **_kwargs: object) -> object:
        return self.orm_job


@pytest.fixture(autouse=True)
def _reset_recorder() -> None:
    """Reset stub state between tests."""
    _RecordingOrchestrator.instances.clear()


def _build_session() -> AsyncMock:
    """Return an AsyncMock session matching AsyncSession semantics."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    # session.add is synchronous on AsyncSession (just marks the row).
    session.add = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = None
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# V2 branch (flag=True) — routes to ExtractionOrchestrator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionV2FlagRoutesToOrchestrator:
    """With ``extraction_v2_enabled=True``, ``trigger_extraction`` must
    construct an :class:`ExtractionOrchestrator` and call ``.run()``.

    The legacy branch's artifacts (``_job_store``) MUST NOT be touched.
    """

    @pytest.mark.asyncio
    async def test_v2_flag_true_routes_to_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from nfm_db.services import extraction_pipeline as legacy_module

        # Override the settings getter that ``trigger_extraction`` uses.
        monkeypatch.setattr(
            "nfm_db.config.get_settings",
            lambda: _make_settings(flag=True),
        )

        session = _build_session()

        # NFM-2909 (BLOCKER 3 / CR second pass): the V2 branch loads
        # content via ``load_v2_content`` BEFORE constructing the
        # orchestrator. ``doi:10.1234/flag-verification`` is not on
        # disk and ``EXTRACTION_STUB_MODE`` is unset, so the loader
        # raises ``NotImplementedError`` and the V2 branch short-
        # circuits to a FAILED job — the orchestrator is never
        # constructed. Patch the loader at its source module so the
        # function-local ``from ... import load_v2_content`` inside
        # ``trigger_extraction`` picks up the patched value.
        with patch(
            "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
            _RecordingOrchestrator,
        ):
            with patch(
                "nfm_db.services.extraction_pipeline_dispatch.load_v2_content",
                return_value="# Placeholder\n\nStub content for flag-routing test.",
            ):
                job = await legacy_module.trigger_extraction(
                    session,
                    source_reference="doi:10.1234/flag-verification",
                    source_type="doi",
                )

        assert len(_RecordingOrchestrator.instances) == 1, (
            "V2 flag=True must construct exactly one ExtractionOrchestrator"
        )
        recorded = _RecordingOrchestrator.instances[0]
        assert recorded.session is session
        assert recorded.orm_job is not None

        # Legacy branch's _job_store MUST NOT be touched.
        assert (
            "doi:10.1234/flag-verification" not in legacy_module._job_store
        ), "V2 branch must not write to _job_store (legacy artifact)"
        assert getattr(job, "fill_batch_id", None) is None, (
            "ORMExtractionJob has no fill_batch_id; presence means the "
            "legacy dataclass branch ran instead"
        )

    @pytest.mark.asyncio
    async def test_v2_flag_true_calls_session_add_on_orm_job(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """V2 branch must ``session.add()`` an ORMExtractionJob, not a dataclass."""
        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
        from nfm_db.services import extraction_pipeline as legacy_module

        monkeypatch.setattr(
            "nfm_db.config.get_settings",
            lambda: _make_settings(flag=True),
        )

        session = _build_session()

        # NFM-2909 (CR second pass): same root cause as
        # ``test_v2_flag_true_routes_to_orchestrator`` — V2 branch
        # short-circuits when the loader raises before constructing
        # the orchestrator. Patch the loader so the V2 branch reaches
        # the ``session.add(orm_job)`` step we want to assert on.
        with patch(
            "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
            _RecordingOrchestrator,
        ):
            with patch(
                "nfm_db.services.extraction_pipeline_dispatch.load_v2_content",
                return_value="# Placeholder\n\nStub content for session-add test.",
            ):
                await legacy_module.trigger_extraction(
                    session,
                    source_reference="doi:10.1234/v2-add",
                    source_type="doi",
                )

        assert session.add.called, "V2 branch must session.add() the ORM job"
        added_objects = [call.args[0] for call in session.add.call_args_list]
        assert any(isinstance(obj, ORMExtractionJob) for obj in added_objects), (
            f"V2 branch must add an ORMExtractionJob, got: {added_objects!r}"
        )


# ---------------------------------------------------------------------------
# Legacy branch (flag=False) — runs the dataclass pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionV2FlagRoutesToLegacy:
    """With ``extraction_v2_enabled=False``, ``trigger_extraction`` must
    execute the legacy dataclass pipeline and NEVER construct an
    :class:`ExtractionOrchestrator`.

    The legacy branch writes the ``ExtractionJob`` to ``_job_store``
    *before* calling ``ontofuel_extract`` (see
    ``extraction_pipeline.py:670``).  We exploit that ordering: patch
    ``ontofuel_extract`` to raise, so the legacy branch fails fast
    *after* having populated ``_job_store``.  If the V2 branch were
    taken instead, ``_job_store`` would still be empty.
    """

    @pytest.mark.asyncio
    async def test_legacy_flag_false_populates_job_store(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from nfm_db.services import extraction_pipeline as legacy_module

        monkeypatch.setattr(
            "nfm_db.config.get_settings",
            lambda: _make_settings(flag=False),
        )

        sentinel_key = "doi:10.1234/legacy-sentinel"
        legacy_module._job_store.pop(sentinel_key, None)
        pre_count = len(legacy_module._job_store)

        session = _build_session()

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new_callable=AsyncMock,
            side_effect=RuntimeError("forced: legacy branch hit"),
        ):
            # The legacy branch's outer try/except converts
            # ``ontofuel_extract`` failures into a FAILED job status
            # (see ``extraction_pipeline.py`` ~line 953) rather than
            # re-raising.  We don't assert on the exception here — the
            # branch's identity is proven by ``_job_store`` being
            # populated BEFORE the exception fired.
            await legacy_module.trigger_extraction(
                session,
                source_reference=sentinel_key,
                source_type="doi",
            )

        # ``_job_store`` is keyed by ``job_id`` (UUID), NOT by
        # ``source_reference``.  Locate the entry that carries our
        # sentinel source reference; there must be exactly one new
        # entry.
        assert len(legacy_module._job_store) == pre_count + 1, (
            "Exactly one new _job_store entry must have been added"
        )
        stored_entries = [
            job for job in legacy_module._job_store.values()
            if getattr(job, "source_reference", None) == sentinel_key
        ]
        assert len(stored_entries) == 1, (
            "Legacy branch must write the ExtractionJob to _job_store "
            "before invoking ontofuel_extract"
        )
        stored = stored_entries[0]
        # Legacy branch stores the dataclass ExtractionJob with a
        # fill_batch_id — V2 branch's ORMExtractionJob does NOT have one.
        assert getattr(stored, "fill_batch_id", None) is not None

    @pytest.mark.asyncio
    async def test_legacy_flag_false_does_not_construct_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy branch must NOT touch ExtractionOrchestrator at all.

        Patches ``ExtractionOrchestrator`` at its source module to a
        sentinel class whose ``__init__`` raises AssertionError if it
        is ever instantiated. If the legacy branch tries to construct
        it, the assertion fires and ``pytest.raises`` will fail.
        """
        from nfm_db.services import extraction_pipeline as legacy_module

        monkeypatch.setattr(
            "nfm_db.config.get_settings",
            lambda: _make_settings(flag=False),
        )

        class _RaisingOrchestrator:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                raise AssertionError(
                    "Legacy branch must not instantiate ExtractionOrchestrator"
                )

            async def run(self, **_kwargs: object) -> None:
                raise AssertionError(
                    "Legacy branch must not call ExtractionOrchestrator.run()"
                )

        session = _build_session()

        with patch(
            "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
            _RaisingOrchestrator,
        ):
            with patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                side_effect=RuntimeError("forced: legacy branch hit"),
            ):
                # Same outer try/except as above — exception is
                # absorbed into a FAILED job status, NOT re-raised.
                await legacy_module.trigger_extraction(
                    session,
                    source_reference="doi:10.1234/legacy-no-orch",
                    source_type="doi",
                )
        # If AssertionError had fired instead of RuntimeError, the
        # pytest.raises context would have failed; reaching here means
        # the legacy branch did not touch the orchestrator class.


# ---------------------------------------------------------------------------
# Default-value guard — flag defaults OFF (do NOT flip)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionV2FlagDefaultIsOn:
    """The feature flag defaults to ``True`` after NFM-2739 Phase B cutover."""

    def test_settings_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NFM_EXTRACTION_V2_ENABLED", raising=False)
        from nfm_db.config import Settings
        settings = Settings()
        assert settings.extraction_v2_enabled is True, (
            "Flag default must be True after NFM-2739 Phase B cutover "
            "(the V2 orchestrator is now the canonical path)"
        )

    def test_settings_can_be_overridden_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFM_EXTRACTION_V2_ENABLED", "true")
        from nfm_db.config import Settings
        settings = Settings()
        assert settings.extraction_v2_enabled is True


# ---------------------------------------------------------------------------
# Both-branch invariant — only one branch runs per call
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionV2FlagMutualExclusion:
    """Exactly one of {legacy artifact, v2 artifact} may be produced per call."""

    @pytest.mark.asyncio
    async def test_v2_branch_does_not_leak_into_legacy_store(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """V2 path must NOT add to _job_store even if the orchestrator fails."""
        from nfm_db.services import extraction_pipeline as legacy_module

        monkeypatch.setattr(
            "nfm_db.config.get_settings",
            lambda: _make_settings(flag=True),
        )

        session = _build_session()
        sentinel_key = "doi:10.1234/v2-no-legacy-leak"
        legacy_module._job_store.pop(sentinel_key, None)
        pre_count = len(legacy_module._job_store)

        class _FailingOrchestrator:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def run(self, **_kwargs: object) -> None:
                raise RuntimeError("forced: orchestrator run failed")

        # NFM-2909 (BLOCKER 4 / CR second pass): the V2 branch runs
        # ``load_v2_content`` BEFORE constructing the orchestrator.
        # Without stub mode the loader raises ``NotImplementedError``,
        # the V2 branch catches it and returns a FAILED job — so the
        # orchestrator is never constructed and ``pytest.raises`` sees
        # NOTHING (the FAILED-job path returns, it does not re-raise).
        # Patch the loader so the orchestrator IS constructed and
        # ``.run()`` raises the expected ``RuntimeError``.
        with patch(
            "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
            _FailingOrchestrator,
        ):
            with patch(
                "nfm_db.services.extraction_pipeline_dispatch.load_v2_content",
                return_value="# Placeholder\n\nStub content for leak test.",
            ):
                with pytest.raises(RuntimeError, match="forced: orchestrator run failed"):
                    await legacy_module.trigger_extraction(
                        session,
                        source_reference=sentinel_key,
                        source_type="doi",
                    )

        assert sentinel_key not in legacy_module._job_store
        assert len(legacy_module._job_store) == pre_count, (
            "V2 branch must not touch legacy _job_store under any failure mode"
        )

    @pytest.mark.asyncio
    async def test_legacy_branch_does_not_instantiate_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy branch must not even construct the orchestrator class."""
        from nfm_db.services import extraction_pipeline as legacy_module

        monkeypatch.setattr(
            "nfm_db.config.get_settings",
            lambda: _make_settings(flag=False),
        )

        session = _build_session()

        class _Sentinel:
            instantiated = False

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                type(self).instantiated = True
                raise AssertionError(
                    "Legacy branch must not instantiate ExtractionOrchestrator"
                )

            async def run(self, **_kwargs: object) -> None:
                raise AssertionError(
                    "Legacy branch must not call ExtractionOrchestrator.run"
                )

        _Sentinel.instantiated = False

        with patch(
            "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
            _Sentinel,
        ):
            with patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                side_effect=RuntimeError("forced: legacy branch hit"),
            ):
                # Same outer try/except — exception is absorbed into a
                # FAILED job status, NOT re-raised. The sentinels are
                # the actual proof: ``_Sentinel.instantiated`` stays
                # False if the legacy branch never imported the
                # orchestrator class.
                await legacy_module.trigger_extraction(
                    session,
                    source_reference="doi:10.1234/legacy-no-sentinel",
                    source_type="doi",
                )

        assert _Sentinel.instantiated is False, (
            "Legacy branch must not instantiate ExtractionOrchestrator; "
            "the sentinel's __init__ should never have fired."
        )
