#!/usr/bin/env python3
"""NFM-4066: pre-flight guard that refuses to start the staging API when the
image's bundled alembic revisions are older than the DB's current revision.

Why this exists
---------------
Before this script, the staging container's ``CMD`` ran
``alembic upgrade head && exec uvicorn ...``. When a stale image was deployed
on top of a DB whose schema had already moved past that image's migration
graph (NFM-4063), alembic crashed with the famously unhelpful message::

    ERROR [alembic.util.messaging] Can't locate revision identified by 065_widen_property_measurements_numeric
    FAILED: Cant locate revision identified by 065_widen_property_measurements_numeric

That bare ``Can't locate revision`` told the on-call operator nothing about
*why* — was the migration file missing? Was the DB stamped wrong? Was the
image stale? In NFM-4063 it was the image, and the operator had to discover
that by manually diffing the running image's ``/app/migrations`` against
``origin/main``.

This script runs BEFORE ``alembic upgrade head`` and turns the silent crash
into an explicit "image is older than DB" diagnosis with the offending
revision, the image's head, and a one-line fix instruction.

Exit codes
----------
0  Image is at or ahead of the DB. Safe to run ``alembic upgrade head``.
1  DB revision is not in the image's bundled revisions — image is older
   than the DB. Do NOT proceed to ``alembic upgrade head``; doing so would
   reproduce the NFM-4063 crash loop.
2  Configuration / IO error (missing DATABASE_URL, missing migrations dir,
   no alembic_version row on a fresh DB is *not* an error — that is a
   normal pre-baseline state, exit 0).

Usage
-----
Run inside the staging-api container before ``alembic upgrade head``::

    python /usr/local/bin/check_staging_revision.py \\
      && alembic upgrade head \\
      && exec uvicorn nfm_db.main:app --host 0.0.0.0 --port 8000

Env vars consumed
-----------------
``NFM_DATABASE_URL``   Required. The same URL alembic would use.
``ALEMBIC_MIGRATIONS_DIR``  Optional. Defaults to ``/migrations``. Set to
    the alembic script location for non-container invocations (tests).

Implementation notes
--------------------
* Uses ``alembic.script.ScriptDirectory`` rather than parsing files by
  hand — handles merges, multiple heads, branch labels, and file templates
  exactly the same way alembic itself does.
* Uses ``asyncpg`` directly (not via SQLAlchemy) because this script must
  work even when the bundled ``nfm_db`` package cannot import due to schema
  drift (which is exactly the failure mode we are trying to surface).
* Treats a missing ``alembic_version`` table as "fresh DB, no error"
  rather than an error. That matches alembic's own behaviour on first run
  (it will create the table and stamp ``base``).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory


def _load_image_revisions(migrations_dir: Path) -> tuple[set[str], list[str]]:
    """Return (all revisions, head revisions) bundled in this image.

    ``heads`` is a list because alembic supports multiple heads during merge
    windows. We refuse the "image is older than DB" verdict only if the DB
    revision is absent from *every* head's reachable set.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    script = ScriptDirectory.from_config(cfg)
    all_revs: set[str] = set()
    for rev in script.walk_revisions():
        all_revs.add(rev.revision)
    head_revs = list(script.get_heads())
    return all_revs, head_revs


async def _fetch_db_revision(database_url: str) -> str | None:
    """Return the ``alembic_version.version_num`` row or ``None`` if missing.

    Returns ``None`` when the ``alembic_version`` table does not exist
    (i.e. a fresh DB that has never been migrated). Anything else — empty
    table, multi-row table, NULL column — surfaces as a real value so the
    guard can flag it.
    """
    conn = await asyncpg.connect(database_url)
    try:
        # ``to_regclass`` is NULL when the table is absent — avoids needing
        # to introspect pg_catalog ourselves.
        row = await conn.fetchrow(
            "SELECT version_num FROM alembic_version "
            "WHERE to_regclass('public.alembic_version') IS NOT NULL "
            "LIMIT 1"
        )
        return None if row is None else row["version_num"]
    finally:
        await conn.close()


