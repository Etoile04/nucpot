"""``nucpot create-service-account`` Click command (NFM-1973 / NFM-1972 AC-1).

Provisions a new ``User`` row with ``is_service_account=True`` and emits
a one-time password.  The password is **never** logged or re-displayed;
use ``--save-password-to <path>`` to persist it to a file with mode 600.

Design notes
------------

* **Password source.**  We use :func:`secrets.token_urlsafe` rather than
  the random module because :mod:`secrets` is the only standard-library
  RNG that is cryptographically suitable for credential generation.
  ``token_urlsafe(32)`` yields 43 URL-safe characters of entropy, well
  above the human-user 8-char floor in
  :func:`nfm_db.api.v1.auth_endpoints._validate_password_strength`.

* **Synthetic email.**  ``users.email`` is ``nullable=False, unique=True``
  so we synthesize ``<username>@service.local``.  This is internal-only
  and not used for anything else (service accounts never receive
  transactional email).

* **No password expiry.**  The AC explicitly states service accounts are
  not subject to normal account expiration rules.  We do not write any
  expiration metadata to the row; the lifetime is governed operationally
  by issuing JWTs with a configurable TTL (``NUCPOT_SERVICE_JWT_TTL_MINUTES``)
  and rotating the bcrypt credential on demand by re-running this command.

* **Idempotency.**  Re-running with the same username raises ``Exists``
  instead of silently overwriting — that would mint a new password but
  leave the JWTs issued against the old hash valid until expiry, which
  is exactly the surprise operators don't want.

* **Password persistence (NFM-2012).**  ``--save-password-to <path>``
  writes the plaintext password to a file with mode 0600 so it survives
  beyond the terminal scrollback.  Use ``-`` to explicitly print to
  stdout (the default behavior when the flag is omitted).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import secrets
import sys
from collections.abc import Coroutine
from typing import Any, NoReturn

import click
from sqlalchemy import select

from nfm_db.database import get_session_factory
from nfm_db.models.user import User
from nfm_db.services.auth_service import get_password_hash

# 32 bytes -> 43 base64-url chars (NFM-1973 / NFM-1972 AC-1).
_PASSWORD_BYTES = 32


def _generate_password() -> str:
    """Generate a cryptographically strong one-time password."""
    return secrets.token_urlsafe(_PASSWORD_BYTES)


def _synthesize_service_email(username: str) -> str:
    """Generate the synthetic email for a service account.

    The ``users`` table requires a unique non-null email; service accounts
    don't have a real inbox, so we point them at the reserved ``.local``
    TLD which is RFC 6762-reserved and never resolved externally.
    """
    return f"{username}@service.local"


def _write_password_to_file(filepath: str, password: str) -> None:
    """Write the plaintext password to *filepath* with mode 0600.

    The file is created (or truncated) with owner-only read/write
    permissions so that other users on the same host cannot read the
    credential.  A trailing newline is appended for editor friendliness.
    """
    fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, f"{password}\n".encode())
    finally:
        os.close(fd)


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine from a (possibly already-running) sync context.

    Operators invoke ``nucpot`` from a normal shell where ``asyncio.run``
    is safe.  Tests — including this repo's pytest-asyncio suite — call
    it from inside a running event loop, where ``asyncio.run`` raises
    ``RuntimeError('asyncio.run() cannot be called from a running event loop')``.

    Strategy:
    * No running loop -> ``asyncio.run(coro)`` (the fast path used in
      production).
    * Already inside a loop -> dispatch ``asyncio.run`` to a one-shot
      worker thread.  The worker has its own loop so the coroutine runs
      to completion, and the parent thread blocks on the future so the
      CLI output ordering is preserved.

    SQLite/in-memory engines used by tests are per-thread, so the
    spawned loop sees the same engine that :func:`get_session_factory`
    resolves at call time — the test fixture is responsible for
    swapping the factory accessor before invoking Click.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


async def _create_service_account_async(
    username: str,
    password: str,
) -> User:
    """Insert a service-account row and return the persisted ``User``."""
    async with get_session_factory()() as session:
        # Reject duplicates explicitly so the operator gets a clean error
        # rather than a SQLAlchemy IntegrityError stack trace.
        existing = await session.execute(
            select(User).where(User.username == username),
        )
        if existing.scalar_one_or_none() is not None:
            raise click.ClickException(
                f"User '{username}' already exists; refusing to overwrite "
                f"a service account. Rotate the password by deleting the "
                f"row first or pick a different username."
            )

        user = User(
            username=username,
            email=_synthesize_service_email(username),
            full_name=f"service:{username}",
            hashed_password=get_password_hash(password),
            # Service accounts have no blog role — authorization is
            # scoped exclusively via ServiceAccountScope.  See
            # ``nfm_db.models.user.User.permissions``.
            blog_role=None,
            is_active=True,
            is_service_account=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _emit_banner(user: User, password: str) -> None:
    """Print the account-creation banner with the one-time password."""
    click.echo("")
    click.echo("=" * 72)
    click.echo(f"Service account created: {user.username}")
    click.echo(f"  user_id        = {user.id}")
    click.echo("  is_service_account = True")
    click.echo(f"  is_active      = {user.is_active}")
    click.echo(f"  email (synthetic)  = {user.email}")
    click.echo(f"  created_at     = {user.created_at.isoformat() if user.created_at else 'n/a'}")
    click.echo("=" * 72)
    click.echo("")
    click.echo("ONE-TIME PASSWORD (copy now — cannot be recovered):")
    click.echo("")
    click.echo(f"    {password}")
    click.echo("")
    click.echo("=" * 72)
    click.echo(
        "Save this password in your password manager before closing the "
        "terminal.  The plaintext is not stored anywhere in the database "
        "or application logs."
    )
    click.echo("=" * 72)


@click.command(
    name="create-service-account",
    short_help="Create a service account and emit a one-time password.",
    help=(
        "Provision a new service account (NFM-1973 / NFM-1972 AC-1). "
        "A cryptographically random password is generated and emitted "
        "exactly once.  By default it prints to stdout; use "
        "``--save-password-to <path>`` to persist it to a file with "
        "mode 0600, or ``--save-password-to -`` to explicitly print "
        "to stdout.\n\n"
        "The created user authenticates via the standard "
        "``/auth/login`` endpoint but is gated to "
        "``/api/v1/extraction/ingest`` only; every other endpoint "
        "(including ``/admin/*``) returns ``403 Forbidden``."
    ),
)
@click.option(
    "--username",
    required=True,
    help="Username for the new service account (must be unique).",
)
@click.option(
    "--role",
    required=True,
    type=click.Choice(["service"], case_sensitive=False),
    help=(
        "Role to assign.  Only ``service`` is supported today because "
        "that is the sole RBAC identity admitted by the OntoFuel "
        "ingest endpoint; future machine identities will be added as "
        "new ``ServiceAccountScope`` enum members."
    ),
)
@click.option(
    "--save-password-to",
    default=None,
    metavar="FILEPATH",
    help=(
        "Write the plaintext password to FILEPATH with mode 0600. "
        "Use ``-`` to print to stdout (default behavior)."
    ),
)
def create_service_account(
    username: str,
    role: str,
    save_password_to: str | None,
) -> NoReturn:
    """Create a service account and print its one-time password."""
    # ``role`` is captured in the command for forward compatibility
    # (the AC names it explicitly even though only ``service`` exists),
    # but today the column shape is fixed — service accounts are the
    # only kind this factory emits.
    del role  # reserved for future scope-aware provisioning

    password = _generate_password()
    user = _run_async(_create_service_account_async(username, password))

    if save_password_to is not None and save_password_to != "-":
        # Write to file with restricted permissions.
        _write_password_to_file(save_password_to, password)
        click.echo(f"Password saved to {save_password_to} (mode 0600).")
        click.echo(f"Service account created: {user.username}")
        click.echo(f"  user_id = {user.id}")
    else:
        # Print the banner + password to stdout (default or "-").
        _emit_banner(user, password)

    sys.exit(0)


__all__ = [
    "_create_service_account_async",
    "_generate_password",
    "_run_async",
    "_write_password_to_file",
    "create_service_account",
]
