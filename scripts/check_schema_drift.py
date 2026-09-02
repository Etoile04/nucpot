#!/usr/bin/env python3
"""CI guard against model/migration schema drift (NFM-3372 / NFM-3377).

Runs ``alembic upgrade head`` against a clean Postgres and diffs the
resulting schema against ``Base.metadata``. Exits 0 if the schema matches,
exits 1 with a list of specific drifted tables/columns if it does not.

This catches the class of defect where a SQLAlchemy model exists but the
corresponding migration is missing or has been reverted — e.g. the
``kg_entity_types`` / ``kg_relation_types`` gap that caused the prod
``UndefinedTableError`` in NFM-3311 -> NFM-3340 -> NFM-3349 -> NFM-3364/3369/3370.

Severity classification (NFM-3446 Phase 1):

The drift guard first surfaced 253 items across 38 tables on main (job
96649637808, c3dfb4c8). Of those, 165 are docstring/index churn that
should not block CI:

  * ``modify_comment`` (96) — docstring-only diff between model and migration
  * ``remove_index`` (42) — model dropped an index, migration followed
  * ``missing_index`` (27) — model declares an index the migration never added

These categories are demoted to WARN: logged with a ``WARN:`` prefix and
non-blocking by default. The remaining 88 (missing_column, missing_table,
missing_fk, modify_nullable, modify_type, remove_column, add_constraint,
remove_fk) are real semantic drift and remain FAIL.

Pass ``--strict`` to re-enable full blocking behaviour (treat every drift
as FAIL) — useful when investigating the backlog.

Usage in CI (Postgres service container, apps/api working dir)::

    NFM_DATABASE_URL=postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db \\
        uv run python ../../scripts/check_schema_drift.py

Local smoke (assumes Postgres is already at head)::

    python scripts/check_schema_drift.py --no-apply-migrations

Failure output is greppable: every FAIL line starts with ``DRIFT:`` and
every soft-fail line starts with ``WARN:``, each followed by
``kind table (detail)``.
"""

# ruff: noqa: N818, SIM102
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "apps" / "api"
ALEMBIC_INI = API_DIR / "alembic.ini"
MIGRATIONS_DIR = API_DIR / "migrations"


class Severity(Enum):
    """Whether a drift kind should block CI or just be logged."""

    WARN = "WARN"
    FAIL = "FAIL"


# Re-export at module scope so tests and downstream importers can use
# ``from check_schema_drift import WARN, FAIL`` without importing the enum.
WARN = Severity.WARN
FAIL = Severity.FAIL


# NFM-3446 Phase 1: harmless categories that are demoted to WARN. Adding a
# new kind here MUST be intentional — Phase 2 will write the migration
# that brings it back to FAIL.
WARN_KINDS: frozenset[str] = frozenset(
    {
        "modify_comment",  # docstring diff — never blocks
        "remove_index",  # model dropped an index — intentional
        "missing_index",  # model has an index migration never added
    }
)


# Migration 070 creates three rollback-only backup tables for
# ``alembic downgrade``:
#   * ``data_sources_backup_070``
#   * ``datasets_backup_070``
#   * ``property_measurements_backup_070``
#
# These tables are not part of the SQLAlchemy metadata — they're
# transient state produced by the migration itself, dropped by
# downgrade() after restore.  Without a whitelist they surface as
# ``remove_table`` FAILs in CI on every fresh DB that has run
# migration 070 (NFM-4128 schema-drift guard regression).  The
# demotion is table-specific rather than kind-wide because
# ``remove_table`` is otherwise a hard FAIL signal (a model
# declared a table the migration dropped).
EXPECTED_TRANSIENT_TABLES: frozenset[str] = frozenset(
    {
        "data_sources_backup_070",
        "datasets_backup_070",
        "property_measurements_backup_070",
    }
)


def compute_severity(kind: str, strict: bool = False) -> Severity:
    """Map an alembic drift kind to its CI-blocking severity.

    Args:
        kind: The alembic op / drift kind label (e.g. ``"missing_column"``).
        strict: When ``True``, every kind is treated as FAIL — used by the
            ``--strict`` CLI flag so reviewers can re-investigate the full
            backlog without losing the WARN information.

    Returns:
        ``Severity.WARN`` for harmless categories (modify_comment,
        remove_index, missing_index) and ``Severity.FAIL`` for everything
        else. The default for any unknown kind is FAIL — that is the
        NFM-3372 hard rule: drift must never be silently swallowed.
    """

    if strict:
        return FAIL
    if kind in WARN_KINDS:
        return WARN
    return FAIL


