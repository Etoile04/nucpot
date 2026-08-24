#!/usr/bin/env python3
"""Create ontology 0.3.0 draft: merge enhanced ontology into latest base.

NFM-3478 前置治理 Step 1 — establishes the DB as the single source of
truth by importing the enhanced material ontology (139 classes /
162 objectProperties / 279 datatypeProperties) as an additive ``classes``
layer on top of the latest published extraction ontology (0.2.0).

The script is idempotent-safe: it refuses to run when a 0.3.0 row
already exists, and refuses to merge onto a base that already carries a
classes layer. It writes a **draft** — publishing stays a human/API
decision (POST /ontology/versions/{id}/publish with a changelog).

Usage (inside the API container, or locally against prod)::

    # Dry-run: show what would be created, write nothing.
    env -u PYTHONPATH -u VIRTUAL_ENV python apps/api/scripts/ontology_import_030.py --dry-run

    # Write the draft row.
    env -u PYTHONPATH -u VIRTUAL_ENV python apps/api/scripts/ontology_import_030.py

Requires DATABASE_URL / NFM_DATABASE_URL to point at the target DB and a
``--created-by`` user UUID to attribute the draft to.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_SRC = _REPO_ROOT / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from nfm_db.models.ontology_version import OntologyVersion  # noqa: E402
from nfm_db.models.user import User  # noqa: E402
from nfm_db.services.ontology_import import (  # noqa: E402
    build_enhanced_layer,
    load_enhanced_document,
    merge_ontology_data,
)

TARGET_VERSION = "0.3.0"
DEFAULT_CHANGELOG = (
    "ontology 0.3.0: import enhanced material ontology (139 classes, "
    "162 objectProperties, 279 datatypeProperties) as additive classes "
    "layer; extraction keys unchanged (NFM-3478 前置治理 Step 1). "
    "Individuals intentionally not imported (instance data, not schema)."
)


async def _main(dry_run: bool, created_by: uuid.UUID | None) -> int:
    # --- Resolve DB URL ---------------------------------------------------
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("NFM_DATABASE_URL")
    if not db_url:
        print(
            "ERROR: DATABASE_URL / NFM_DATABASE_URL not set — point it at "
            "the target DB (e.g. postgresql+asyncpg://nfm:…@localhost:5433/nfm_db).",
            file=sys.stderr,
        )
        return 2
    engine = create_async_engine(db_url)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        # --- Guards -------------------------------------------------------
        existing = (
            await session.execute(
                select(OntologyVersion).where(
                    OntologyVersion.version == TARGET_VERSION
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(
                f"REFUSED: ontology version {TARGET_VERSION} already exists "
                f"(id={existing.id}, status={existing.status}). Inspect it "
                "via GET /ontology/versions — this script never overwrites."
            )
            return 1

        base = (
            await session.execute(
                select(OntologyVersion)
                .where(OntologyVersion.status == "published")
                .order_by(OntologyVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if base is None:
            print("ERROR: no published ontology version found to merge onto.")
            return 2
        if base.ontology_data is None:
            print(f"ERROR: base version {base.version} has empty ontology_data.")
            return 2

        if created_by is None:
            any_user = (
                await session.execute(select(User).limit(1))
            ).scalar_one_or_none()
            if any_user is None:
                print(
                    "ERROR: no --created-by given and the users table is empty."
                )
                return 2
            created_by = any_user.id
            print(f"No --created-by given; attributing draft to {any_user.id}.")
        else:
            found = await session.get(User, created_by)
            if found is None:
                print(f"ERROR: user {created_by} not found.")
                return 2

        # --- Build merged payload ------------------------------------------
        doc = load_enhanced_document()
        layer = build_enhanced_layer(doc)
        merged = merge_ontology_data(base.ontology_data, layer)

        counts = layer["enhanced_ontology_source"]["counts"]
        print(f"Base: {base.version} (id={base.id}) status={base.status}")
        print(
            f"Layer: {counts['classes']} classes, "
            f"{counts['object_properties']} objectProperties, "
            f"{counts['datatype_properties']} datatypeProperties "
            f"({counts['individuals_not_imported']} individuals not imported)"
        )
        print(f"Merged payload: {len(json.dumps(merged))} bytes, keys={list(merged.keys())}")

        if dry_run:
            print("DRY-RUN: no row written.")
            return 0

        draft = OntologyVersion(
            version=TARGET_VERSION,
            status="draft",
            changelog=DEFAULT_CHANGELOG,
            ontology_data=merged,
            created_by=created_by,
        )
        session.add(draft)
        await session.commit()
        print(
            f"CREATED draft: version=0.3.0 id={draft.id} "
            f"created_by={created_by} — review then publish via API "
            f"(changelog required)."
        )
        return 0
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merge summary without writing any row.",
    )
    parser.add_argument(
        "--created-by",
        type=uuid.UUID,
        default=None,
        help="User UUID to attribute the draft to (default: first user).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.dry_run, args.created_by)))


if __name__ == "__main__":
    main()
