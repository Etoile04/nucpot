"""Tests for the §4.3-a reconcile routine + 5-wedge test fixture (NFM-3600).

Acceptance criteria under test (per NFM-3600 description):

* [ ] Test fixture seeds 5 fake cancelled-blocker wedges — 5
      dependent issues each with ``blockedByIssueIds`` containing
      exactly 1 cancelled UUID.
* [ ] Test asserts all 5 dependents' ``blockedByIssueIds == []``
      post-run (asserted on the routine's :class:`ClearedDependency`
      snapshot).
* [ ] Test asserts 5 audit log entries written with the correct shape.
* [ ] Test asserts idempotence — running the routine a second time
      makes zero changes.

Plus a few defensive tests to lock in the routine's contract:

* Flag-off short-circuit returns ``skipped_flag_off=True`` and
  writes nothing.
* Non-terminal blockers (in-progress / blocked / todo) are kept.
* Multi-UUID dependents are partitioned correctly (one audit row
  per removed UUID).
* The dry-run path returns the same ``cleared`` snapshot without
  writing any audit rows.
* Mutating path without a session raises ``ValueError`` (refusing
  to audit-log without a session is the safe failure mode).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from nfm_db.models.adr009_reconcile_audit import Adr009ReconcileAuditLog
from nfm_db.services.adr009_flag import (  # noqa: F401  — used via `global` below
    _FLAG_CACHE,
    is_reconcile_routine_enabled,
)
from nfm_db.services.adr009_reconcile_routine import (
    TERMINAL_BLOCKER_STATUSES,
    ClearedDependency,
    IssueLike,
    ReconcileResult,
    reconcile_blocked_by_issue_ids,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def _enable_adr009_flag(monkeypatch: pytest.MonkeyPatch):
    """All tests in this module turn the §4.3 reconcile feature flag ON.

    Mutates the env var and resets the module-level memoisation cache
    directly so we never mutate the real environment.
    """
    monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "on")
    global _FLAG_CACHE
    _FLAG_CACHE = None
    assert is_reconcile_routine_enabled(), "test precondition: flag must be ON"
    yield
    _FLAG_CACHE = None


@pytest.fixture
def sync_audit_engine():
    """In-memory SQLite engine with the §4.3 audit table pre-created.

    The reconcile routine writes audit rows via a sync
    :class:`sqlalchemy.orm.Session` (mirroring how the production
    cron driver uses sync sessions; see §4.3-c NFM-3586). The global
    ``db_session`` fixture is an :class:`AsyncSession`, which is why
    these tests need their own sync session rather than reusing the
    project-wide async fixture.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Adr009ReconcileAuditLog.__table__.create(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sync_audit_engine):
    """Sync :class:`Session` exposing the §4.3 audit table to tests.

    Overrides the project-wide ``db_session`` (AsyncSession) so the
    sync reconcile routine can call ``session.flush()`` /
    ``session.commit()`` without ``await``.
    """
    SessionLocal = sessionmaker(bind=sync_audit_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_dependent(
    blocked_by: list[uuid.UUID] | tuple[uuid.UUID, ...] = (),
    *,
    identifier: str | None = None,
    status: str = "blocked",
) -> IssueLike:
    """Build an :class:`IssueLike` with a fresh UUID."""
    return IssueLike(
        id=uuid.uuid4(),
        identifier=identifier or f"NFM-DEP-{uuid.uuid4().hex[:6].upper()}",
        status=status,
        blocked_by_issue_ids=tuple(blocked_by),
    )


def _make_cancelled(identifier: str | None = None) -> IssueLike:
    """Build a cancelled blocker with a stable identifier for assertions."""
    return IssueLike(
        id=uuid.uuid4(),
        identifier=identifier or f"NFM-CXL-{uuid.uuid4().hex[:6].upper()}",
        status="cancelled",
        blocked_by_issue_ids=(),
    )


def _status_lookup_from_issues(
    issues: list[IssueLike],
) -> dict[uuid.UUID, str]:
    """Map every issue's UUID to its status (mimics paperclip's row lookup)."""
    return {issue.id: issue.status for issue in issues}


class TestFiveCancelledWedgeFixture:
    """The canonical §4.3 acceptance test — five fake cancelled-blocker
    wedges, one reconcile pass, assert all cleared + 5 audit rows + idempotence.
    """

    def test_five_cancelled_blocker_wedges_all_cleared_and_audited(
        self,
        db_session: Session,
    ) -> None:
        # Seed: 5 cancelled blockers + 5 dependents each blocked by
        # exactly one cancelled UUID.
        blockers: list[IssueLike] = [_make_cancelled() for _ in range(5)]
        dependents: list[IssueLike] = [
            _make_dependent(
                blocked_by=[blocker.id],
                identifier=f"NFM-DEP-{i:03d}",
            )
            for i, blocker in enumerate(blockers)
        ]

        statuses = _status_lookup_from_issues(blockers + dependents)

        # --- 1. Run reconcile (mutating path) -----------------------
        result: ReconcileResult = reconcile_blocked_by_issue_ids(
            dependents,
            lookup_status=statuses.get,
            session=db_session,
        )

        # --- 2. Assert all 5 dependents' after-blockedByIssueIds ----
        # The routine does not mutate the input; instead it returns
        # the cleared snapshot. The snapshot represents what a
        # paperclip-side implementation would PATCH.
        assert result.touched == 5, f"expected 5 touched dependents, got {result.touched}"
        assert result.uuids_to_remove == 5, (
            f"expected 5 UUIDs removed total, got {result.uuids_to_remove}"
        )
        assert len(result.cleared) == 5, f"expected 5 cleared entries, got {len(result.cleared)}"
        for entry in result.cleared:
            assert entry.after == (), (
                f"{entry.dependent_identifier}: after must be empty, got {entry.after}"
            )
            assert entry.closing_issue_status == "cancelled"
            assert len(entry.before) == 1

        # --- 3. Assert 5 audit log entries written -----------------
        rows: list[Adr009ReconcileAuditLog] = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id.in_([d.id for d in dependents])
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 5, f"expected 5 audit rows, got {len(rows)}"

        # Every row has the correct shape per the §4.1-c spec.
        for row in rows:
            assert row.routine == "adr009-daily-reconcile"
            assert row.after_blockedByIssueIds == []
            assert len(row.before_blockedByIssueIds) == 1
            assert row.status_transition is None
            assert row.wake_fired is False
            assert row.feature_flag == "ADR_009_RECONCILIATION_HOOK_ENABLED"
            assert row.closing_issue_identifier.startswith("UNKNOWN-")
            # closing_issue_id round-trips as a real UUID.
            assert isinstance(row.closing_issue_id, uuid.UUID)

        assert result.audit_entries_written == 5

        # --- 4. Assert idempotence: second run = no changes -------
        result2: ReconcileResult = reconcile_blocked_by_issue_ids(
            dependents,
            lookup_status=statuses.get,
            session=db_session,
        )
        assert result2.touched == 0, f"second pass should be a no-op, got touched={result2.touched}"
        assert result2.uuids_to_remove == 0
        assert result2.cleared == ()
        # No additional audit rows.
        rows_after = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id.in_([d.id for d in dependents])
                )
            )
            .scalars()
            .all()
        )
        assert len(rows_after) == 5, (
            f"idempotence guard violated: {len(rows_after)} audit rows after second pass"
        )