def _load_base_metadata() -> MetaData:
    """Lazy import of ``nfm_db.models.Base.metadata``.

    Deferred so ``import check_schema_drift`` succeeds in environments that
    do not have the full ``nfm_db`` package installed (e.g. unit tests with
    a sqlite-only metadata).  ``apps/api/src`` is added to ``sys.path`` on
    first call.
    """
    src = str(API_DIR / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from nfm_db.models import Base  # type: ignore[import-not-found]

    return Base.metadata


@dataclass(frozen=True)
class Drift:
    """A single schema-drift finding rendered to CI logs.

    ``kind`` is one of ``"missing_table"``, ``"missing_column"``,
    ``"missing_index"``, ``"missing_fk"``, or the raw Alembic op class name
    for ops without an explicit handler.  ``table`` is the table name (or
    ``"<n/a>"`` for op kinds that have no table).  ``severity`` defaults
    to ``FAIL`` so an unknown alembic op blocks CI until a human re-
    classifies it (NFM-3372 hard rule: never silently swallow drift).
    """

    kind: str
    table: str
    detail: str = ""
    severity: Severity = FAIL

    def render(self) -> str:
        prefix = "DRIFT" if self.severity is FAIL else "WARN"
        if self.detail:
            return f"{prefix}: {self.kind} {self.table} ({self.detail})"
        return f"{prefix}: {self.kind} {self.table}"


def _iter_diffs(diffs: Sequence[Any]) -> Iterable[tuple]:
    """Flatten the (possibly nested) diff directives from ``compare_metadata``.

    ``compare_metadata`` returns a list whose elements are tuples such as
    ``("add_table", Table)`` or ``("add_column", schema, table_name, Column)``;
    multiple ``modify_*`` directives may be grouped inside a nested list.
    We walk the structure depth-first, yielding every leaf tuple so each
    one can be normalized independently.
    """
    for d in diffs:
        if isinstance(d, (list, tuple)):
            # A diff tuple starts with a string action name; anything else
            # (e.g. an empty list) is treated as a grouping wrapper.
            if len(d) and isinstance(d[0], str):
                yield d
            else:
                yield from _iter_diffs(d)


def _table_name_of_op(op: Any) -> str:
    """Best-effort extraction of the table name from any diff directive.

    Different actions use different tuple shapes:
      * ``("add_table", Table)`` / ``("remove_table", Table)`` — op[1] is the Table
      * ``("add_column", schema, table_name, column)`` — op[2] is the name
      * ``("add_index", Index)`` / ``("remove_index", Index)`` — op[1].table.name
      * ``("add_fk", ForeignKeyConstraint)`` — op[1].table.name
      * ``("modify_*", schema, table_name, column, ...)`` — op[2] is the name

    Returns ``"<n/a>"`` when no name is extractable.
    """
    if not isinstance(op, (list, tuple)) or not op:
        return "<n/a>"
    second = op[1] if len(op) >= 2 else None
    if hasattr(second, "table") and hasattr(second.table, "name"):
        return str(second.table.name)
    if hasattr(second, "name") and not isinstance(second, str):
        # Could be a Table itself (for add_table / remove_table) or a Column.
        # Skip Column — Columns have ``name`` too, but we want the table.
        if hasattr(second, "columns"):
            return str(second.name)
    if len(op) >= 3 and isinstance(op[2], str):
        return op[2]
    return "<n/a>"


def _normalize_op(op: Any) -> Iterable[Drift]:
    """Translate one diff directive into one-or-more Drift rows.

    ``op`` is one of the tuple/list shapes produced by
    ``alembic.autogenerate.compare_metadata`` — see its docstring for the
    full grammar.  Only forward-looking drift is rendered: directives
    where the *model* declares something the *migration* did not create
    (``add_table`` / ``add_column`` / ``add_index`` / ``add_fk`` /
    ``add_unique_constraint`` / ``add_pk_constraint``).  The reverse cases
    (``remove_*``) are surfaced with their raw action name (so reverse
    drift is *visible* in CI logs) but are not classified as
    forward-looking drift.

    Each Drift row carries a ``severity`` (WARN or FAIL) — see
    ``compute_severity`` for the demotion table.  Severity is computed
    from the *output* drift kind (e.g. ``missing_index``) rather than
    the alembic action label (``add_index``) so the demotion rule stays
    anchored to what reviewers see in CI logs.  NFM-3372 hard rule is
    preserved: an unknown alembic op falls through to the fallback branch
    and defaults to FAIL.

    Unknown directive shapes still produce a Drift row so the diff is
    *never* silently dropped — that is the hard constraint in NFM-3372
    ("do not silently ignore drift without CTO sign-off").
    """
    if not isinstance(op, (list, tuple)) or not op or not isinstance(op[0], str):
        # Fallback: surface the unexpected shape so it can never be
        # silently dropped.
        kind = type(op).__name__
        yield Drift(
            kind=kind,
            table="<n/a>",
            severity=compute_severity(kind),
        )
        return

    action = op[0]
    table_name = _table_name_of_op(op)

    if action == "add_table":
        table = op[1]
        cols = ", ".join(sorted(c.name for c in table.columns)) or "<no columns>"
        yield Drift(
            kind="missing_table",
            table=table.name,
            detail=cols,
            severity=compute_severity("missing_table"),
        )
        return
    if action == "add_column":
        # (action, schema, table_name, column)
        column = op[3]
        yield Drift(
            kind="missing_column",
            table=table_name,
            detail=column.name,
            severity=compute_severity("missing_column"),
        )
        return
    if action == "add_index":
        index = op[1]
        yield Drift(
            kind="missing_index",
            table=table_name,
            detail=index.name,
            severity=compute_severity("missing_index"),
        )
        return
    if action == "add_fk":
        fk = op[1]
        yield Drift(
            kind="missing_fk",
            table=table_name,
            detail=fk.name or "<unnamed>",
            severity=compute_severity("missing_fk"),
        )
        return
    if action == "add_unique_constraint":
        # (action, schema, table_name, constraint_name, ...)
        constraint_name = op[3] if len(op) >= 4 else "<unnamed>"
        yield Drift(
            kind="missing_unique_constraint",
            table=table_name,
            detail=constraint_name,
            severity=compute_severity("missing_unique_constraint"),
        )
        return
    if action == "add_pk_constraint":
        constraint_name = op[3] if len(op) >= 4 else "<unnamed>"
        yield Drift(
            kind="missing_pk_constraint",
            table=table_name,
            detail=constraint_name,
            severity=compute_severity("missing_pk_constraint"),
        )
        return

    # Fallback: surface the action so reverse-direction drift and unknown
    # shapes still appear in CI logs (never silently dropped).  Severity
    # is computed from the action name — unknown ops default to FAIL.
    #
    # Exception: ``remove_table`` for transient migration 070 backup
    # tables is demoted to WARN via ``EXPECTED_TRANSIENT_TABLES``.  See
    # the comment block above that constant for the rationale.
    if action == "remove_table" and table_name in EXPECTED_TRANSIENT_TABLES:
        severity = WARN
    else:
        severity = compute_severity(action)
    yield Drift(
        kind=action,
        table=table_name,
        severity=severity,
    )


def compute_drift(
    connection: Any,
    target_metadata: MetaData | None = None,
) -> list[Drift]:
    """Compare ``target_metadata`` against the schema present on ``connection``.

    ``connection`` is a SQLAlchemy ``Connection``. ``target_metadata`` defaults
    to ``nfm_db.models.Base.metadata`` but can be overridden by callers
    (e.g. tests).
    """
    metadata = target_metadata if target_metadata is not None else _load_base_metadata()
    context = MigrationContext.configure(connection)
    raw = compare_metadata(context, metadata)
    drifts: list[Drift] = []
    for diff in _iter_diffs(raw):
        drifts.extend(_normalize_op(diff))
    return drifts


def _sync_engine_from_url(database_url: str) -> Engine:
    """Build a sync SQLAlchemy engine for the diff path.

    ``compare_metadata`` is sync; the production URL is
    ``postgresql+asyncpg://...`` (the env.py migrator uses asyncpg), so we
    strip the ``+asyncpg`` driver suffix and use psycopg's sync driver. CI
    installs ``psycopg[binary]`` for this purpose (mirroring
    ``test-deploy-lock-pg``'s pattern in test-api.yml).
    """
    from sqlalchemy import create_engine

    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    return create_engine(sync_url, future=True)


def run_migrations(database_url: str) -> None:
    """Apply all migrations to ``database_url`` via alembic.

    Sets ``NFM_DATABASE_URL`` so ``nfm_db.config.get_settings()`` (which is
    what ``apps/api/migrations/env.py`` reads at line 41) returns our URL
    instead of the default. The deploy-lock advisory lock is still acquired
    by env.py — that is the production-correct migration path.

    Raises :class:`MigrationFailure` if alembic errors out (e.g. ``055`` in
    origin/main attempts to add a FK column to ``kg_entity_types`` before
    the table itself has been created by any migration). The caller is
    expected to surface this as a CI-blocking failure.
    """
    os.environ["NFM_DATABASE_URL"] = database_url
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url)
    try:
        alembic_command.upgrade(cfg, "head")
    except Exception as exc:
        raise MigrationFailure(str(exc)) from exc


