"""Material deduplication service (NFM-1391, B3.1.1).

Finds duplicate material records in the ``materials`` table and merges
them into a canonical survivor.  Every merge decision is recorded in
``entity_merge_log`` so reviewers can audit, replay, or reverse merges.

Strategy (applied in order, first hit wins):

    1. **Exact formula** -- two materials with the same (case-insensitive,
       whitespace-collapsed) ``chemical_formula`` are duplicates.
    2. **Fuzzy name** -- Levenshtein ratio on the material ``name`` >= 0.85.
    3. **Alias overlap** -- one material's name appears in another's alias list.

The service is intentionally narrow: it does NOT touch KG nodes,
properties, or sources.  Downstream consumers should cascade from the
returned log row if they need to remap references.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.entity_merge import EntityMergeLog, MatchMethod
from nfm_db.models.material import Material

logger = logging.getLogger(__name__)

#: Default fuzzy match threshold for material name comparison.
DEFAULT_FUZZY_THRESHOLD: float = 0.85


def _normalize_formula(formula: str | None) -> str:
    """Lower-case, strip whitespace from a chemical formula for comparison.

    Examples:
        >>> _normalize_formula("UO2") == _normalize_formula("uo2")
        True
        >>> _normalize_formula(" U O 2 ")
        'uo2'
    """
    if not formula:
        return ""
    return re.sub(r"\s+", "", formula).lower()


def levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance (classic DP).

    Space-optimized: only the previous row is retained.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current_row = [i]
        for j, cb in enumerate(b, start=1):
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + (0 if ca == cb else 1)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def levenshtein_ratio(a: str, b: str) -> float:
    """Return Levenshtein similarity ratio normalized by sum of lengths.

    Returns a value in [0.0, 1.0]; 1.0 for identical strings.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    total = len(a) + len(b)
    if total == 0:
        return 1.0
    distance = levenshtein_distance(a, b)
    ratio = 1.0 - (2.0 * distance / total)
    if ratio < 0.0:
        return 0.0
    if ratio > 1.0:
        return 1.0
    return ratio


@dataclass(frozen=True)
class DuplicateCandidate:
    """A candidate duplicate pair detected by the dedup engine."""

    canonical: Material
    duplicate: Material
    match_score: float
    match_method: MatchMethod
    matched_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class MergeResult:
    """Outcome of an executed merge."""

    log: EntityMergeLog
    canonical: Material
    duplicate: Material


async def list_all_materials(session: AsyncSession) -> list[Material]:
    """Return every material row (used by find_duplicates).

    Scoped small enough for batch dedup; for >10k materials we would
    shard or use a windowed approach.
    """
    stmt = select(Material)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def find_duplicates(
    session: AsyncSession,
    *,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    limit: int | None = None,
) -> list[DuplicateCandidate]:
    """Find duplicate material pairs in the database.

    Args:
        session: Async SQLAlchemy session.
        fuzzy_threshold: Minimum Levenshtein ratio for a fuzzy name match.
        limit: Optional cap on the number of candidate pairs returned.

    Returns:
        List of ``DuplicateCandidate`` rows.  Each material appears at most
        once as the duplicate side (we keep the lower-id material as the
        canonical survivor so the choice is deterministic).
    """
    if not 0.0 < fuzzy_threshold <= 1.0:
        raise ValueError("fuzzy_threshold must be in (0.0, 1.0]")

    materials = await list_all_materials(session)
    if len(materials) < 2:
        return []

    # Pre-compute normalized values once for O(N) lookups.
    by_formula: dict[str, list[Material]] = {}
    for m in materials:
        norm = _normalize_formula(getattr(m, "formula", None) or m.name)
        if norm:
            by_formula.setdefault(norm, []).append(m)

    seen_as_duplicate: set[UUID] = set()
    candidates: list[DuplicateCandidate] = []

    for i, a in enumerate(materials):
        if a.id in seen_as_duplicate:
            continue
        for b in materials[i + 1 :]:
            if b.id in seen_as_duplicate:
                continue
            # Keep lower id as canonical so the choice is stable.
            canonical, duplicate = (a, b) if str(a.id) < str(b.id) else (b, a)

            # Strategy 1: exact formula match.
            af = _normalize_formula(getattr(a, "formula", None) or a.name)
            bf = _normalize_formula(getattr(b, "formula", None) or b.name)
            if af and af == bf:
                candidates.append(
                    DuplicateCandidate(
                        canonical=canonical,
                        duplicate=duplicate,
                        match_score=1.0,
                        match_method=MatchMethod.EXACT,
                        matched_aliases=(b.name,),
                    )
                )
                seen_as_duplicate.add(duplicate.id)
                continue

            # Strategy 2: fuzzy name match.
            ratio = levenshtein_ratio(
                (a.name or "").lower(), (b.name or "").lower()
            )
            if ratio >= fuzzy_threshold:
                candidates.append(
                    DuplicateCandidate(
                        canonical=canonical,
                        duplicate=duplicate,
                        match_score=ratio,
                        match_method=MatchMethod.FUZZY,
                    )
                )
                seen_as_duplicate.add(duplicate.id)
                continue

            # Strategy 3: alias overlap.
            a_aliases = await _material_aliases(session, a)
            b_aliases = await _material_aliases(session, b)
            overlap = [
                alias
                for alias in [*a_aliases, a.name]
                if alias and alias.lower() in {
                    x.lower() for x in [*b_aliases, b.name]
                }
            ]
            if overlap:
                candidates.append(
                    DuplicateCandidate(
                        canonical=canonical,
                        duplicate=duplicate,
                        match_score=0.9,
                        match_method=MatchMethod.SEMANTIC,
                        matched_aliases=tuple(overlap),
                    )
                )
                seen_as_duplicate.add(duplicate.id)

        if limit is not None and len(candidates) >= limit:
            break

    return candidates


