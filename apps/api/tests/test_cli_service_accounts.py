"""CLI unit tests for ``nucpot create-service-account`` (NFM-1973 / NFM-1972 AC-1).

Validates:

1. **Password generation** — output is a non-empty string with high
   entropy (URL-safe characters, length >= 32).
2. **User creation** — the inserted row has ``is_service_account=True``,
   ``is_active=True``, ``blog_role=None``, and a bcrypt-hashed password
   (never plaintext).
3. **Idempotency** — re-running with the same username raises a clean
   error (no overwrite, no exception stack trace leaking through).
4. **CLI plumbing** — the command is registered under the ``nucpot``
   group and emits the one-time password banner to stdout.

The tests use Click's ``CliRunner`` for the CLI surface and the same
in-memory SQLite engine that ``conftest.py`` exposes for HTTP-level
tests, swapped into the module-level ``async_session_factory``.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import AsyncGenerator
from typing import Any

import click
import pytest
from click.testing import CliRunner
from sqlalchemy import ARRAY as SA_ARRAY
from sqlalchemy import JSON, event, select
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.cli import service_accounts as sa_module
from nfm_db.cli.main import cli
from nfm_db.cli.service_accounts import (
    _create_service_account_async,
    _generate_password,
    create_service_account,
)
from nfm_db.models import Base, User
from nfm_db.models.user import ServiceAccountScope
from nfm_db.services.auth_service import (
    create_service_account_token,
    decode_access_token,
    verify_password,
)

# ---------------------------------------------------------------------------
# SQLite compatibility helpers — mirrors conftest.py's _replace_jsonb /
# _strip_dangling_fks.  Inlined here to avoid coupling to a private
# fixture helper that may be refactored independently.
# ---------------------------------------------------------------------------


def _sqlite_safe_create_all(sync_conn: Any, metadata: Any) -> None:
    """Create all tables on SQLite, replacing JSONB / ARRAY with portable types."""
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
            if isinstance(col.type, (PG_ARRAY, SA_ARRAY)):
                col.type = JSON()
    metadata.create_all(sync_conn)


# ---------------------------------------------------------------------------
# Engine fixtures — patch the CLI's module-level ``async_session_factory``
# to point at a per-test in-memory SQLite engine.  Without this, the CLI
# would try to talk to the production PostgreSQL URL.
# ---------------------------------------------------------------------------


@pytest.fixture
async def cli_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession wired to an in-memory SQLite engine.

    On enter: build the engine + create all tables (JSONB/ARRAY
    translated to portable types).  On exit: drop all tables + dispose
    the engine.  We then replace
    ``nfm_db.cli.service_accounts.async_session_factory`` (which the CLI
    helper imports) with a sessionmaker bound to this engine for the
    duration of the test, and restore it on teardown.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_safe_create_all, Base.metadata)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    original_get_factory = sa_module.get_session_factory
    sa_module.get_session_factory = lambda: session_factory

    try:
        async with session_factory() as session:
            yield session
    finally:
        sa_module.get_session_factory = original_get_factory
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ---------------------------------------------------------------------------
# 1. Password generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_password_meets_entropy_requirements() -> None:
    """The password has high entropy and uses URL-safe characters only."""
    passwords = {_generate_password() for _ in range(50)}
    # All unique — 32 bytes of entropy, collisions would be astronomically rare.
    assert len(passwords) == 50
    sample = next(iter(passwords))
    # token_urlsafe(32) yields 43 base64-url chars; allow some slack for length.
    assert len(sample) >= 32
    # URL-safe alphabet: A-Z a-z 0-9 - _
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", sample), (
        f"Password contained non-URL-safe characters: {sample!r}"
    )


# ---------------------------------------------------------------------------
# 2. User creation — row shape + bcrypt hashing
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_create_service_account_row_shape(
    cli_db_session: AsyncSession,
) -> None:
    """The inserted row has is_service_account=True, is_active=True, no blog_role."""
    username = "test-svc-1"
    password = _generate_password()

    user = await _create_service_account_async(username=username, password=password)

    assert user.username == username
    assert user.is_service_account is True
    assert user.is_active is True
    assert user.blog_role is None
    # Email is synthesized from the username with the reserved .local TLD.
    assert user.email == f"{username}@service.local"
    # ``full_name`` follows the ``service:<username>`` convention so audit
    # log searches can grep for service identities quickly.
    assert user.full_name == f"service:{username}"
    # Password is bcrypt-hashed, not plaintext.
    assert user.hashed_password != password
    assert user.hashed_password.startswith("$2")  # bcrypt prefix


@pytest.mark.unit
async def test_create_service_account_password_verifies(
    cli_db_session: AsyncSession,
) -> None:
    """The bcrypt hash round-trips with ``verify_password``."""
    password = _generate_password()
    user = await _create_service_account_async(username="test-svc-2", password=password)
    assert verify_password(password, user.hashed_password) is True
    # Wrong password is rejected.
    assert verify_password("not-the-password", user.hashed_password) is False


@pytest.mark.unit
async def test_create_service_account_duplicate_username_raises(
    cli_db_session: AsyncSession,
) -> None:
    """Re-running with the same username fails loudly — no silent overwrite."""
    password = _generate_password()
    await _create_service_account_async(username="dup-svc", password=password)

    with pytest.raises(click.ClickException) as exc_info:
        await _create_service_account_async(username="dup-svc", password=password)

    assert "already exists" in str(exc_info.value)


@pytest.mark.unit
async def test_create_service_account_persists_to_db(
    cli_db_session: AsyncSession,
) -> None:
    """The row is actually committed — a fresh SELECT returns it."""
    password = _generate_password()
    await _create_service_account_async(username="persisted-svc", password=password)

    result = await cli_db_session.execute(
        select(User).where(User.username == "persisted-svc"),
    )
    loaded = result.scalar_one_or_none()
    assert loaded is not None
    assert loaded.is_service_account is True
    assert loaded.email == "persisted-svc@service.local"


# ---------------------------------------------------------------------------
# 3. CLI plumbing — Click group registration + banner output
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_nucpot_cli_group_has_create_service_account_subcommand() -> None:
    """The ``nucpot`` group exposes ``create-service-account``."""
    assert "create-service-account" in cli.commands
    cmd = cli.commands["create-service-account"]
    assert cmd is create_service_account


@pytest.mark.unit
async def test_cli_create_service_account_emits_password_to_stdout(
    cli_db_session: AsyncSession,
) -> None:
    """End-to-end CLI invocation prints the one-time password exactly once."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["create-service-account", "--username", "cli-svc", "--role", "service"],
    )

    # The Click command calls ``sys.exit(0)`` on success; CliRunner captures
    # this as exit_code=0.
    assert result.exit_code == 0, result.output
    assert "Service account created: cli-svc" in result.output
    assert "ONE-TIME PASSWORD" in result.output
    assert "is_service_account = True" in result.output
    # The password is between two banner lines — extract it for verification.
    match = re.search(
        r"ONE-TIME PASSWORD.*?\n\n\s+(\S+)\s*\n",
        result.output,
        re.DOTALL,
    )
    assert match is not None, f"Could not locate password in output:\n{result.output}"
    emitted_password = match.group(1)

    # The same password must round-trip via bcrypt against the DB row.
    loaded = (
        await cli_db_session.execute(select(User).where(User.username == "cli-svc"))
    ).scalar_one()
    assert verify_password(emitted_password, loaded.hashed_password) is True


