"""F4 ingest bypass guard — reject UUID-titled ``data_sources`` rows.

Revision ID: 071_f4_uuid_titled_source_guard
Revises: 070_d2_dedup_bad_data_sources
Create Date: 2026-09-02

NFM-4089 — F4 ingest-bypass audit + monitoring (NFM-4084 follow-up)
===================================================================

Root cause
----------
The NFM-4084 F4 investigation discovered that ``extraction_jobs`` had
been idle since 2026-08-02, yet ``data_sources`` continued to receive
fresh rows (most recently 2026-09-01).  The bypass ingest paths
(``literature.py``, ``source_service.py``,
``extraction_to_db_mapper.py``) all called ``DataSource(...)``
directly without first checking that ``title`` was a real reference
and not a 36-char UUID artefact of the upstream extraction chain.
Migration 070 (NFM-4088) cleaned up the legacy bad rows; this
migration closes the hole so the same bug cannot regress from any
bypass path.

Strategy
--------
Install a BEFORE INSERT OR UPDATE OF title trigger on ``data_sources``
that rejects any row whose ``title`` matches the canonical UUID
pattern (``^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$``).
On rejection the trigger:

1. INSERTs a structured ``health_events`` row
   (``event_type='uuid_titled_source_blocked'``, ``severity='critical'``,
   ``source_service='ingest'``) so the existing
   ``GET /api/v1/health/alerts`` and ``/health/alerts/summary`` endpoints
   immediately surface the regression to the ops dashboard.
2. RAISES EXCEPTION with the offending ``title`` so the caller's
   transaction rolls back cleanly and the original exception survives
   debugging.

Schema prerequisites
--------------------

* ``health_events`` must exist (added in migration 013 — long since
  shipped on all environments).
* ``data_sources`` must exist (migration 001 — long since shipped).
* Migration 070 must have run so no legacy UUID-titled rows survive
  to be re-blocked; the trigger is intentionally installed
  **after** 070 to keep the order hard.

Cross-references
----------------

* NFM-4084 — F4 调研 + 决策表
* NFM-4088 — D2 data-side migration (predecessor; trigger is gated on it)
* ADR-009 — ``health_events`` audit trail
"""

from collections.abc import Sequence

from alembic import op

revision: str = "071_f4_uuid_titled_source_guard"
down_revision: str | Sequence[str] | None = "070_d2_dedup_bad_data_sources"


# ---------------------------------------------------------------------------
# Trigger function — DB-level gate that rejects UUID-titled data_sources.
# ---------------------------------------------------------------------------
# Notes:
# * CREATE OR REPLACE FUNCTION: idempotent re-run during partial-failure
#   recovery should not error.
# * ECPG-style ``$$ ... $$`` quoting keeps the function body untouched
#   by Python string escapes.
# * ``severity = 'critical'`` matches the canonical ``HealthEvent.severity``
#   vocabulary already used by NFM-2220 emitters.
# * The trigger always returns ``NEW`` on the no-block path; the PG
#   documentation requires an explicit RETURN for row-level triggers.


def upgrade() -> None:
    # ----- 1. Install the trigger function. ----------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_uuid_titled_source()
        RETURNS trigger AS $$
        DECLARE
          v_title text := COALESCE(NEW.title, '');
        BEGIN
          IF v_title ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
            INSERT INTO health_events (
              event_type,
              severity,
              source_service,
              context,
              created_at
            ) VALUES (
              'uuid_titled_source_blocked',
              'critical',
              'ingest',
              jsonb_build_object(
                'title', v_title,
                'doi', NEW.doi,
                'source_table', TG_TABLE_NAME,
                'op', TG_OP,
                'txid', txid_current()
              ),
              now()
            );

            RAISE EXCEPTION
              'uuid_titled_source_blocked: data_sources.title looks like a UUID (%) — ingest bug regression guard (NFM-4089)',
              v_title
              USING ERRCODE = 'check_violation';
          END IF;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # ----- 2. Install the BEFORE INSERT/UPDATE trigger. ----------------
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_data_sources_uuid_title ON data_sources;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_data_sources_uuid_title
          BEFORE INSERT OR UPDATE OF title ON data_sources
          FOR EACH ROW EXECUTE FUNCTION reject_uuid_titled_source();
        """
    )


def downgrade() -> None:
    # Drop the trigger first so the function dependency clears before we
    # remove the function itself.  PostgreSQL does not require this order
    # because the trigger holds an implicit dependency, but explicit
    # ordering makes the rollback intent obvious to readers.
    op.execute("DROP TRIGGER IF EXISTS trg_data_sources_uuid_title ON data_sources;")
    op.execute("DROP FUNCTION IF EXISTS reject_uuid_titled_source();")
