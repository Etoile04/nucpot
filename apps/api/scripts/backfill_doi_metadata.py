#!/usr/bin/env python3
"""Backfill ``data_sources.journal`` / ``year`` for stock DOI rows (NFM-4313).

Production literature list showed "—" in the journal / year columns for
entries ingested via DOI (e.g. DOI ``10.1016/j.jnucmat.2018.05.039``).
PR #1141's BUG-17 fix only healed the *incremental* from-doi path — and
only via a markdown regex that cannot match Semantic Scholar's
abstract-only output — so existing rows were never backfilled.

This script re-resolves every stock DOI against Crossref
(``https://api.crossref.org/works/{doi}``) and fills the missing
``journal`` / ``year`` columns.

Why a script (not a migration)
------------------------------
The heal depends on an external HTTP API — migrations must stay
offline-deterministic.  A script also gives the operator a dry-run and
a per-run report, and can be re-run whenever new stock is discovered.

Idempotency contract
--------------------
* Candidate selection only picks rows with ``doi IS NOT NULL`` **and**
  (``journal IS NULL`` **or** ``year IS NULL``) — healed rows drop out
  of the candidate set on the next run.
* Every UPDATE carries per-field ``IS NULL`` guards, so a concurrent
  writer (curator, incremental ingest) is never overwritten and a
  re-run over the same snapshot is a no-op.
* Crossref values only ever fill NULLs; curated values win.

Usage
-----
::

    cd apps/api && python scripts/backfill_doi_metadata.py --dry-run
    cd apps/api && python scripts/backfill_doi_metadata.py            # apply
    cd apps/api && python scripts/backfill_doi_metadata.py --limit 5 # sample

Set ``CROSSREF_MAILTO`` (email) to join Crossref's polite pool.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make ``nfm_db`` importable when run as a script (mirrors the pattern
# used in ``scripts/backfill_material_category.py``).
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from sqlalchemy import bindparam, text  # noqa: E402

from nfm_db.config import get_settings  # noqa: E402
from nfm_db.database import get_session_factory  # noqa: E402
from nfm_db.services.crossref_metadata import fetch_crossref_metadata  # noqa: E402

logger = logging.getLogger("backfill_doi_metadata")

#: Default pause between Crossref calls (seconds) — stays well inside
#: the polite-pool expectations for a sequential single client.
DEFAULT_SLEEP_S = 1.0

#: Metadata resolver — injectable for tests.
FetchFn = Callable[[str], dict[str, Any] | None]


@dataclass
class BackfillReport:
    """Per-run counts for the operator summary."""

    candidates: int = 0
    crossref_hits: int = 0
    crossref_misses: int = 0
    journal_filled: int = 0
    year_filled: int = 0
    rows_updated: int = 0
    rows_no_new_fields: int = 0
    would_update_rows: int = 0
    dry_run: bool = False

    def render(self) -> str:
        lines = [
            "DOI metadata backfill (NFM-4313) — "
            + ("DRY-RUN (no writes)" if self.dry_run else "applied"),
            f"  candidates={self.candidates} "
            f"crossref_hits={self.crossref_hits} "
            f"crossref_misses={self.crossref_misses}",
            f"  journal_filled={self.journal_filled} "
            f"year_filled={self.year_filled} "
            f"rows_no_new_fields={self.rows_no_new_fields}",
        ]
        if self.dry_run:
            lines.append(f"  would_update_rows={self.would_update_rows}")
        else:
            lines.append(f"  rows_updated={self.rows_updated}")
        return "\n".join(lines)


_CANDIDATE_SELECT = text(
    "SELECT id, doi, journal, year FROM data_sources "
    "WHERE doi IS NOT NULL AND (journal IS NULL OR year IS NULL) "
    "ORDER BY created_at"
)


async def run_backfill(
    session,
    *,
    fetch: FetchFn = fetch_crossref_metadata,
    dry_run: bool = False,
    verbose: bool = False,
    limit: int | None = None,
    sleep_s: float = DEFAULT_SLEEP_S,
) -> BackfillReport:
    """Backfill NULL journal/year on DOI rows; return the run report.

    ``fetch`` resolves a DOI to ``{title, journal, year}`` (or ``None``)
    and is injectable so tests never touch the network.
    """
    report = BackfillReport(dry_run=dry_run)

    select = _CANDIDATE_SELECT
    if limit is not None:
        # Push the cap into SQL; slice again below so injected test
        # sessions (which ignore LIMIT) observe the same semantics.
        select = text(f"{_CANDIDATE_SELECT.text} LIMIT :limit").bindparams(
            bindparam("limit", limit)
        )
    rows = (await session.execute(select)).all()
    if limit is not None:
        rows = rows[:limit]
    report.candidates = len(rows)

    for row_id, doi, journal, year in rows:
        meta = fetch(doi)
        if meta is None:
            report.crossref_misses += 1
            if verbose:
                logger.debug("row %s doi=%s — no Crossref record", row_id, doi)
            continue
        report.crossref_hits += 1

        # Fill only what is currently NULL (curated values always win).
        fills: dict[str, Any] = {}
        if journal is None and meta.get("journal") is not None:
            fills["journal"] = meta["journal"]
            report.journal_filled += 1
        if year is None and meta.get("year") is not None:
            fills["year"] = meta["year"]
            report.year_filled += 1

        if not fills:
            report.rows_no_new_fields += 1
            if verbose:
                logger.debug(
                    "row %s doi=%s — Crossref record adds no missing field",
                    row_id,
                    doi,
                )
            continue

        if dry_run:
            report.would_update_rows += 1
            if verbose:
                logger.debug(
                    "row %s doi=%s — would set %s (dry-run)",
                    row_id,
                    doi,
                    sorted(fills),
                )
        else:
            set_clause = ", ".join(f"{col} = :{col}" for col in fills)
            null_guard = " AND ".join(f"{col} IS NULL" for col in fills)
            await session.execute(
                text(
                    "UPDATE data_sources "
                    f"SET {set_clause} "
                    "WHERE id = CAST(:row_id AS UUID) "
                    f"AND {null_guard}"
                ),
                {**fills, "row_id": str(row_id)},
            )
            report.rows_updated += 1
            if verbose:
                logger.debug("row %s doi=%s — set %s", row_id, doi, sorted(fills))

        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

    return report


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_doi_metadata",
        description=(
            "Backfill data_sources.journal/year for stock DOI rows from "
            "Crossref (NFM-4313). Idempotent: only NULL fields are ever "
            "written, so re-running against healed rows is a no-op."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve every candidate and print the report, but emit no UPDATE.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Emit per-row DEBUG log lines.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of candidate rows processed (e.g. sample first 5).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_S,
        help="Pause between Crossref calls in seconds (default: %(default)s).",
    )
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    settings = get_settings()
    logger.info("connecting to %s", _redact_url(settings.database_url))
    async with get_session_factory()() as session:
        report = await run_backfill(
            session,
            dry_run=args.dry_run,
            verbose=args.verbose,
            limit=args.limit,
            sleep_s=args.sleep,
        )
        if args.dry_run:
            # Belt-and-braces: a dry-run leaves the DB untouched even if
            # a future refactor starts writing inside run_backfill.
            await session.rollback()
        else:
            await session.commit()

    print(report.render())
    return 0


def _redact_url(url: str) -> str:
    """Strip credentials from a database URL for log lines."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        _, _, host_part = rest.partition("@")
        return f"{scheme}://***@{host_part}"
    return url


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
