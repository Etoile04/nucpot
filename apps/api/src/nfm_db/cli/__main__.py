"""CLI entry point for nfm_db management commands.

Usage:
    python -m nfm_db.cli seed-ontofuel [--dry-run] [--force] [--json PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nfm_db.cli",
        description="NFM database management CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser(
        "seed-ontofuel",
        help="Seed the OntoFuel ontology into the KG.",
    )
    seed.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print stats without writing to DB.",
    )
    seed.add_argument(
        "--force",
        action="store_true",
        help="Re-seed even if corpus already exists.",
    )
    seed.add_argument(
        "--json",
        type=str,
        default=None,
        help="Path to nvl_ontology_data.json (default: viewer static artifact).",
    )
    return parser


async def _run_seed(args: argparse.Namespace) -> int:
    from pathlib import Path

    json_path = Path(args.json) if args.json else None

    # Use the app's existing session factory
    from nfm_db.database import async_session_factory

    async with async_session_factory() as session:
        from nfm_db.services.seed_ontofuel import seed_ontofuel

        stats = await seed_ontofuel(
            session,
            json_path=json_path,
            dry_run=args.dry_run,
            force=args.force,
        )
        if not args.dry_run:
            await session.commit()

    print(stats.summary())
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "seed-ontofuel":
        return asyncio.run(_run_seed(args))

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
