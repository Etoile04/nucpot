"""Alembic env.py configured for async SQLAlchemy with autogenerate.

NFM-2146 D3 / ADR-NFM-2139 §5 D3 / NFM-2196
============================================
The ``run_async_migrations`` coroutine acquires a Postgres advisory
lock on the SAME async connection that drives ``do_run_migrations``.
This is the canonical fix for the C1 bug the NFM-2196 review flagged:
the pre-fix implementation acquired the lock in a transient ``psql -tAc``
session that exited immediately, releasing the session-level lock
before alembic started. Two concurrent deploys would both pass the
acquire check and race into ``upgrade head`` against the same database.

By taking ``pg_advisory_lock(KEY)`` on the migration's own connection
(``async with connectable.connect() as connection:``), the lock spans
the entire migration and is auto-released on disconnect even if the
process is killed (``session_end`` semantics on a NullPool connection).
A concurrent migrator calling ``pg_try_advisory_lock(KEY)`` from a
different connection observes the lock and refuses to proceed.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from nfm_db.config import get_settings
from nfm_db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the app's database URL from settings
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# NFM-2146 D3 / NFM-2196: deploy lock key. Same default as prod_migrate.sh
# (7423912). Override via the ``NFMD_DEPLOY_LOCK_KEY`` env var.
NFMD_DEPLOY_LOCK_KEY = int(os.environ.get("NFMD_DEPLOY_LOCK_KEY", "7423912"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    """Run migrations using the provided connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine.

    Acquires ``pg_advisory_lock(NFMD_DEPLOY_LOCK_KEY)`` on the migration
    connection BEFORE invoking ``do_run_migrations``, so concurrent
    migrators (parallel CI, cron + manual ssh) cannot race into
    ``alembic upgrade head`` against the same database. The lock is
    released in a ``finally`` block on the same connection; if the
    process is killed mid-migration, the connection's session-end
    release is the safety net (NullPool disposes immediately on exit).
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # NFM-2146 D3 / NFM-2196: deploy lock must live on THIS connection,
        # not in a transient session that releases it on exit. See module
        # docstring for the C1 bug this closes.
        #
        # NFM-2782: SQLAlchemy's async connection autobegins a transaction
        # on first use. The ``SELECT pg_advisory_lock(...)`` call runs in
        # that autobegun transaction. When ``connection.run_sync`` then
        # hands control to ``do_run_migrations``, alembic's
        # ``context.begin_transaction()`` sees the connection already has
        # an active autobegun transaction and returns ``nullcontext()``
        # (no real transaction demarcation). The migration DDL runs in
        # the autobegun transaction, but alembic never issues a COMMIT —
        # the autobegun transaction is rolled back when the ``async with``
        # block exits and the connection closes, dropping every DDL
        # statement and the alembic_version update.
        # Symptom: alembic logs "Running upgrade N -> N+1" with rc=0, but
        # ``alembic_version.version_num`` and all schema changes are lost.
        # Fix: explicitly COMMIT after run_sync so the autobegun transaction
        # that contains the DDL actually persists. The advisory lock is
        # session-scoped and survives the COMMIT.
        await connection.execute(text(f"SELECT pg_advisory_lock({NFMD_DEPLOY_LOCK_KEY})"))
        try:
            await connection.run_sync(do_run_migrations)
            # NFM-2782: see comment block above. The autobegun transaction
            # must be committed explicitly because alembic's begin_transaction
            # returned nullcontext() when it saw the autobegun tx already
            # active, leaving no commit point for the DDL.
            await connection.commit()
        finally:
            # Best-effort release; session-end release is the real safety
            # net for a crashed migration. ``pg_advisory_unlock`` from a
            # different connection returns f, so the call site must be on
            # the same connection that took the lock.
            # If the transaction is already aborted (e.g. migration DDL
            # failed), the unlock must be issued in a fresh savepoint or
            # the advisory lock will auto-release on connection close
            # (NullPool dispose). Suppress the unlock error to avoid
            # masking the original migration failure.
            try:
                await connection.execute(text(f"SELECT pg_advisory_unlock({NFMD_DEPLOY_LOCK_KEY})"))
            except Exception:
                pass  # Lock auto-releases on connection close

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (async)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
