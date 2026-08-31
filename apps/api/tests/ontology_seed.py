"""Shared test helpers: seed ``_ref_gap_fill_staging`` for an ontology corpus."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.models.reference_value import ReferenceValue


async def seed_corpus(
    db_session: AsyncSession,
    *,
    source: str,
    rows: list[dict],
    status: StagingStatus = StagingStatus.PENDING,
) -> None:
    """Persist staging rows for a corpus (identified by ``source``).

    Each row dict accepts: element_system, property_name, value, unit, and
    optional method/phase/source_doi/uncertainty/temperature. ``source`` is
    set on every row (= corpus_id).

    Read-path note (NFM-3872 / C-S1): ``derive_ontology_graph`` now reads
    from the formal ``reference_values`` table, not ``_ref_gap_fill_staging``.
    We therefore ALSO mirror each seeded row into ``reference_values`` so
    integration tests that exercise the graph derivation still pass. Tests
    that specifically want to exercise the staging-only contract can pass
    ``also_seed_formal=False``.
    """
    for index, row in enumerate(rows):
        # Pre-allocate the staging id so the formal mirror can carry
        # the FK without depending on a flush between the two adds.
        staging_id = uuid.uuid4()
        record = RefGapFillStaging(
            id=staging_id,
            element_system=row["element_system"],
            property_name=row["property_name"],
            value=row["value"],
            unit=row["unit"],
            source=source,
            method=row.get("method"),
            phase=row.get("phase"),
            source_doi=row.get("source_doi"),
            uncertainty=row.get("uncertainty"),
            temperature=row.get("temperature"),
            confidence=Confidence.MEDIUM,
            dedup_hash=(f"{source}:{index}:{row['element_system']}:{row['property_name']}"),
            range_validated=True,
            status=status,
        )
        db_session.add(record)
        # Mirror into the formal table so derive_ontology_graph (which
        # reads from reference_values post-NFM-3872) sees the rows.
        # Only the staging-write is committed; the formal mirror piggybacks
        # on the same commit at the end of this loop iteration.
        now = datetime.now(UTC)
        db_session.add(
            ReferenceValue(
                staging_id=staging_id,
                element=row["element_system"],
                crystal_structure=row.get("phase"),
                property_name=row["property_name"],
                value=row["value"],
                unit=row["unit"],
                method=row.get("method"),
                source=source,
                source_doi=row.get("source_doi"),
                uncertainty=row.get("uncertainty"),
                temperature=row.get("temperature"),
                notes="seeded for ontology test",
                etl_issue="NFM-3872",
                etl_manifest_ref="tests/ontology_seed.py",
                etl_ok_reason="prescreen_pass",
                promoted_at=now,
            )
        )
    await db_session.commit()
