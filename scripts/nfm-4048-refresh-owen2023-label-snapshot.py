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
1 = query/connection failure or an empty corpus (which would silently
weaken the pin, so it is treated as an error rather than a valid refresh).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

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

_CORPUS_SQL = """
SELECT DISTINCT label
FROM kg_nodes
WHERE source_id = %(source_id)s
  AND node_type = 'Material'
"""

_NEWEST_SQL = """
SELECT max(created_at)
FROM kg_nodes
WHERE source_id = %(source_id)s
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        required=True,
        help="libpq connection string for the KG database (read-only usage)",
    )
    parser.add_argument(
        "--source-id",
        default=OWEN2023_SOURCE_ID,
        help=f"datasource UUID to snapshot (default: {OWEN2023_SOURCE_ID})",
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


def _fetch_corpus(dsn: str, source_id: str) -> tuple[list[str], str | None]:
    """Return (labels, newest_node_created_at_iso) from the live KG."""
    import psycopg

    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(_CORPUS_SQL, {"source_id": source_id})
        labels = [row[0] for row in cur.fetchall()]
        cur.execute(_NEWEST_SQL, {"source_id": source_id})
        row = cur.fetchone()
    newest = row[0].isoformat() if row and row[0] is not None else None
    return labels, newest


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    try:
        labels, newest = _fetch_corpus(args.dsn, args.source_id)
    except Exception as exc:  # surface any driver/connection error to the caller
        print(f"ERROR: could not read the KG corpus: {exc}", file=sys.stderr)
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
        newest_node_created_at=newest,
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
