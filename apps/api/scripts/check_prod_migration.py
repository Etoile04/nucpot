#!/usr/bin/env python3
"""NFM-4106: pre-flight guard that refuses to migrate the **production**
database unless the caller is an authorised release-engineering deploy.

Why this exists
---------------
The prod API container's CMD is ``uvicorn`` only (NFM-2146), so the
prod API itself never runs ``alembic upgrade head`` on start. The
deploy-time migration is owned by ``scripts/prod_migrate.sh``, which
invokes alembic inside an ephemeral prod-api container with
``--entrypoint alembic api upgrade head``. That path is gated on the
``NFMD_DEPLOY_LOCK_KEY`` advisory lock held by
``apps/api/migrations/env.py``.

The structural hole surfaced during the NFM-4087 QA session
(2026-09-02): a preview container ``nucpot-prod-api:preview-nfm4087-…``
was started with ``NFM_DATABASE_URL=postgresql://…nucpot-prod-db…`` and
the operator ran ``alembic upgrade head`` inside it. It failed
harmlessly on migration 070's asyncpg bind-param defect, but the path
is structurally indistinguishable from a release-engineering deploy:

  * same image (``nucpot-prod-api:<tag>``)
  * same compose-level env file
  * same ``nucpot-prod-db`` URL
  * same ``alembic upgrade head`` invocation

A *working* destructive migration (e.g. the embargoed 070 in its
original form, which collapses 18 placeholder-titled sources) would
have applied silently and destroyed attribution for 10 prod datasets.

This guard is the structural counterpart of the social embargo on
applying 070 to prod. It runs BEFORE ``alembic upgrade head`` inside
the ephemeral container and refuses unless the caller opts in with
``NFMD_PROD_MIGRATION_PERMITTED=1`` — a value that is *not* present in
any env file in the repo, only in the deploy workflow's ``run`` step
and in ``scripts/prod_migrate.sh`` (both of which are the only
authorised invocation paths).

Exit codes
----------
0  Authorised AND image is at or ahead of the DB. Safe to migrate.
1  DB revision is not in the image's bundled revisions — image is older
   than the DB. Do NOT proceed to ``alembic upgrade head`` (would
   reproduce the NFM-4063 crash loop).
2  Configuration / IO error (missing DATABASE_URL, missing migrations
   dir, audit log unwritable).
3  Permission denied. ``NFMD_PROD_MIGRATION_PERMITTED`` is unset or not
   ``"1"``. The caller is not an authorised RE deploy. ``alembic
   upgrade head`` MUST NOT be invoked.

Usage
-----
Inside the ephemeral prod-api container that prod_migrate.sh runs::

    python /usr/local/bin/check_prod_migration.py \\
      && alembic upgrade head

Env vars consumed
-----------------
``NFMD_PROD_MIGRATION_PERMITTED``  Required, must equal ``"1"``. Set
    only by ``scripts/prod_migrate.sh`` and the
    ``.github/workflows/production-deployment.yml`` deploy step. Never
    set in any committed env file.
``NFM_DATABASE_URL``               Required. Same DSN alembic uses.
``ALEMBIC_MIGRATIONS_DIR``         Optional. Defaults to
    ``/app/migrations``.
``PROD_IMAGE_TAG``                 Optional. Recorded into the audit
    log row. Falls back to ``"unknown"``.
``NFMD_OPERATOR``                  Optional. Recorded into the audit
    log row. Falls back to the container hostname (``HOSTNAME`` env)
    so we at least know which container issued the call.
``NFMD_PROD_MIGRATION_AUDIT_LOG``  Optional. Defaults to
    ``/var/log/nfmd/prod-migrations.log`` (the path the RE runbook
    greps); falls back to ``/tmp/prod-migration-audit.log`` if the
    preferred path is unwritable (e.g. local dev, QA preview).

Why this is bypass-resistant
----------------------------
The flag is checked AFTER the alembic scripts dir is loaded and the DB
revision is fetched, but BEFORE any DDL runs. A QA agent with shell
access to a preview container pointed at the prod DB still has to:

  1. know the flag exists (``NFMD_PROD_MIGRATION_PERMITTED``),
  2. know it must equal the literal string ``"1"`` (not ``true``, not
     ``yes``, not anything else),
  3. set it, and
  4. have the script write an audit row that the on-call runbook
     greps for.

That is a real barrier (vs. the current ``docker exec <preview>
alembic upgrade head`` zero-step path), and it composes with the
NFM-2196 advisory lock so a real RE deploy and a bypass attempt cannot
race. The barrier is not absolute — a determined operator with shell
access can still set the flag — but the bar moves from "any typo
triggers" to "intentional bypass, fully audited".

Companion: see ``docs/runbooks/prod-deploy.md`` §6 for the audit log
layout, who may set the flag, and how QA agents get a mutable database
(scratch DB restored from a prod snapshot) instead.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Default path the RE runbook greps. Kept outside the image so a
#: container restart does not erase the audit trail; the path is
#: bind-mounted in docker-compose.prod.yml (RE-managed). The fallback
#: path is in-memory after container restart but still surfaces the
#: event in ``docker logs`` because every run writes to stderr as well.
DEFAULT_AUDIT_LOG = "/var/log/nfmd/prod-migrations.log"
FALLBACK_AUDIT_LOG = "/tmp/prod-migration-audit.log"


def _load_image_revisions(migrations_dir: Path) -> tuple[set[str], list[str]]:
    """Return (all revisions, head revisions) bundled in this image.

    Mirrors :func:`apps.api.scripts.check_staging_revision._load_image_revisions`
    so the behaviour is identical to the staging guard — we want a
    single semantic for "image vs DB revision" across both paths.
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
    """Return the ``alembic_version.version_num`` row or ``None``.

    Identical to the staging guard. ``None`` means the alembic_version
    table does not exist (a fresh DB that has never been migrated);
    that is a normal pre-baseline state, not an error.
    """
    # asyncpg speaks libpq, not SQLAlchemy — strip any ``+driver``
    # suffix (``postgresql+asyncpg://``, ``postgresql+psycopg2://``)
    # so the DSN parses cleanly. The prod app uses
    # ``postgresql+asyncpg://``; the guard needs the plain
    # ``postgresql://`` form.
    scheme, _, rest = database_url.partition("://")
    scheme = scheme.split("+", 1)[0]
    dsn = f"{scheme}://{rest}"
    conn = await asyncpg.connect(dsn)
    try:
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


