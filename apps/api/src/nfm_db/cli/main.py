"""Top-level Click group for the ``nucpot`` console script (NFM-1973).

Groups operator-facing subcommands under a single ``nucpot`` entry point
so adding new commands (e.g. ``nucpot rotate-service-account``,
``nucpot list-service-accounts``) is a one-import change.
"""

from __future__ import annotations

import click

from nfm_db.cli.service_accounts import create_service_account


@click.group(
    name="nucpot",
    help=(
        "Operator CLI for the nucpot backend.\n\n"
        "Subcommands talk directly to the database — they do not require "
        "the FastAPI server to be running.  Configure connection via the "
        "``NFM_DATABASE_URL`` env var (Pydantic settings prefix)."
    ),
)
def cli() -> None:
    """Top-level CLI entry point."""


# Register subcommands.  Importing the module binds the command object
# against ``@cli.command`` below.
cli.add_command(create_service_account)


__all__ = ["cli"]
