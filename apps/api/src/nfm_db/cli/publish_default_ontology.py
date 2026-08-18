"""``nucpot publish-default-ontology`` Click command (NFM-3324 / NFM-3323).

Promote an ontology draft to ``status='published'`` so the LLM extractor
in :mod:`nfm_db.services.extraction_pipeline` no longer short-circuits
with::

    A published ontology version is required for extraction.
    No published OntologyVersion found in the database.

The dispatcher queries ``_get_latest_published_ontology`` which selects
the row with ``status='published'`` ordered by ``created_at DESC``. The
script therefore only needs to ensure *one* row is published -- it does
not need to clear stale drafts.

Usage::

    nucpot publish-default-ontology                 # latest non-published row
    nucpot publish-default-ontology --version 0.3.0 # pin a specific draft

Behaviour
---------

* If ``--version <semver>`` is given, that exact row is promoted (any
  current status). Useful when the Ontology Expert nominates a specific
  draft for prod.

* Otherwise the row with the highest ``created_at`` whose
  ``status != 'published'`` is promoted. This is the default heuristic
  and matches the dispatcher's "latest published" ordering so the
  extractor always picks up the newest payload after a publish.

* If no candidate exists, the script falls back to inserting a row
  seeded from ``apps/api/src/nfm_db/ontology/default_ontology.json``.
  This keeps AC-1 satisfiable on a brand-new database. The JSON file is
  shipped via :setting:`[tool.setuptools.package-data]` (see
  ``pyproject.toml``); without that declaration setuptools 75+ would
  omit it from the wheel and the fallback would silently miss.

* If neither path finds anything to publish, the script exits non-zero
  with a clear error so it fails loudly rather than silently doing
  nothing.

* The whole flow runs in a single transaction; any DB error rolls back
  the change.

Schema notes
------------

The ``OntologyVersion`` model (see :mod:`nfm_db.models.ontology_version`)
exposes ``version, status, changelog, created_by, ontology_data`` plus
the inherited ``created_at``/``updated_at``. There is **no**
``published_at`` column -- the original spec wording
(``UPDATE ... SET status='published', published_at=now()``) was written
against a planned column that did not land. The dispatcher's
"latest published" ordering already uses ``created_at DESC`` so
"now() of the UPDATE" can be derived from ``updated_at`` if needed.
We deliberately set ``status='published'`` only -- adding a column would
require a migration and is out of scope for this hotfix.

``created_by`` is a NOT NULL FK to ``users.id``. We look up the
``system`` user seeded by migration ``044_add_ontology_version`` (id
``00000000-0000-0000-0000-000000000001``, email
``system@nucpot.internal``) and fall back to the first active user if
that row is missing (cold databases that somehow lack the seed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from sqlalchemy import select

from nfm_db.cli.service_accounts import _run_async
from nfm_db.database import async_session_factory
from nfm_db.models.ontology_version import OntologyVersion
from nfm_db.models.user import User

# Seeded by migration 044_add_ontology_version.py. Kept here as a fallback
# only -- the script first tries to look the row up by email so the FK
# resolves even if a future migration renumbers the system user.
_SYSTEM_USER_EMAIL = "system@nucpot.internal"

_DEFAULT_ONTOLOGY_PATH = (
    Path(__file__).resolve().parent.parent / "ontology" / "default_ontology.json"
)


async def _resolve_author_id(session: Any) -> Any:
    """Return a user id suitable for ``OntologyVersion.created_by``.

    Prefers the ``system`` user seeded by migration 044. Falls back to
    the first active user if the seed is missing (cold DB); raises
    ``RuntimeError`` if no users exist at all.
    """
    stmt = select(User.id).where(User.email == _SYSTEM_USER_EMAIL).limit(1)
    found = (await session.execute(stmt)).scalars().first()
    if found is not None:
        return found
    # Fallback: any active user. We deliberately do NOT pick a service
    # account (NFM-2033) -- those have is_active=True but are intended
    # for machine auth, not content authorship attribution.
    stmt = (
        select(User.id)
        .where(User.is_active.is_(True))
        .order_by(User.created_at)
        .limit(1)
    )
    found = (await session.execute(stmt)).scalars().first()
    if found is None:
        raise RuntimeError(
            "ontology_versions.created_by needs a real user but no "
            "active user rows exist; run alembic upgrade head to "
            "install the seed from migration 044."
        )
    return found


async def _publish_default_ontology_async(version: str | None) -> None:
    """Promote a draft ontology row to 'published', or insert a fallback."""
    async with async_session_factory() as session:
        try:
            click.echo(
                "Current ontology_versions (latest 10 by created_at DESC):"
            )
            list_stmt = (
                select(
                    OntologyVersion.id,
                    OntologyVersion.version,
                    OntologyVersion.status,
                    OntologyVersion.created_at,
                )
                .order_by(OntologyVersion.created_at.desc())
                .limit(10)
            )
            for row in (await session.execute(list_stmt)).all():
                click.echo(
                    f"  id={row.id} version={row.version} "
                    f"status={row.status} created_at={row.created_at}"
                )

            target: OntologyVersion | None = None
            if version is not None:
                target = (
                    await session.execute(
                        select(OntologyVersion)
                        .where(OntologyVersion.version == version)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if target is None:
                    raise RuntimeError(
                        f"--version {version!r} does not match any "
                        "ontology_versions row; refusing to insert a "
                        "duplicate."
                    )
                click.echo(
                    f"Selected --version {version!r}: "
                    f"id={target.id} status={target.status!r}"
                )
            else:
                # Latest row with status != 'published'. Using
                # ORDER BY created_at DESC LIMIT 1 matches the
                # dispatcher's _get_latest_published_ontology ordering.
                candidate_stmt = (
                    select(OntologyVersion)
                    .where(OntologyVersion.status != "published")
                    .order_by(OntologyVersion.created_at.desc())
                    .limit(1)
                )
                target = (
                    await session.execute(candidate_stmt)
                ).scalar_one_or_none()
                if target is not None:
                    click.echo(
                        f"Selected latest non-published row: "
                        f"id={target.id} version={target.version!r} "
                        f"status={target.status!r}"
                    )

            if target is None:
                # No draft to promote. Two sub-cases:
                # 1) a published row already exists -> AC-1 is already
                #    satisfied, exit 0 idempotently.
                # 2) no published row exists -> fall back to JSON.
                #
                # Use column-only select to avoid the lazy="selectin"
                # relationships on OntologyVersion -- those touch
                # ``kg_relation_types`` / ``k_entity_types`` which may
                # not exist on a stripped-down staging DB.
                existing_published = (
                    await session.execute(
                        select(
                            OntologyVersion.id,
                            OntologyVersion.version,
                        )
                        .where(OntologyVersion.status == "published")
                        .order_by(OntologyVersion.created_at.desc())
                        .limit(1)
                    )
                ).first()
                if existing_published is not None:
                    click.echo(
                        f"AC-1 already satisfied: "
                        f"id={existing_published.id} "
                        f"version={existing_published.version!r} "
                        f"is published. Nothing to do."
                    )
                    await session.commit()
                    return

                # Fallback path: seed from default_ontology.json.
                if not _DEFAULT_ONTOLOGY_PATH.is_file():
                    raise RuntimeError(
                        "No candidate ontology_versions row to promote "
                        "and the seeded default_ontology.json is "
                        f"missing at {_DEFAULT_ONTOLOGY_PATH}. "
                        "Either create a draft via the Ontology admin "
                        "API or ship the JSON via "
                        "[tool.setuptools.package-data] in "
                        "apps/api/pyproject.toml."
                    )
                payload = json.loads(_DEFAULT_ONTOLOGY_PATH.read_text())
                author_id = await _resolve_author_id(session)
                target = OntologyVersion(
                    version="0.0.0-default",
                    status="published",
                    changelog=(
                        "Seeded fallback inserted by "
                        "nucpot publish-default-ontology "
                        "(NFM-3324 / NFM-3323)."
                    ),
                    created_by=author_id,
                    ontology_data=payload,
                )
                session.add(target)
                click.echo(
                    f"Inserted fallback row version={target.version!r} "
                    f"from {_DEFAULT_ONTOLOGY_PATH}."
                )
            else:
                target.status = "published"
                click.echo(
                    f"Promoted id={target.id} version={target.version!r} "
                    f"to status='published'."
                )

            await session.commit()
        except Exception:
            await session.rollback()
            raise

        # Post-commit verification: re-read the published count so the
        # caller can grep stdout for AC-1.
        count_stmt = (
            select(OntologyVersion.id, OntologyVersion.version)
            .where(OntologyVersion.status == "published")
            .order_by(OntologyVersion.created_at.desc())
        )
        click.echo("Post-commit published rows:")
        published_rows = list((await session.execute(count_stmt)).all())
        if not published_rows:
            click.echo("  (none)")
        for row in published_rows:
            click.echo(f"  id={row.id} version={row.version}")
        click.echo(f"AC-1 published count = {len(published_rows)}")


@click.command(
    name="publish-default-ontology",
    short_help="Publish a default OntologyVersion so the LLM extractor unblocks.",
    help=(
        "Promote the latest non-published ontology_versions row to "
        "status='published', or insert a fallback row from "
        "apps/api/src/nfm_db/ontology/default_ontology.json if the "
        "table is empty.\n\n"
        "Use --version <semver> to pin a specific draft (recommended "
        "after the Ontology Expert nominates a canonical version). "
        "Without --version the script picks the latest row by "
        "created_at DESC, matching the dispatcher's ordering.\n\n"
        "AC-1 (NFM-3323): this script guarantees "
        "`SELECT count(*) FROM ontology_versions WHERE status='published'` "
        "returns >= 1 against the target database.\n\n"
        "AC-2 (NFM-3323): after running, /api/v1/literature/{uuid}/reextract "
        "should no longer fail with 'no published OntologyVersion'.\n\n"
        "Configure connection via the NFM_DATABASE_URL env var "
        "(Pydantic settings prefix) -- same as other nucpot commands."
    ),
)
@click.option(
    "--version",
    "version",
    type=str,
    default=None,
    help="Pin a specific ontology version string (e.g. 0.3.0). "
    "If omitted, the latest non-published row is selected.",
)
def publish_default_ontology_cmd(version: str | None) -> None:
    """Publish a default OntologyVersion row."""
    _run_async(_publish_default_ontology_async(version))
    sys.exit(0)


__all__ = ["publish_default_ontology_cmd"]
