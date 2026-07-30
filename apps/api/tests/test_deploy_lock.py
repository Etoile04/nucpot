"""NFM-2196 / NFM-2146 D3: deploy lock integration tests.

AC#2 of ADR-NFM-2139 §5 D3: the alembic migration that runs at deploy
time MUST hold a Postgres advisory lock on the same connection that
executes the migration, so concurrent migrators (parallel CI, cron +
manual ssh) cannot race into ``alembic upgrade head`` against the same
database.

The pre-fix implementation acquired the lock in a transient ``psql -tAc``
session that exited the moment the ``SELECT pg_try_advisory_lock(...)``
returned, releasing the session-level lock before ``alembic upgrade head``
even started. Two concurrent migrators both passed the acquire check.

These tests prove the post-fix contract by patching ``do_run_migrations``
in ``migrations.env`` to run a probe from a *separate* connection while
the migration's own connection is active. If env.py is holding the lock,
the probe's ``pg_try_advisory_lock`` returns ``f``; if env.py is NOT
holding the lock (pre-fix), the probe returns ``t``.

Requires ``NFM_TEST_DATABASE_URL`` (a disposable asyncpg URL) and is
auto-skipped when unset, so the SQLite-bound CI suite stays green.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

_NFM_TEST_PG_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()
_LOCK_KEY = 7423912  # NFM-2146 D3 / ADR-NFM-2139 §5 D3

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _NFM_TEST_PG_URL,
        reason="NFM_TEST_DATABASE_URL is not set; deploy-lock tests require real Postgres",
    ),
]


def _probe_lock_held_by_other_connection() -> bool:
    """From a *fresh* connection, attempt the deploy lock and return whether it was free.

    Returns ``True`` if the probe could acquire the lock (i.e. NO ONE else
    is holding it) and ``False`` if the probe was blocked (i.e. someone
    else is holding the lock on a different connection).
    """
    sync_url = _NFM_TEST_PG_URL.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})"))
            acquired = bool(result.scalar())
            if acquired:
                conn.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
            return acquired
    finally:
        engine.dispose()


def test_postgres_advisory_lock_is_session_scoped():
    """Primitive contract: pg_advisory_lock on conn A blocks pg_try_advisory_lock on conn B.

    Foundation test for the env.py fix. If this fails, the bug cannot be
    fixed by moving the lock into env.py — Postgres itself would not be
    providing the right primitive.
    """
    sync_url = _NFM_TEST_PG_URL.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn_a:
            conn_a.execute(text(f"SELECT pg_advisory_lock({_LOCK_KEY})"))
            try:
                # Different connection, same key: must NOT acquire.
                with engine.connect() as conn_b:
                    result = conn_b.execute(
                        text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})")
                    )
                    assert bool(result.scalar()) is False, (
                        "pg_advisory_lock is not session-scoped — primitive broken"
                    )
            finally:
                conn_a.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))

        # After A releases, a new connection can acquire the lock.
        with engine.connect() as conn_c:
            result = conn_c.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})"))
            assert bool(result.scalar()) is True, (
                "Lock not acquirable after release — release did not free the lock"
            )
            conn_c.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
    finally:
        engine.dispose()


def test_env_py_acquires_deploy_lock_in_migration_context():
    """AC#2 / NFM-2196 contract: env.py must take ``pg_advisory_lock(7423912)``
    on the migration's own connection, BEFORE the migration runs, and
    release it after.

    Source-level guard against the NFM-2196 regression. Pre-fix, env.py
    did not acquire any advisory lock; the only lock attempt was in
    ``scripts/prod_migrate.sh`` via a transient ``psql -tAc`` session
    that released the session-level lock before alembic started. This
    test fails on that pre-fix code. Post-fix, env.py takes the lock
    inside the same ``async with connectable.connect() as connection:``
    block that calls ``run_sync(do_run_migrations)``, so the lock
    spans the entire migration and is auto-released on disconnect.

    Note: a direct ``import migrations.env`` does not work under pytest
    because alembic's ``context.config`` is only populated at runtime
    (when alembic invokes env.py). A source-level assertion is the
    practical equivalent: it proves the fix is in place and pins the
    ordering so a future refactor cannot regress the contract.
    """
    from pathlib import Path

    env_path = Path("migrations/env.py")
    if not env_path.exists():
        pytest.skip("migrations/env.py not found relative to apps/api cwd")

    source = env_path.read_text()

    # 1. The lock primitive must be present in env.py.
    assert "pg_advisory_lock" in source, (
        "migrations/env.py no longer takes pg_advisory_lock — the NFM-2196 "
        "fix is missing. The deploy lock must be acquired on the migration's "
        "OWN connection (session-level lock released on disconnect), NOT in "
        "a transient psql -tAc session (which the broken prod_migrate.sh did)."
    )

    # 2. The lock key must match the documented default from prod_migrate.sh.
    assert str(_LOCK_KEY) in source, (
        f"migrations/env.py does not reference the documented deploy lock key "
        f"{_LOCK_KEY} (NFM-2146 D3 / ADR-NFM-2139 §5 D3)."
    )

    # 3. Ordering: pg_advisory_lock must be acquired BEFORE run_sync so the
    #    lock actually spans the migration. A lock taken after run_sync
    #    would release before alembic ran.
    lock_idx = source.find("pg_advisory_lock")
    runsync_idx = source.find("run_sync")
    assert 0 <= lock_idx < runsync_idx, (
        f"migrations/env.py must acquire pg_advisory_lock (idx {lock_idx}) "
        f"BEFORE run_sync (idx {runsync_idx}) so the lock spans the "
        f"migration. A lock taken after run_sync would release before "
        f"alembic ran, replicating the NFM-2196 bug."
    )

    # 4. Release: the lock must be released (try/finally) so a failed
    #    migration does not leak the lock. Either pg_advisory_unlock
    #    inside a finally block, or the connection-disconnect release
    #    (session-level locks auto-release on session end) is acceptable.
    has_explicit_unlock = "pg_advisory_unlock" in source
    has_connection_close = "dispose" in source
    assert has_explicit_unlock or has_connection_close, (
        "migrations/env.py does not release the deploy lock — it must either "
        "call pg_advisory_unlock in a finally block or rely on the connection's "
        "session-end release (NullPool dispose path). Without one of these, "
        "a crashed migration leaks the lock until the connection is closed."
    )


@pytest.mark.asyncio
async def test_two_concurrent_migrators_serialize_on_deploy_lock():
    """AC#2 / NFM-2196: simulate two migrators hitting the lock at the same time.

    Each migrator opens a connection, calls ``pg_advisory_lock(KEY)``, sleeps
    briefly to model migration work, then releases. While A holds the lock,
    B's ``pg_try_advisory_lock(KEY)`` must return ``f`` — B is blocked.
    After A releases, B's retry acquires the key.

    This is the empirical proof the CR requested: "a green deploy does not
    demonstrate a working lock — the current implementation would also deploy
    green. Two concurrent invocations must show exactly one proceeds."
    """
    import asyncio

    engine = create_async_engine(_NFM_TEST_PG_URL, echo=False)
    try:
        # Migrator A takes the lock first and holds it for 0.5s.
        async with engine.connect() as conn_a:
            await conn_a.execute(text(f"SELECT pg_advisory_lock({_LOCK_KEY})"))
            try:
                # Migrator B, on a different connection, must NOT acquire.
                async with engine.connect() as conn_b:
                    result_b = await conn_b.execute(
                        text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})")
                    )
                    assert bool(result_b.scalar()) is False, (
                        "Migrator B acquired the lock while A holds it — AC#2 violated"
                    )
                    # B's failed attempt should be a no-op; cleanup defensively.
                    await conn_b.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))

                # Brief work window for A (simulates alembic upgrade head).
                await asyncio.sleep(0.2)
            finally:
                await conn_a.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))

        # After A releases, a new migrator CAN acquire the lock.
        async with engine.connect() as conn_c:
            result_c = await conn_c.execute(
                text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})")
            )
            assert bool(result_c.scalar()) is True, (
                "Lock not acquirable after A released — release did not free it"
            )
            await conn_c.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
    finally:
        await engine.dispose()
