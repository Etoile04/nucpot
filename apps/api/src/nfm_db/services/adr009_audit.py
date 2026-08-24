"""ADR-009 §4.3 reconcile audit writer (NFM-3586).

Self-contained audit writer for the daily reconcile routine. Reads the
``ADR_009_RECONCILIATION_HOOK_ENABLED`` feature flag (NFM-3586 acceptance
criterion: OFF ⇒ no scan, no writes, no audit entries) and writes
entries whose shape is byte-for-byte compatible with ADR-009 §4.1's
``close-hook`` audit writer (NFM-3571).

The composite unique constraint on
``(routine, dependent_id, closing_issue_id, run_date)`` is the
idempotency guard. A retry of the routine on a partially-failed state
hits the unique constraint; the writer converts the ``IntegrityError``
into a silent skip and the caller sees ``None`` from
``write_audit_entry``.

Public surface:

* :func:`write_audit_entry` — main entry point used by the reconcile
  routine (NFM-3594 sibling).
* :func:`get_audit_entry` — helper used by tests and post-mortem tooling.
* :data:`ADR_009_RECONCILE_ROUTINE` — exported ``routine`` string constant.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from nfm_db.models.adr009_reconcile_audit import Adr009ReconcileAuditLog
from nfm_db.services.adr009_flag import (
    feature_flag_name,
    is_reconcile_routine_enabled,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


ADR_009_RECONCILE_ROUTINE: str = "adr009-daily-reconcile"
"""``routine`` field value written by §4.3 (vs §4.1's ``adr009-close-hook``)."""


def _to_utc_naive(ts: datetime) -> datetime:
    """Strip tzinfo from an aware datetime and convert to UTC."""
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


def _run_date_from_ts(ts: datetime) -> date:
    """Derive the idempotency-bucket date from the entry timestamp."""
    return _to_utc_naive(ts).date()


def write_audit_entry(
    session: Session,
    *,
    ts: datetime,
    routine: str,
    closing_issue_id: uuid.UUID,
    closing_issue_identifier: str,
    dependent_id: uuid.UUID,
    dependent_identifier: str,
    before_blockedByIssueIds: list[uuid.UUID],
    after_blockedByIssueIds: list[uuid.UUID],
    status_transition: dict[str, str] | None,
    wake_fired: bool,
    feature_flag: str | None = None,
) -> Adr009ReconcileAuditLog | None:
    """Persist a §4.3 reconcile audit entry.

    Returns the persisted row, or ``None`` if:

    * the feature flag is OFF (caller short-circuited), or
    * a row with the same ``(routine, dependent_id, closing_issue_id,
      run_date)`` already exists (idempotency hit; the reconcile
      routine is retry-safe).

    The function never raises on the idempotency path — the
    ``IntegrityError`` is rolled back inside the session so the
    caller's session remains usable for the next entry.
    """
    if not is_reconcile_routine_enabled():
        logger.debug(
            "adr009 audit short-circuit: feature flag off (routine=%s, dependent=%s)",
            routine,
            dependent_identifier,
        )
        return None

    flag = feature_flag if feature_flag is not None else feature_flag_name()

    entry = Adr009ReconcileAuditLog(
        ts=ts,
        routine=routine,
        closing_issue_id=closing_issue_id,
        closing_issue_identifier=closing_issue_identifier,
        dependent_id=dependent_id,
        dependent_identifier=dependent_identifier,
        before_blockedByIssueIds=list(before_blockedByIssueIds),
        after_blockedByIssueIds=list(after_blockedByIssueIds),
        status_transition=status_transition,
        wake_fired=wake_fired,
        feature_flag=flag,
        run_date=_run_date_from_ts(ts),
    )

    session.add(entry)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        logger.info(
            "adr009 audit idempotency hit: %s (routine=%s, dependent=%s)",
            exc.orig,
            routine,
            dependent_identifier,
        )
        return None
    return entry


def get_audit_entry(
    session: Session,
    entry_id: uuid.UUID,
) -> Adr009ReconcileAuditLog | None:
    """Look up an audit entry by primary key. Used by tests and tooling."""
    return session.get(Adr009ReconcileAuditLog, entry_id)


__all__ = [
    "ADR_009_RECONCILE_ROUTINE",
    "get_audit_entry",
    "write_audit_entry",
]
