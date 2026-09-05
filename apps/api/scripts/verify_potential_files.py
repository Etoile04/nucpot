#!/usr/bin/env python3
"""NFM-4309 (BUG-37): post-deploy sweep — every non-empty ``potentials.file_url``
must anonymously download HTTP 200 with bytes > 0.

Run inside the prod API container after migration 083 + deploy::

    docker exec nucpot-prod-api python scripts/verify_potential_files.py          # dry-run
    docker exec nucpot-prod-api python scripts/verify_potential_files.py --apply  # blank missing

What it does
------------

1. Loads every potential with a non-empty ``file_url``.
2. Resolves the backing storage (``extra.file_storage`` first, legacy
   ``file_url`` forms as fallback — same rules as the download proxy).
3. Verifies accessibility:
   * ``uploads`` — the referenced file exists in the upload directory
     (the shared ``prod-uploads`` volume) and is non-empty;
   * ``supabase`` — ``GET`` the public object URL and confirm
     HTTP 200 with at least one byte (streams and discards).
4. Rows whose file cannot be recovered are **blanked** (``file_url=''``)
   with a ``extra.file_url_note`` recording why (spec: 置空并保留来源备注),
   preserving the object/file name for a future re-upload. Only ``--apply``
   writes; the default run is a read-only report.

Exit codes
----------

0  all non-empty file_urls verified downloadable (after --apply, none missing)
1  some rows still missing files (dry-run findings — run --apply or fix uploads)
2  configuration error (no NFM_DATABASE_URL, unreachable DB)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from uuid import UUID

# Make `nfm_db` importable when invoked as a bare script inside the container
# (the package lives under src/ in the image).
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from nfm_db.services.potential_file_resolver import (  # noqa: E402
    public_object_url,
    resolve_storage_ref,
)
from nfm_db.services.upload_service import get_upload_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("verify_potential_files")

_MISSING_NOTE = (
    "file_url cleared by verify_potential_files (NFM-4309/BUG-37): "
    "referenced file is missing ({kind}; name {name})"
)


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (ValueError, AttributeError):
        return False
    return True


async def _verify_uploads(key: str) -> tuple[bool, str]:
    upload_root = get_upload_dir().resolve()
    candidate = (upload_root / key).resolve()
    if not candidate.is_relative_to(upload_root):
        return False, f"key escapes upload dir: {key!r}"
    if not candidate.is_file():
        return False, f"file not found in upload dir: {key!r}"
    if candidate.stat().st_size <= 0:
        return False, f"file is empty: {key!r}"
    return True, ""


async def _verify_supabase(url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return False, f"HTTP {response.status_code} for {url}"
            if len(response.content) == 0:
                return False, f"empty body for {url}"
            return True, ""
    except httpx.HTTPError as exc:
        return False, f"fetch error for {url}: {exc}"


async def sweep(apply: bool) -> int:
    database_url = os.environ.get("NFM_DATABASE_URL")
    if not database_url:
        logger.error("NFM_DATABASE_URL is not set")
        return 2
    engine = create_async_engine(database_url)

    findings: list[dict] = []
    cleared = 0
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT id, name, file_url, extra FROM potentials "
                        "WHERE file_url IS NOT NULL AND file_url <> ''"
                    )
                )
            ).fetchall()

            logger.info("checking %d potentials with non-empty file_url", len(rows))
            for row in rows:
                pid = str(row.id)
                extra = row.extra if isinstance(row.extra, dict) else {}
                ref = resolve_storage_ref(extra, str(row.file_url))
                if ref is None:
                    findings.append(
                        {
                            "id": pid,
                            "name": row.name,
                            "error": "no resolvable storage ref",
                            "extra": extra,
                        }
                    )
                    continue
                if ref.get("kind") == "uploads":
                    ok, error = await _verify_uploads(str(ref.get("key", "")))
                else:
                    objects = [str(o) for o in (ref.get("objects") or [])]
                    if not objects:
                        findings.append(
                            {
                                "id": pid,
                                "name": row.name,
                                "error": "empty supabase object list",
                                "extra": extra,
                            }
                        )
                        continue
                    ok, error = await _verify_supabase(public_object_url(objects[0]))
                if ok:
                    continue
                findings.append({"id": pid, "name": row.name, "error": error, "extra": extra})

            if apply and findings:
                for finding in findings:
                    if not _is_uuid(finding["id"]):
                        logger.error("refusing to clear non-UUID id %r", finding["id"])
                        continue
                    note = (
                        _MISSING_NOTE.format(kind="see error", name=finding["name"])
                        + f" — {finding['error']}"
                    )
                    merged_extra = {
                        **(finding.get("extra") or {}),
                        "file_url_note": note,
                    }
                    await conn.execute(
                        sa.text(
                            "UPDATE potentials SET file_url = '', extra = CAST(:extra AS json) "
                            "WHERE id = CAST(:id AS uuid)"
                        ),
                        {
                            "extra": json.dumps(merged_extra, ensure_ascii=False),
                            "id": finding["id"],
                        },
                    )
                    cleared += 1
                await conn.commit()
    finally:
        await engine.dispose()

    logger.info("sweep complete: %d missing, %d cleared (apply=%s)", len(findings), cleared, apply)
    if findings:
        for finding in findings:
            logger.warning("MISSING %s (%s): %s", finding["name"], finding["id"], finding["error"])
        report = Path("/var/log/nfmd/potential_file_verify_report.json")
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("report written to %s", report)
        except OSError:
            logger.info("report not writable; findings above are the record")
        return 1 if not apply else 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="blank file_url for rows whose files are missing (default: dry-run)",
    )
    args = parser.parse_args()
    return asyncio.run(sweep(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
