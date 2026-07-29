"""``nucpot seed-ontofuel`` Click command (NFM-768).

Loads the NVL ontology JSON into the knowledge graph.

Usage:

    nucpot seed-ontofuel [--dry-run] [--force] [--json PATH]

Design notes
------------

* **Idempotency.**  Pre-flight SELECT collects existing IDs, then only
  inserts new rows.  Re-running is a safe no-op (unless ``--force`` is
  passed, which skips the early-return check).

* **Deterministic IDs.**  NVL IDs are mapped to internal UUIDs via
  ``uuid.uuid5(NAMESPACE_URL, "ontofuel:<nvl_id>")`` so the same
  NVL node always resolves to the same ``kg_nodes.id``.

* **All nodes get KGNode rows.**  ``ontology_id_map.node_id`` has a
  foreign key to ``kg_nodes.id``, so even class-type ontology concepts
  need a ``KGNode`` row (``node_type=Material`` as a placeholder).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from nfm_db.cli.service_accounts import _run_async
from nfm_db.services.seed_ontofuel import seed_ontofuel


@click.command(
    name="seed-ontofuel",
    short_help="Seed the OntoFuel ontology into the KG.",
    help=(
        "Load the NVL ontology JSON (927 nodes, 1061 edges) into the "
        "knowledge graph.  By default this is a no-op if the corpus "
        "has already been seeded; use --force to re-seed.\n\n"
        "The JSON path defaults to the viewer static artifact.\n\n"
        "NFM-768 AC#2: --dry-run prints stats without writing.\n"
        "NFM-768 AC#3: Idempotent (safe to re-run).\n"
        "NFM-768 AC#5: Corpus ID = 'ontofuel'."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Parse and print stats without writing to DB.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-seed even if corpus already exists.",
)
@click.option(
    "--json",
    "json_path",
    type=click.Path(exists=False),
    default=None,
    help="Path to nvl_ontology_data.json (default: viewer static artifact).",
)
def seed_ontofuel_cmd(dry_run: bool, force: bool, json_path: str | None) -> None:
    """Seed the OntoFuel ontology into the KG."""
    from nfm_db.database import async_session_factory

    path = Path(json_path) if json_path else None

    async def _run() -> None:
        async with async_session_factory() as session:
            stats = await seed_ontofuel(
                session,
                json_path=path,
                dry_run=dry_run,
                force=force,
            )
            if not dry_run:
                await session.commit()
            click.echo(stats.summary())

    _run_async(_run())
    sys.exit(0)


__all__ = ["seed_ontofuel_cmd"]