def _build_audit_row(
    *,
    outcome: str,
    db_revision: str | None,
    image_head_revisions: list[str],
    image_revision_count: int,
    permission_granted: bool,
    refusal_reason: str | None = None,
) -> dict[str, Any]:
    """Construct the JSON row written to the audit log.

    Schema is forward-compatible: every field except ``ts`` /
    ``outcome`` is optional in the runbook query, so adding fields
    later (e.g. ``git_sha`` once we plumb it through the image) is a
    non-breaking change.
    """
    return {
        "ts": datetime.now(UTC).isoformat(),
        "outcome": outcome,
        "image_tag": os.environ.get("PROD_IMAGE_TAG", "unknown"),
        "image_revision_count": image_revision_count,
        "image_head_revisions": image_head_revisions,
        "db_revision": db_revision,
        "permission_granted": permission_granted,
        "operator": os.environ.get("NFMD_OPERATOR")
        or os.environ.get("HOSTNAME")
        or socket.gethostname(),
        "container_hostname": os.environ.get("HOSTNAME") or socket.gethostname(),
        "refusal_reason": refusal_reason,
        "script": "check_prod_migration.py",
        "issue": "NFM-4106",
    }


def _resolve_audit_log_path() -> tuple[Path, bool]:
    """Pick the audit log path, preferring the configured location.

    Returns ``(path, fell_back)``. ``fell_back`` is True when the
    preferred path was unwritable and we fell back to the fallback
    path; the audit row's ``audit_log_path`` records this so the
    runbook can spot QA containers that keep falling back.

    Skips the fallback dance when the operator explicitly chose the
    fallback path (``NFMD_PROD_MIGRATION_AUDIT_LOG`` already equals
    ``FALLBACK_AUDIT_LOG``).
    """
    preferred = Path(
        os.environ.get("NFMD_PROD_MIGRATION_AUDIT_LOG", DEFAULT_AUDIT_LOG)
    )
    if str(preferred) == FALLBACK_AUDIT_LOG:
        return preferred, False
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        # Open append-only to surface permission issues at write time.
        with preferred.open("a"):
            pass
        return preferred, False
    except OSError:
        return Path(FALLBACK_AUDIT_LOG), True


