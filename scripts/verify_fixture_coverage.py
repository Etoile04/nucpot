#!/usr/bin/env python3
"""Verify golden fixture corpus coverage targets (B1.6).

Walks the fixture tree and asserts that each figure type meets the
expected minimum count and that the total is at least 50.

Usage:
    python scripts/verify_fixture_coverage.py \\
        --fixtures apps/api/tests/fixtures/extraction
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a standalone script outside the package.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from eval_extraction_accuracy import EXPECTED_COUNTS, discover_fixtures  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fixtures",
        type=Path,
        required=True,
        help="Root of the golden fixture tree.",
    )
    args = parser.parse_args(argv)

    fixtures = discover_fixtures(args.fixtures)
    counts: dict[str, int] = {}
    for f in fixtures:
        counts[f.figure_type] = counts.get(f.figure_type, 0) + 1

    print("fixtures per type:", counts)

    for fig_type, expected in EXPECTED_COUNTS.items():
        actual = counts.get(fig_type, 0)
        if actual < expected:
            print(
                f"FAIL: {fig_type}: {actual} < {expected}",
                file=sys.stderr,
            )
            return 1

    total = sum(counts.values())
    if total < 50:
        print(f"FAIL: total {total} < 50", file=sys.stderr)
        return 1

    print(f"OK: {total} fixtures across {len(counts)} types", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
