#!/usr/bin/env python3
"""NFM-4012 / NFM-4013 Path (a) — enumeration harness for LLM-only unknown property names.

CPO direction (NFM-4012 Path (a)): when ``extraction_to_db_mapper._lookup_property_type``
returns ``None``, capture a structured record into
``MappingResult.skipped_unknown_details`` and surface it through
``process_literature_sync``. This harness drives the sync wrapper on one or more
datasource UUIDs, aggregates the captured records across the sample, and emits
a TSV at ``docs/reports/2026-09-01-nfm4009-unknown-properties.tsv`` for NDE to
fold into NFM-4008 AC-1 (the full 27-name classification table; the static-gap
analysis already supplies 13 names, this supplies the remaining 14 LLM-only
names).

Read-only against prod. Does NOT write new ``property_types`` rows, does NOT
modify the prod ``kg_nodes`` table, and does NOT introduce any schema
migration. ``MappingResult.skipped_unknown_details`` is in-memory; the
``process_literature_sync`` return dict is the only persistence surface.

Usage::

    # Default — drive the Owen2023 9320cb50 source.
    DATABASE_URL=... python scripts/nfm-4012-unknown-property-enumeration.py

    # Multiple datasources (AC-5 5-paper sample, when LE has the IDs).
    DATABASE_URL=... python scripts/nfm-4012-unknown-property-enumeration.py \\
        --datasources 9320cb50-eb65-4178-8d2e-c56aeb848b21,<uuid-2>,<uuid-3>

    # Custom output path.
    DATABASE_URL=... python scripts/nfm-4012-unknown-property-enumeration.py \\
        --output /tmp/probe.tsv

Exit code 0 on success (TSV written with >=1 row). Exit code 1 if no
``skipped_unknown_details`` records were captured.

Pre-registration note: this is exploratory measurement (NFM-4012 description §
"Pre-registration discipline"); PREREG is NOT required.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure apps/api/src is importable so we can import the sync wrapper.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = str(_REPO_ROOT / "apps" / "api" / "src")
if _API_SRC not in sys.path:
    sys.path.insert(0, _API_SRC)

logger = logging.getLogger("nfm4012.enumeration")


# ---------------------------------------------------------------------------
# Default datasource sample.
# ---------------------------------------------------------------------------
# NFM-4012 specifies the AC-5 5-paper sample as the preferred input. The IDs
# are not yet enumerated in this worktree (the AC-5 staging scorecard in
# NFM-4005 is owned by RE). Defaulting to the Owen2023 ``9320cb50`` source is
# the lowest-friction path and matches the NFM-3986 worker run that already
# captured ``skipped_unknown=27`` on this source (see NFM-4012 description §
# "Cross-references"). LE/RE can extend the sample via ``--datasources`` when
# the AC-5 IDs are available.
DEFAULT_DATASOURCE_IDS: tuple[str, ...] = ("9320cb50-eb65-4178-8d2e-c56aeb848b21",)

DEFAULT_OUTPUT_PATH = _REPO_ROOT / "docs" / "reports" / "2026-09-01-nfm4009-unknown-properties.tsv"

# TSV column order (NFM-4012 AC-1).
TSV_COLUMNS: tuple[str, ...] = (
    "rank",
    "raw_property_name",
    "normalized_property_name",
    "category_slug",
    "raw_category",
    "frequency",
    "sample_value",
    "source_papers",
)


# ---------------------------------------------------------------------------
# Result type (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateRow:
    """Aggregated record for a unique ``(category_slug, raw_category, property_name)`` tuple."""

    raw_property_name: str
    normalized_property_name: str
    category_slug: str | None
    raw_category: str
    frequency: int
    sample_value: Any
    source_papers: tuple[str, ...]


# ---------------------------------------------------------------------------
# Normalisation helpers (NFM-4012 § 3 "Normalise")
# ---------------------------------------------------------------------------

# Collapse runs of underscores + whitespace into a single underscore.
_MULTI_SEP_RE = re.compile(r"[_\s]+")


def _normalize_property_name(raw: str | None) -> str:
    """Lower-case + strip + collapse ``_<mult>space`` on ``property_name``.

    Examples
    --------
    >>> _normalize_property_name(" Cr-Doped_Diffusion_Ea  ")
    'cr-doped_diffusion_ea'
    >>> _normalize_property_name("Activation Energy")
    'activation_energy'
    """
    if raw is None:
        return ""
    cleaned = _MULTI_SEP_RE.sub("_", raw.strip()).lower()
    return cleaned


def _normalize_category_slug(slug: str | None) -> str | None:
    """Lower-case the category slug, preserve ``None`` when the OntoFuel literal is unknown."""
    if slug is None:
        return None
    return slug.strip().lower() or None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(records: list[dict[str, Any]]) -> list[AggregateRow]:
    """Aggregate a flat list of unknown-detail records into unique rows.

    Bucket key is ``(category_slug, raw_category, property_name)``. ``frequency``
    is the count across the sample; ``sample_value`` is the first non-null value
    seen (NFM-4012 § 3); ``source_papers`` is a sorted unique tuple of DOI /
    source_file / material_name joined strings.
    """
    bucket: dict[tuple[str | None, str, str], dict[str, Any]] = {}

    for rec in records:
        category_slug = _normalize_category_slug(rec.get("category_slug"))
        raw_category = (rec.get("raw_category") or "").strip()
        property_name = (rec.get("property_name") or "").strip()
        key = (category_slug, raw_category, property_name)
        if key not in bucket:
            bucket[key] = {
                "raw_property_name": property_name,
                "normalized_property_name": _normalize_property_name(property_name),
                "category_slug": category_slug,
                "raw_category": raw_category,
                "frequency": 0,
                "sample_value": None,
                "source_papers": set(),
            }
        entry = bucket[key]
        entry["frequency"] += 1

        sample_value = rec.get("sample_value")
        if entry["sample_value"] is None and sample_value not in (None, ""):
            entry["sample_value"] = sample_value

        # Source-paper provenance: prefer DOI; fall back to source_file +
        # material_name to disambiguate two papers with no DOI.
        paper_tag: str | None = None
        doi = (rec.get("source_doi") or "").strip()
        if doi:
            paper_tag = f"doi:{doi}"
        else:
            src = (rec.get("source_file") or "").strip()
            mat = (rec.get("material_name") or "").strip()
            if src or mat:
                paper_tag = f"file:{src}|mat:{mat}" if src else f"mat:{mat}"
        if paper_tag:
            entry["source_papers"].add(paper_tag)

    rows: list[AggregateRow] = []
    for entry in bucket.values():
        rows.append(
            AggregateRow(
                raw_property_name=entry["raw_property_name"],
                normalized_property_name=entry["normalized_property_name"],
                category_slug=entry["category_slug"],
                raw_category=entry["raw_category"],
                frequency=entry["frequency"],
                sample_value=entry["sample_value"],
                source_papers=tuple(sorted(entry["source_papers"])),
            ),
        )

    # Sort: frequency DESC, raw_category ASC, property_name ASC (NFM-4012 § 3).
    rows.sort(key=lambda r: (-r.frequency, r.raw_category, r.raw_property_name))
    return rows


# ---------------------------------------------------------------------------
# Sync invocation (delegate to literature_service.process_literature_sync)
# ---------------------------------------------------------------------------


def _drive_all(datasource_ids: list[str]) -> list[dict[str, Any]]:
    """Drive all datasource calls from a single asyncio loop.

    SQLAlchemy's async engine caches asyncpg connections in a pool, each
    bound to the loop that opened it. Sequential ``asyncio.run`` calls
    create a fresh loop per datasource and surface
    "attached to a different loop" on the second invocation. Keeping
    every call inside one loop avoids that by sharing the pool across
    calls and disposing it after the last one.
    """
    from uuid import UUID

    from nfm_db.database import async_session_factory, engine
    from nfm_db.services.literature_service import process_literature

    async def _run_all() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ds_id in datasource_ids:
            try:
                ds_uuid = UUID(ds_id)
            except ValueError:
                logger.warning("Skipping invalid UUID: %s", ds_id)
                continue
            async with async_session_factory() as session:
                logger.info(
                    "Driving process_literature for datasource_id=%s",
                    ds_id,
                )
                result = await process_literature(session, ds_uuid)
            if not isinstance(result, dict):
                logger.warning(
                    "process_literature returned non-dict (%s) for datasource_id=%s",
                    type(result).__name__,
                    ds_id,
                )
                continue
            skipped_count = result.get("skipped_unknown_properties", 0)
            details = result.get("skipped_unknown_details") or []
            logger.info(
                "datasource_id=%s status=%s skipped_unknown=%d captured_details=%d",
                ds_id,
                result.get("status"),
                skipped_count,
                len(details),
            )
            out.extend(details)
        return out

    try:
        return asyncio.run(_run_all())
    finally:
        # Best-effort: dispose the asyncpg pool so the next run starts clean.
        # ``asyncio.run`` closes its loop, so we open a fresh one for the
        # dispose call.
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(engine.dispose())
            finally:
                loop.close()
        except Exception:
            logger.debug("engine.dispose() failed during cleanup", exc_info=True)


# ---------------------------------------------------------------------------
# TSV writer
# ---------------------------------------------------------------------------


def _write_tsv(rows: list[AggregateRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(TSV_COLUMNS)
        for rank, row in enumerate(rows, start=1):
            writer.writerow(
                [
                    rank,
                    row.raw_property_name,
                    row.normalized_property_name,
                    row.category_slug if row.category_slug is not None else "",
                    row.raw_category,
                    row.frequency,
                    "" if row.sample_value is None else str(row.sample_value),
                    ";".join(row.source_papers),
                ],
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NFM-4012 Path (a): enumerate LLM-only unknown property names.",
    )
    parser.add_argument(
        "--datasources",
        type=str,
        default=",".join(DEFAULT_DATASOURCE_IDS),
        help=(
            "Comma-separated list of datasource UUIDs to drive. "
            f"Default: {','.join(DEFAULT_DATASOURCE_IDS)}"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output TSV path. Default: {DEFAULT_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level. Default: INFO.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    datasource_ids = [token.strip() for token in args.datasources.split(",") if token.strip()]
    if not datasource_ids:
        logger.error("No datasource UUIDs supplied (--datasources is empty)")
        return 1

    all_records: list[dict[str, Any]] = _drive_all(datasource_ids)

    rows = _aggregate(all_records)
    _write_tsv(rows, args.output)

    unique_pairs = sum(1 for _ in rows)
    total_records = len(all_records)
    logger.info(
        "Wrote %d aggregated rows (from %d raw records) to %s",
        unique_pairs,
        total_records,
        args.output,
    )
    print(
        f"OK: {unique_pairs} unique (category_slug, raw_category, property_name) rows, "
        f"{total_records} raw records → {args.output}",
    )
    # AC-1 gate: at least 1 unique pair (NFM-4012 spec expects ≥14 for the
    # full AC-5 sample; the default single-datasource run is naturally
    # smaller, so we only require non-zero output here).
    if unique_pairs == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
