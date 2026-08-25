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

NFM-3587 (§4.3-d) additions — multi-hop cascade variant.

The NFM-3587 acceptance criteria explicitly permit (and recommend) a
multi-hop variant ``A → B → C → D`` where several ancestors are
cancelled, asserting the cascade. ``TestMultiHopCascadeFixture``
covers this case with all three ancestors ``A``, ``B``, ``C``
cancelled and the deep descendant ``D`` carrying all three UUIDs in
``blocked_by_issue_ids``. The variant is included because it adds
two assertions the canonical 5-wedge test cannot make:

1. **Wake-per-dependent, not per-UUID** — a dependent cleared of
   three cancelled blockers emits exactly one :class:`WakeIntent`,
   not three. The §4.3-b AC matrix says one wake per dependent.
2. **Cascade effect in one pass** — when every ancestor in a chain
   is terminal, a single §4.3-a pass fully unblocks the deep
   descendant. This locks in the assumption the §4.3-i integration
   runner relies on (it can hand a deep-dependents list to the
   routine once and rely on the routine's transitive-blocker removal).

The test deliberately does not exercise cross-pass cascade (where
pass N unblocks ``B``, then pass N+1 unblocks ``C`` because ``B``'s
status flipped to ``todo``). The §4.3-a routine is single-pass and
does not re-feed dependents — cross-pass cascade is the §4.3-i
integration runner's responsibility, not §4.3-d's. That boundary is
called out so a future reader does not add a fragile "two-pass"
test here.
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
    WakeIntent,
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
        # §4.3-b (NFM-3688): because the fixture dependents have
        # ``status="blocked"`` and the sweep cleared their only blocker,
        # the auto-transition branch fires with ``status_transition=
        # {from: blocked, to: todo}``. The §4.3-a fixture deliberately
        # omits an ``assignee_agent_id``, so ``wake_fired`` is ``False``
        # even though the transition fires — this exercises the
        # §4.3-b "no assignee -> transition but no wake" edge.
        for row in rows:
            assert row.routine == "adr009-daily-reconcile"
            assert row.after_blockedByIssueIds == []
            assert len(row.before_blockedByIssueIds) == 1
            assert row.status_transition == {"from": "blocked", "to": "todo"}
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


class TestMultiHopCascadeFixture:
    """NFM-3587 §4.3-d multi-hop variant.

    The canonical 5-wedge fixture exercises the
    one-blocker-per-dependent case. The multi-hop variant exercises
    a deep descendant ``D`` whose ``blocked_by_issue_ids`` carries
    three terminal ancestors simultaneously — the consolidated state
    after a real cascade has finished collapsing a chain ``A → B → C
    → D`` where each of ``A``, ``B``, ``C`` ended in a terminal
    state.

    Two assertions are unique to this variant (see the module
    docstring for rationale):

    1. The routine writes one audit row per removed UUID but emits
       exactly **one** :class:`WakeIntent` per dependent — not one
       per UUID. This is the §4.3-b AC matrix and the §4.3-i
       integration runner's contract: the caller fires one wake,
       never three.
    2. The :class:`WakeIntent.idempotency_key` is deterministic for
       the same ``(dependent_id, sorted_cleared_blocker_ids)`` pair,
       so the integration runner's retry-on-partial-failure path
       cannot double-wake the assignee.

    The test deliberately exercises a single pass — the §4.3-a
    routine is single-pass by design, and cross-pass cascade
    belongs to the §4.3-i runner, not §4.3-d. The fixture's three
    cancelled ancestors simulate the post-cascade state so a single
    pass is sufficient to assert full resolution.
    """

    def test_three_cancelled_ancestors_unblock_deep_descendant_in_one_pass(
        self,
        db_session: Session,
    ) -> None:
        # Chain: A → B → C → D. After the cascade the three
        # ancestors are all in terminal states; ``D`` still carries
        # their UUIDs in ``blocked_by_issue_ids``. Setup mirrors that
        # post-cascade state.
        a = _make_cancelled(identifier="NFM-MHOP-A")
        b = _make_cancelled(identifier="NFM-MHOP-B")
        c = _make_cancelled(identifier="NFM-MHOP-C")

        # ``D`` is the deep descendant. It must carry an assignee so
        # the §4.3-b wake branch fires — without an assignee the
        # transition still happens but ``wake_fired=False`` and no
        # WakeIntent is emitted (see NFM-3688 AC). Picking a known
        # UUID lets the test assert the WakeIntent's
        # ``assignee_agent_id`` round-trips.
        deep_assignee = uuid.uuid4()
        d = IssueLike(
            id=uuid.uuid4(),
            identifier="NFM-MHOP-D",
            status="blocked",
            blocked_by_issue_ids=(a.id, b.id, c.id),
            assignee_agent_id=deep_assignee,
        )
        statuses = _status_lookup_from_issues([a, b, c, d])

        # --- Single reconcile pass over the deep dependent --------
        result: ReconcileResult = reconcile_blocked_by_issue_ids(
            [d],
            lookup_status=statuses.get,
            session=db_session,
        )

        # The routine reports three UUIDs removed in one pass.
        assert result.touched == 1, (
            f"deep descendant must be touched exactly once, got {result.touched}"
        )
        assert result.uuids_to_remove == 3, (
            f"three cancelled ancestors must yield three UUID removals, "
            f"got {result.uuids_to_remove}"
        )
        assert len(result.cleared) == 3, (
            f"three ClearedDependency entries expected, got {len(result.cleared)}"
        )
        for entry in result.cleared:
            assert entry.dependent_identifier == "NFM-MHOP-D"
            assert entry.after == (), (
                f"{entry.dependent_identifier}: after must be empty, got {entry.after}"
            )
            assert entry.closing_issue_status == "cancelled"
            assert len(entry.before) == 3

        # Three audit rows persisted, one per removed UUID.
        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == d.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3, (
            f"three audit rows expected, got {len(rows)}"
        )
        for row in rows:
            assert row.routine == "adr009-daily-reconcile"
            assert row.after_blockedByIssueIds == []
            assert len(row.before_blockedByIssueIds) == 3
            # The cascade-effect assertion: every audit row carries
            # the same status_transition for ``D``. The transition
            # is a property of the dependent, not of any individual
            # blocker — so all three rows must agree byte-for-byte.
            assert row.status_transition == {"from": "blocked", "to": "todo"}
            assert row.wake_fired is True, (
                "wake_fired must be True when assignee is set and "
                "the dependent transitions out of blocked"
            )
            assert row.feature_flag == "ADR_009_RECONCILIATION_HOOK_ENABLED"

        # The §4.3-b AC assertion unique to this variant: exactly
        # ONE WakeIntent per dependent, not one per UUID.
        assert len(result.wake_intents) == 1, (
            f"deep descendant must emit exactly one WakeIntent, "
            f"got {len(result.wake_intents)}"
        )
        intent: WakeIntent = result.wake_intents[0]
        assert intent.dependent_id == d.id
        assert intent.dependent_identifier == "NFM-MHOP-D"
        assert intent.assignee_agent_id == deep_assignee
        assert intent.status_transition == {"from": "blocked", "to": "todo"}
        # Cleared blockers are sorted deterministically — important
        # because the idempotency_key hashes the sorted tuple.
        assert intent.cleared_blocker_ids == tuple(
            sorted([a.id, b.id, c.id], key=str)
        )
        assert intent.idempotency_key.startswith("adr-009:4.3-b:")
        # The same (dependent_id, sorted_cleared_blocker_ids) pair
        # must produce the same key — re-running the routine in the
        # same window would not double-wake.
        from nfm_db.services.adr009_reconcile_routine import (
            _build_wake_intent_key,
        )

        assert intent.idempotency_key == _build_wake_intent_key(
            d.id, intent.cleared_blocker_ids
        )

        # --- Idempotence: second pass is a no-op --------------------
        result2 = reconcile_blocked_by_issue_ids(
            [d],
            lookup_status=statuses.get,
            session=db_session,
        )
        assert result2.touched == 0, (
            f"second pass must be a no-op, got touched={result2.touched}"
        )
        assert result2.uuids_to_remove == 0
        assert result2.cleared == ()
        assert result2.wake_intents == ()
        # No additional audit rows.
        rows_after = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == d.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows_after) == 3, (
            f"idempotence guard violated: {len(rows_after)} audit rows after second pass"
        )

    def test_partial_cascade_does_not_resolve_intermediate_blocked(
        self,
    ) -> None:
        """When only the deepest ancestor is cancelled and an
        intermediate ancestor is still in a non-terminal status,
        the routine must clear *only* the cancelled UUID — not the
        intermediate one — and must NOT cascade the dependent's
        status out of blocked.

        This locks the boundary between §4.3-d's one-pass behaviour
        and the §4.3-i runner's responsibility: the routine never
        crosses a live blocker. Without this assertion a future
        reader could mistake the cascade effect for a transitive
        status flip.
        """
        # Chain A → B → D. ``A`` is cancelled; ``B`` is in_progress
        # (a live, non-terminal blocker). ``D`` blocked by both.
        a = _make_cancelled(identifier="NFM-PCASCADE-A")
        b = IssueLike(
            id=uuid.uuid4(),
            identifier="NFM-PCASCADE-B",
            status="in_progress",
            blocked_by_issue_ids=(),
        )
        d = _make_dependent(
            blocked_by=[a.id, b.id],
            identifier="NFM-PCASCADE-D",
        )
        statuses = _status_lookup_from_issues([a, b, d])

        result = reconcile_blocked_by_issue_ids(
            [d],
            lookup_status=statuses.get,
            session=None,
            dry_run=True,
        )

        # Only ``a`` is cleared; ``b`` stays.
        assert result.touched == 1
        assert result.uuids_to_remove == 1
        assert len(result.cleared) == 1
        assert result.cleared[0].closing_issue_id == a.id
        # ``D`` still has ``B`` in its blocked list — no status flip.
        assert result.cleared[0].after == (b.id,)
        # No WakeIntent in dry_run (caller has not committed).
        assert result.wake_intents == ()


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