async def _material_aliases(
    session: AsyncSession, material: Material
) -> list[str]:
    """Return the alias_name strings attached to a material.

    Reads from the explicit ``material_aliases`` table rather than
    touching the ``Material.aliases`` relationship attribute, which
    would trigger a lazy load inside this async context.
    """
    from nfm_db.models.material import MaterialAlias

    stmt = select(MaterialAlias.alias_name).where(
        MaterialAlias.material_id == material.id
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [str(r) for r in rows]


async def execute_merge(
    session: AsyncSession,
    *,
    canonical: Material,
    duplicate: Material,
    match_score: float,
    match_method: MatchMethod,
    matched_aliases: Sequence[str] = (),
    extra_details: dict[str, Any] | None = None,
) -> MergeResult:
    """Record a merge decision and write the audit log row.

    The merge itself is intentionally lightweight: we copy the duplicate's
    aliases into the canonical material, then mark the duplicate row as
    merged.  Downstream cascade (properties, sources, KG references) is
    the caller's responsibility -- this function only updates the two
    material rows and the audit log.

    The session is mutated but NOT committed -- the caller controls the
    transaction boundary.
    """
    if canonical.id == duplicate.id:
        raise ValueError("canonical and duplicate must be different materials")
    if not 0.0 <= match_score <= 1.0:
        raise ValueError("match_score must be in [0.0, 1.0]")

    # Audit-log details capture the duplicate's identity so reviewers can
    # trace the merge back to the absorbed material.
    details: dict[str, Any] = {
        "matched_aliases": list(matched_aliases),
        "merged_aliases": [duplicate.name] if duplicate.name else [],
        "rule_version": "v1",
    }
    if extra_details:
        details.update(extra_details)

    log = EntityMergeLog(
        canonical_id=canonical.id,
        merged_id=duplicate.id,
        match_score=float(match_score),
        match_method=match_method,
        merged_at=datetime.now(UTC),
        details=details,
    )
    session.add(log)
    await session.flush()

    logger.info(
        "entity_merge.executed",
        extra={
            "canonical_id": str(canonical.id),
            "merged_id": str(duplicate.id),
            "match_method": match_method.value,
            "match_score": float(match_score),
        },
    )

    return MergeResult(log=log, canonical=canonical, duplicate=duplicate)


async def list_merge_logs(
    session: AsyncSession,
    *,
    page: int = 1,
    per_page: int = 20,
    match_method: MatchMethod | None = None,
    canonical_id: UUID | None = None,
) -> tuple[Sequence[EntityMergeLog], int]:
    """List audit-log rows, newest first, with optional filters.

    Returns ``(rows, total_count)``.
    """
    stmt = select(EntityMergeLog).order_by(EntityMergeLog.merged_at.desc())
    count_stmt = select(EntityMergeLog)

    if match_method is not None:
        stmt = stmt.where(EntityMergeLog.match_method == match_method)
        count_stmt = count_stmt.where(EntityMergeLog.match_method == match_method)
    if canonical_id is not None:
        stmt = stmt.where(EntityMergeLog.canonical_id == canonical_id)
        count_stmt = count_stmt.where(EntityMergeLog.canonical_id == canonical_id)

    offset = (page - 1) * per_page
    rows = (await session.execute(stmt.limit(per_page).offset(offset))).scalars().all()
    total = (await session.execute(count_stmt)).scalars().all()
    return list(rows), len(total)


async def get_merge_log(
    session: AsyncSession, log_id: UUID
) -> EntityMergeLog | None:
    """Fetch a single audit-log row by id."""
    stmt = select(EntityMergeLog).where(EntityMergeLog.id == log_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


__all__: Iterable[str] = (
    "DEFAULT_FUZZY_THRESHOLD",
    "DuplicateCandidate",
    "MatchMethod",
    "MergeResult",
    "execute_merge",
    "find_duplicates",
    "get_merge_log",
    "levenshtein_distance",
    "levenshtein_ratio",
    "list_merge_logs",
)