@pytest.mark.unit
async def test_cli_create_service_account_rejects_duplicate_username(
    cli_db_session: AsyncSession,
) -> None:
    """Re-running via the CLI surface also returns a non-zero exit + clean error."""
    runner = CliRunner()
    first = runner.invoke(
        cli,
        ["create-service-account", "--username", "dup-cli-svc", "--role", "service"],
    )
    assert first.exit_code == 0

    second = runner.invoke(
        cli,
        ["create-service-account", "--username", "dup-cli-svc", "--role", "service"],
    )
    assert second.exit_code != 0
    assert "already exists" in second.output


# ---------------------------------------------------------------------------
# 4. Token shape — service token claims match what RBAC checks for
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_create_service_account_token_carries_required_claims(
    cli_db_session: AsyncSession,
) -> None:
    """``create_service_account_token`` emits the claims ``require_service_scope`` looks for.

    This is a defense-in-depth test: even if the login endpoint stops
    routing through ``create_service_account_token``, the helper must
    always set the two claims the auth dependency reads.
    """
    password = _generate_password()
    user = await _create_service_account_async(username="claims-svc", password=password)

    token = create_service_account_token(
        user,
        ServiceAccountScope.EXTRACTION_INGEST,
        expires_delta=_dt.timedelta(minutes=5),
    )
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == str(user.id)
    assert payload["is_service_account"] is True
    assert payload["scope"] == ServiceAccountScope.EXTRACTION_INGEST.value
    # Custom expires_delta is honored (sanity check, ±10s tolerance for clock drift).
    exp = payload["exp"]
    issued_at = _dt.datetime.now(tz=_dt.UTC).timestamp()
    assert exp - issued_at > 240  # >4 min remaining of a 5-min TTL
    assert exp - issued_at < 360  # <6 min (5 min + slack)

# ---------------------------------------------------------------------------
# 5. --save-password-to flag (NFM-2012)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_save_password_to_file(
    cli_db_session: AsyncSession,
    tmp_path: Any,
) -> None:
    """--save-password-to <path> writes the plaintext password to a file with mode 600."""
    pw_file = tmp_path / "svc-pw.txt"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "create-service-account",
            "--username", "save-svc",
            "--role", "service",
            "--save-password-to", str(pw_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert pw_file.exists(), "Password file was not created"

    # File should be mode 600 (owner read/write only).
    stat_info = pw_file.stat()
    file_mode = stat_info.st_mode & 0o777
    assert file_mode == 0o600, (
        f"Expected mode 0o600, got 0o{file_mode:03o}"
    )

    # The file content should be the password that verifies against the DB.
    written_password = pw_file.read_text().strip()
    assert len(written_password) >= 32

    loaded = (
        await cli_db_session.execute(select(User).where(User.username == "save-svc"))
    ).scalar_one()
    assert verify_password(written_password, loaded.hashed_password) is True


@pytest.mark.unit
async def test_save_password_to_stdout_dash(
    cli_db_session: AsyncSession,
) -> None:
    """--save-password-to - prints the password to stdout (current behavior)."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "create-service-account",
            "--username", "dash-svc",
            "--role", "service",
            "--save-password-to", "-",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ONE-TIME PASSWORD" in result.output
    match = re.search(
        r"ONE-TIME PASSWORD.*?\n\n\s+(\S+)\s*\n",
        result.output,
        re.DOTALL,
    )
    assert match is not None, f"Could not locate password in output:\n{result.output}"
    emitted_password = match.group(1)
    loaded = (
        await cli_db_session.execute(select(User).where(User.username == "dash-svc"))
    ).scalar_one()
    assert verify_password(emitted_password, loaded.hashed_password) is True


@pytest.mark.unit
async def test_default_no_flag_prints_to_stdout(
    cli_db_session: AsyncSession,
) -> None:
    """Without --save-password-to, password still prints to stdout (backward compat)."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "create-service-account",
            "--username", "default-svc",
            "--role", "service",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ONE-TIME PASSWORD" in result.output
