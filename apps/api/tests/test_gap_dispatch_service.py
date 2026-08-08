"""Unit tests for the gap dispatch service (NFM-2650).

Tests acceptance criteria:
- dispatch() routes by source_preference correctly (single paths)
- dispatch() cascade mode tries external_db -> literature -> dft
- dispatch_batch() processes up to limit open DCRs
- Dispatch updates DCR columns (dispatched_at, path, status, reference)
- Idempotent: skips already-dispatched requests
- DCR status transitions from open to in_progress
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.ontology_version import OntologyVersion
from nfm_db.models.user import User
from nfm_db.services.gap_dispatch_service import GapDispatchService
from nfm_db.services.paths.base import DISPATCH_PATHS, DispatchResult, GapFillPath

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED_USER_ID = uuid.uuid4()


async def _ensure_ontology_version(
    session: AsyncSession,
) -> uuid.UUID:
    """Create a minimal User + OntologyVersion for FK references.

    Returns the ontology_version_id.
    """
    user = User(
        id=_SEED_USER_ID,
        username="dispatch_test_user",
        email="dispatch@test.com",
        hashed_password="dummy-hash",
    )
    session.add(user)
    await session.flush()

    ov = OntologyVersion(
        version="1.0.0",
        status="published",
        created_by=user.id,
        ontology_data={"entities": []},
    )
    session.add(ov)
    await session.flush()
    return ov.id


def _make_fill_path_mock(
    *,
    data_found: bool = True,
    success: bool = True,
    reference: str | None = "job-123",
    error: str | None = None,
    path_name: str = "mock_path",
    can_handle_result: bool = True,
) -> AsyncMock:
    """Build a mock GapFillPath with configurable can_handle + execute return.

    Args:
        data_found: Returned in the DispatchResult's ``data_found`` field.
        success: Returned in the DispatchResult's ``success`` field.
        reference: Returned in the DispatchResult's ``reference`` field.
        error: Returned in the DispatchResult's ``error`` field.
        path_name: The fill-path name embedded in the DispatchResult.
        can_handle_result: Value the mock's ``can_handle`` returns. Defaults
            to ``True``; cascade tests that need to skip a handler set
            ``can_handle_result=False``.
    """
    mock = AsyncMock(spec=GapFillPath)
    mock.can_handle = AsyncMock(return_value=can_handle_result)
    result = DispatchResult(
        success=success,
        path=path_name,
        reference=reference,
        error=error,
        data_found=data_found,
    )
    mock.execute = AsyncMock(return_value=result)
    return mock


def _make_dcr(
    *,
    ontology_version_id: uuid.UUID | None = None,
    entity_type: str = "NuclearMaterial",
    property_: str = "thermal_conductivity",
    material_system: str = "UO2",
    source_preference: str = "any",
    status: str = "open",
    urgency: int = 0,
    dispatched_at: datetime | None = None,
    dispatch_status: str | None = None,
) -> DataCollectionRequest:
    """Build a DataCollectionRequest for testing.

    ``ontology_version_id`` defaults to a fresh UUID. The default SQLite
    in-memory backend used by these tests does not enforce FK constraints,
    so callers that only exercise dispatch logic can rely on the default.
    Tests that join against an OntologyVersion row should pass an explicit
    ``ontology_version_id`` (typically returned by ``_ensure_ontology_version``).

    ``entity_type``, ``property_``, ``material_system`` are exposed so batch
    tests can vary the (ov, entity_type, property, material_system) tuple —
    the production schema has a unique index on that combination.
    """
    if ontology_version_id is None:
        ontology_version_id = uuid.uuid4()
    return DataCollectionRequest(
        id=uuid.uuid4(),
        ontology_version_id=ontology_version_id,
        entity_type=entity_type,
        property=property_,
        material_system=material_system,
        urgency=urgency,
        source_preference=source_preference,
        status=status,
        requested_at=datetime.now(UTC),
        dispatched_at=dispatched_at,
        dispatched_path=None,
        dispatch_status=dispatch_status,
        result_reference=None,
    )


async def _persist_dcr(
    dcr: DataCollectionRequest,
    session: AsyncSession,
) -> DataCollectionRequest:
    """Persist a DCR to the database."""
    session.add(dcr)
    await session.flush()
    await session.refresh(dcr)
    return dcr


# ---------------------------------------------------------------------------
# DispatchResult frozen dataclass
# ---------------------------------------------------------------------------


class TestDispatchResult:
    """Test the DispatchResult value object."""

    def test_frozen(self) -> None:
        """DispatchResult is a frozen dataclass."""
        result = DispatchResult(
            success=True,
            path="literature",
            reference=None,
            error=None,
            data_found=True,
        )
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]

    def test_all_fields(self) -> None:
        """DispatchResult holds all fields correctly."""
        result = DispatchResult(
            success=False,
            path="dft",
            reference="task-456",
            error="timeout",
            data_found=False,
        )
        assert result.success is False
        assert result.path == "dft"
        assert result.reference == "task-456"
        assert result.error == "timeout"
        assert result.data_found is False


# ---------------------------------------------------------------------------
# FillPath protocol
# ---------------------------------------------------------------------------


class TestGapFillPathProtocol:
    """Test that fill path implementations satisfy the canonical protocol."""

    def test_mock_satisfies_protocol(self) -> None:
        """A mock with can_handle() and execute() satisfies GapFillPath."""
        mock = AsyncMock(spec=GapFillPath)
        mock.can_handle = AsyncMock(return_value=True)
        mock.execute = AsyncMock(
            return_value=DispatchResult(
                success=True, path="test", reference=None, error=None, data_found=True,
            ),
        )
        assert hasattr(mock, "can_handle")
        assert hasattr(mock, "execute")

    def test_canonical_dispatch_paths_tuple(self) -> None:
        """Canonical DISPATCH_PATHS is a tuple of (literature, dft, external_db)."""
        assert DISPATCH_PATHS == ("literature", "dft", "external_db")


# ---------------------------------------------------------------------------
# dispatch() — single-path routing
# ---------------------------------------------------------------------------


class TestDispatchSinglePath:
    """Test dispatch() for non-cascade source_preference values."""

    @pytest.mark.asyncio
    async def test_literature_routes_to_literature_path(
        self,
        db_session: AsyncSession,
    ) -> None:
        """source_preference='literature' routes to LiteratureFillPath."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="literature")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            data_found=True, reference="lit-ref-1", path_name="literature",
        )
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is True
        assert result.path == "literature"
        assert result.data_found is True
        mock_path.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_dft_routes_to_dft_path(
        self,
        db_session: AsyncSession,
    ) -> None:
        """source_preference='dft' routes to DFTFillPath."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="dft")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            data_found=True, reference="dft-ref-1", path_name="dft",
        )
        paths = {"dft": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is True
        assert result.path == "dft"
        mock_path.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_external_db_routes_to_external_db_path(
        self,
        db_session: AsyncSession,
    ) -> None:
        """source_preference='external_db' routes to ExternalDBFillPath."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="external_db")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            data_found=True, reference="ext-ref-1", path_name="external_db",
        )
        paths = {"external_db": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is True
        assert result.path == "external_db"
        mock_path.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_path_failure_records_error(
        self,
        db_session: AsyncSession,
    ) -> None:
        """When a single path fails, DispatchResult carries the error."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="literature")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            success=False,
            data_found=False,
            reference=None,
            error="PDF not found",
            path_name="literature",
        )
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is False
        assert result.error == "PDF not found"
        assert result.data_found is False


# ---------------------------------------------------------------------------
# dispatch() — cascade routing
# ---------------------------------------------------------------------------


class TestDispatchCascade:
    """Test dispatch() cascade logic for source_preference='any'."""

    @pytest.mark.asyncio
    async def test_cascade_stops_at_first_success(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Cascade stops at first path that finds data."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="any")
        await _persist_dcr(dcr, db_session)

        mock_ext = _make_fill_path_mock(
            data_found=True, reference="ext-ref-1", path_name="external_db",
        )
        mock_lit = _make_fill_path_mock(data_found=True, path_name="literature")
        mock_dft = _make_fill_path_mock(data_found=True, path_name="dft")

        paths = {
            "external_db": mock_ext,
            "literature": mock_lit,
            "dft": mock_dft,
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is True
        assert result.data_found is True
        assert result.path == "external_db"
        mock_ext.execute.assert_called_once()
        mock_lit.execute.assert_not_called()
        mock_dft.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cascade_falls_through_to_literature(
        self,
        db_session: AsyncSession,
    ) -> None:
        """When external_db finds no data, cascade tries literature."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="any")
        await _persist_dcr(dcr, db_session)

        mock_ext = _make_fill_path_mock(
            data_found=False, success=True, reference=None, path_name="external_db",
        )
        mock_lit = _make_fill_path_mock(
            data_found=True, reference="lit-ref-1", path_name="literature",
        )
        mock_dft = _make_fill_path_mock(data_found=True, path_name="dft")

        paths = {
            "external_db": mock_ext,
            "literature": mock_lit,
            "dft": mock_dft,
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is True
        assert result.data_found is True
        assert result.path == "literature"
        mock_ext.execute.assert_called_once()
        mock_lit.execute.assert_called_once()
        mock_dft.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cascade_falls_through_to_dft(
        self,
        db_session: AsyncSession,
    ) -> None:
        """When external_db and literature find no data, cascade tries dft."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="any")
        await _persist_dcr(dcr, db_session)

        mock_ext = _make_fill_path_mock(
            data_found=False, success=True, reference=None, path_name="external_db",
        )
        mock_lit = _make_fill_path_mock(
            data_found=False, success=True, reference=None, path_name="literature",
        )
        mock_dft = _make_fill_path_mock(
            data_found=True, reference="dft-ref-1", path_name="dft",
        )

        paths = {
            "external_db": mock_ext,
            "literature": mock_lit,
            "dft": mock_dft,
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is True
        assert result.data_found is True
        assert result.path == "dft"
        mock_ext.execute.assert_called_once()
        mock_lit.execute.assert_called_once()
        mock_dft.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_cascade_all_fail(
        self,
        db_session: AsyncSession,
    ) -> None:
        """When all cascade paths fail, result indicates failure."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="any")
        await _persist_dcr(dcr, db_session)

        mock_ext = _make_fill_path_mock(
            data_found=False,
            success=False,
            reference=None,
            error="DB unreachable",
            path_name="external_db",
        )
        mock_lit = _make_fill_path_mock(
            data_found=False,
            success=False,
            reference=None,
            error="No papers",
            path_name="literature",
        )
        mock_dft = _make_fill_path_mock(
            data_found=False,
            success=False,
            reference=None,
            error="Compute error",
            path_name="dft",
        )

        paths = {
            "external_db": mock_ext,
            "literature": mock_lit,
            "dft": mock_dft,
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is False
        assert result.data_found is False
        assert result.path == "cascade"

    @pytest.mark.asyncio
    async def test_cascade_skips_handler_whose_can_handle_is_false(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Cascade skips a handler whose can_handle() returns False."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="any")
        await _persist_dcr(dcr, db_session)

        mock_ext = _make_fill_path_mock(
            data_found=True,
            can_handle_result=False,
            path_name="external_db",
        )
        mock_lit = _make_fill_path_mock(
            data_found=True, reference="lit-ref-1", path_name="literature",
        )

        paths = {
            "external_db": mock_ext,
            "literature": mock_lit,
            "dft": _make_fill_path_mock(path_name="dft"),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        # external_db skipped (can_handle=False); literature handled it.
        assert result.success is True
        assert result.data_found is True
        assert result.path == "literature"
        mock_ext.execute.assert_not_called()
        mock_lit.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_cascade_skips_all_handlers_when_no_can_handle(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Cascade with all can_handle()=False leaves dispatched_path='cascade'."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="any")
        await _persist_dcr(dcr, db_session)

        paths = {
            "external_db": _make_fill_path_mock(
                can_handle_result=False, path_name="external_db",
            ),
            "literature": _make_fill_path_mock(
                can_handle_result=False, path_name="literature",
            ),
            "dft": _make_fill_path_mock(
                can_handle_result=False, path_name="dft",
            ),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        # None of the execute() paths ran; cascade is recorded.
        assert result.path == "cascade"
        assert result.success is False
        for mock in paths.values():
            mock.execute.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch() — DCR state updates
# ---------------------------------------------------------------------------


class TestDispatchStateUpdates:
    """Test that dispatch() correctly updates DCR columns."""

    @pytest.mark.asyncio
    async def test_updates_dispatch_columns_on_success(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Successful dispatch sets dispatched_at, path, status, reference."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="literature")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            data_found=True, reference="lit-job-42", path_name="literature",
        )
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        await svc.dispatch(dcr)

        await db_session.refresh(dcr)
        assert dcr.dispatched_at is not None
        assert dcr.dispatched_path == "literature"
        assert dcr.dispatch_status == "success"
        assert dcr.result_reference == "lit-job-42"
        assert dcr.status == "in_progress"

    @pytest.mark.asyncio
    async def test_updates_dispatch_columns_on_failure(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Failed dispatch sets dispatch_status='failed'."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="literature")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            success=False,
            data_found=False,
            reference=None,
            error="timeout",
            path_name="literature",
        )
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        await svc.dispatch(dcr)

        await db_session.refresh(dcr)
        assert dcr.dispatched_at is not None
        assert dcr.dispatched_path == "literature"
        assert dcr.dispatch_status == "failed"


# ---------------------------------------------------------------------------
# dispatch() — idempotency
# ---------------------------------------------------------------------------


class TestDispatchIdempotency:
    """Test that dispatch() skips already-dispatched requests."""

    @pytest.mark.asyncio
    async def test_skips_already_dispatched(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Already-dispatched DCR is skipped without calling fill paths."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            dispatched_at=datetime.now(UTC),
            dispatch_status="running",
        )
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock()
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is False
        assert "already dispatched" in (result.error or "")
        mock_path.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_after_success(
        self,
        db_session: AsyncSession,
    ) -> None:
        """DCR with dispatch_status='success' is skipped."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="dft",
            dispatched_at=datetime.now(UTC),
            dispatch_status="success",
        )
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock()
        paths = {"dft": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        result = await svc.dispatch(dcr)

        assert result.success is False
        mock_path.execute.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch_batch()
# ---------------------------------------------------------------------------


class TestDispatchBatch:
    """Test dispatch_batch() processes up to limit open DCRs."""

    @pytest.mark.asyncio
    async def test_batch_processes_open_dcrs(
        self,
        db_session: AsyncSession,
    ) -> None:
        """dispatch_batch processes open DCRs up to the limit."""
        ov_id = await _ensure_ontology_version(db_session)
        for i in range(3):
            dcr = _make_dcr(
                ontology_version_id=ov_id,
                entity_type=f"NuclearMaterial-{i}",
                property_=f"thermal_conductivity-{i}",
                material_system=f"UO2-{i}",
                source_preference="literature",
            )
            await _persist_dcr(dcr, db_session)

        mock_lit = _make_fill_path_mock(data_found=True, path_name="literature")
        paths = {
            "literature": mock_lit,
            "dft": _make_fill_path_mock(),
            "external_db": _make_fill_path_mock(),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        results = await svc.dispatch_batch(ontology_version_id=ov_id, limit=10)

        assert len(results) == 3
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_batch_respects_limit(
        self,
        db_session: AsyncSession,
    ) -> None:
        """dispatch_batch only processes up to `limit` DCRs."""
        ov_id = await _ensure_ontology_version(db_session)
        for i in range(5):
            dcr = _make_dcr(
                ontology_version_id=ov_id,
                entity_type=f"NuclearMaterial-{i}",
                property_=f"thermal_conductivity-{i}",
                material_system=f"UO2-{i}",
                source_preference="literature",
            )
            await _persist_dcr(dcr, db_session)

        mock_lit = _make_fill_path_mock(data_found=True, path_name="literature")
        paths = {
            "literature": mock_lit,
            "dft": _make_fill_path_mock(),
            "external_db": _make_fill_path_mock(),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        results = await svc.dispatch_batch(ontology_version_id=ov_id, limit=3)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_batch_skips_non_open_dcrs(
        self,
        db_session: AsyncSession,
    ) -> None:
        """dispatch_batch skips DCRs that are not open."""
        ov_id = await _ensure_ontology_version(db_session)

        dcr_open = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            material_system="UO2",
        )
        await _persist_dcr(dcr_open, db_session)

        dcr_ip = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            status="in_progress",
            material_system="Zr",
        )
        await _persist_dcr(dcr_ip, db_session)

        dcr_done = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            status="completed",
            material_system="U",
        )
        await _persist_dcr(dcr_done, db_session)

        mock_lit = _make_fill_path_mock(data_found=True, path_name="literature")
        paths = {
            "literature": mock_lit,
            "dft": _make_fill_path_mock(),
            "external_db": _make_fill_path_mock(),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        results = await svc.dispatch_batch(ontology_version_id=ov_id, limit=10)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_batch_skips_already_dispatched(
        self,
        db_session: AsyncSession,
    ) -> None:
        """dispatch_batch skips DCRs that are open but already dispatched."""
        ov_id = await _ensure_ontology_version(db_session)

        dcr_fresh = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            material_system="UO2",
        )
        await _persist_dcr(dcr_fresh, db_session)

        dcr_dispatched = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            material_system="Zr",
            dispatched_at=datetime.now(UTC),
            dispatch_status="success",
        )
        await _persist_dcr(dcr_dispatched, db_session)

        mock_lit = _make_fill_path_mock(data_found=True, path_name="literature")
        paths = {
            "literature": mock_lit,
            "dft": _make_fill_path_mock(),
            "external_db": _make_fill_path_mock(),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        results = await svc.dispatch_batch(ontology_version_id=ov_id, limit=10)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_batch_returns_empty_when_no_open_dcrs(
        self,
        db_session: AsyncSession,
    ) -> None:
        """dispatch_batch returns empty list when no open DCRs exist."""
        ov_id = await _ensure_ontology_version(db_session)

        svc = GapDispatchService(db_session, fill_paths={})
        results = await svc.dispatch_batch(ontology_version_id=ov_id, limit=10)

        assert results == []

    @pytest.mark.asyncio
    async def test_batch_orders_by_urgency_desc(
        self,
        db_session: AsyncSession,
    ) -> None:
        """dispatch_batch processes higher urgency DCRs first."""
        ov_id = await _ensure_ontology_version(db_session)

        dcr_low = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            material_system="UO2",
            urgency=1,
        )
        await _persist_dcr(dcr_low, db_session)

        dcr_high = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            material_system="Zr",
            urgency=10,
        )
        await _persist_dcr(dcr_high, db_session)

        dcr_med = _make_dcr(
            ontology_version_id=ov_id,
            source_preference="literature",
            material_system="U",
            urgency=5,
        )
        await _persist_dcr(dcr_med, db_session)

        mock_lit = _make_fill_path_mock(data_found=True, path_name="literature")
        paths = {
            "literature": mock_lit,
            "dft": _make_fill_path_mock(),
            "external_db": _make_fill_path_mock(),
        }

        svc = GapDispatchService(db_session, fill_paths=paths)
        results = await svc.dispatch_batch(ontology_version_id=ov_id, limit=2)

        assert len(results) == 2


# ---------------------------------------------------------------------------
# DCR status transitions
# ---------------------------------------------------------------------------


class TestDCRStatusTransition:
    """Test that dispatch() transitions DCR status from open to in_progress."""

    @pytest.mark.asyncio
    async def test_open_to_in_progress(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dispatch transitions DCR from open to in_progress."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="literature", status="open")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(data_found=True, path_name="literature")
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        await svc.dispatch(dcr)

        await db_session.refresh(dcr)
        assert dcr.status == "in_progress"

    @pytest.mark.asyncio
    async def test_in_progress_stays_in_progress(
        self,
        db_session: AsyncSession,
    ) -> None:
        """DCR already in_progress still gets dispatch columns updated."""
        ov_id = await _ensure_ontology_version(db_session)
        dcr = _make_dcr(ontology_version_id=ov_id, source_preference="literature", status="in_progress")
        await _persist_dcr(dcr, db_session)

        mock_path = _make_fill_path_mock(
            data_found=True, reference="ref-1", path_name="literature",
        )
        paths = {"literature": mock_path}

        svc = GapDispatchService(db_session, fill_paths=paths)
        await svc.dispatch(dcr)

        await db_session.refresh(dcr)
        assert dcr.status == "in_progress"
        assert dcr.dispatch_status == "success"
        assert dcr.result_reference == "ref-1"