def _print_block(label: str, lines: Iterable[str]) -> None:
    bar = "=" * 72
    sys.stderr.write(f"\n{bar}\n[{label}]\n{bar}\n")
    for ln in lines:
        sys.stderr.write(f"  {ln}\n")
    sys.stderr.write(f"{bar}\n")


async def _run(database_url: str, migrations_dir: Path) -> int:
    if not migrations_dir.exists() or not migrations_dir.is_dir():
        _print_block(
            "check_staging_revision: CONFIGURATION ERROR",
            [
                f"Migrations directory not found: {migrations_dir}",
                "The image build did not COPY migrations into the container,",
                "or ALEMBIC_MIGRATIONS_DIR points to the wrong path.",
                "Refusing to start: alembic would crash with an unrelated error.",
            ],
        )
        return 2

    image_revs, head_revs = _load_image_revisions(migrations_dir)
    sys.stderr.write(
        f"[check_staging_revision] image bundles {len(image_revs)} revisions, "
        f"head(s)={head_revs!r}\n"
    )

    try:
        db_rev = await _fetch_db_revision(database_url)
    except Exception as exc:  # noqa: BLE001 — DB connectivity errors must NOT be swallowed
        _print_block(
            "check_staging_revision: DB CONNECTIVITY ERROR",
            [
                f"Could not read alembic_version from the database: {exc!r}",
                "Refusing to start: alembic would crash with a less informative",
                "error, and the operator would lose 30+ seconds of health-gate",
                "timeouts before discovering the same root cause.",
            ],
        )
        return 2

    if db_rev is None:
        sys.stderr.write(
            "[check_staging_revision] DB has no alembic_version row — fresh DB; "
            "alembic upgrade head will stamp base. Proceeding.\n"
        )
        return 0

    sys.stderr.write(f"[check_staging_revision] DB alembic_version = {db_rev!r}\n")

    if db_rev in image_revs:
        sys.stderr.write(
            f"[check_staging_revision] OK — DB revision {db_rev!r} is in the "
            "image's revision graph. alembic upgrade head will apply any "
            "downstream migrations.\n"
        )
        return 0

    # DB revision is NOT in the image. Image is older than DB.
    sample_known = sorted(image_revs)[:5]
    _print_block(
        "check_staging_revision: REFUSING TO START — IMAGE IS OLDER THAN DB",
        [
            f"DB alembic_version:        {db_rev!r}",
            f"Image bundled head(s):     {head_revs!r}",
            f"Image bundled revisions:   {len(image_revs)} total "
            f"(first 5: {sample_known!r})",
            "",
            "Running `alembic upgrade head` against this image would crash with:",
            f"    Can't locate revision identified by {db_rev}",
            "",
            "ROOT CAUSE: the deployed image was built before a migration was",
            "added to the codebase. The DB has already been migrated past that",
            "point (probably by a previous image, or by a separate manual",
            "migration on this staging database).",
            "",
            "FIX (pick one):",
            "  1. Rebuild and redeploy from origin/main so the image bundles",
            "     the missing revision(s).",
            "  2. Roll back the DB to a revision this image knows (DESTRUCTIVE",
            "     if the revision added columns the app now relies on).",
            "",
            "This guard was added in NFM-4066 to make the bare",
            "`Can't locate revision` crash self-diagnosing after NFM-4063.",
        ],
    )
    return 1


def main() -> int:
    database_url = os.environ.get("NFM_DATABASE_URL", "")
    if not database_url:
        _print_block(
            "check_staging_revision: CONFIGURATION ERROR",
            ["NFM_DATABASE_URL is not set. The container's env file is broken."],
        )
        return 2

    migrations_dir = Path(os.environ.get("ALEMBIC_MIGRATIONS_DIR", "/migrations"))
    return asyncio.run(_run(database_url, migrations_dir))


if __name__ == "__main__":
    raise SystemExit(main())
