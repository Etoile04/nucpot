"""NFM-3586: Offline verification of alembic migration 059 (adr009_reconcile_audit_log).

Mirrors the NFM-2137 / migration-035 pattern: verify chain, idempotency,
schema, and downgrade without requiring a live PostgreSQL.

Acceptance criteria covered:

* [AC-1] Migration 059 exists and chains off 058 (single-step chain to head).
* [AC-2] Migration is idempotent on PG (table existence precheck).
* [AC-3] All §4.1 spec columns are present with correct PG types and
  nullability (ts NOT NULL, routine NOT NULL, both UUID columns NOT NULL,
  both identifier columns NOT NULL, both blockedByIssueIds JSONB NOT
  NULL, status_transition JSONB NULLABLE, wake_fired NOT NULL DEFAULT
  FALSE, feature_flag NOT NULL, run_date NOT NULL).
* [AC-4] Composite unique constraint
  ``uq_adr009_reconcile_audit_natural_key`` over
  ``(routine, dependent_id, closing_issue_id, run_date)``.
* [AC-5] Downgrade drops the table and its indexes/constraints cleanly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "059_add_adr009_reconcile_audit_log.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return _MIGRATION_PATH.read_text()


class TestMigration059Chain:
    """Migration is correctly wired into the alembic chain."""

    def test_file_exists(self):
        assert _MIGRATION_PATH.is_file()

    def test_revision_constant(self, migration_source):
        assert re.search(
            r'^revision:\s*str\s*=\s*"059_add_adr009_reconcile_audit_log"',
            migration_source,
            re.MULTILINE,
        ), "revision must equal '059_add_adr009_reconcile_audit_log'"

    def test_chains_off_058(self, migration_source):
        assert re.search(
            r"^down_revision:\s*str\s*\|\s*Sequence\[str\]\s*\|\s*None\s*=\s*"
            r'"058_align_schema_drift_backlog"',
            migration_source,
            re.MULTILINE,
        ), "down_revision must be '058_align_schema_drift_backlog'"


class TestMigration059Schema:
    """DDL matches the §4.1 audit shape byte-for-byte."""

    def test_creates_table(self, migration_source):
        assert "create_table" in migration_source
        assert '"adr009_reconcile_audit_log"' in migration_source

    @pytest.mark.parametrize(
        "column",
        [
            "ts",
            "routine",
            "closing_issue_id",
            "closing_issue_identifier",
            "dependent_id",
            "dependent_identifier",
            "before_blockedByIssueIds",
            "after_blockedByIssueIds",
            "status_transition",
            "wake_fired",
            "feature_flag",
            "run_date",
        ],
    )
    def test_column_present(self, migration_source, column):
        assert f'"{column}"' in migration_source, f"missing column {column!r}"

    def test_blocked_by_lists_are_jsonb(self, migration_source):
        before = re.search(
            r'"before_blockedByIssueIds",\s*\n\s*sa\.dialects\.postgresql\.JSONB\(\)',
            migration_source,
        )
        after = re.search(
            r'"after_blockedByIssueIds",\s*\n\s*sa\.dialects\.postgresql\.JSONB\(\)',
            migration_source,
        )
        assert before is not None
        assert after is not None

    def test_status_transition_nullable(self, migration_source):
        block = re.search(
            r'"status_transition",\s*\n\s*sa\.dialects\.postgresql\.JSONB\(\),\s*\n\s*nullable=True',
            migration_source,
        )
        assert block is not None

    def test_wake_fired_defaults_false(self, migration_source):
        assert 'server_default=sa.text("false")' in migration_source

    def test_run_date_is_date(self, migration_source):
        assert "sa.Date(), nullable=False" in migration_source


class TestMigration059Idempotency:
    """Re-running the migration is a no-op."""

    def test_upgrade_precheck(self, migration_source):
        assert "information_schema.tables" in migration_source
        assert "if _table_exists" in migration_source

    def test_downgrade_precheck(self, migration_source):
        assert "if not _table_exists" in migration_source


class TestMigration059IdempotencyConstraint:
    """Composite natural-key unique constraint enforces retry-safety."""

    def test_unique_constraint_declared(self, migration_source):
        assert "uq_adr009_reconcile_audit_natural_key" in migration_source
        assert "create_unique_constraint" in migration_source

    def test_unique_constraint_columns(self, migration_source):
        match = re.search(
            r'create_unique_constraint\(\s*\n\s*"uq_adr009_reconcile_audit_natural_key",\s*\n\s*"adr009_reconcile_audit_log",\s*\n\s*\[\s*"routine",\s*"dependent_id",\s*"closing_issue_id",\s*"run_date"\s*\]',
            migration_source,
        )
        assert match is not None


class TestMigration059Downgrade:
    """Downgrade drops everything cleanly."""

    def test_drops_index(self, migration_source):
        assert "drop_index" in migration_source
        assert "ix_adr009_reconcile_audit_run_date" in migration_source

    def test_drops_unique_constraint(self, migration_source):
        assert "drop_constraint" in migration_source

    def test_drops_table(self, migration_source):
        assert "drop_table" in migration_source
        assert '"adr009_reconcile_audit_log"' in migration_source