class TestRoutineContract:
    """Lock in the operational contract of the §4.3-a routine."""

    def test_dry_run_returns_cleared_snapshot_without_writes(
        self,
        db_session: Session,
    ) -> None:
        blocker = _make_cancelled()
        dependent = _make_dependent(blocked_by=[blocker.id])
        statuses = _status_lookup_from_issues([blocker, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
            dry_run=True,
        )

        assert result.touched == 1
        assert result.uuids_to_remove == 1
        assert result.audit_entries_written == 0
        assert len(result.cleared) == 1
        assert result.cleared[0].after == ()

        rows = db_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert rows == [], "dry_run must not write audit rows"

    def test_dry_run_does_not_require_session(self) -> None:
        """``dry_run=True`` may be called with ``session=None`` (used by
        the standalone ``tools/reconcile_cancelled_blockers.py`` script)."""
        blocker = _make_cancelled()
        dependent = _make_dependent(blocked_by=[blocker.id])
        statuses = _status_lookup_from_issues([blocker, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=None,
            dry_run=True,
        )

        assert result.touched == 1
        assert result.audit_entries_written == 0

    def test_non_terminal_blockers_are_kept(self) -> None:
        """Blockers in non-terminal statuses (todo / in_progress /
        in_review / blocked) must NOT be cleared."""
        in_progress = IssueLike(
            id=uuid.uuid4(),
            identifier="NFM-INPROG",
            status="in_progress",
            blocked_by_issue_ids=(),
        )
        blocked = IssueLike(
            id=uuid.uuid4(),
            identifier="NFM-BLOCKED",
            status="blocked",
            blocked_by_issue_ids=(),
        )
        dependent = _make_dependent(
            blocked_by=[in_progress.id, blocked.id],
        )
        statuses = _status_lookup_from_issues([in_progress, blocked, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=None,
            dry_run=True,
        )

        assert result.touched == 0
        assert result.cleared == ()

    def test_multi_uuid_dependent_yields_one_audit_row_per_removed_uuid(
        self,
        db_session: Session,
    ) -> None:
        """A dependent blocked by 2 cancelled issues produces 2 audit rows
        (one per removed UUID) — mirrors §4.1-c's per-removed-blocker shape."""
        c1 = _make_cancelled(identifier="NFM-CXL-001")
        c2 = _make_cancelled(identifier="NFM-CXL-002")
        d = _make_dependent(
            blocked_by=[c1.id, c2.id],
            identifier="NFM-MULTI",
        )
        statuses = _status_lookup_from_issues([c1, c2, d])

        result = reconcile_blocked_by_issue_ids(
            [d],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.touched == 1
        assert result.uuids_to_remove == 2
        assert len(result.cleared) == 2

        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(Adr009ReconcileAuditLog.dependent_id == d.id)
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2

    def test_mutating_path_requires_session(self) -> None:
        """``dry_run=False`` without a session must raise — refusing to
        audit-log without a session is the safe failure mode."""
        dependent = _make_dependent(blocked_by=[uuid.uuid4()])
        statuses = {dependent.id: "blocked"}

        with pytest.raises(ValueError, match="requires a SQLAlchemy session"):
            reconcile_blocked_by_issue_ids(
                [dependent],
                lookup_status=statuses.get,
                session=None,
                dry_run=False,
            )

    def test_terminal_statuses_constant_is_locked(self) -> None:
        """The terminal-blocker set is part of the §4.3 public contract."""
        assert frozenset({"done", "cancelled"}) == TERMINAL_BLOCKER_STATUSES

    def test_cleared_dependency_is_frozen(self) -> None:
        """``ClearedDependency`` is a frozen dataclass — protects the
        audit snapshot from accidental mutation by callers."""
        entry = ClearedDependency(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-X",
            closing_issue_id=uuid.uuid4(),
            closing_issue_identifier="UNKNOWN-DEADBEEF",
            closing_issue_status="cancelled",
            before=(uuid.uuid4(),),
            after=(),
        )
        with pytest.raises((AttributeError, Exception)):
            entry.after = (uuid.uuid4(),)  # type: ignore[misc]
