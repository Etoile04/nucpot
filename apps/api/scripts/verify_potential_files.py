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
     Only definitive upstream verdicts (400/404, empty 200 body)
     count as missing; network errors and 429/5xx are *transient* and
     never blank anything. Foreign-origin absolute URLs are not
     server-fetched at all (SSRF guard) and are reported as
     unverifiable.
4. Rows whose file is definitively missing are **blanked** (``file_url=''``)
   with a ``extra.file_url_note`` recording why (spec: 置空并保留来源备注),
   preserving the object/file name for a future re-upload. Only ``--apply``
   writes; the default run is a read-only report.

Exit codes
----------

0  all non-empty file_urls verified downloadable (after --apply, none missing)
1  findings (dry-run), or rows that could not be verified due to transient
   upstream errors / foreign origins — never cleared automatically, re-run
   once the upstream is healthy
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
    is_supabase_public_url,
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


async def _verify_uploads(key: str) -> tuple[str, str]:
    upload_root = get_upload_dir().resolve()
    candidate = (upload_root / key).resolve()
    if not candidate.is_relative_to(upload_root):
        return "missing", f"key escapes upload dir: {key!r}"
    if not candidate.is_file():
        return "missing", f"file not found in upload dir: {key!r}"
    if candidate.stat().st_size <= 0:
        return "missing", f"file is empty: {key!r}"
    return "ok", ""


async def _verify_supabase(url: str) -> tuple[str, str]:
    """Probe a Supabase public object URL.

    Returns ``(state, error)`` where state is:

    * ``"ok"`` — HTTP 200 with at least one byte;
    * ``"missing"`` — definitive not-found verdict (400/404, or an
      existing-but-empty object); safe to blank on ``--apply``;
    * ``"unverifiable"`` — transient upstream condition (network error,
      429, 5xx); must NOT be blanked, the operator re-runs later.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return "unverifiable", f"fetch error for {url}: {exc}"
    if response.status_code in (400, 404):
        return "missing", f"HTTP {response.status_code} for {url}"
    if response.status_code != 200:
        return "unverifiable", f"HTTP {response.status_code} for {url}"
    if len(response.content) == 0:
        return "missing", f"empty body for {url}"
    return "ok", ""


def _object_fetch_url(obj: str) -> str | None:
    """Server-side fetch URL for a supabase object, or ``None``.

    Foreign-origin absolute URLs are never fetched by the container
    (SSRF guard — same policy as the download proxy).
    """
    url = public_object_url(obj)
    return url if is_supabase_public_url(url) else None


async def sweep(apply: bool) -> int:
    database_url = os.environ.get("NFM_DATABASE_URL")
    if not database_url:
        logger.error("NFM_DATABASE_URL is not set")
        return 2
    engine = create_async_engine(database_url)

    findings: list[dict] = []
    unverifiable: list[dict] = []
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
                    state, error = await _verify_uploads(str(ref.get("key", "")))
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
                    fetch_url = _object_fetch_url(objects[0])
                    if fetch_url is None:
                        unverifiable.append(
                            {
                                "id": pid,
                                "name": row.name,
                                "error": (
                                    "foreign-origin object URL not fetched server-side "
                                    f"(SSRF guard): {objects[0]}"
                                ),
                                "extra": extra,
                            }
                        )
                        continue
                    state, error = await _verify_supabase(fetch_url)
                if state == "ok":
                    continue
                if state == "unverifiable":
                    unverifiable.append(
                        {"id": pid, "name": row.name, "error": error, "extra": extra}
                    )
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
                            "UPDATE potentials SET file_url = '', extra = :extra "
                            "WHERE id = :pid"
                        ).bindparams(
                            sa.bindparam("extra", type_=sa.JSON),
                            sa.bindparam("pid", value=UUID(finding["id"])),
                        ),
                        {"extra": merged_extra},
                    )
                    cleared += 1
                await conn.commit()
    finally:
        await engine.dispose()

    logger.info(
        "sweep complete: %d missing, %d unverifiable, %d cleared (apply=%s)",
        len(findings),
        len(unverifiable),
        cleared,
        apply,
    )
    if unverifiable:
        for row in unverifiable:
            logger.error(
                "UNVERIFIABLE %s (%s): %s — left untouched, re-run later",
                row["name"],
                row["id"],
                row["error"],
            )
    if findings or unverifiable:
        for finding in findings:
            logger.warning("MISSING %s (%s): %s", finding["name"], finding["id"], finding["error"])
        report = Path("/var/log/nfmd/potential_file_verify_report.json")
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps(
                    {"missing": findings, "unverifiable": unverifiable},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.info("report written to %s", report)
        except OSError:
            logger.info("report not writable; findings above are the record")
        if unverifiable:
            return 1
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
