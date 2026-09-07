"""CLI: run the DOI ETL admission gate over ``_ref_gap_fill_staging``.

Implements NFM-3871 (C-I1) of the Wayfinder pilot C-line. Reads every
row from the staging table, applies the deterministic pre-screen, then
samples 30 % of the passing rows for a Crossref / OpenAlex
cross-validation. Writes a JSON manifest to ``--output-manifest`` and
a short text summary to stdout for the C-S1 ETL pass to consume.

Usage::

    # Live run against the configured DB:
    python -m nfm_db.scripts.doi_etl_admit \
        --output-manifest /tmp/doi_admit_manifest.json \
        --sample-rate 0.30 --seed 20260830

    # Dry-run / smoke test (no backends wired):
    python -m nfm_db.scripts.doi_etl_admit --dry-run

    # Override DB URL for local testing:
    NFM_DATABASE_URL=sqlite+aiosqlite:///./local.db \\
        python -m nfm_db.scripts.doi_etl_admit --output-manifest /tmp/x.json

The script is idempotent — re-running with the same ``--seed`` produces
an identical manifest, which the C-S1 ETL consumer relies on for
reproducible review-queue selection during incident postmortems.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# Allow ``python scripts/doi_etl_admit.py`` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_SRC = _REPO_ROOT / "apps/api/src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from nfm_db.config import get_settings  # noqa: E402
from nfm_db.database import get_session_factory  # noqa: E402
from nfm_db.models.ref_gap_fill import RefGapFillStaging  # noqa: E402
from nfm_db.services.doi_etl_admission import (  # noqa: E402
    DOIMetadata,
    DOISecondarySourceBackend,
    StagingRow,
    build_admission_manifest,
    manifest_to_jsonable,
)

logger = logging.getLogger("doi_etl_admit")

CROSSREF_BASE = "https://api.crossref.org/works/"
OPENALEX_BASE = "https://api.openalex.org/works/doi:"
HTTP_TIMEOUT = 15.0  # seconds per backend call


# ---------------------------------------------------------------------------
# Live backends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CrossrefRecord:
    """Subset of the Crossref ``/works/{doi}`` message we care about."""

    title: str | None
    first_author: str | None
    year: int | None


class CrossrefBackend:
    """Crossref REST backend for DOI metadata.

    Uses the public ``/works/{doi}`` endpoint. No API key required for
    the polite-pool; the default User-Agent identifies the script so
    Crossref can rate-limit abusive clients without blocking ours.
    """

    name = "crossref"

    def __init__(
        self,
        *,
        timeout: float = HTTP_TIMEOUT,
        mailto: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._mailto = mailto or os.environ.get("CROSSREF_MAILTO", "")

    def _headers(self) -> dict[str, str]:
        ua = "nucpot-doi-etl-admit/1.0 (NFM-3871)"
        if self._mailto:
            ua = f"{ua} (mailto:{self._mailto})"
        return {"User-Agent": ua, "Accept": "application/json"}

    def lookup(self, doi: str) -> DOIMetadata:
        url = f"{CROSSREF_BASE}{doi}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.warning("crossref: network error for %s: %s", doi, exc)
            return DOIMetadata(found=False)
        if resp.status_code != 200:
            logger.info("crossref: %s for %s (status=%d)", _short_status(resp), doi, resp.status_code)
            return DOIMetadata(found=False)
        try:
            payload = resp.json().get("message", {})
            rec = _parse_crossref(payload)
        except (ValueError, KeyError) as exc:
            logger.warning("crossref: parse error for %s: %s", doi, exc)
            return DOIMetadata(found=False)
        if rec is None:
            return DOIMetadata(found=False)
        return DOIMetadata(
            found=True,
            title=rec.title,
            first_author=rec.first_author,
            year=rec.year,
        )


class OpenAlexBackend:
    """OpenAlex REST backend for DOI metadata.

    Uses the public ``/works/doi:{doi}`` endpoint. No API key required
    for anonymous queries; OpenAlex rate-limits anonymous clients to
    ~10 req/s, which is well above the 30 %-of-170 (~50) sample.
    """

    name = "openalex"

    def __init__(self, *, timeout: float = HTTP_TIMEOUT) -> None:
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "nucpot-doi-etl-admit/1.0 (NFM-3871)",
            "Accept": "application/json",
        }

    def lookup(self, doi: str) -> DOIMetadata:
        url = f"{OPENALEX_BASE}{doi}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.warning("openalex: network error for %s: %s", doi, exc)
            return DOIMetadata(found=False)
        if resp.status_code != 200:
            logger.info("openalex: %s for %s (status=%d)", _short_status(resp), doi, resp.status_code)
            return DOIMetadata(found=False)
        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("openalex: parse error for %s: %s", doi, exc)
            return DOIMetadata(found=False)
        return _parse_openalex(payload)


def _short_status(resp: httpx.Response) -> str:
    """Short status phrase for log lines (avoids dumping full bodies)."""
    return f"{resp.status_code} {resp.reason_phrase or ''}".strip()


def _parse_crossref(message: dict[str, Any]) -> _CrossrefRecord | None:
    """Pull title / first author / year out of a Crossref message."""
    titles = message.get("title") or []
    title = titles[0].strip() if titles else None

    authors = message.get("author") or []
    first_author = None
    if authors and isinstance(authors[0], dict):
        given = (authors[0].get("given") or "").strip()
        family = (authors[0].get("family") or "").strip()
        first_author = f"{given} {family}".strip() or None

    issued = message.get("issued", {}).get("date-parts") or []
    year: int | None = None
    if issued and isinstance(issued[0], list) and issued[0]:
        first = issued[0][0]
        if isinstance(first, int):
            year = first
    return _CrossrefRecord(title=title, first_author=first_author, year=year)


def _parse_openalex(payload: dict[str, Any]) -> DOIMetadata:
    """Pull title / first author / year out of an OpenAlex work record."""
    title = (payload.get("title") or "").strip() or None
    authorships = payload.get("authorships") or []
    first_author: str | None = None
    if authorships and isinstance(authorships[0], dict):
        author = authorships[0].get("author") or {}
        if isinstance(author, dict):
            name = (author.get("display_name") or "").strip()
            first_author = name or None
    pub_date = payload.get("publication_date") or ""
    year: int | None = None
    if pub_date and len(pub_date) >= 4 and pub_date[:4].isdigit():
        year = int(pub_date[:4])
    return DOIMetadata(found=True, title=title, first_author=first_author, year=year)


# ---------------------------------------------------------------------------
# Database read
# ---------------------------------------------------------------------------


async def _fetch_rows(session: AsyncSession) -> list[StagingRow]:
    """Read all rows from ``_ref_gap_fill_staging`` as ``StagingRow`` projections."""
    stmt = select(
        RefGapFillStaging.id,
        RefGapFillStaging.source,
        RefGapFillStaging.source_doi,
    )
    result = await session.execute(stmt)
    rows: list[StagingRow] = []
    for rid, source, source_doi in result.all():
        rows.append(
            StagingRow(
                id=rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid)),
                source=source,
                source_doi=source_doi,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="doi_etl_admit",
        description=(
            "Run the DOI pre-screen + 30 %% statistical validation ETL "
            "admission gate over _ref_gap_fill_staging (NFM-3871)."
        ),
    )
    p.add_argument(
        "--output-manifest",
        type=Path,
        default=Path("./doi_admit_manifest.json"),
        help="Path to write the JSON manifest (default: ./doi_admit_manifest.json).",
    )
    p.add_argument(
        "--sample-rate",
        type=float,
        default=0.30,
        help="Fraction of prescreen-passing rows to sample (default: 0.30).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=20260830,
        help="RNG seed for the deterministic sample (default: 20260830, "
             "the day the C-D7 amendment was decided).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the Crossref/OpenAlex calls; admit on prescreen alone. "
             "Useful for CI smoke tests and Playbook dry-run rehearsals.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of staging rows read (default: all). "
             "Used by CI smoke tests against the production DB.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging on the crossref/openalex clients.",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _build_backends(args: argparse.Namespace) -> tuple[
    DOISecondarySourceBackend | None,
    DOISecondarySourceBackend | None,
]:
    if args.dry_run:
        return None, None
    return CrossrefBackend(), OpenAlexBackend()


async def _run(args: argparse.Namespace) -> int:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    settings = get_settings()
    logger.info("connecting to %s", _redact_url(settings.database_url))
    async with get_session_factory()() as session:
        rows = await _fetch_rows(session)
    if args.limit is not None:
        rows = rows[: args.limit]
    logger.info("read %d staging rows", len(rows))

    backend_a, backend_b = _build_backends(args)
    if backend_a is None:
        logger.info("dry-run mode: prescreen-only, no secondary-source checks")
    else:
        logger.info("wired backends: %s + %s", backend_a.name, backend_b.name)

    decisions, summary = build_admission_manifest(
        rows,
        backend_a=backend_a,
        backend_b=backend_b,
        sample_rate=args.sample_rate,
        seed=args.seed,
    )

    payload = manifest_to_jsonable(
        decisions,
        summary,
        issue="NFM-3871",
        generated_at=datetime.now(UTC).isoformat(),
    )

    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(payload, indent=2, sort_keys=True))
    logger.info("manifest written to %s", args.output_manifest)

    # Stdout summary for the operator (also useful when C-S1 reads the
    # handoff comment from this script's logs).
    blocked_by = summary.blocked_by_reason or {}
    print(
        f"NFM-3871 manifest: total={summary.total_rows} "
        f"prescreen_pass={summary.prescreen_pass} "
        f"prescreen_blocked={summary.prescreen_blocked} "
        f"sample_size={summary.sample_size} "
        f"validated={summary.validated} "
        f"validated_partial={summary.validated_partial} "
        f"validated_fail={summary.validated_fail} "
        f"etl_ok={summary.etl_ok} "
        f"etl_blocked={summary.etl_blocked} "
        f"blocked_by={blocked_by} "
        f"manifest={args.output_manifest}"
    )

    # CI / smoke-test exit codes: non-zero if every row was blocked
    # (something has gone wrong with the gate or the data).
    return 0 if summary.etl_ok > 0 else 2


def _redact_url(url: str) -> str:
    """Strip the password from a SQLAlchemy URL for safe logging."""
    if "@" not in url:
        return url
    head, _, tail = url.rpartition("@")
    scheme, _, creds = head.partition("://")
    if ":" in creds:
        user, _, _ = creds.partition(":")
        return f"{scheme}://{user}:***@{tail}"
    return url


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
