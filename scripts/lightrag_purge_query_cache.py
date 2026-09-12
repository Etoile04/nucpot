#!/usr/bin/env python3
"""NFM-4804 — purge stale prompt-keyed LLM query-cache entries (LightRAG).

Why this exists
---------------
LightRAG caches LLM answers keyed by (mode, prompt-hash) in the KV
storage's ``llm_response_cache`` collection (PG table
``lightrag_llm_cache`` after the NFM-4736 / PR #1338 cutover from
JsonKVStorage).  A KG restore invalidates those cached ANSWERS while the
retrieval side keeps returning fresh references — observed on prod
2026-09-12: the hybrid query 「UO2 热导率」 replayed a pre-restore
answer body alongside references pointing at the re-ingested Owen UO2
source.  Every cache hit for such prompts keeps serving the stale answer
until the row is deleted (or its TTL, which JsonKV never enforced,
expires).

Scope discipline
----------------
- Deletes ONLY ``cache_type = 'query'`` rows — stale ANSWER replays.
  ``extract``-type rows (ingestion-time LLM call caches) cost a re-LLM
  at worst and are left alone.
- Read/write goes through the host's Postgres port (127.0.0.1:5433 on
  the prod host). Never ``docker exec`` — ADR-013 G2.

Usage::

    # Report only (default): row counts + age spread, no writes.
    scripts/lightrag_purge_query_cache.py \\
        --dsn "postgresql://nfm@127.0.0.1:5433/nfm_db"

    # Actually delete the stale query-cache rows.
    scripts/lightrag_purge_query_cache.py --dsn ... --yes

    # Only rows older than 24h.
    scripts/lightrag_purge_query_cache.py --dsn ... --yes --older-than 24

Exit codes: 0 = report printed / purge executed;
1 = database failure (connection, permission, SQL);
2 = usage or environment error (missing asyncpg driver, bad args).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

# cache_type values in llm_response_cache: 'query' (answer replays) and
# 'extract' (ingestion-time LLM caches). Only the former is purged.
QUERY_CACHE_TYPE = "query"

_COUNT_SQL = """
SELECT count(*) AS n, min(create_time) AS oldest, max(create_time) AS newest
FROM lightrag_llm_cache
WHERE cache_type = $1
"""

_DELETE_ALL_SQL = """
DELETE FROM lightrag_llm_cache
WHERE cache_type = $1
"""

_DELETE_OLDER_THAN_SQL = """
DELETE FROM lightrag_llm_cache
WHERE cache_type = $1
  AND create_time < now() - make_interval(hours => $2)
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        required=True,
        help=(
            "libpq connection string for the NFMD database hosting the "
            "lightrag_llm_cache table (on the prod host: "
            "postgresql://nfm@127.0.0.1:5433/nfm_db — the host port, "
            "never docker exec)."
        ),
    )
    parser.add_argument(
        "--older-than",
        type=float,
        default=None,
        metavar="HOURS",
        help="only purge query-cache rows created more than HOURS ago "
        "(default: purge all query rows)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually DELETE (without it the script is a read-only report)",
    )
    return parser.parse_args(argv)


async def _report_and_purge_async(
    dsn: str,
    *,
    older_than_hours: float | None,
    execute: bool,
    pg: asyncpg,
) -> int:
    """Connect, report the query-cache rows, and optionally purge them.

    Parameterised SQL only — the cache_type discriminator is bound as
    ``$1`` even though it is a constant, so the statement shape stays
    injection-proof under future edits.
    """
    conn = await pg.connect(dsn, timeout=15)
    try:
        row = await conn.fetchrow(_COUNT_SQL, QUERY_CACHE_TYPE)
        n = row["n"] if row else 0
        oldest = row["oldest"] if row else None
        newest = row["newest"] if row else None
        print(
            f"lightrag_llm_cache cache_type='query': {n} row(s) "
            f"(oldest={oldest}, newest={newest})"
        )
        if n == 0:
            print(
                "NOTE: 0 query rows. If the sidecar still runs JsonKVStorage "
                "(pre-#1338 cutover), the LLM cache lives in the container "
                "volume file, not this table — the cutover deploy orphans it "
                "and nothing needs purging here."
            )
            return 0
        if not execute:
            print("dry-run: no rows deleted (pass --yes to purge)")
            return 0
        if older_than_hours is not None:
            deleted = await conn.execute(
                _DELETE_OLDER_THAN_SQL, QUERY_CACHE_TYPE, older_than_hours
            )
        else:
            deleted = await conn.execute(_DELETE_ALL_SQL, QUERY_CACHE_TYPE)
        print(f"purged: {deleted}")
        return 0
    finally:
        await conn.close()


def _run(dsn: str, *, older_than_hours: float | None, execute: bool) -> int:
    import asyncpg

    return asyncio.run(
        _report_and_purge_async(
            dsn,
            older_than_hours=older_than_hours,
            execute=execute,
            pg=asyncpg,
        )
    )


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.older_than is not None and args.older_than < 0:
        print("ERROR: --older-than must be >= 0", file=sys.stderr)
        return 2
    try:
        return _run(
            args.dsn,
            older_than_hours=args.older_than,
            execute=args.yes,
        )
    except ImportError as exc:
        print(
            f"ERROR: could not load the asyncpg driver: {exc}. "
            "asyncpg is a declared dependency in apps/api/pyproject.toml — "
            "install it (or activate the project venv) before retrying.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # CLI boundary — report, never traceback
        print(f"ERROR: database operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
