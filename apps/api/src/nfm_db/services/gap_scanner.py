"""GapScanService — post-extraction gap detection (NFM-2586 / NFM-2575-T2).

Compares the content of every ``ExtractionChunk`` produced by a job
against the expected ``entity_type``/``property`` schema declared in an
``OntologyVersion``, and records an ``ExtractionGap`` row for every
(expected property, chunk set) pair where the property value cannot be
found.

Writes a single ``ExtractionStep`` row with ``step_type='gap_scan'`` and
``status='completed'`` for every scan, regardless of how many gaps were
created — operators use this step to audit when each scan ran.

Deduplication rules (per the issue spec):

* ``open``    — already tracked → skip (don't re-insert)
* ``filling`` — currently being resolved → skip (don't pile up)
* ``filled``  — previously filled → skip (don't reopen)
* ``wont_fix`` — explicitly skipped → skip (don't reopen)

Deduplication is enforced in application code (SELECT before INSERT)
because the SQLite dialect used in tests does not always enforce the
5-tuple unique composite index added in migration 047.  Postgres in
production enforces it at the DB layer too — the app-level check is a
deliberate belt-and-braces.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    ExtractionChunk,
    ExtractionGap,
    ExtractionStep,
    OntologyVersion,
)
from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GapScanResult:
    """Summary of a single ``GapScanService.scan_for_gaps`` invocation.

    Attributes:
        total_expected: Total number of (entity_type, property) pairs
            the ontology expects for this job (counted across all
            entity_types and their declared ``properties`` lists).
        gaps_found: Number of pairs where the chunk set did not contain
            a substring hit for the property name.
        gaps_created: Number of new ``ExtractionGap`` rows actually
            inserted.  Always ``<= gaps_found`` (the rest were
            dedup-skipped).
        scan_duration_ms: Wall-clock duration of the scan, used by the
            audit pipeline and surfaced in the ``ExtractionStep``
            ``metadata_`` JSON blob.
    """

    total_expected: int
    gaps_found: int
    gaps_created: int
    scan_duration_ms: int


# ---------------------------------------------------------------------------
# Helpers (public — exported for tests & callers)
# ---------------------------------------------------------------------------


def iter_property_names(properties: Iterable[Any] | None) -> Iterable[str]:
    """Normalize the ``properties`` field of an entity_type entry.

    The NFM-2580 ontology upload accepts either::

        properties: ["density", "melting_point"]                # str list
        properties: [{"name": "density", "datatype": "float"},   # dict list
                     {"name": "symbol",   "datatype": "string"}]

    Returns an iterator over the property-name strings only.  Entries
    without a non-empty ``name`` are silently dropped (they fail
    ontology upload validation upstream, so this is purely defensive).
    """
    if not properties:
        return
    for prop in properties:
        if isinstance(prop, str):
            if prop.strip():
                yield prop.strip()
        elif isinstance(prop, dict):
            name = prop.get("name")
            if isinstance(name, str) and name.strip():
                yield name.strip()


def chunk_content_mentions_property(content: str, property_name: str) -> bool:
    """Return True if *content* (case-insensitive) contains *property_name*.

    Used as a lightweight heuristic for whether a chunk has a value for
    the expected property.  This is intentionally over-permissive — we
    deliberately trade false-negatives (chunk has the value but doesn't
    mention the property name) for false-positives (false gap), because
    the alternative — parsing structured extraction results — is a
    future NFM scoped work and the current goal is coverage visibility,
    not precision.

    Whitespace at the start/end of *property_name* is ignored; both
    sides are lowered before comparison.
    """
    if not content or not property_name:
        return False
    return property_name.strip().lower() in content.lower()


# ---------------------------------------------------------------------------
# GapScanService
# ---------------------------------------------------------------------------


class GapScanService:
    """Scan job chunks against an ontology version and record gaps.

    Usage::

        svc = GapScanService(session)
        result = await svc.scan_for_gaps(
            job_id=job.id, ontology_version_id=ov.id,
        )
        # result.gaps_created == N
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def scan_for_gaps(
        self,
        *,
        job_id: uuid.UUID,
        ontology_version_id: uuid.UUID,
    ) -> GapScanResult:
        """Compare *job_id*'s chunks against the ontology's expected schema.

        Returns a :class:`GapScanResult` summarizing the run.  Raises
        ``ValueError`` if the *ontology_version_id* cannot be found in
        the DB; missing jobs produce an empty result (no error).
        """
        start = time.monotonic()

        ov = await self._load_ontology(ontology_version_id)
        chunks = await self._load_chunks(job_id)
        existing = await self._load_existing_gaps(
            ontology_version_id=ontology_version_id,
        )

        total_expected = 0
        gaps_found = 0
        gaps_created = 0

        entity_types = self._entity_types(ov)
        for entity_type, property_name in self._iter_expected_pairs(
            entity_types,
        ):
            total_expected += 1
            if self._is_present(property_name, chunks):
                continue
            gaps_found += 1
            if self._already_tracked(
                existing, entity_type, property_name,
            ):
                continue
            new_gap = ExtractionGap(
                ontology_version_id=ov.id,
                entity_type=entity_type,
                property=property_name,
                gap_status="open",
            )
            self._session.add(new_gap)
            gaps_created += 1

        await self._session.flush()

        scan_duration_ms = int((time.monotonic() - start) * 1000)

        # Always write a single gap_scan step — even when no gaps were
        # found, the operator needs to know the scan ran.
        step = ExtractionStep(
            job_id=job_id,
            step_type="gap_scan",
            status="completed",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            metadata_={
                "ontology_version_id": str(ontology_version_id),
                "total_expected": total_expected,
                "gaps_found": gaps_found,
                "gaps_created": gaps_created,
                "scan_duration_ms": scan_duration_ms,
            },
        )
        self._session.add(step)
        await self._session.flush()

        logger.info(
            "GapScanService: job=%s ontology=%s expected=%d found=%d created=%d",
            job_id,
            ontology_version_id,
            total_expected,
            gaps_found,
            gaps_created,
        )

        return GapScanResult(
            total_expected=total_expected,
            gaps_found=gaps_found,
            gaps_created=gaps_created,
            scan_duration_ms=scan_duration_ms,
        )

    # ------------------------------------------------------------------
    # Helpers (private — kept narrow so tests can target them later)
    # ------------------------------------------------------------------

    async def _load_ontology(
        self,
        ontology_version_id: uuid.UUID,
    ) -> OntologyVersion:
        stmt = select(OntologyVersion).where(
            OntologyVersion.id == ontology_version_id,
        )
        result = await self._session.execute(stmt)
        ov = result.scalar_one_or_none()
        if ov is None:
            raise ValueError(
                f"OntologyVersion not found: {ontology_version_id}",
            )
        return ov

    async def _load_chunks(
        self,
        job_id: uuid.UUID,
    ) -> list[ExtractionChunk]:
        stmt = select(ExtractionChunk).where(
            ExtractionChunk.job_id == job_id,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _load_existing_gaps(
        self,
        *,
        ontology_version_id: uuid.UUID,
    ) -> list[ExtractionGap]:
        """Existing gaps for this ontology version (any status).

        Used by deduplication.  Reads once and indexes in-memory because
        the gap set per ontology version is small.
        """
        stmt = select(ExtractionGap).where(
            ExtractionGap.ontology_version_id == ontology_version_id,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _entity_types(ov: OntologyVersion) -> list[dict[str, Any]]:
        """Return ``entity_types`` list from the ontology JSON blob.

        Falls back to an empty list for ``None``/missing payloads; this
        is one of the AC #5 guarantees (empty ontology → no errors).
        """
        data = ov.ontology_data
        if not isinstance(data, dict):
            return []
        raw = data.get("entity_types") or []
        if not isinstance(raw, list):
            return []
        return [e for e in raw if isinstance(e, dict)]

    @staticmethod
    def _iter_expected_pairs(
        entity_types: list[dict[str, Any]],
    ) -> Iterable[tuple[str, str]]:
        """Yield ``(entity_type, property)`` for every declared property.

        Entity types without a ``name`` are dropped (they would fail
        upload validation upstream — defensive only).  Entity types
        without a ``properties`` field contribute zero pairs (AC #5:
        empty ontology handled gracefully).
        """
        for entity_type_dict in entity_types:
            name = entity_type_dict.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            for prop_name in iter_property_names(
                entity_type_dict.get("properties"),
            ):
                yield (name.strip(), prop_name)

    @staticmethod
    def _is_present(
        property_name: str,
        chunks: Iterable[ExtractionChunk],
    ) -> bool:
        """True if *any* chunk content mentions *property_name*."""
        for chunk in chunks:
            if chunk_content_mentions_property(
                chunk.content, property_name,
            ):
                return True
        return False

    @staticmethod
    def _already_tracked(
        existing: Iterable[ExtractionGap],
        entity_type: str,
        property_name: str,
    ) -> bool:
        """True if a gap for this (entity, property) already exists.

        All four gap statuses (open|filling|filled|wont_fix) count as
        "already tracked" — the spec says none of them should be re-
        inserted, reopened, or duplicated.
        """
        for gap in existing:
            if (
                gap.entity_type == entity_type
                and gap.property == property_name
            ):
                return True
        return False


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Sanity check that the step_type constant matches what we write.
    assert "gap_scan" in EXTRACTION_STEP_TYPES
