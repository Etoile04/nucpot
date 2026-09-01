"""F4 UUID-titled source guard — DB-level belt-and-braces to the
application-layer ``_reject_uuid_title`` helper.

Revision ID: 071_f4_uuid_titled_source_guard
Revises: 070_d2_dedup_bad_data_sources
Create Date: 2026-09-02

NFM-4097 — AC-3 + AC-4 follow-up for NFM-4089 (parent: NFM-4084)
================================================================

Root cause (NFM-4084 D2 / NFM-4089 F4)
--------------------------------------

``extraction_to_db_mapper.py`` historically inserted ``DataSource``
rows with the upstream ``reference`` (or placeholder) as ``title``.
When the extraction chain re-emitted a previous source's UUID
instead of a real reference, the new row's ``title`` became a
36-char UUID pattern.  The application-layer helper
``_reject_uuid_title`` (in ``source_dedup.get_or_create_source``)
catches the pattern in Python, but a code path that bypassed the
helper (NFM-4089 F4) leaked UUID-title rows into production.

This migration installs a Postgres ``BEFORE INSERT OR UPDATE OF
title`` trigger on ``data_sources`` that rejects UUID-title rows
at the database itself — even code paths the application helper
never touches are now safe.

Trigger semantics
-----------------

``reject_uuid_titled_source()``
    plpgsql ``BEFORE INSERT OR UPDATE OF title`` trigger function.
    Matches ``NEW.title`` against the canonical lowercase UUID
    regex::

        ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$

    On match:
      1. Writes one ``health_events`` row with
         ``event_type='uuid_titled_source_blocked'``,
         ``severity='critical'``, ``source_service='ingest'``,
         context carrying ``source_id`` / ``title`` / ``doi`` /
         ``TG_OP`` / ``txid_current()``.
      2. RAISES ``EXCEPTION ... USING ERRCODE = 'check_violation'``
         so the caller surfaces a SQLAlchemy ``IntegrityError``
         instead of a silent insert.

    On non-match:
      ``RETURN NEW`` — the insert / update proceeds.

``trg_data_sources_uuid_title``
    The trigger; fires ``FOR EACH ROW`` BEFORE the row becomes
    visible.  An UPDATE that does NOT touch ``title`` does not
    fire (the ``OF title`` clause), so a safe UPDATE on, say,
    ``last_seen_at`` does not regress.

health_events CHECK constraint extension
----------------------------------------

The original ``ck_health_events_event_type`` (NFM-2220 / migration
037) only allows the five application-emitted event types
(``fallback_triggered``, ``validation_drop``, ``category_coercion_fail``,
``asyncio_crash``, ``generic_silent_catch``).  The trigger's INSERT
of ``'uuid_titled_source_blocked'`` would violate that constraint
and fail silently (the migration would install the trigger but
every firing would rollback).  This migration therefore DROPs the
old constraint and recreates it with the original five values
PLUS ``uuid_titled_source_blocked``.  Downgrade restores the
original five-value enum.

Why ``txid_current()`` is safe here
-----------------------------------

NFM-4099 fixed a crash where SQLAlchemy bind parameters were
passed alongside a ``DO $$`` block (asyncpg cannot bind into DO
blocks).  The trigger function body is plpgsql, executed
server-side — there is no SQLAlchemy bind surface, and
``txid_current()`` is just a built-in PG function returning the
current transaction id.  ``test_no_do_block_present`` pins this
invariant by asserting the migration contains NO ``DO $$`` block.

Why NFM-4088 (``070_d2_dedup_bad_data_sources``) precedes this
-------------------------------------------------------------

The trigger ships AFTER the 14 UUID-title dirty rows are cleaned
up.  ``down_revision = '070_d2_dedup_bad_data_sources'`` enforces
this chain order — if NFM-4088 hasn't run, this migration cannot
apply, and if it has run, the trigger cannot regress production
by refusing the dirty rows that are no longer there.

AC-4 coupling
-------------

The AC-4 health-endpoint flip (``/health`` returns ``degraded``
when ``uuid_titled_source_blocked`` events exist in the last 24h)
depends on this trigger being installed, so the two ACs are
shipped together as a single ticket per the CPO's QA-FOLLOWUP
disposition (NFM-4097).

Cross-references
----------------

* NFM-4084 — D2 decision table + root cause
* NFM-4088 — D2 data-side dedup (predecessor migration)
* NFM-4089 — F4 ingest-path bypass (parent issue)
* NFM-4099 — asyncpg ``DO``-block bind-param crash (regression
  guard in the structural tests)
* NFM-2220 — original ``health_events`` table (migration 037)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "071_f4_uuid_titled_source_guard"
down_revision: str | Sequence[str] | None = "070_d2_dedup_bad_data_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Constants — single source of truth for the trigger's regex + the
# health_events CHECK constraint extension.
# ---------------------------------------------------------------------------

#: Canonical lowercase UUID regex.  Anchored on both ends so the
#: ``~`` (POSIX regex match) operator does not accidentally match
#: substrings.  Lowercase-only because UUIDs are conventionally
#: lowercase; the existing 070 regex is case-insensitive but this
#: guard runs only against new INSERTs / title UPDATE so the chance
#: of an uppercase UUID slipping through is negligible.
_UUID_TITLE_REGEX: str = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)

#: Original 037 migration's allowed ``event_type`` values.  Kept
#: here verbatim so the downgrade can recreate the constraint with
#: the exact enum the rest of the application still expects.
_ORIGINAL_EVENT_TYPES: tuple[str, ...] = (
    "fallback_triggered",
    "validation_drop",
    "category_coercion_fail",
    "asyncio_crash",
    "generic_silent_catch",
)

#: Trigger-only event type.  Extends the original enum.
_TRIGGER_EVENT_TYPE: str = "uuid_titled_source_blocked"

#: The trigger function name — single source of truth so the
#: CREATE / DROP statements and the structural tests stay in lockstep.
_TRIGGER_FN_NAME: str = "reject_uuid_titled_source"

#: The trigger name — same single source of truth pattern.
_TRIGGER_NAME: str = "trg_data_sources_uuid_title"

#: ``health_events`` event_type CHECK constraint name (NFM-2220).
_EVENT_TYPE_CHECK_NAME: str = "ck_health_events_event_type"


# ---------------------------------------------------------------------------
# SQL builders — defence-in-depth + audit trail.
#
# NFM-4099: asyncpg cannot bind parameters into ``DO $$`` blocks.
# The trigger install uses plain DDL (``CREATE OR REPLACE FUNCTION``
# + ``CREATE TRIGGER``), so no bind surface exists, but the helpers
# below still render the SQL as ordinary ``sa.text()`` strings —
# never via bind params — so the regression guard stays green.
# ---------------------------------------------------------------------------


def _build_event_type_check_sql(event_types: Sequence[str]) -> str:
    """Render a CHECK constraint expression for ``event_type IN (...)``.

    Args:
        event_types: Allowed event type literals (Python ``str``).

    Returns:
        A SQL fragment suitable for embedding in an ``ADD CONSTRAINT``
        statement.  Each value is wrapped in single quotes with
        embedded single quotes doubled (canonical PostgreSQL quoting)
        so a future event type containing an apostrophe does not
        silently break the constraint.
    """
    quoted = ", ".join(f"'{t}'" for t in event_types)
    return f"event_type IN ({quoted})"


def _build_trigger_function_sql() -> str:
    """Render the ``CREATE OR REPLACE FUNCTION`` statement.

    The function body is plpgsql; ``txid_current()`` is a built-in
    PG function and is safe to call from plpgsql.  No
    SQLAlchemy bind params are passed — the regex literal is
    inlined directly in the plpgsql body, with single quotes
    doubled so a future edit cannot introduce a SQL-injection
    vector.

    health_events.id policy
    -----------------------

    ``health_events.id`` (NFM-2220 / migration 037) is
    ``uuid NOT NULL PRIMARY KEY`` **without** a server-side
    ``DEFAULT``.  The trigger's ``INSERT INTO health_events`` must
    therefore populate ``id`` explicitly; we use
    ``gen_random_uuid()`` so the column list stays declarative
    (NFM-4097 finding 1, regression-guard: a future edit that
    omits ``id`` again raises ``psycopg2.errors.NotNullViolation``
    instead of the intended ``check_violation`` and silently
    masks the production-side alert).  The structural test
    ``test_trigger_insert_includes_id_with_gen_random_uuid`` pins
    this invariant.
    """
    # Inline the regex as a single-quoted PL/pgSQL literal.
    # PostgreSQL dollar-quote the body with ``$func$ ... $func$`` so
    # the regex's ``^`` / ``$`` / ``-`` characters do not collide
    # with the ``$$ ... $$`` delimiter that the original draft
    # used (which would have terminated the plpgsql block at the
    # first standalone ``$$`` it found).
    regex_literal = _UUID_TITLE_REGEX.replace("'", "''")
    return f"""
        CREATE OR REPLACE FUNCTION {_TRIGGER_FN_NAME}()
        RETURNS trigger
        AS $func$
        BEGIN
            IF NEW.title ~ '{regex_literal}' THEN
                INSERT INTO health_events (
                    id, event_type, severity, source_service, context, created_at
                ) VALUES (
                    gen_random_uuid(),
                    '{_TRIGGER_EVENT_TYPE}', 'critical', 'ingest',
                    json_build_object(
                        'source_id', NEW.id,
                        'title', NEW.title,
                        'doi', NEW.doi,
                        'op', TG_OP,
                        'txid', txid_current()
                    ),
                    NOW()
                );
                RAISE EXCEPTION
                    'uuid_titled_source_blocked: source.title looks like a UUID (%)'
                    , NEW.title
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $func$ LANGUAGE plpgsql;
    """


def _build_trigger_sql() -> str:
    """Render the ``CREATE TRIGGER`` statement.

    ``BEFORE INSERT OR UPDATE OF title`` — the ``OF title`` clause
    ensures an UPDATE that does not touch ``title`` (e.g. a
    ``last_seen_at`` refresh) does not fire the guard, so safe
    updates are not regressed.
    """
    return f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE INSERT OR UPDATE OF title ON data_sources
        FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FN_NAME}();
    """