def _write_audit_row(row: dict[str, Any], path: Path) -> None:
    """Append one JSONL row. Best-effort: failure logs to stderr but
    does not fail the guard's main verdict (the verdict is the load-
    bearing thing; the audit row is the trail)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        sys.stderr.write(
            f"[check_prod_migration] WARNING: audit log write failed: {exc!r}\n"
        )
        return
    # Mirror to stderr so ``docker logs`` shows the row even if the
    # bind mount is misconfigured.
    sys.stderr.write(f"[check_prod_migration] AUDIT {line.rstrip()}\n")


async def _run(database_url: str, migrations_dir: Path) -> int:
    preferred_audit = Path(
        os.environ.get("NFMD_PROD_MIGRATION_AUDIT_LOG", DEFAULT_AUDIT_LOG)
    )
    audit_path, audit_fell_back = _resolve_audit_log_path()
    if audit_fell_back:
        sys.stderr.write(
            f"[check_prod_migration] WARNING: preferred audit log path "
            f"{preferred_audit} was unwritable; falling back to "
            f"{FALLBACK_AUDIT_LOG}. The RE runbook greps the preferred "
            f"path; a fallback row will NOT be visible there.\n"
        )

    if not migrations_dir.exists() or not migrations_dir.is_dir():
        _print_block(
            "check_prod_migration: CONFIGURATION ERROR",
            [
                f"Migrations directory not found: {migrations_dir}",
                "The image build did not COPY migrations into the container,",
                "or ALEMBIC_MIGRATIONS_DIR points to the wrong path.",
                "Refusing to start: alembic would crash with an unrelated error.",
            ],
        )
        _write_audit_row(
            _build_audit_row(
                outcome="config_error",
                db_revision=None,
                image_head_revisions=[],
                image_revision_count=0,
                permission_granted=False,
                refusal_reason="migrations_dir_missing",
            ),
            audit_path,
        )
        return 2

    image_revs, head_revs = _load_image_revisions(migrations_dir)
    sys.stderr.write(
        f"[check_prod_migration] image bundles {len(image_revs)} revisions, "
        f"head(s)={head_revs!r}\n"
    )

    try:
        db_rev = await _fetch_db_revision(database_url)
    except Exception as exc:
        _print_block(
            "check_prod_migration: DB CONNECTIVITY ERROR",
            [
                f"Could not read alembic_version from the database: {exc!r}",
                "Refusing to start: alembic would crash with a less informative",
                "error, and the operator would lose 30+ seconds of health-gate",
                "timeouts before discovering the same root cause.",
            ],
        )
        _write_audit_row(
            _build_audit_row(
                outcome="db_connectivity_error",
                db_revision=None,
                image_head_revisions=head_revs,
                image_revision_count=len(image_revs),
                permission_granted=False,
                refusal_reason=str(exc),
            ),
            audit_path,
        )
        return 2

    # Permission check — NFMD_PROD_MIGRATION_PERMITTED must equal "1".
    # We check AFTER DB connectivity so a misconfigured container
    # surfaces the most useful error first (a missing DB URL is a
    # broken container, not a hostile caller).
    permitted_raw = os.environ.get("NFMD_PROD_MIGRATION_PERMITTED", "")
    permitted = permitted_raw == "1"
    if not permitted:
        _print_block(
            "check_prod_migration: PERMISSION DENIED",
            [
                f"NFMD_PROD_MIGRATION_PERMITTED is {permitted_raw!r}; "
                f"only the literal string '1' grants permission.",
                "",
                "This guard was added in NFM-4106 so a QA/preview container",
                "pointed at nucpot-prod-db cannot advance alembic_version on",
                "the production database. The flag is set only by:",
                "  1. scripts/prod_migrate.sh (the RE deploy path), and",
                "  2. .github/workflows/production-deployment.yml (the CI",
                "     deploy step).",
                "",
                "If you are a QA agent and you need to run alembic upgrade",
                "head, ask SRE for a scratch database restored from a prod",
                "snapshot (see docs/runbooks/prod-deploy.md §6). Do NOT",
                "point your preview container at nucpot-prod-db.",
                "",
                "If you are the on-call operator executing an emergency",
                "hotfix outside the deploy workflow, set the flag and",
                "document the invocation in the audit log:",
                "  NFMD_PROD_MIGRATION_PERMITTED=1 NFMD_OPERATOR=<your-id> \\",
                "      python /usr/local/bin/check_prod_migration.py",
                "",
                "Every invocation is logged to:",
                f"  {audit_path}",
                "",
                "DB alembic_version:        " + (db_rev if db_rev else "<none>"),
                f"Image bundled head(s):     {head_revs!r}",
                f"Image bundled revisions:   {len(image_revs)} total",
                "Image bundled head count:  "
                f"{len(image_revs)} revisions",
                "",
                "This guard was added in NFM-4106 to make the structural",
                "embargo on migration 070 (and any future destructive",
                "migration) auditable instead of purely social.",
            ],
        )
        _write_audit_row(
            _build_audit_row(
                outcome="permission_denied",
                db_revision=db_rev,
                image_head_revisions=head_revs,
                image_revision_count=len(image_revs),
                permission_granted=False,
                refusal_reason="NFMD_PROD_MIGRATION_PERMITTED unset or not '1'",
            ),
            audit_path,
        )
        return 3

    # Permission granted. From here on we behave like the staging
    # guard: refuse if image is older than DB.
    if db_rev is None:
        sys.stderr.write(
            "[check_prod_migration] DB has no alembic_version row — fresh DB; "
            "alembic upgrade head will stamp base. Proceeding.\n"
        )
        _write_audit_row(
            _build_audit_row(
                outcome="ok_fresh_db",
                db_revision=None,
                image_head_revisions=head_revs,
                image_revision_count=len(image_revs),
                permission_granted=True,
            ),
            audit_path,
        )
        return 0

    sys.stderr.write(f"[check_prod_migration] DB alembic_version = {db_rev!r}\n")

    if db_rev in image_revs:
        sys.stderr.write(
            f"[check_prod_migration] OK — DB revision {db_rev!r} is in the "
            "image's revision graph. alembic upgrade head will apply any "
            "downstream migrations.\n"
        )
        _write_audit_row(
            _build_audit_row(
                outcome="ok",
                db_revision=db_rev,
                image_head_revisions=head_revs,
                image_revision_count=len(image_revs),
                permission_granted=True,
            ),
            audit_path,
        )
        return 0

    # DB revision is NOT in the image. Image is older than DB.
    sample_known = sorted(image_revs)[:5]
    _print_block(
        "check_prod_migration: REFUSING TO START — IMAGE IS OLDER THAN DB",
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
            "migration on this production database).",
            "",
            "FIX (pick one):",
            "  1. Rebuild and redeploy from origin/main so the image bundles",
            "     the missing revision(s).",
            "  2. Roll back the DB to a revision this image knows (DESTRUCTIVE",
            "     if the revision added columns the app now relies on).",
            "",
            "This guard was added in NFM-4106 to make the bare",
            "`Can't locate revision` crash self-diagnosing after NFM-4063,",
            "AND to refuse migration from any caller that did not opt in",
            "with NFMD_PROD_MIGRATION_PERMITTED=1.",
        ],
    )
    _write_audit_row(
        _build_audit_row(
            outcome="image_older_than_db",
            db_revision=db_rev,
            image_head_revisions=head_revs,
            image_revision_count=len(image_revs),
            permission_granted=True,
            refusal_reason=f"DB revision {db_rev!r} not in image's revisions",
        ),
        audit_path,
    )
    return 1


def main() -> int:
    database_url = os.environ.get("NFM_DATABASE_URL", "")
    if not database_url:
        _print_block(
            "check_prod_migration: CONFIGURATION ERROR",
            [
                "NFM_DATABASE_URL is not set. The container's env file is broken.",
                "Refusing to start without a database target.",
            ],
        )
        return 2

    migrations_dir = Path(os.environ.get("ALEMBIC_MIGRATIONS_DIR", "/app/migrations"))
    return asyncio.run(_run(database_url, migrations_dir))


if __name__ == "__main__":
    raise SystemExit(main())
