"""CLI: promote C-I1-admitted staging rows into ``reference_values`` (NFM-3872 / C-S1).

Reads the manifest produced by ``doi_etl_admit.py`` (NFM-3871) and
INSERT-or-UPDATE every ``etl_ok`` row into the formal
``reference_values`` table. Re-runs are idempotent (UNIQUE on
``staging_id``), so an operator can replay the promotion after an
incident review without producing duplicate formal rows.

Usage::

    # Live run against the configured DB:
    python -m nfm_db.scripts.promote_staging_to_reference_values \\
        --manifest /tmp/doi_admit_manifest.json

    # Override the issue stamp (mostly for tests):
    python -m nfm_db.scripts.promote_staging_to_reference_values \\
        --manifest /tmp/x.json --etl-issue NFM-3872-test

The script prints a one-line summary to stdout that the C-S1 handoff
comment can lift verbatim. A non-zero exit code means the promotion
failed — the operator should re-run after addressing the printed
error (which is most often a dropped staging row referenced by the
manifest, or a malformed manifest schema).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow ``python scripts/promote_staging_to_reference_values.py`` from
# the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_SRC = _REPO_ROOT / "apps/api/src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from nfm_db.config import get_settings  # noqa: E402
from nfm_db.database import get_session_factory  # noqa: E402
from nfm_db.services.promote_staging_etl import (  # noqa: E402
    ETL_ISSUE_ID,
    promote_admitted_rows,
)

logger = logging.getLogger("promote_staging_etl")


def _redact_url(url: str) -> str:
    """Strip the password from a SQLAlchemy URL for safe logging."""
    if "@" not in url:
        return url
    head, _, tail = url.rpartition("@")
    scheme, _, creds = head.partition("://")
    if ":" in creds:
        user, _, _ = creds.partition(":")
        return f"{scheme}://{user}:***@{tail}"
    return url


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="promote_staging_to_reference_values",
        description=(
            "Promote C-I1-admitted _ref_gap_fill_staging rows into the "
            "formal reference_values table (NFM-3872 / C-S1)."
        ),
    )
    p.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to the C-I1 admission manifest (JSON, produced by "
             "doi_etl_admit.py / NFM-3871).",
    )
    p.add_argument(
        "--etl-issue",
        type=str,
        default=ETL_ISSUE_ID,
        help=(
            "Paperclip issue ID stamped onto every formal row's "
            "etl_issue column (default: NFM-3872). Tests override this "
            "so the formal table's audit trail cleanly identifies the "
            "promotion run."
        ),
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not args.manifest.exists():
        logger.error("manifest not found: %s", args.manifest)
        return 2

    settings = get_settings()
    logger.info("connecting to %s", _redact_url(settings.database_url))
    async with get_session_factory()() as session:
        report = await promote_admitted_rows(
            session,
            args.manifest,
            etl_issue=args.etl_issue,
        )

    s = report.summary
    print(
        f"NFM-3872 promote_staging_etl: etl_issue={s.etl_issue} "
        f"manifest={s.manifest_ref} "
        f"total_decisions={s.total_decisions} "
        f"admitted={s.admitted} "
        f"skipped_blocked={s.skipped_blocked} "
        f"inserted={s.inserted} "
        f"updated={s.updated} "
        f"staging_status_marked={s.staging_status_marked}"
    )

    # CI / smoke-test exit codes:
    # 0 — at least one row promoted (success, or a no-op replay).
    # 2 — manifest had zero admitted rows (the gate is broken or the
    #     data drifted). Per NFM-3871 the same exit code is returned
    #     when etl_ok == 0; we mirror that here so a CI job that
    #     chained C-I1 + C-S1 can branch on one signal.
    return 0 if s.admitted > 0 else 2


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
