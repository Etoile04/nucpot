"""ADR-009 §4.3-a reconcile routine (NFM-3600).

Self-contained Python scanner that walks every dependent's
``blocked_by_issue_ids``, removes UUIDs whose referenced issue is in a
terminal state (``done`` or ``cancelled``), and writes an audit entry
per dependent touched via the §4.3-c ``write_audit_entry`` helper.

The routine is the §4.3 sibling that ``tools/reconcile_cancelled_blockers.py``
imports to back its ``--dry-run`` mode and that ``apps/api/tests/``
imports for the §4.3 test fixture (NFM-3600 acceptance criteria).

Public surface:

* :class:`IssueLike` — minimal interface the routine needs from an
  issue (id / identifier / status / blocked_by_issue_ids). Designed so
  callers can pass either ORM rows, dataclasses, or a paperclip HTTP
  payload.
* :class:`ClearedDependency` — a single dependent whose
  ``blocked_by_issue_ids`` was trimmed, with before/after snapshots.
* :class:`ReconcileResult` — aggregate result of one reconcile pass.
* :func:`reconcile_blocked_by_issue_ids` — main entry point used by
  the dry-run script and the test fixture.

Operational contract (mirrors §4.1-c, NFM-3571):

* Feature-flag gated. ``ADR_009_RECONCILE_ROUTINE_ENABLED=off`` ⇒
  ``ReconcileResult(skipped_flag_off=True)``, zero DB writes.
* Idempotence guard. If ``before == after`` for a dependent, the
  routine skips it (no audit entry, no mutation).
* Dry-run. ``dry_run=True`` ⇒ no mutation, no audit writes; the
  ``cleared`` snapshot still reports what *would* change.
* Audit shape. One ``write_audit_entry`` call per touched dependent;
  shape byte-for-byte compatible with §4.1-c's close-hook writer.

References:

* NFM-3519 §4.3 — source spec.
* NFM-3586 §4.3-c — audit writer + flag (this routine's downstream).
* NFM-3571 §4.1-c — analogous reference implementation in the §4.1
  family (audit writer + flag, no scanner).
* NFM-3600 — dry-run script + 5-wedge test fixture that imports
  this routine.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from nfm_db.models.adr009_reconcile_audit import Adr009ReconcileAuditLog
from nfm_db.services.adr009_audit import (
    ADR_009_RECONCILE_ROUTINE,
    _run_date_from_ts,
    write_audit_entry,
)
from nfm_db.services.adr009_flag import (
    feature_flag_name,
    is_reconcile_routine_enabled,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


#: Statuses whose blockers can be cleared (§4.3 source spec).
TERMINAL_BLOCKER_STATUSES: frozenset[str] = frozenset({"done", "cancelled"})


@dataclass(frozen=True)
class IssueLike:
    """Minimal interface the routine needs from an issue.

    The routine deliberately does not couple to a specific ORM model so
    the same routine can be exercised against paperclip HTTP payloads,
    in-memory test fixtures, and SQLAlchemy rows.
    """

    id: uuid.UUID
    identifier: str
    status: str
    blocked_by_issue_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class ClearedDependency:
    """Snapshot of one dependent whose blockers were trimmed.

    ``closing_issue_identifier`` records which cancelled/done issue
    triggered the clear (one entry per removed UUID — a dependent
    blocked by two cancelled issues yields two :class:`ClearedDependency`
    rows).
    """

    dependent_id: uuid.UUID
    dependent_identifier: str
    closing_issue_id: uuid.UUID
    closing_issue_identifier: str
    closing_issue_status: str
    before: tuple[uuid.UUID, ...]
    after: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class ReconcileResult:
    """Aggregate result of one reconcile pass.

    * ``skipped_flag_off`` — ``True`` iff the feature flag was OFF and
      the routine returned without touching anything.
    * ``scanned`` — number of dependents visited (including those
      that turned out to be no-ops).
    * ``touched`` — number of dependents whose
      ``blocked_by_issue_ids`` actually changed.
    * ``uuids_to_remove`` — total individual UUID removals across all
      touched dependents (the count before removal, summed).
    * ``cleared`` — per-dependent cleared snapshot, ordered by
      ``dependent_identifier`` for deterministic dry-run output.
    * ``audit_entries_written`` — count of audit rows the writer
      persisted (``0`` when ``dry_run=True``).
    """

    skipped_flag_off: bool = False
    scanned: int = 0
    touched: int = 0
    uuids_to_remove: int = 0
    cleared: tuple[ClearedDependency, ...] = field(default_factory=tuple)
    audit_entries_written: int = 0


def reconcile_blocked_by_issue_ids(
    dependents: Iterable[IssueLike],
    *,
    lookup_status: Callable[[uuid.UUID], str | None],
    session: Session | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ReconcileResult:
    """Walk every dependent's blockers and prune terminal ones.

    Parameters
    ----------
    dependents:
        Iterable of dependents to scan. The routine does not mutate
        the input — it works on a snapshot of ``blocked_by_issue_ids``.
    lookup_status:
        Callable returning the status (``"done"`` / ``"cancelled"`` /
        ``"todo"`` / ``"in_progress"`` / ``"in_review"`` / ``"blocked"`` /
        ``None``) of the referenced issue, or ``None`` if the UUID is
        unknown. The routine is read-only against the lookup.
    session:
        SQLAlchemy session for audit writes. Required when
        ``dry_run=False``; ignored otherwise.
    dry_run:
        If ``True``, do not mutate and do not write audit entries —
        just report what would change. ``cleared`` is still populated.
    now:
        Override timestamp for deterministic testing. Defaults to
        ``datetime.now(UTC)`` at call time.

    Returns
    -------
    ReconcileResult
        Aggregate counts and the per-dependent cleared snapshot.
    """
    if not is_reconcile_routine_enabled():
        return ReconcileResult(skipped_flag_off=True)

    if not dry_run and session is None:
        raise ValueError(
            "reconcile_blocked_by_issue_ids requires a SQLAlchemy "
            "session unless dry_run=True (refusing to audit-log "
            "without a session)",
        )

    ts = (now or datetime.now(UTC)).replace(microsecond=0)
    flag_name = feature_flag_name()
    run_date: date = _run_date_from_ts(ts)

    # Snapshot dependents into a list so the caller can pass a generator.
    snapshot: list[IssueLike] = list(dependents)

    # Idempotency pre-query: a re-run on the same day skips dependents
    # whose every-removed-blocker is already on file in the audit log.
    # The writer's unique constraint provides the second line of defence
    # (catches concurrent retries) but pre-querying lets the routine
    # report ``touched=0`` on a no-op re-run, which the §4.3 AC test
    # asserts explicitly.
    already_audited: set[tuple[uuid.UUID, uuid.UUID]] = set()
    if not dry_run and session is not None:
        prior_rows = session.execute(
            select(
                Adr009ReconcileAuditLog.dependent_id,
                Adr009ReconcileAuditLog.closing_issue_id,
            ).where(
                Adr009ReconcileAuditLog.routine == ADR_009_RECONCILE_ROUTINE,
                Adr009ReconcileAuditLog.run_date == run_date,
            )
        ).all()
        already_audited = {(row.dependent_id, row.closing_issue_id) for row in prior_rows}

    cleared: list[ClearedDependency] = []
    touched = 0
    uuids_total = 0
    audit_entries_written = 0

    for dependent in snapshot:
        before = dependent.blocked_by_issue_ids
        if not before:
            continue

        # Partition into keep / remove based on the referenced status.
        keep: list[uuid.UUID] = []
        remove: list[uuid.UUID] = []
        remove_statuses: dict[uuid.UUID, str] = {}

        for blocker_id in before:
            status = lookup_status(blocker_id)
            if status in TERMINAL_BLOCKER_STATUSES:
                remove.append(blocker_id)
                remove_statuses[blocker_id] = status
            else:
                keep.append(blocker_id)

        after = tuple(keep)
        # Idempotence guard: nothing changed ⇒ no audit, no mutation.
        if after == before:
            continue

        # Drop removals already audited on this run_date — a re-run on
        # the same day is a no-op for that (dependent, closing_issue)
        # pair even though the input still contains the cancelled UUID
        # (the paperclip-side PATCH hasn't happened yet, or we're in a
        # test fixture that doesn't PATCH at all).
        pending_remove: list[uuid.UUID] = []
        for removed_id in remove:
            if (dependent.id, removed_id) in already_audited:
                continue
            pending_remove.append(removed_id)
        if not pending_remove:
            continue
        remove = pending_remove

        # Sort for deterministic dry-run output.
        sorted_remove = tuple(
            sorted(remove, key=lambda bid: (remove_statuses[bid], str(bid))),
        )

        for removed_id in sorted_remove:
            closing_status = remove_statuses[removed_id]
            cleared.append(
                ClearedDependency(
                    dependent_id=dependent.id,
                    dependent_identifier=dependent.identifier,
                    closing_issue_id=removed_id,
                    closing_issue_identifier="",  # scanner-only view
                    closing_issue_status=closing_status,
                    before=before,
                    after=after,
                )
            )
            uuids_total += 1

        touched += 1

        if not dry_run:
            # One audit row per removed UUID (§4.1-c shape parity).
            for removed_id in sorted_remove:
                closing_status = remove_statuses[removed_id]
                # closing_issue_identifier is unknown to the scanner;
                # the integration step (§4.3-i) can backfill via a
                # secondary lookup if needed. Until then we emit the
                # synthetic placeholder "UNKNOWN-<first-8-hex>" so the
                # audit shape is still parseable end-to-end.
                closing_identifier = f"UNKNOWN-{removed_id.hex[:8].upper()}"
                entry = write_audit_entry(
                    session,
                    ts=ts,
                    routine=ADR_009_RECONCILE_ROUTINE,
                    closing_issue_id=removed_id,
                    closing_issue_identifier=closing_identifier,
                    dependent_id=dependent.id,
                    dependent_identifier=dependent.identifier,
                    before_blockedByIssueIds=list(before),
                    after_blockedByIssueIds=list(after),
                    status_transition=None,  # §4.3-b scope; auto-transition lands in §4.3-a sibling
                    wake_fired=False,
                    feature_flag=flag_name,
                )
                if entry is not None:
                    audit_entries_written += 1

    cleared.sort(key=lambda c: (c.dependent_identifier, str(c.closing_issue_id)))

    return ReconcileResult(
        skipped_flag_off=False,
        scanned=len(snapshot),
        touched=touched,
        uuids_to_remove=uuids_total,
        cleared=tuple(cleared),
        audit_entries_written=audit_entries_written,
    )


__all__ = [
    "TERMINAL_BLOCKER_STATUSES",
    "ClearedDependency",
    "IssueLike",
    "ReconcileResult",
    "reconcile_blocked_by_issue_ids",
]