class MigrationFailure(RuntimeError):
    """Raised when ``alembic upgrade head`` fails against the scratch DB.

    The drift check cannot proceed when the migration chain itself does
    not reach head, but the failure IS the drift signal: a model that
    declares a table the migration chain never created.  ``main()``
    catches this and exits 1 with a greppable message.
    """


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI guard against model/migration schema drift.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("NFM_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        help="Postgres URL for the scratch database (default: $NFM_DATABASE_URL).",
    )
    apply = parser.add_mutually_exclusive_group()
    apply.add_argument(
        "--apply-migrations",
        dest="apply_migrations",
        action="store_true",
        default=True,
        help="Run `alembic upgrade head` against the scratch DB before diffing (default).",
    )
    apply.add_argument(
        "--no-apply-migrations",
        dest="apply_migrations",
        action="store_false",
        help="Skip alembic upgrade (assume the DB is already at head).",
    )
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=False,
        help=(
            "Re-enable full strict mode: every drift kind — including "
            "modify_comment / remove_index / missing_index — is treated as "
            "FAIL. Default (soft-fail) demotes those harmless categories to "
            "WARN so the backlog does not block CI. See NFM-3446."
        ),
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        print(
            "ERROR: --database-url or NFM_DATABASE_URL is required",
            file=sys.stderr,
        )
        return 2

    if args.apply_migrations:
        print(f"==> alembic upgrade head against {args.database_url}")
        try:
            run_migrations(args.database_url)
        except MigrationFailure as exc:
            print(
                "FAIL: alembic upgrade head did not reach head — "
                "the migration chain itself does not match Base.metadata.",
                file=sys.stderr,
            )
            print(
                "DRIFT: migration_chain <alembic upgrade head> (see traceback below)",
                file=sys.stderr,
            )
            print(str(exc), file=sys.stderr)
            return 1

    engine = _sync_engine_from_url(args.database_url)
    try:
        with engine.connect() as conn:
            raw_drifts = compute_drift(conn)
    finally:
        engine.dispose()

    # Apply --strict: re-promote every WARN to FAIL so the script exits 1
    # even on the harmless backlog. Useful for one-off investigations.
    drifts: list[Drift] = [
        d
        if not args.strict
        else Drift(
            kind=d.kind,
            table=d.table,
            detail=d.detail,
            severity=FAIL,
        )
        for d in raw_drifts
    ]

    if not drifts:
        print("OK: Base.metadata matches alembic head")
        return 0

    warn_count = sum(1 for d in drifts if d.severity is WARN)
    fail_count = sum(1 for d in drifts if d.severity is FAIL)

    if fail_count:
        print(
            f"FAIL: {fail_count} failing drift(s), {warn_count} warning(s)",
            file=sys.stderr,
        )
    elif warn_count:
        print(
            f"WARN: {warn_count} soft-fail drift(s) (no failing drift)",
            file=sys.stderr,
        )

    # Render in FAIL-first order so reviewers can grep the most actionable
    # block first; WARN lines trail so they remain visible.
    for d in drifts:
        if d.severity is FAIL:
            print(d.render(), file=sys.stderr)
    for d in drifts:
        if d.severity is WARN:
            print(d.render(), file=sys.stderr)

    # Exit 1 only if at least one FAIL drift is present. Soft-fail mode
    # treats WARN-only output as exit 0 (the script's job is to surface
    # the warnings; CI blocking stays reserved for real semantic drift).
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
