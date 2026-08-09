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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
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
    "CoverageMetrics",
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


@dataclass(frozen=True)
class CoverageMetrics:
    """Per-ontology-version coverage across all linked literatures (NFM-2697-T3).

    Coverage is computed across the corpus of literatures associated with
    a specific ontology version string.  A literature is "fully covered"
    when it has no ``open`` or ``filling`` ``ExtractionGap`` rows tied to
    that ontology version.

    Attributes:
        ontology_version: The semver string of the ontology version these
            metrics cover (per ADR Section 2: TEXT, not UUID).
        literature_total: Distinct literature rows associated with this
            ontology version.
        literature_fully_covered: Literature rows whose gap count for
            (open | filling) is zero.
        gap_distribution: Map of ``(entity_type, property)`` to the
            number of open-or-filling gaps across all literatures.
        coverage_rate: ``literature_fully_covered / literature_total``.
            Documented 0/0 behaviour: ``0.0`` (i.e. an empty corpus
            contributes zero coverage, distinguishing from "fully
            covered" which returns ``1.0``).
        computed_at: Timestamp when these metrics were calculated.
    """

    ontology_version: str
    literature_total: int
    literature_fully_covered: int
    gap_distribution: dict[tuple[str, str], int]
    coverage_rate: float
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(UTC),
    )


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
    # ADR Section 2 surface (NFM-2697-T3)
    # ------------------------------------------------------------------

    async def scan_literature(
        self,
        literature_id: uuid.UUID,
        ontology_version: str,
        *,
        only_open: bool = True,
        persist: bool = True,
    ) -> list[ExtractionGap]:
        """Scan a single literature's chunks against an ontology version.

        Per ADR-NFM-2675 Section 2, this is the per-literature counterpart
        to ``scan_for_gaps``.  Behaviour:

        * Resolves ``ontology_version`` (semver string) to an
          ``OntologyVersion`` row; raises ``ValueError`` when absent.
        * Identifies all ``ExtractionJob`` rows that belong to
          ``literature_id`` AND are tagged with this OV
          (``ExtractionJob.ontology_version_id``) so that jobs scoped to
          other ontology versions do not pollute the per-literature scan.
          (Until T1 adds a real ``literature_id`` column on
          ``extraction_gaps``, literature identity is approximated by
          ``ExtractionJob.corpus_id`` / ``source_reference`` — the
          integration task NFM-2736 will switch this to use the FK.)
        * Loads chunks for those jobs and computes expected
          (entity_type, property) pairs from the ontology schema.
        * Dedups against pre-existing ``ExtractionGap`` rows for this
          ontology version.  When ``only_open=True`` (default), every
          status (``open``/``filling``/``filled``/``wont_fix``) blocks
          creation — matching the existing ``scan_for_gaps`` contract.
          When ``only_open=False``, only active gaps
          (``open``/``filling``) block; ``filled``/``wont_fix`` rows
          are treated as historical and do NOT block creation of a
          fresh gap (callers can use this to re-open a previously
          resolved gap after, e.g., a content re-extraction).
        * When ``persist=False`` (dry-run), builds the candidate rows in
          memory without adding them to the session or flushing the
          underlying gap-scan audit ``ExtractionStep``.

        Returns the list of ``ExtractionGap`` instances that either were
        already tracked or would be persisted.  No DB write occurs when
        ``persist=False``; with ``persist=True`` new rows are added to
        ``self._session`` and flushed once, leaving commit/rollback to
        the caller (one commit per call site, per AC).
        """
        ov = await self._resolve_ontology_version(ontology_version)

        literature_str = str(literature_id)
        # Scope jobs to this OV so jobs under a different ontology
        # version do not bleed in.  corpus_id / source_reference are
        # soft-ids (T1 bridge) but the OV FK is authoritative.
        jobs_stmt = select(ExtractionJob).where(
            ExtractionJob.ontology_version_id == ov.id,
            (ExtractionJob.corpus_id == literature_str)
            | (ExtractionJob.source_reference == literature_str),
        )
        jobs = list((await self._session.execute(jobs_stmt)).scalars().all())

        # Load chunks for the matched jobs (one query, then filter).
        job_ids = [j.id for j in jobs]
        chunks: list[ExtractionChunk] = []
        if job_ids:
            chunks_stmt = select(ExtractionChunk).where(
                ExtractionChunk.job_id.in_(job_ids),
            )
            chunks = list(
                (await self._session.execute(chunks_stmt)).scalars().all(),
            )

        # Existing gaps for this ontology version, all statuses.
        existing_stmt = select(ExtractionGap).where(
            ExtractionGap.ontology_version_id == ov.id,
        )
        existing = list(
            (await self._session.execute(existing_stmt)).scalars().all(),
        )

        # Pick which statuses count as "already tracked" for creation
        # gating.  When ``only_open=True`` (default) every status blocks
        # creation; when ``only_open=False`` only active statuses block.
        blocking_statuses: frozenset[str] = (
            frozenset({"open", "filling", "filled", "wont_fix"})
            if only_open
            else frozenset({"open", "filling"})
        )

        entity_types = self._entity_types(ov)
        result: list[ExtractionGap] = []

        for entity_type, property_name in self._iter_expected_pairs(
            entity_types,
        ):
            if self._is_present(property_name, chunks):
                continue
            tracked = self._find_tracked(
                existing, entity_type, property_name,
            )
            if tracked is not None:
                if tracked.gap_status in blocking_statuses:
                    # Active blocking row → return it without creating.
                    result.append(tracked)
                    continue
                if only_open:
                    # Closed row but default mode still treats it as
                    # already-tracked (no re-opening).
                    result.append(tracked)
                    continue
                # only_open=False and the tracked row is closed → fall
                # through to create a fresh open gap.

            new_gap = ExtractionGap(
                ontology_version_id=ov.id,
                entity_type=entity_type,
                property=property_name,
                gap_status="open",
            )
            if persist:
                self._session.add(new_gap)
            result.append(new_gap)

        if persist:
            await self._session.flush()

        return result

    async def compute_recall(
        self,
        literature_id: uuid.UUID,
        ontology_version: str,
    ) -> RecallMetrics:
        """Per-literature recall rate (ADR Section 2).

        Mirrors the module-level :func:`compute_recall` semantics but is
        scoped to ``(literature_id, ontology_version)``.  Counts gaps
        for the ontology version whose source chunks belong to the
        literature, then computes::

            recall_rate = (total_expected - open - filling) / total_expected

        Edge cases:

        * ``total_expected == 0`` → ``recall_rate = 1.0`` (per ADR).
        * Unknown ``ontology_version`` → raises ``ValueError``.
        """
        ov = await self._resolve_ontology_version(ontology_version)

        literature_str = str(literature_id)
        job_ids_stmt = select(ExtractionJob.id).where(
            (ExtractionJob.corpus_id == literature_str)
            | (ExtractionJob.source_reference == literature_str),
        )
        job_ids = [
            row[0] for row in (await self._session.execute(job_ids_stmt)).all()
        ]

        entity_types = self._entity_types(ov)
        total_expected = _count_expected_properties(entity_types)

        if total_expected == 0:
            return RecallMetrics(
                ontology_version_id=ov.id,
                total_expected=0,
                total_gaps=0,
                open_gaps=0,
                filled_gaps=0,
                wont_fix_gaps=0,
                recall_rate=1.0,
                computed_at=datetime.now(UTC),
            )

        if not job_ids:
            # No jobs linked to this literature → no gaps → fully covered.
            return RecallMetrics(
                ontology_version_id=ov.id,
                total_expected=total_expected,
                total_gaps=0,
                open_gaps=0,
                filled_gaps=0,
                wont_fix_gaps=0,
                recall_rate=1.0,
                computed_at=datetime.now(UTC),
            )

        # Gap rows tied to this literature's jobs.
        # Until T1 adds the ``literature_id`` FK on ``extraction_gaps``,
        # the literature attribution goes via ``gap.chunk_id`` →
        # ``chunk.job_id`` → ``job.corpus_id``.  Gaps with ``chunk_id=None``
        # (legacy NFM-2575 rows that pre-date chunk linkage) are also
        # counted: they're attributed to any literature that has at least
        # one job in this OV's corpus.  This is documented in the
        # ``scan_literature`` docstring as a T1-bridge limitation.
        gap_stmt = (
            select(ExtractionGap.gap_status, func.count())
            .where(
                ExtractionGap.ontology_version_id == ov.id,
                or_(
                    ExtractionGap.chunk_id.is_(None),
                    ExtractionGap.chunk_id.in_(
                        select(ExtractionChunk.id).where(
                            ExtractionChunk.job_id.in_(job_ids),
                        ),
                    ),
                ),
            )
            .group_by(ExtractionGap.gap_status)
        )
        rows = (await self._session.execute(gap_stmt)).all()
        status_counts: dict[str, int] = {row[0]: row[1] for row in rows}
        open_gaps = status_counts.get("open", 0)
        filling_gaps = status_counts.get("filling", 0)
        filled_gaps = status_counts.get("filled", 0)
        wont_fix_gaps = status_counts.get("wont_fix", 0)
        total_gaps = open_gaps + filling_gaps + filled_gaps + wont_fix_gaps

        recall_rate = (
            (total_expected - open_gaps - filling_gaps) / total_expected
        )

        return RecallMetrics(
            ontology_version_id=ov.id,
            total_expected=total_expected,
            total_gaps=total_gaps,
            open_gaps=open_gaps,
            filled_gaps=filled_gaps,
            wont_fix_gaps=wont_fix_gaps,
            recall_rate=recall_rate,
            computed_at=datetime.now(UTC),
        )

    async def compute_coverage(
        self,
        ontology_version: str,
    ) -> CoverageMetrics:
        """Per-ontology-version coverage (ADR Section 2).

        Computes::

            coverage_rate = literature_fully_covered / literature_total

        where ``literature_total`` is the distinct count of literature
        rows associated with this ontology version (currently via
        ``ExtractionJob.corpus_id`` / ``source_reference`` soft match;
        see :meth:`scan_literature` for the T1 migration note).

        ``gap_distribution`` is a ``(entity_type, property) -> open-gap
        count`` map across all linked literatures — surfaced for
        operators to see which tuples dominate the coverage debt.

        **Corpus-level gaps.**  A gap row with ``chunk_id IS NULL``
        cannot be attributed to one literature: an *absent* property has
        no chunk to blame, and migration 047's UNIQUE index is the
        3-tuple ``(ontology_version_id, entity_type, property)``, so one
        row represents that debt for the entire ontology version.
        :meth:`scan_literature` emits exactly these rows.  They are
        counted against **every** literature in the OV, matching
        :meth:`compute_recall`, so that the two methods can never
        disagree on identical data.  NFM-2736 (T1+T2) replaces this with
        a real ``literature_id`` FK and per-literature attribution.

        Edge cases:

        * ``literature_total == 0`` → ``coverage_rate = 0.0`` (documented
          choice: an empty corpus is not "fully covered").
        * Unknown ``ontology_version`` → raises ``ValueError``.
        """
        ov = await self._resolve_ontology_version(ontology_version)
        literature_str_set = await self._literature_ids_for_ontology(ov)

        if not literature_str_set:
            return CoverageMetrics(
                ontology_version=ov.version,
                literature_total=0,
                literature_fully_covered=0,
                gap_distribution={},
                coverage_rate=0.0,
                computed_at=datetime.now(UTC),
            )

        # For each literature, count open-or-filling gaps.
        literature_fully_covered = 0
        gap_distribution: dict[tuple[str, str], int] = {}

        # Load all open/filling gaps for this OV in one query, group by
        # literature soft-id via the joined chunk → job.  The job-side
        # OV filter (via the inner where on ExtractionJob) is required so
        # that gaps from other OVs do not bleed into this OV's coverage
        # via the OUTER join (a chunk/job from OV-B must not match a gap
        # tied to OV-A).
        lit_gaps_stmt = (
            select(ExtractionGap, ExtractionChunk.job_id, ExtractionJob)
            .join(
                ExtractionChunk,
                ExtractionGap.chunk_id == ExtractionChunk.id,
                isouter=True,
            )
            .join(
                ExtractionJob,
                ExtractionChunk.job_id == ExtractionJob.id,
                isouter=True,
            )
            .where(
                ExtractionGap.ontology_version_id == ov.id,
                ExtractionGap.gap_status.in_(("open", "filling")),
            )
            .where(
                or_(
                    ExtractionJob.id.is_(None),
                    ExtractionJob.ontology_version_id == ov.id,
                ),
            )
        )
        rows = (await self._session.execute(lit_gaps_stmt)).all()

        # Map literature_id → count of distinct (entity, property) gaps.
        lit_to_pairs: dict[str, set[tuple[str, str]]] = {
            lit: set() for lit in literature_str_set
        }
        for gap, _chunk_job_id, gap_job in rows:
            pair = (gap.entity_type, gap.property)
            if gap_job is None:
                # Corpus-level gap: the row has no chunk, so there is no
                # job to attribute it to.  ``scan_literature`` produces
                # exactly these rows — an *absent* property has no chunk
                # to blame, and migration 047's UNIQUE index is the
                # 3-tuple (ontology_version_id, entity_type, property),
                # so a single row carries that (entity, property) debt
                # for the whole ontology version.
                #
                # ``compute_recall`` already counts these rows against
                # every literature in the OV (the ``chunk_id.is_(None)``
                # branch of its query).  Count them the same way here so
                # the two methods cannot report contradictory answers on
                # identical data.  Previously they were dropped, which
                # made every ``scan_literature`` gap invisible to
                # coverage (recall 0.0 vs coverage 1.0) and left
                # ``gap_distribution`` permanently empty.
                #
                # NFM-2736 (T1+T2 integration) adds the real
                # ``literature_id`` FK, at which point gaps become
                # genuinely per-literature and this branch goes away.
                for pairs in lit_to_pairs.values():
                    pairs.add(pair)
                continue
            lit_key = gap_job.corpus_id or gap_job.source_reference
            if not lit_key or lit_key not in literature_str_set:
                continue
            lit_to_pairs[lit_key].add(pair)

        for _lit_key, pairs in lit_to_pairs.items():
            if not pairs:
                literature_fully_covered += 1
            for pair in pairs:
                gap_distribution[pair] = gap_distribution.get(pair, 0) + 1

        literature_total = len(literature_str_set)
        coverage_rate = (
            literature_fully_covered / literature_total
            if literature_total > 0
            else 0.0
        )

        return CoverageMetrics(
            ontology_version=ov.version,
            literature_total=literature_total,
            literature_fully_covered=literature_fully_covered,
            gap_distribution=gap_distribution,
            coverage_rate=coverage_rate,
            computed_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Helpers (private — kept narrow so tests can target them later)
    # ------------------------------------------------------------------

    async def _resolve_ontology_version(
        self,
        ontology_version: str,
    ) -> OntologyVersion:
        """Look up an OntologyVersion by its ``.version`` string.

        Raises:
            ValueError: If no published ontology with that semver exists.
        """
        stmt = select(OntologyVersion).where(
            OntologyVersion.version == ontology_version,
        )
        result = await self._session.execute(stmt)
        ov = result.scalar_one_or_none()
        if ov is None:
            raise ValueError(
                f"OntologyVersion not found: {ontology_version}",
            )
        return ov

    async def _literature_ids_for_ontology(
        self,
        ov: OntologyVersion,
    ) -> set[str]:
        """Return the distinct set of literature soft-ids for an OV.

        Scoped to the requested ontology version via
        ``ExtractionJob.ontology_version_id == ov.id`` — without that
        filter every OV would see the union of all jobs in the DB and
        coverage reports would be silently identical across OVs.

        Until T1 adds the real ``literature_id`` FK on
        ``extraction_gaps``, literature identity is approximated by
        ``ExtractionJob.corpus_id`` (preferred) falling back to
        ``source_reference``.  This is enough for the ADR Section 2
        contract (``coverage_rate`` and ``gap_distribution``) and will be
        replaced by direct FK joins when NFM-2736 merges T1+T2+T3.
        """
        stmt = select(
            ExtractionJob.corpus_id, ExtractionJob.source_reference,
        ).where(ExtractionJob.ontology_version_id == ov.id)
        rows = (await self._session.execute(stmt)).all()
        lit_set: set[str] = set()
        for corpus_id, source_reference in rows:
            if corpus_id:
                lit_set.add(corpus_id)
            elif source_reference:
                lit_set.add(source_reference)
        return lit_set

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
        return (
            GapScanService._find_tracked(
                existing, entity_type, property_name,
            )
            is not None
        )

    @staticmethod
    def _find_tracked(
        existing: Iterable[ExtractionGap],
        entity_type: str,
        property_name: str,
    ) -> ExtractionGap | None:
        """Return the existing gap for (entity, property), or None."""
        for gap in existing:
            if (
                gap.entity_type == entity_type
                and gap.property == property_name
            ):
                return gap
        return None


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Sanity check that the step_type constant matches what we write.
    assert "gap_scan" in EXTRACTION_STEP_TYPES
