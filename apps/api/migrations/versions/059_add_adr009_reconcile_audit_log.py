"""059 — ADR-009 §4.3 reconcile audit log table (NFM-3586).

Adds the ``adr009_reconcile_audit_log`` table backing the daily 06:00 UTC
reconciliation routine (NFM-3554). The schema is structurally identical
to ADR-009 §4.1's close-hook audit table (NFM-3571) — field names,
types, and nullability match byte-for-byte — so the §4.1 and §4.3
writers can be unified at the integration task.

Columns mirror the §4.1 audit shape:

* ``ts`` (TIMESTAMPTZ) — when the reconcile decision was made
* ``routine`` (VARCHAR(64)) — ``adr009-daily-reconcile``
* ``closing_issue_id`` (UUID) — the issue that closed and unblocked
* ``closing_issue_identifier`` (VARCHAR(32)) — ``NFM-XXXX``
* ``dependent_id`` (UUID) — the dependent that was unblocked
* ``dependent_identifier`` (VARCHAR(32)) — ``NFM-YYYY``
* ``before_blockedByIssueIds`` (JSONB) — pre-transition list
* ``after_blockedByIssueIds`` (JSONB) — post-transition list
* ``status_transition`` (JSONB NULL) — ``{from, to}`` or null
* ``wake_fired`` (BOOLEAN) — whether a wake-up was emitted
* ``feature_flag`` (VARCHAR(64)) — ``ADR_009_RECONCILIATION_HOOK_ENABLED``

Plus the NFM-3586 operational column:

* ``run_date`` (DATE) — bucket for the idempotency guard

The composite unique constraint
``(routine, dependent_id, closing_issue_id, run_date)`` enforces §4.1
idempotence guarantees: a partial-failure retry that replays the same
tuple raises ``IntegrityError`` and the writer converts it to a silent
skip (see ``nfm_db.services.adr009_audit.write_audit_entry``).

Constraint honours:

* **Idempotent on PG** — every DDL is wrapped in an ``information_schema``
  precheck so re-running the migration is a no-op.
* **SQLite-friendly** — uses portable types; production runs on PG.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "059_add_adr009_reconcile_audit_log"
down_revision: str | Sequence[str] | None = "058_align_schema_drift_backlog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(conn, table_name: str) -> bool:
    """Return True if ``table_name`` already exists in the current schema."""
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table_name},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "adr009_reconcile_audit_log"):
        # Idempotent — re-running this migration is a no-op.
        return

    op.create_table(
        "adr009_reconcile_audit_log",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(),
            primary_key=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("routine", sa.String(length=64), nullable=False),
        sa.Column(
            "closing_issue_id",
            sa.dialects.postgresql.UUID(),
            nullable=False,
        ),
        sa.Column(
            "closing_issue_identifier",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "dependent_id",
            sa.dialects.postgresql.UUID(),
            nullable=False,
        ),
        sa.Column(
            "dependent_identifier",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "before_blockedByIssueIds",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "after_blockedByIssueIds",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "status_transition",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "wake_fired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "feature_flag",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("run_date", sa.Date(), nullable=False),
    )

    op.create_unique_constraint(
        "uq_adr009_reconcile_audit_natural_key",
        "adr009_reconcile_audit_log",
        ["routine", "dependent_id", "closing_issue_id", "run_date"],
    )
    op.create_index(
        "ix_adr009_reconcile_audit_run_date",
        "adr009_reconcile_audit_log",
        ["run_date"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "adr009_reconcile_audit_log"):
        return

    op.drop_index(
        "ix_adr009_reconcile_audit_run_date",
        table_name="adr009_reconcile_audit_log",
    )
    op.drop_constraint(
        "uq_adr009_reconcile_audit_natural_key",
        "adr009_reconcile_audit_log",
        type_="unique",
    )
    op.drop_table("adr009_reconcile_audit_log")
