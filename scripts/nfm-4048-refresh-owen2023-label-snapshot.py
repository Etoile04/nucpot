#!/usr/bin/env python3
"""NFM-4048 — refresh the production Owen2023 Material-label snapshot.

Read-only against the KG. Runs one ``SELECT DISTINCT label`` over
``kg_nodes`` for the Owen2023 datasource and rewrites
``apps/api/tests/fixtures/owen2023_material_labels.json`` in place, then
reports which labels appeared or disappeared since the last capture.

Why this exists
---------------
The audit pin in ``apps/api/tests/test_kg_to_staging_bridge.py`` is only as
current as the snapshot it reads.  NFM-4037's pin drifted five labels behind
production precisely because refreshing it was a manual, undocumented step
(QA warnings W1/W2).  This script makes the refresh one command, so the
loop is: refresh → suite fails on any newly unpinned label → add the label
plus its expected ``element_system`` → suite green.

Usage::

    # Against the prod container on this host.
    scripts/nfm-4048-refresh-owen2023-label-snapshot.py \\
        --dsn "postgresql://nfm@localhost:5432/nfm_db"

    # Show the diff without touching the fixture.
    scripts/nfm-4048-refresh-owen2023-label-snapshot.py --dsn ... --dry-run

Exit codes: 0 = snapshot already current or rewritten successfully;
1 = database read failure or an empty corpus (which would silently weaken
the pin, so it is treated as an error rather than a valid refresh);
2 = environment error (e.g. ``asyncpg`` missing — see NFM-4051).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS = _REPO_ROOT / "apps" / "api"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from tests._helpers.owen2023_corpus import (  # noqa: E402
    OWEN2023_SOURCE_ID,
    build_snapshot,
    dump_snapshot,
    load_snapshot,
    snapshot_path,
)

if TYPE_CHECKING:
    import asyncpg

_CORPUS_SQL = """
SELECT DISTINCT label
FROM kg_nodes
WHERE source_id = $1
  AND node_type = 'Material'
"""

_NEWEST_SQL = """
SELECT max(created_at)
FROM kg_nodes
WHERE source_id = $1
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        required=True,
        help=(
            "libpq connection string for the KG database (read-only usage). "
            "The plain ``postgresql://user:pass@host:port/db`` form is "
            "expected; the SQLAlchemy ``+asyncpg`` URL suffix is rejected "
            "by ``asyncpg.connect``."
        ),
    )
    parser.add_argument(
        "--source-id",
        default=OWEN2023_SOURCE_ID,
        help=(
            f"datasource UUID to snapshot. This script is purpose-built for "
            f"the Owen2023 audit pin ({OWEN2023_SOURCE_ID}); any other value "
            f"is rejected — see the NFM-4051 note in the README."
        ),
    )
    parser.add_argument(
        "--captured-from",
        default="nucpot-prod-db, database nfm_db",
        help="human-readable provenance recorded in the snapshot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff without rewriting the fixture",
    )
    return parser.parse_args(argv)


async def _fetch_corpus_async(
    dsn: str,
    source_id: str,
    *,
    pg: asyncpg,
) -> tuple[list[str], object | None]:
    """Return (labels, newest_node_created_at) from the live KG via asyncpg.

    Parameterised queries (``$1``) — never string-interpolate ``source_id``.
    Caller passes the already-imported ``asyncpg`` module so a missing
    driver surfaces as ``ImportError`` at the CLI layer (exit 2) rather
    than deep inside this helper.
    """
    conn = await pg.connect(dsn, timeout=15)
    try:
        label_rows = await conn.fetch(_CORPUS_SQL, source_id)
        newest_row = await conn.fetchrow(_NEWEST_SQL, source_id)
    finally:
        await conn.close()
    labels = [row["label"] for row in label_rows]
    newest = newest_row[0] if newest_row is not None else None
    return labels, newest


def _fetch_corpus(dsn: str, source_id: str) -> tuple[list[str], object | None]:
    """Sync wrapper around :func:`_fetch_corpus_async` that bridges to asyncpg.

    Raises ``ImportError`` if ``asyncpg`` is not installed (the operator
    then sees a driver-missing diagnostic with exit code 2 — distinct from
    the database-side error that exits 1).
    """
    import asyncpg

    return asyncio.run(_fetch_corpus_async(dsn, source_id, pg=asyncpg))


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    # Defence-in-depth: even though build_snapshot now honours its source_id
    # kwarg, this script's only reason to exist is the Owen2023 audit pin.
    # Refuse a non-default --source-id at the CLI layer so a typo or
    # experiment never silently writes another source's labels under the
    # Owen2023 fixture (NFM-4051 CR follow-up on NFM-4048's LOW finding).
    if args.source_id != OWEN2023_SOURCE_ID:
        print(
            f"ERROR: --source-id must be {OWEN2023_SOURCE_ID} (the Owen2023 "
            f"datasource this script refreshes); got {args.source_id!r}. "
            "To snapshot another datasource, write a new refresh script "
            "with its own pin/fixture pair.",
            file=sys.stderr,
        )
        return 2

    try:
        labels, newest = _fetch_corpus(args.dsn, args.source_id)
    except ImportError as exc:
        # Driver missing — distinct from a database-side failure so the
        # operator does not waste time debugging connectivity.
        print(
            f"ERROR: could not load the asyncpg driver: {exc}. "
            "asyncpg is a declared dependency in apps/api/pyproject.toml — "
            "install it (or activate the project venv) before retrying.",
            file=sys.stderr,
        )
        return 2
    except (OSError, TimeoutError) as exc:
        # Socket-level failure: DNS, port closed, connection refused,
        # timeout.  Distinguishes "the host/port is wrong" from "the SQL
        # query is wrong".
        print(
            f"ERROR: database connection failed for {args.dsn!r}: {exc}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        # asyncpg.PostgresError and anything the driver itself raises
        # (auth failure, syntax error, missing relation, ...).  Tag the
        # type so the operator can grep without parsing prose.
        print(
            f"ERROR: database read failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 1

    if not labels:
        print(
            f"ERROR: source {args.source_id} has no Material labels. Refusing to "
            "write an empty snapshot — an empty pin guards nothing.",
            file=sys.stderr,
        )
        return 1

    path = snapshot_path()
    previous = load_snapshot(path)
    before = set(previous["labels"])
    after = set(labels)

    added = sorted(after - before)
    removed = sorted(before - after)

    print(f"live corpus:  {len(after)} distinct Material label(s)")
    print(f"snapshot:     {len(before)} label(s) (captured {previous['captured_at']})")
    for label in added:
        print(f"  + {label}")
    for label in removed:
        print(f"  - {label}")
    if not added and not removed:
        print("snapshot is already current; nothing to do.")
        return 0

    if args.dry_run:
        print("\n--dry-run: fixture left unchanged.")
        return 0

    payload = build_snapshot(
        labels,
        captured_at=dt.date.today().isoformat(),
        captured_from=args.captured_from,
        newest_node_created_at=newest.isoformat() if newest is not None else None,
        source_id=args.source_id,
        template=previous,
    )
    path.write_text(dump_snapshot(payload), encoding="utf-8")
    print(f"\nwrote {path}")
    if added:
        print(
            "Next: pin each new label in _OWEN2023_LABELS "
            "(apps/api/tests/test_kg_to_staging_bridge.py) with its expected "
            "element_system, then re-run "
            "`pytest apps/api/tests/test_kg_to_staging_bridge.py`."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
