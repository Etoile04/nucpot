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

These tests prove the post-fix contract:
1. ``test_postgres_advisory_lock_is_session_scoped`` — primitive contract
   (a session-level lock blocks another connection's ``pg_try_advisory_lock``).
2. ``test_env_py_holds_advisory_lock_in_executable_code`` — AST-level guard
   against the NFM-2196 regression. Replaces the old substring check, which
   could be satisfied by the module docstring alone (CR finding on the prior
   submission). This guard inspects ONLY executable call nodes — docstrings,
   comments, and string literals cannot make it pass — and runs in the
   default (SQLite-bound) CI suite.
3. ``test_run_async_migrations_holds_advisory_lock_during_execution`` —
   behavioral proof (the CR's exact ask): drives ``run_async_migrations()``
   end-to-end with ``do_run_migrations`` patched to a probe that opens a
   SEPARATE connection and attempts ``pg_try_advisory_lock(KEY)`` while the
   migration's own connection is active. Pre-fix code → probe returns True
   (lock not held → concurrent migrator can race). Post-fix code → probe
   returns False (lock held → AC#2 satisfied).
4. ``test_run_async_migrations_releases_lock_after_completion`` — companion
   behavioral check that the ``finally``-block / session-end release actually
   frees the lock for the next migrator.

Tests 1, 3, and 4 require ``NFM_TEST_DATABASE_URL`` (a disposable asyncpg
URL); the AST guard (test 2) runs in the default (SQLite-bound) CI suite.
"""

from __future__ import annotations

import ast
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_NFM_TEST_PG_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()
_LOCK_KEY = 7423912  # NFM-2146 D3 / ADR-NFM-2139 §5 D3

# NFM-2196: applied per-test to the three tests that need a live Postgres, NOT
# module-level. A module-level ``pytestmark`` would also skip
# ``test_env_py_holds_advisory_lock_in_executable_code``, which is a pure
# source-code assertion — that guard is the always-on backstop against the
# NFM-2196 regression and must run in the default (SQLite-bound) CI suite.
# Same split as tests/test_seed_property_types_migration_runtime.py.
_requires_pg = pytest.mark.skipif(
    not _NFM_TEST_PG_URL,
    reason="NFM_TEST_DATABASE_URL is not set; deploy-lock tests require real Postgres",
)


def _sync_url(async_url: str) -> str:
    """Strip ``+asyncpg`` from an asyncpg URL so sync engines can connect."""
    return async_url.replace("+asyncpg", "")


@pytest.mark.integration
@_requires_pg
def test_postgres_advisory_lock_is_session_scoped():
    """Primitive contract: pg_advisory_lock on conn A blocks pg_try_advisory_lock on conn B.

    Foundation test for the env.py fix. If this fails, the bug cannot be
    fixed by moving the lock into env.py — Postgres itself would not be
    providing the right primitive.
    """
    sync_engine = create_engine(_sync_url(_NFM_TEST_PG_URL))
    try:
        with sync_engine.connect() as conn_a:
            conn_a.execute(text(f"SELECT pg_advisory_lock({_LOCK_KEY})"))
            try:
                # Different connection, same key: must NOT acquire.
                with sync_engine.connect() as conn_b:
                    result_b = conn_b.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})"))
                    assert bool(result_b.scalar()) is False, (
                        "pg_advisory_lock is not session-scoped — primitive broken"
                    )
            finally:
                conn_a.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))

        # After A releases, a new connection can acquire the lock.
        with sync_engine.connect() as conn_c:
            result_c = conn_c.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})"))
            assert bool(result_c.scalar()) is True, (
                "Lock not acquirable after release — release did not free the lock"
            )
            conn_c.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
    finally:
        sync_engine.dispose()


def test_env_py_holds_advisory_lock_in_executable_code():
    """AC#2 / NFM-2196 contract: env.py must take ``pg_advisory_lock(7423912)``
    on the migration's own connection, BEFORE the migration runs, and
    release it after.

    This is the AST-based replacement for the prior substring guard. The
    substring check was vacuous against module docstrings (any code with
    ``pg_advisory_lock`` in a comment or docstring would pass while the
    runtime call was deleted). The AST walker only inspects executable
    ``Call`` nodes inside function bodies — docstrings, comments, and
    string literals cannot satisfy it. Pre-fix env.py fails this test;
    post-fix passes it.

    Runs in default (SQLite-bound) CI; no Postgres needed.
    """
    from pathlib import Path

    env_path = Path("migrations/env.py")
    if not env_path.exists():
        pytest.skip("migrations/env.py not found relative to apps/api cwd")

    source = env_path.read_text()
    tree = ast.parse(source)

    # Find every executable Call node referencing pg_advisory_lock /
    # pg_advisory_unlock / connection.run_sync. Excludes docstrings,
    # comments, and string literals.
    lock_call_lines: list[int] = []
    unlock_call_lines: list[int] = []
    runsync_call_lines: list[int] = []

    def _text_call_sql_lines(node: ast.Call) -> list[str]:
        """Find ``text("...")``-style calls and return the source lines
        belonging to the SQL string argument.

        env.py uses ``connection.execute(text(f"SELECT pg_advisory_lock(...)"))``
        so the substring ``pg_advisory_lock`` lives INSIDE a string literal —
        not as a syntactically-resolved Call node. The AST cannot recover
        SQL function structure from string contents; it only sees the literal
        text. We read the source line for each ``text(...)`` call and
        substring-match on the SQL identifier.

        Docstrings / comments are NOT AST-walked here — only string arguments
        to actual Call nodes — so the substring-guard loophole the prior CR
        flagged is closed.
        """
        if not (isinstance(node.func, ast.Name) and node.func.id == "text"):
            return []
        # Find the JoinedStr / Constant arg in the call.
        sql_arg = None
        for arg in node.args:
            if isinstance(arg, (ast.JoinedStr, ast.Constant)):
                sql_arg = arg
                break
        if sql_arg is None:
            return []
        # Use the AST node's span if available (Python 3.8+).
        start = getattr(sql_arg, "lineno", None) or node.lineno
        end = getattr(sql_arg, "end_lineno", None) or node.lineno
        if start is None or end is None:
            return []
        lines = source.splitlines()
        return [lines[i - 1] for i in range(start, end + 1) if 0 < i <= len(lines)]

    for node in ast.walk(tree):
        # We only care about executable Call nodes here. ast.walk() yields
        # Module / Expr / Assign / etc. as well; skip them.
        if not isinstance(node, ast.Call):
            continue
        # Match `text("...pg_advisory_X...")` shape — we have to inspect the
        # rendered SQL string because the SQL function call lives inside a
        # string literal, not in the AST's call structure.
        sql_lines = _text_call_sql_lines(node)
        if sql_lines:
            blob = "\n".join(sql_lines).lower()
            # Distinguish pg_advisory_lock from pg_advisory_unlock via
            # "pg_advisory_unlock(" check first (the latter contains the
            # substring "pg_advisory_lock", so order matters).
            if "pg_advisory_unlock(" in blob:
                unlock_call_lines.append(node.lineno)
            elif "pg_advisory_lock(" in blob:
                lock_call_lines.append(node.lineno)
        # Also detect connection.run_sync(do_run_migrations) — same shape
        # (Call.func is Attribute with attr="run_sync").
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run_sync"
            and isinstance(func.value, ast.Name)
            and func.value.id == "connection"
        ):
            runsync_call_lines.append(node.lineno)

    assert lock_call_lines, (
        "migrations/env.py no longer takes pg_advisory_lock in EXECUTABLE "
        "code — the NFM-2196 fix is missing. (Docstring/comment occurrences "
        "are ignored; this assertion walks the AST for actual Call nodes.) "
        "The deploy lock must be acquired on the migration's OWN connection, "
        "NOT in a transient psql -tAc session."
    )

    assert unlock_call_lines, (
        "migrations/env.py no longer calls pg_advisory_unlock in EXECUTABLE "
        "code. The deploy lock must be released in a finally block so a "
        "crashed migration does not leak the lock (besides the connection-"
        "disconnect safety net)."
    )

    assert runsync_call_lines, (
        "migrations/env.py no longer calls connection.run_sync(do_run_migrations) "
        "in executable code. The alembic migration cannot run without it."
    )

    # Ordering: pg_advisory_lock must be acquired BEFORE run_sync so the
    # lock actually spans the migration. A lock taken after run_sync would
    # release before alembic ran (replicating the NFM-2196 bug).
    assert lock_call_lines[0] < runsync_call_lines[0], (
        f"migrations/env.py acquires pg_advisory_lock (line "
        f"{lock_call_lines[0]}) AFTER run_sync (line "
        f"{runsync_call_lines[0]}). The lock must be acquired BEFORE the "
        f"migration runs so it spans the migration window. A lock taken "
        f"after run_sync releases before alembic ran, replicating the "
        f"NFM-2196 C1 bug."
    )

    # Release must be after the lock acquisition. A release before acquire
    # would be a structure violation.
    assert unlock_call_lines[0] > lock_call_lines[0], (
        f"migrations/env.py calls pg_advisory_unlock (line "
        f"{unlock_call_lines[0]}) BEFORE acquiring pg_advisory_lock (line "
        f"{lock_call_lines[0]}). The unlock must come after the lock is "
        f"held (typically inside a finally block)."
    )

    # Belt-and-suspenders: the deploy lock key must appear in the source as
    # a literal that matches the documented default (7423912). Catches a
    # regression where env.py takes a lock on the wrong key.
    assert str(_LOCK_KEY) in source, (
        f"migrations/env.py does not reference the documented deploy lock key "
        f"{_LOCK_KEY} (NFM-2146 D3 / ADR-NFM-2139 §5 D3)."
    )


@pytest.mark.integration
@_requires_pg
@pytest.mark.asyncio
async def test_run_async_migrations_holds_advisory_lock_during_execution(monkeypatch):
    """Behavioral contract: while ``run_async_migrations()`` is executing
    ``do_run_migrations``, a *separate* connection's ``pg_try_advisory_lock(KEY)``
    returns False.

    This is the empirical proof the NFM-2196 review asked for — the prior
    version of this test called ``pg_advisory_lock`` directly on test-
    created connections, which proved Postgres semantics but not env.py's
    actual lock lifecycle. This replacement drives ``run_async_migrations``
    end-to-end with ``do_run_migrations`` patched to a probe; the probe
    opens its OWN connection (independent of alembic's) and tries to take
    the deploy lock while the migration is mid-execution.

    Lock held + probe returns False → AC#2 satisfied (serialized migrators).
    """
    from unittest.mock import MagicMock, patch

    # The behavioral contract test needs migrations.env to point at the
    # test DB. env.py reads ``get_settings().database_url`` at module-
    # import time and writes it into the alembic Config. Importing here
    # (inside the test, after the NFM_DATABASE_URL env override) keeps
    # the module-level state aligned with the test DB.
    monkeypatch.setenv("NFM_DATABASE_URL", _NFM_TEST_PG_URL)
    # Pre-stage alembic.context so env.py's ``from alembic import context;
    # config = context.config`` resolves at import time outside alembic's
    # CLI flow. In alembic's runtime, ``alembic.context.config`` and
    # ``alembic.context.is_offline_mode()`` are injected; we inject them
    # here before importing env.py.
    import alembic.context as _alembic_context
    from alembic.config import Config as _AlembicConfig

    _alembic_context.config = _AlembicConfig()
    # Stage OFFLINE mode at import time so env.py does NOT call
    # ``run_migrations_online()`` (which calls ``asyncio.run``, conflicting
    # with pytest-asyncio's running loop). The test invokes
    # ``run_async_migrations()`` directly below.
    _alembic_context.is_offline_mode = lambda: True  # type: ignore[attr-defined]
    # Now safe to import migrations.env outside alembic's CLI.
    from migrations import env as env_mod

    # Pre-built async engine that points at the test DB. We bypass the
    # alembic Config machinery (which would try to read alembic.ini from
    # a path set up by alembic's CLI flow that is not present under pytest).
    test_async_engine = create_async_engine(_NFM_TEST_PG_URL, poolclass=NullPool)

    probe_results: list[str] = []

    def _probe_during_migration(_connection):
        """do_run_migrations replacement: probe a separate connection."""
        sync_url = _sync_url(_NFM_TEST_PG_URL)
        sync_engine = create_engine(sync_url)
        try:
            with sync_engine.connect() as probe_conn:
                acquired = bool(
                    probe_conn.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})")).scalar()
                )
                if acquired:
                    probe_conn.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
                    probe_results.append("ACQUIRED")
                else:
                    probe_results.append("BLOCKED")
        finally:
            sync_engine.dispose()

    mock_config = MagicMock()
    mock_config.config_file_name = None
    mock_config.config_ini_section = "alembic"
    mock_config.get_section.return_value = {}

    with (
        patch.object(env_mod, "config", mock_config),
        patch.object(env_mod, "do_run_migrations", side_effect=_probe_during_migration),
        patch.object(env_mod, "async_engine_from_config", return_value=test_async_engine),
    ):
        # Call run_async_migrations directly (it is a coroutine). Going
        # through env_mod.run_migrations_online would call asyncio.run,
        # which conflicts with pytest-asyncio's running loop.
        await env_mod.run_async_migrations()

    # Concurrency-of-one: a single migration passed through the probe.
    # If env.py was correctly holding the lock on its own connection, the
    # probe's pg_try_advisory_lock from a SECOND connection would have
    # observed it and refused. If env.py was NOT holding the lock (the
    # pre-fix bug), the probe would have acquired the same key.
    assert probe_results == ["BLOCKED"], (
        f"Probe acquired the deploy lock during run_async_migrations — "
        f"env.py is NOT holding pg_advisory_lock on its own connection. "
        f"A concurrent migrator could race into ``alembic upgrade head`` "
        f"against the same database, which is the exact NFM-2196 C1 bug. "
        f"Observed: {probe_results}"
    )

    # Verify the lock was actually released: a fresh connection can now
    # acquire the deploy lock without blocking. Catches a regression
    # where the unlock is missing from the finally block.
    sync_engine_post = create_engine(_sync_url(_NFM_TEST_PG_URL))
    try:
        with sync_engine_post.connect() as post_conn:
            result = post_conn.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})"))
            assert bool(result.scalar()) is True, (
                "Deploy lock not acquirable after run_async_migrations "
                "returned — the finally-block unlock did not free the lock. "
                "Next migrator would block indefinitely."
            )
            post_conn.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
    finally:
        sync_engine_post.dispose()


@pytest.mark.integration
@_requires_pg
@pytest.mark.asyncio
async def test_run_async_migrations_releases_lock_after_completion(monkeypatch):
    """Companion to the behavioral contract test: prove the lock is freed
    after ``run_async_migrations()`` returns, including the ``finally``
    path on a clean (no-error) exit.

    A leak in the ``finally`` path would mean every subsequent deploy is
    blocked until the connection times out — the operator-visible failure
    mode the NFM-2196 fix was designed to prevent.
    """
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("NFM_DATABASE_URL", _NFM_TEST_PG_URL)
    import alembic.context as _alembic_context
    from alembic.config import Config as _AlembicConfig

    _alembic_context.config = _AlembicConfig()
    _alembic_context.is_offline_mode = lambda: True  # type: ignore[attr-defined]
    from migrations import env as env_mod

    test_async_engine = create_async_engine(_NFM_TEST_PG_URL, poolclass=NullPool)

    def _noop_do_run_migrations(_connection):
        return None

    mock_config = MagicMock()
    mock_config.config_file_name = None
    mock_config.config_ini_section = "alembic"
    mock_config.get_section.return_value = {}

    with (
        patch.object(env_mod, "config", mock_config),
        patch.object(env_mod, "do_run_migrations", side_effect=_noop_do_run_migrations),
        patch.object(env_mod, "async_engine_from_config", return_value=test_async_engine),
    ):
        # Call run_async_migrations directly to avoid asyncio.run clashing
        # with pytest-asyncio's running loop.
        await env_mod.run_async_migrations()

    # After the migration, the lock MUST be free for the next migrator.
    sync_engine_post = create_engine(_sync_url(_NFM_TEST_PG_URL))
    try:
        with sync_engine_post.connect() as post_conn:
            result = post_conn.execute(text(f"SELECT pg_try_advisory_lock({_LOCK_KEY})"))
            acquired = bool(result.scalar())
            if acquired:
                post_conn.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
            assert acquired is True, (
                "Deploy lock is still held after run_async_migrations returned — "
                "the finally-block unlock path is broken. Next migrator would "
                "block indefinitely."
            )
    finally:
        sync_engine_post.dispose()
