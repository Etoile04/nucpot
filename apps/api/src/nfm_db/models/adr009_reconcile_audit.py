"""ADR-009 §4.3 reconcile audit log (NFM-3601).

Byte-for-byte shape parity with ADR-009 §4.1's ``close-hook`` audit entry.
The §4.1 writer is defined in NFM-3571; this table is the §4.3 equivalent
and is structurally identical apart from the ``routine`` value
(``adr009-daily-reconcile`` here vs ``adr009-close-hook`` in §4.1).

Idempotency is enforced by a composite unique constraint over
``(routine, dependent_id, closing_issue_id, run_date)`` so partial-failure
retries do not double-write audit rows.

The ``before_blockedByIssueIds`` and ``after_blockedByIssueIds`` columns
hold UUID lists — JSONB on PostgreSQL, JSON text on SQLite. We coerce
the lists to ``str`` on bind so the SQLite fallback serialises cleanly
via :class:`CompatJSONB`. PostgreSQL's JSONB adapter accepts strings
verbatim so the PG round-trip remains byte-for-byte identical to §4.1.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Dialect,
    Index,
    String,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB as _NATIVE_JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB


class _UuidListJSONB(TypeDecorator[list[uuid.UUID] | None]):
    """JSONB column storing a list of UUIDs.

    On PostgreSQL, persists native ``jsonb``; on SQLite, serialises to a
    JSON-encoded text column. UUIDs are coerced to ``str`` on bind so the
    SQLite fallback's ``json.dumps`` succeeds; on read we re-hydrate them
    back to :class:`uuid.UUID` instances. PostgreSQL handles either form
    transparently.
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_NATIVE_JSONB())
        return dialect.type_descriptor(String())

    def process_bind_param(self, value: list[uuid.UUID] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return [str(u) for u in value]
        import json

        return json.dumps([str(u) for u in value])

    def process_result_value(self, value: Any, dialect: Dialect) -> list[uuid.UUID] | None:
        if value is None:
            return None
        if isinstance(value, str):
            import json

            value = json.loads(value)
        return [uuid.UUID(str(item)) for item in value]


class Adr009ReconcileAuditLog(Base):
    """One row per dependent-issue reconciliation decision (NFM-3601).

    Matches the §4.1 audit shape exactly. The composite unique constraint
    on ``(routine, dependent_id, closing_issue_id, run_date)`` is the
    idempotency guard — a partial-failure retry that replays the same
    tuple will raise ``IntegrityError`` and the writer converts it to
    a silent skip.
    """

    __tablename__ = "adr009_reconcile_audit_log"
    __table_args__ = (
        UniqueConstraint(
            "routine",
            "dependent_id",
            "closing_issue_id",
            "run_date",
            name="uq_adr009_reconcile_audit_natural_key",
        ),
        Index(
            "ix_adr009_reconcile_audit_run_date",
            "run_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- §4.1-shape fields -----------------------------------------------
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    routine: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    closing_issue_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
    closing_issue_identifier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    dependent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
    dependent_identifier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    before_blockedByIssueIds: Mapped[list[uuid.UUID]] = mapped_column(  # noqa: N815
        _UuidListJSONB,
        nullable=False,
    )
    after_blockedByIssueIds: Mapped[list[uuid.UUID]] = mapped_column(  # noqa: N815
        _UuidListJSONB,
        nullable=False,
    )
    status_transition: Mapped[dict[str, str] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
    )
    wake_fired: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    feature_flag: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # --- NFM-3601 operational field (not part of §4.1 spec) ---------------
    run_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Adr009ReconcileAuditLog id={self.id!s} "
            f"routine={self.routine!r} "
            f"dependent={self.dependent_identifier!r} "
            f"closing={self.closing_issue_identifier!r} "
            f"run_date={self.run_date.isoformat()}>"
        )


__all__ = ["Adr009ReconcileAuditLog"]