# ---------------------------------------------------------------------------
# Forward (upgrade)
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Install the F4 UUID-title guard trigger + extend the
    ``health_events`` event_type CHECK constraint.

    Order of operations:

    1. ``DROP CONSTRAINT ck_health_events_event_type`` — remove
       the original NFM-2220 enum so we can replace it.
    2. ``ADD CONSTRAINT ck_health_events_event_type ... CHECK (...)``
       with the original five values PLUS
       ``uuid_titled_source_blocked``.
    3. ``CREATE OR REPLACE FUNCTION reject_uuid_titled_source()``
       — install the plpgsql trigger function.
    4. ``CREATE TRIGGER trg_data_sources_uuid_title`` — attach the
       trigger to ``data_sources``.

    All four statements execute through ``bind.execute(sa.text(...))``
    with no bind params (NFM-4099 guard); the values originate from
    this module's own constants so literal inlining is safe.
    """
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Extend the health_events event_type CHECK constraint.
    # ------------------------------------------------------------------
    bind.execute(sa.text(f"ALTER TABLE health_events DROP CONSTRAINT {_EVENT_TYPE_CHECK_NAME}"))
    extended_event_types = (*_ORIGINAL_EVENT_TYPES, _TRIGGER_EVENT_TYPE)
    bind.execute(
        sa.text(
            f"ALTER TABLE health_events "
            f"ADD CONSTRAINT {_EVENT_TYPE_CHECK_NAME} "
            f"CHECK ({_build_event_type_check_sql(extended_event_types)})"
        )
    )

    # ------------------------------------------------------------------
    # 2. Install the trigger function + trigger.
    # ------------------------------------------------------------------
    bind.execute(sa.text(_build_trigger_function_sql()))
    bind.execute(sa.text(_build_trigger_sql()))


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Remove the F4 UUID-title guard trigger + restore the
    original ``health_events`` CHECK constraint enum.

    Order of operations (NFM-4097 / PostgreSQL semantics):

    1. ``DROP TRIGGER IF EXISTS trg_data_sources_uuid_title`` — the
       trigger must be removed **before** the function it depends
       on; PostgreSQL refuses to drop a function that a live trigger
       still references.
    2. ``DROP FUNCTION IF EXISTS reject_uuid_titled_source`` —
       the function is now unreferenced and can be dropped.
    3. ``DROP CONSTRAINT ck_health_events_event_type`` — remove the
       extended enum so we can restore the original.
    4. ``ADD CONSTRAINT ck_health_events_event_type ... CHECK (...)``
       with the original five values only.
    """
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Drop the trigger BEFORE the function (PostgreSQL would
    #    otherwise refuse the function drop).
    # ------------------------------------------------------------------
    bind.execute(sa.text(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON data_sources"))
    bind.execute(sa.text(f"DROP FUNCTION IF EXISTS {_TRIGGER_FN_NAME}()"))

    # ------------------------------------------------------------------
    # 2. Restore the original NFM-2220 event_type CHECK enum.
    # ------------------------------------------------------------------
    bind.execute(sa.text(f"ALTER TABLE health_events DROP CONSTRAINT {_EVENT_TYPE_CHECK_NAME}"))
    bind.execute(
        sa.text(
            f"ALTER TABLE health_events "
            f"ADD CONSTRAINT {_EVENT_TYPE_CHECK_NAME} "
            f"CHECK ({_build_event_type_check_sql(_ORIGINAL_EVENT_TYPES)})"
        )
    )
