"""Command-line interface for nucpot (NFM-1973).

Provides the ``nucpot`` console script entry point with subcommands for
operating on the database outside the HTTP API.  Currently ships:

- ``nucpot create-service-account`` — provision a service account and
  emit a one-time password.

Add new subcommands here when they belong in the same operator workflow.
"""

from nfm_db.cli.main import cli

__all__ = ["cli"]
