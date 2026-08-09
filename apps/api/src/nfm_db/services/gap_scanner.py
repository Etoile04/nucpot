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

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    ExtractionChunk,
    ExtractionGap,
    ExtractionJob,
    ExtractionStep,
    OntologyVersion,
)
from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES

__all__ = [
    "GapScanResult",
    "GapScanService",
    "RecallMetrics",
    "compute_recall",
    "extract_entity_types",
    "iter_property_names",
]

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


def extract_entity_types(ov: OntologyVersion) -> list[dict[str, Any]]:
    """Return ``entity_types`` list from the ontology JSON blob.

    Module-level helper so both ``GapScanService`` and ``compute_recall``
    can count expected properties without instantiating the service.

    Falls back to an empty list for ``None``/missing payloads.
    """
    data = ov.ontology_data
    if not isinstance(data, dict):
        return []
    raw = data.get("entity_types") or []
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _count_expected_properties(entity_types: list[dict[str, Any]]) -> int:
    """Count total expected (entity_type, property) pairs.

    Reuses :func:`iter_property_names` for property normalisation.
    """
    count = 0
    for entity_type_dict in entity_types:
        name = entity_type_dict.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        for _prop_name in iter_property_names(
            entity_type_dict.get("properties"),
        ):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Recall computation (NFM-2614 / NFM-2575-T4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecallMetrics:
    """Aggregated recall statistics for a single ontology version.

    Attributes:
        ontology_version_id: The ontology version these metrics cover.
        total_expected: Total (entity_type, property) pairs declared.
        total_gaps: Total gap records across all statuses.
        open_gaps: Gaps with status ``open``.
        filled_gaps: Gaps with status ``filled``.
        wont_fix_gaps: Gaps with status ``wont_fix``.
        recall_rate: Fraction of expected properties that are covered
            (not open or filling).  Range [0.0, 1.0].
        computed_at: Timestamp when these metrics were calculated.
    """

    ontology_version_id: uuid.UUID
    total_expected: int
    total_gaps: int
    open_gaps: int
    filled_gaps: int
    wont_fix_gaps: int
    recall_rate: float
    computed_at: datetime


async def compute_recall(
    session: AsyncSession,
    ontology_version_id: uuid.UUID,
) -> RecallMetrics:
    """Compute recall metrics for an ontology version.

    Reads the ontology schema to count total expected properties, then
    counts gap records grouped by status.  Recall rate is defined as::

        recall = (total_expected - open_gaps - filling_gaps) / total_expected

    Both ``open`` and ``filling`` gaps are treated as "uncovered" in the
    numerator.  ``filled`` and ``wont_fix`` gaps count as covered.

    Edge case: when *total_expected* is 0, returns *recall_rate = 1.0*.

    Raises:
        ValueError: If the *ontology_version_id* does not exist.
    """
    # Load OntologyVersion — raise ValueError if not found.
    stmt = select(OntologyVersion).where(
        OntologyVersion.id == ontology_version_id,
    )
    result = await session.execute(stmt)
    ov = result.scalar_one_or_none()
    if ov is None:
        raise ValueError(
            f"OntologyVersion not found: {ontology_version_id}",
        )

    # Count expected properties from ontology schema.
    entity_types = extract_entity_types(ov)
    total_expected = _count_expected_properties(entity_types)

    # Count gap records grouped by status.
    gap_stmt = (
        select(ExtractionGap.gap_status, func.count())
        .where(ExtractionGap.ontology_version_id == ontology_version_id)
        .group_by(ExtractionGap.gap_status)
    )
    gap_rows = (await session.execute(gap_stmt)).all()

    status_counts: dict[str, int] = {row[0]: row[1] for row in gap_rows}
    open_gaps = status_counts.get("open", 0)
    filled_gaps = status_counts.get("filled", 0)
    wont_fix_gaps = status_counts.get("wont_fix", 0)
    filling_gaps = status_counts.get("filling", 0)

    total_gaps = open_gaps + filled_gaps + wont_fix_gaps + filling_gaps

    # Recall = covered / total_expected.
    # Covered = total_expected - open - filling (both count as missing).
    if total_expected == 0:
        recall_rate = 1.0
    else:
        recall_rate = (
            (total_expected - open_gaps - filling_gaps) / total_expected
        )

    return RecallMetrics(
        ontology_version_id=ontology_version_id,
        total_expected=total_expected,
        total_gaps=total_gaps,
        open_gaps=open_gaps,
        filled_gaps=filled_gaps,
        wont_fix_gaps=wont_fix_gaps,
        recall_rate=recall_rate,
        computed_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Per-literature recall + per-ontology coverage (NFM-2697-T4 / ADR §3)
# ---------------------------------------------------------------------------
#
# These two helpers back the new spec-mandated endpoints:
#
#   GET /api/v1/literature/{id}/recall?ontology_version=vN
#     -> 200 { recall_rate, extracted_slots, expected_slots, gaps: [...] }
#
#   GET /api/v1/ontology/{version}/coverage
#     -> 200 { coverage_rate, literature_total, literature_fully_covered,
#              gap_distribution: {entity_type+property: int} }
#
# Both treat gaps with status ``open`` or ``filling`` as "uncovered";
# ``filled`` / ``wont_fix`` count as covered.
#
# A literature row is linked to its chunks via ``ExtractionJob.corpus_id``
# matching the DataSource's ``doi`` (cast to string) — the literature
# upload pipeline stores the corpus slug in the job's ``corpus_id`` column
# and the same value in the DataSource's ``doi`` so the two sides can be
# joined cheaply.
#
# If a future migration moves this linkage to a first-class column on
# ExtractionGap (e.g. ``literature_id``) the helpers below can be
# re-pointed without changing the public endpoint contracts.


@dataclass(frozen=True)
class LiteratureRecallItem:
    """One open/filling gap surfaced in the per-literature recall response."""

    entity_type: str
    property: str
    gap_status: str


@dataclass(frozen=True)
class LiteratureRecall:
    """Per-literature recall payload (NFM-2697-T4 ADR §3).

    Attributes:
        literature_id: The DataSource id (literature row) these metrics cover.
        ontology_version_id: The ontology version the recall is measured
            against.
        recall_rate: ``extracted_slots / expected_slots`` (clamped to
            ``[0.0, 1.0]``).  ``1.0`` when ``expected_slots == 0``.
        extracted_slots: ``expected_slots`` minus the count of open /
            filling gaps linked to the literature's chunks.
        expected_slots: Total (entity_type, property) pairs declared by the
            ontology version.
        gaps: Per-gap detail (open/filling only).
    """

    literature_id: uuid.UUID
    ontology_version_id: uuid.UUID
    recall_rate: float
    extracted_slots: int
    expected_slots: int
    gaps: list[LiteratureRecallItem]


@dataclass(frozen=True)
class OntologyCoverage:
    """Per-ontology coverage payload (NFM-2697-T4 ADR §3).

    Attributes:
        ontology_version_id: The ontology version these metrics cover.
        coverage_rate: ``literature_fully_covered / literature_total``
            (clamped to ``[0.0, 1.0]``).  ``1.0`` when
            ``literature_total == 0``.
        literature_total: Distinct DataSource (literature) rows that have
            at least one ExtractionJob whose ``corpus_id`` matches the
            DataSource's ``doi`` (i.e. the literature has been processed
            against this ontology).
        literature_fully_covered: Subset of ``literature_total`` whose
            chunks carry zero open/filling gaps.
        gap_distribution: ``{f"{entity_type}.{property}": int}`` counts of
            every open/filling gap observed across the ontology's
            literature set, regardless of which literature owns the gap.
    """

    ontology_version_id: uuid.UUID
    coverage_rate: float
    literature_total: int
    literature_fully_covered: int
    gap_distribution: dict[str, int]


_UNCOVERED_STATUSES: frozenset[str] = frozenset({"open", "filling"})


async def _load_ontology_or_value_error(
    session: AsyncSession,
    ontology_version_id: uuid.UUID,
) -> OntologyVersion:
    """Resolve an OntologyVersion, raising ``ValueError`` if not found.

    Centralised so both new helpers translate the missing-row case
    identically (callers raise 404 from the ValueError).
    """
    stmt = select(OntologyVersion).where(
        OntologyVersion.id == ontology_version_id,
    )
    result = await session.execute(stmt)
    ov = result.scalar_one_or_none()
    if ov is None:
        raise ValueError(
            f"OntologyVersion not found: {ontology_version_id}",
        )
    return ov


async def _collect_chunks_for_dois(
    session: AsyncSession,
    dois: list[str],
) -> dict[str, list[uuid.UUID]]:
    """Return ``{doi: [chunk_id, ...]}`` for the given corpus DOIs.

    Joins ``ExtractionJob.corpus_id`` to ``DataSource.doi``.  Returns an
    empty mapping if no jobs match (no literature has been processed
    against this corpus yet).
    """
    if not dois:
        return {}
    stmt = select(ExtractionChunk.id, ExtractionJob.corpus_id).join(
        ExtractionJob, ExtractionJob.id == ExtractionChunk.job_id,
    ).where(ExtractionJob.corpus_id.in_(dois))
    rows = (await session.execute(stmt)).all()
    grouped: dict[str, list[uuid.UUID]] = {}
    for chunk_id, corpus_id in rows:
        grouped.setdefault(corpus_id, []).append(chunk_id)
    return grouped


async def _collect_processed_corpora(session: AsyncSession) -> list[str]:
    """Return distinct ``corpus_id`` strings from non-null job rows."""
    stmt = select(distinct(ExtractionJob.corpus_id)).where(
        ExtractionJob.corpus_id.isnot(None),
    )
    return [row[0] for row in (await session.execute(stmt)).all() if row[0]]


async def compute_literature_recall(
    session: AsyncSession,
    literature_id: uuid.UUID,
    ontology_version_id: uuid.UUID,
) -> LiteratureRecall:
    """Compute per-literature recall for ``(literature, ontology_version)``.

    Looks up the DataSource and joins through ``ExtractionJob.corpus_id ==
    DataSource.doi`` to find the literature's chunks; counts open/filling
    gaps on those chunks against the ontology's expected (entity_type,
    property) pairs.

    Raises:
        ValueError: if *literature_id* or *ontology_version_id* does not
            exist (callers map to HTTP 404).
    """
    from nfm_db.models.source import DataSource

    ds_stmt = select(DataSource).where(DataSource.id == literature_id)
    ds = (await session.execute(ds_stmt)).scalar_one_or_none()
    if ds is None:
        raise ValueError(f"Literature not found: {literature_id}")

    ov = await _load_ontology_or_value_error(session, ontology_version_id)

    chunks_by_doi = await _collect_chunks_for_dois(session, [ds.doi])
    chunk_ids: list[uuid.UUID] = chunks_by_doi.get(ds.doi or "", [])

    entity_types = extract_entity_types(ov)
    expected_slots = _count_expected_properties(entity_types)

    if expected_slots == 0:
        return LiteratureRecall(
            literature_id=literature_id,
            ontology_version_id=ontology_version_id,
            recall_rate=1.0,
            extracted_slots=0,
            expected_slots=0,
            gaps=[],
        )

    if not chunk_ids:
        # No chunks observed for this literature.  Per ADR §3 we treat
        # "no recorded gaps" as full coverage of the declared schema:
        # recall = 1.0, extracted = expected.  Callers that need a
        # "haven't been processed yet" signal can detect this via
        # ``expected_slots > 0 and not gaps and not chunk_ids``.
        return LiteratureRecall(
            literature_id=literature_id,
            ontology_version_id=ontology_version_id,
            recall_rate=1.0,
            extracted_slots=expected_slots,
            expected_slots=expected_slots,
            gaps=[],
        )

    gap_stmt = select(
        ExtractionGap.entity_type,
        ExtractionGap.property,
        ExtractionGap.gap_status,
    ).where(
        ExtractionGap.ontology_version_id == ontology_version_id,
        ExtractionGap.chunk_id.in_(chunk_ids),
        ExtractionGap.gap_status.in_(_UNCOVERED_STATUSES),
    )
    rows = (await session.execute(gap_stmt)).all()
    gap_items = [
        LiteratureRecallItem(
            entity_type=row.entity_type,
            property=row.property,
            gap_status=row.gap_status,
        )
        for row in rows
    ]

    open_count = len(gap_items)
    extracted_slots = max(expected_slots - open_count, 0)
    recall_rate = extracted_slots / expected_slots

    return LiteratureRecall(
        literature_id=literature_id,
        ontology_version_id=ontology_version_id,
        recall_rate=recall_rate,
        extracted_slots=extracted_slots,
        expected_slots=expected_slots,
        gaps=gap_items,
    )


async def compute_ontology_coverage(
    session: AsyncSession,
    ontology_version_id: uuid.UUID,
) -> OntologyCoverage:
    """Compute per-ontology coverage with per-literature breakdown.

    Counts distinct literature rows that have at least one ExtractionJob
    whose ``corpus_id`` matches the literature's ``doi``; a literature is
    "fully covered" when none of its chunks have open/filling gaps for
    this ontology version.

    Raises:
        ValueError: if *ontology_version_id* does not exist.
    """
    from nfm_db.models.source import DataSource

    await _load_ontology_or_value_error(session, ontology_version_id)  # 404 check

    lit_stmt = select(DataSource.id, DataSource.doi).where(
        DataSource.source_type == "literature",
    )
    lit_rows = (await session.execute(lit_stmt)).all()
    lit_id_by_doi: dict[str, uuid.UUID] = {
        doi: lid for lid, doi in lit_rows if doi
    }

    processed_corpora = set(await _collect_processed_corpora(session))
    processed_lit_ids: list[uuid.UUID] = [
        lit_id_by_doi[doi]
        for doi in processed_corpora
        if doi in lit_id_by_doi
    ]

    if not processed_lit_ids:
        return OntologyCoverage(
            ontology_version_id=ontology_version_id,
            coverage_rate=1.0,
            literature_total=0,
            literature_fully_covered=0,
            gap_distribution={},
        )

    relevant_dois = [doi for doi in processed_corpora if doi in lit_id_by_doi]
    chunks_by_doi = await _collect_chunks_for_dois(session, relevant_dois)

    gap_stmt = select(
        ExtractionGap.entity_type,
        ExtractionGap.property,
        ExtractionGap.chunk_id,
    ).where(
        ExtractionGap.ontology_version_id == ontology_version_id,
        ExtractionGap.gap_status.in_(_UNCOVERED_STATUSES),
    )
    gap_rows = (await session.execute(gap_stmt)).all()

    gaps_by_chunk: dict[uuid.UUID | None, list[tuple[str, str]]] = {}
    for row in gap_rows:
        gaps_by_chunk.setdefault(row.chunk_id, []).append(
            (row.entity_type, row.property),
        )

    gap_distribution: dict[str, int] = {}
    for pairs in gaps_by_chunk.values():
        for et, pn in pairs:
            key = f"{et}.{pn}"
            gap_distribution[key] = gap_distribution.get(key, 0) + 1

    literature_fully_covered = 0
    for lit_id in processed_lit_ids:
        doi = next((d for d, lid in lit_id_by_doi.items() if lid == lit_id), None)
        if doi is None:
            continue
        lit_chunk_ids = chunks_by_doi.get(doi, [])
        if any(cid in gaps_by_chunk for cid in lit_chunk_ids):
            continue
        literature_fully_covered += 1

    literature_total = len(processed_lit_ids)
    coverage_rate = (
        literature_fully_covered / literature_total
        if literature_total > 0
        else 1.0
    )

    return OntologyCoverage(
        ontology_version_id=ontology_version_id,
        coverage_rate=coverage_rate,
        literature_total=literature_total,
        literature_fully_covered=literature_fully_covered,
        gap_distribution=gap_distribution,
    )


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

        Delegates to the module-level :func:`extract_entity_types`.
        """
        return extract_entity_types(ov)

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
