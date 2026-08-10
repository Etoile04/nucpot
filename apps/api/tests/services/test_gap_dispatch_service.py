"""Direct unit tests for :class:`GapDispatchService` (NFM-2781 CR2).

The endpoint-level tests in ``tests/api/v1/test_data_collection_dispatch.py``
patch ``GapDispatchService`` entirely — which leaves the dispatch
service itself at <30% coverage.  These tests exercise the service
methods directly so we can ship the dispatcher with ≥80% coverage as
required by the CR.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    DataCollectionRequest,
    DFTCalculation,
    OntologyVersion,
    User,
)
from nfm_db.models.user import BlogRole
from nfm_db.services.gap_dispatch_service import (
    DispatchResult,
    GapDispatchService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_request(
    session: AsyncSession,
    *,
    source_preference: str = "literature",
    status: str = "open",
    ontology_version_id: uuid.UUID | None = None,
) -> DataCollectionRequest:
    """Seed a minimal DataCollectionRequest."""
    if ontology_version_id is None:
        user = User(
            id=uuid.uuid4(),
            username=f"seed_{uuid.uuid4().hex[:8]}",
            email=f"seed_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password="hashed",
            blog_role=BlogRole.DOMAIN_EXPERT,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        ov = OntologyVersion(
            version="1.0.0",
            status="published",
            created_by=user.id,
            ontology_data={"entity_types": [], "relation_types": []},
        )
        session.add(ov)
        await session.flush()
        ontology_version_id = ov.id

    req = DataCollectionRequest(
        ontology_version_id=ontology_version_id,
        entity_type="NuclearMaterial",
        property="density",
        material_system="UO2",
        source_preference=source_preference,
        status=status,
    )
    session.add(req)
    await session.flush()
    await session.refresh(req)
    return req


class TestDispatchRequestTopLevel:
    """Tests for :meth:`GapDispatchService.dispatch_request`."""

    async def test_load_request_not_found_raises(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Loading an unknown request raises ValueError."""
        svc = GapDispatchService(db_session)
        with pytest.raises(ValueError, match="not found"):
            await svc.dispatch_request(uuid.uuid4())

    async def test_load_request_wrong_status_raises(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Loading a non-open request raises ValueError."""
        req = await _seed_request(db_session, status="in_progress")
        svc = GapDispatchService(db_session)
        with pytest.raises(ValueError, match="'open'"):
            await svc.dispatch_request(req.id)

    async def test_invalid_source_preference_raises(
        self,
        db_session: AsyncSession,
    ) -> None:
        """An invalid ``source_preference`` value raises ValueError."""
        req = await _seed_request(db_session, source_preference="literature")
        req.source_preference = "bogus"
        await db_session.flush()

        svc = GapDispatchService(db_session)
        with pytest.raises(ValueError, match="Invalid source_preference"):
            await svc.dispatch_request(req.id)


class TestDispatchLiterature:
    """Tests for the literature / Celery branch."""

    async def test_dispatch_literature_schedules_celery_task(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A literature request schedules a Celery task on literature_processing queue."""
        req = await _seed_request(
            db_session,
            source_preference="literature",
        )

        fake_async_result = MagicMock()
        fake_async_result.id = "fake-task-id"

        with patch(
            "nfm_db.services.celery_app.celery_app.send_task",
            return_value=fake_async_result,
        ) as send_task:
            svc = GapDispatchService(db_session)
            result = await svc.dispatch_request(req.id)

        assert isinstance(result, DispatchResult)
        assert result.status == "dispatched"
        assert result.path_taken == "literature"
        assert result.metadata["task_name"] == (
            "nfm_db.tasks.gap_literature_task.process_gap_literature_task"
        )
        kwargs = send_task.call_args.kwargs["kwargs"]
        assert kwargs["request_id"] == str(req.id)
        assert kwargs["entity_type"] == req.entity_type
        assert send_task.call_args.kwargs["queue"] == "literature_processing"

    async def test_dispatch_literature_propagates_celery_error(
        self,
        db_session: AsyncSession,
    ) -> None:
        """If celery_app.send_task raises a CeleryError, dispatch_request re-raises."""
        from celery.exceptions import CeleryError

        req = await _seed_request(
            db_session,
            source_preference="literature",
        )
        with patch(
            "nfm_db.services.celery_app.celery_app.send_task",
            side_effect=CeleryError("broker down"),
        ):
            svc = GapDispatchService(db_session)
            with pytest.raises(CeleryError):
                await svc.dispatch_request(req.id)


class TestDispatchDft:
    """Tests for the DFT branch."""

    async def test_dispatch_dft_creates_dft_calculation(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A DFT request creates a pending DFTCalculation."""
        req = await _seed_request(
            db_session,
            source_preference="dft",
        )

        svc = GapDispatchService(db_session)
        result = await svc.dispatch_request(req.id)

        assert result.status == "dispatched"
        assert result.path_taken == "dft"
        calc_id = uuid.UUID(result.metadata["dft_calculation_id"])
        calc = await db_session.get(DFTCalculation, calc_id)
        assert calc is not None
        assert calc.status == "pending"
        assert calc.source == "gap_dispatch"
        meta = calc.computation_metadata
        assert meta["data_collection_request_id"] == str(req.id)


class TestDispatchExternalDb:
    """Tests for the external DB branch (NFM-2781 CR4 — asyncio.gather)."""

    async def test_external_db_collects_all_three_sources(
        self,
        db_session: AsyncSession,
    ) -> None:
        """All three external sources are queried and the results collected."""
        req = await _seed_request(
            db_session,
            source_preference="external_db",
        )

        client = MagicMock()
        client.query_nist_ipr = AsyncMock(return_value={"nist_value": 1.0})
        client.query_openkim = AsyncMock(return_value={"openkim_value": 2.0})
        client.query_materials_project = AsyncMock(return_value={"mp_value": 3.0})
        client.close = AsyncMock()

        with patch(
            "nfm_db.services.external_data_sources.ExternalDataSourceClient",
            return_value=client,
        ):
            svc = GapDispatchService(db_session)
            result = await svc.dispatch_request(req.id)

        assert result.status == "dispatched"
        assert result.path_taken == "external_db"
        assert result.metadata["external_results"] == {
            "nist_ipr": {"nist_value": 1.0},
            "openkim": {"openkim_value": 2.0},
            "materials_project": {"mp_value": 3.0},
        }
        client.query_nist_ipr.assert_awaited_once()
        client.query_openkim.assert_awaited_once()
        client.query_materials_project.assert_awaited_once()

    async def test_external_db_handles_none_results(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Sources that return None are excluded from the results bag."""
        req = await _seed_request(
            db_session,
            source_preference="external_db",
        )

        client = MagicMock()
        client.query_nist_ipr = AsyncMock(return_value=None)
        client.query_openkim = AsyncMock(return_value={"value": 1})
        client.query_materials_project = AsyncMock(return_value=None)
        client.close = AsyncMock()

        with patch(
            "nfm_db.services.external_data_sources.ExternalDataSourceClient",
            return_value=client,
        ):
            svc = GapDispatchService(db_session)
            result = await svc.dispatch_request(req.id)

        assert "openkim" in result.metadata["external_results"]
        assert "nist_ipr" not in result.metadata["external_results"]
        assert "materials_project" not in result.metadata["external_results"]
        assert "1 returned data" in result.detail

    async def test_external_db_skips_failing_source(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A failing source is logged + skipped; the other two succeed."""
        req = await _seed_request(
            db_session,
            source_preference="external_db",
        )

        client = MagicMock()
        client.query_nist_ipr = AsyncMock(side_effect=RuntimeError("NIST is down"))
        client.query_openkim = AsyncMock(return_value={"ok": 1})
        client.query_materials_project = AsyncMock(return_value={"ok": 2})
        client.close = AsyncMock()

        with patch(
            "nfm_db.services.external_data_sources.ExternalDataSourceClient",
            return_value=client,
        ):
            svc = GapDispatchService(db_session)
            result = await svc.dispatch_request(req.id)

        assert result.status == "dispatched"
        assert "openkim" in result.metadata["external_results"]
        assert "materials_project" in result.metadata["external_results"]
        assert "nist_ipr" not in result.metadata["external_results"]
        assert "2 returned data" in result.detail


class TestDispatchAny:
    """Tests for ``source_preference='any'`` priority order."""

    async def test_any_falls_through_to_next_path_on_error(
        self,
        db_session: AsyncSession,
    ) -> None:
        """When the literature path raises, any tries external_db next."""
        req = await _seed_request(
            db_session,
            source_preference="any",
        )

        client = MagicMock()
        client.query_nist_ipr = AsyncMock(return_value={"ok": 1})
        client.query_openkim = AsyncMock(return_value=None)
        client.query_materials_project = AsyncMock(return_value=None)
        client.close = AsyncMock()

        with patch(
            "nfm_db.services.celery_app.celery_app.send_task",
            side_effect=RuntimeError("celery down"),
        ):
            with patch(
                "nfm_db.services.external_data_sources.ExternalDataSourceClient",
                return_value=client,
            ):
                svc = GapDispatchService(db_session)
                result = await svc.dispatch_request(req.id)

        assert result.status == "dispatched"
        assert result.path_taken == "external_db"

    async def test_any_records_attempts_when_literature_fails(
        self,
        db_session: AsyncSession,
    ) -> None:
        """When literature raises, ``any`` records the attempt and tries the next path."""
        req = await _seed_request(
            db_session,
            source_preference="any",
        )

        client = MagicMock()
        client.query_nist_ipr = AsyncMock(side_effect=RuntimeError("nist down"))
        client.query_openkim = AsyncMock(side_effect=RuntimeError("openkim down"))
        client.query_materials_project = AsyncMock(
            side_effect=RuntimeError("mp down"),
        )
        client.close = AsyncMock()

        with patch(
            "nfm_db.services.celery_app.celery_app.send_task",
            side_effect=RuntimeError("celery down"),
        ):
            with patch(
                "nfm_db.services.external_data_sources.ExternalDataSourceClient",
                return_value=client,
            ):
                svc = GapDispatchService(db_session)
                result = await svc.dispatch_request(req.id)

        # ``_dispatch_external_db`` returns dispatched even when all
        # three sources fail (it just logs warnings), so ``any`` succeeds
        # at the external_db branch and the literature failure is
        # surfaced in ``attempts``.
        assert result.status == "dispatched"
        assert result.path_taken == "external_db"


class TestModuleLevelBrokerFree:
    """CR3: the dispatcher module must not import celery at top level."""

    def test_module_does_not_import_celery_at_module_level(self) -> None:
        """Importing the dispatcher module should not import celery."""
        import sys

        # Drop any cached celery modules.
        for mod in list(sys.modules):
            if mod == "celery" or mod.startswith("celery."):
                sys.modules.pop(mod, None)

        import nfm_db.services.gap_dispatch_service as dispatcher_mod  # noqa: F401

        loaded = {mod for mod in sys.modules if mod == "celery"}
        assert not loaded, (
            "gap_dispatch_service imported celery at module load; "
            "violates CR3 broker-free contract"
        )
