"""Normalize potentials.file_url to the canonical proxy form (NFM-4309 / BUG-37).

Revision ID: 083_normalize_potential_file_urls
Revises: 082_blog_role_domain_expert
Create Date: 2026-09-05

Full inventory (prod ground truth, nucpot-prod-db @ 2026-09-05, 66 rows)
=======================================================================

Form (classification)                    Count   Disposition
---------------------------------------- ------- ------------------------------
canonical_proxy (/api/v1/…)                  0   (pre-migration)
uploads_relative (/uploads/<key>)            2   → canonical + uploads ref
filesystem_path (/app/uploads/<file>)        1   → canonical + uploads ref
supabase_relative (/storage/v1/object/…)    32   → canonical + supabase ref
http_absolute (https://…supabase.co/…)      13   → canonical + supabase ref
empty (NULL / '')                           18   untouched
bare filename                                0   (none present in prod)

Exception rows (uploads_relative + filesystem_path — the BUG-37 dead links):

* ``Tersoff_SiC_Devanathan_1998``  ceab5f50-fd6e-465f-a93e-6ad52fbcc35c
  ``/uploads/ceab5f50-fd6e-465f-a93e-6ad52fbcc35c.tersoff`` (1913 B —
  file verified present in the prod-uploads volume)
* ``Tersoff_Si_Tersoff_1989``  14607d0a-1a7b-49fd-9b22-1cd5671864c8
  ``/uploads/14607d0a-1a7b-49fd-9b22-1cd5671864c8.tersoff`` (532 B —
  file verified present in the prod-uploads volume)
* ``EAM_Fe_Mendelev_2007v2``  f999bb78-fe4a-45a7-9436-e08686c17c6b
  ``/app/uploads/Fe_Mendelev_2007v2.eam.fs`` (container-path leak; the
  file is NOT present in the volume — the post-deploy sweep
  ``scripts/verify_potential_files.py`` blanks this row with a note)

supabase_relative rows (32): potentials/{huda,library,…} objects listed
in the Ag2S/EAM families — 8 of them carry comma-separated multi-object
fields (e.g. ``EAM_FeCr_Bonny_2011`` d.eam.alloy + s.eam.fs); all object
paths are preserved in ``extra.file_storage.objects``.

http_absolute rows (13): ``potentials/library/`` EAM/MEAM classics
(Mendelev/Cu/Al/Fe/Ti/Ni/Zr, Marinica, Bonny, Cai, Fernandez, Moore) —
the same Supabase origin as the relative form, just written absolute.

What this migration does
========================

1. Emits the live inventory (counts per form + exception list) to the
   migration log and, when writable, to
   ``/var/log/nfmd/potential_file_url_inventory_083.json`` (the original
   values are archived there — 留档).
2. For every non-empty ``file_url``:
   * canonical form → skip (idempotent re-runs are no-ops);
   * ``/uploads/<key>``            → ``file_url = /api/v1/potentials/{id}/file``
                                     + ``extra.file_storage = {uploads, key}``;
   * ``/app/uploads/<key>``        → same, key = volume-relative basename
                                     (container-path form eliminated — AC #2);
   * ``/storage/v1/object/public/…`` (incl. comma lists)
                                    → canonical + ``{supabase, objects[…]}``
                                     (absolute Supabase URLs lose their
                                     baked-in origin);
   * anything else (bare filenames, non-volume filesystem paths)
                                    → ``file_url = ''`` + ``extra.file_url_note``
                                     (unrecoverable — spec §2 “置空并保留来源备注”).
3. Updates run row-by-row with literal-inlined SQL (asyncpg-safe: no
   bind parameters, no temp tables, single statements — the 075/079
   pattern). ``extra`` is JSON-serialized in Python and re-emitted as a
   quoted literal; single quotes are escaped by doubling.

File *existence* is deliberately NOT checked here: the shared uploads
volume is only visible where this migration runs in production, and a
filesystem probe would make the result depend on the deploy host. The
post-deploy sweep ``apps/api/scripts/verify_potential_files.py`` (RE
runbook step) enforces the “every non-empty file_url anonymously
downloads 200 + bytes>0” invariant and blanks missing files with notes.

Downgrade is a documented no-op: original values are archived in the
inventory manifest above (and at runtime in the JSON 留档 file), but
rewriting canonical URLs back to dead legacy forms would re-introduce
the BUG-37 defects.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "083_normalize_potential_file_urls"
down_revision: str | Sequence[str] | None = "082_blog_role_domain_expert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("nfm_db.migrations.083")

_STORAGE_V1_MARKER = "/storage/v1/object/public/"
_CANONICAL_PREFIX = "/api/v1/potentials/"
_UPLOADS_PREFIX = "/uploads/"
_APP_UPLOADS_PREFIX = "/app/uploads/"
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

_MANIFEST_DIR_CANDIDATES = ("/var/log/nfmd",)


def _classify(url: str) -> str:
    """Mirror of nfm_db.services.potential_file_resolver.classify_file_url.

    Duplicated locally so the migration stays import-free of app code
    (alembic runs before/independently of app imports in some contexts).
    """
    value = url.strip()
    if not value:
        return "empty"
    if value.startswith("http://") or value.startswith("https://"):
        return "http_absolute"
    if value.startswith("file://") or _WINDOWS_DRIVE_RE.match(value):
        return "filesystem_path"
    if value.startswith(_CANONICAL_PREFIX):
        return "canonical_proxy"
    if value.startswith(_APP_UPLOADS_PREFIX):
        return "filesystem_path"
    if value.startswith(_STORAGE_V1_MARKER):
        return "supabase_relative"
    if value.startswith(_UPLOADS_PREFIX):
        return "uploads_relative"
    if value.startswith("/"):
        return "filesystem_path"
    return "bare_filename"


def _storage_ref(url: str) -> dict | None:
    """Mirror of storage_ref_from_file_url (see module docstring)."""
    form = _classify(url)
    value = url.strip()
    if form == "uploads_relative":
        key = value[len(_UPLOADS_PREFIX) :]
        return {"kind": "uploads", "key": key} if key else None
    if form == "filesystem_path" and value.startswith(_APP_UPLOADS_PREFIX):
        key = value[len(_APP_UPLOADS_PREFIX) :]
        return {"kind": "uploads", "key": key} if key else None
    if form in ("supabase_relative", "http_absolute"):
        objects = []
        for part in (p.strip() for p in value.split(",")):
            if not part:
                continue
            marker_at = part.find(_STORAGE_V1_MARKER)
            objects.append(part[marker_at + len(_STORAGE_V1_MARKER) :] if marker_at >= 0 else part)
        return {"kind": "supabase", "objects": objects} if objects else None
    return None


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalize_extra(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _write_manifest(manifest: dict) -> None:
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    for directory in _MANIFEST_DIR_CANDIDATES:
        try:
            path = Path(directory) / "potential_file_url_inventory_083.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            logger.info("083 inventory manifest written to %s", path)
            return
        except OSError:
            continue
    logger.info("083 inventory manifest not writable — archived in migration log only")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name, file_url, extra FROM potentials")).fetchall()

    inventory: dict[str, list] = {}
    updates: list[tuple[str, str, str]] = []  # (id, new_url_or_empty, new_extra_json)
    for row in rows:
        raw_url = row.file_url
        if raw_url is None or not str(raw_url).strip():
            continue  # empty rows untouched
        url = str(raw_url).strip()
        form = _classify(url)
        inventory.setdefault(form, []).append(f"{row.name}|{url}")

        if form == "canonical_proxy":
            continue  # already migrated — idempotent no-op

        pid = str(row.id)
        extra = _normalize_extra(row.extra)
        ref = _storage_ref(url)
        if ref is None:
            # Unrecoverable (bare filename / non-volume filesystem path):
            # blank the URL, keep a source note (spec §2). The note stores
            # no container paths — just what was there and why it went away.
            new_extra = {
                **extra,
                "file_url_note": (
                    "file_url cleared by migration 083 (NFM-4309/BUG-37): "
                    f"unrecoverable legacy form ({form})"
                ),
            }
            updates.append((pid, "", json.dumps(new_extra, ensure_ascii=False)))
        else:
            new_extra = {
                **extra,
                "file_storage": ref,
            }
            updates.append(
                (
                    pid,
                    f"{_CANONICAL_PREFIX}{pid}/file",
                    json.dumps(new_extra, ensure_ascii=False),
                )
            )

    counts = {form: len(items) for form, items in sorted(inventory.items())}
    logger.info("083 potentials.file_url inventory: %s", counts)
    for form, items in sorted(inventory.items()):
        if form in ("uploads_relative", "filesystem_path", "bare_filename"):
            for entry in items:
                logger.info("083 %s exception row: %s", form, entry)
    _write_manifest(
        {
            "migration": revision,
            "counts": counts,
            "rows": inventory,
            "rewritten": len(updates),
        }
    )

    for pid, new_url, new_extra_json in updates:
        bind.execute(
            sa.text(
                "UPDATE potentials SET file_url = "
                + _sql_literal(new_url)
                + ", extra = "
                + _sql_literal(new_extra_json)
                + " WHERE id = "
                + _sql_literal(pid)
            )
        )
    logger.info("083 rewrote %d potentials.file_url rows to canonical form", len(updates))


def downgrade() -> None:
    """No-op by design — see module docstring (originals archived in manifest)."""
    logger.info(
        "083 downgrade skipped: canonical file_url rewrite is not reversible "
        "without re-introducing BUG-37 dead-link forms; original values are "
        "archived in the 083 inventory manifest"
    )
