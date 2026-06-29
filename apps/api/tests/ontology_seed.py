"""Shared test helpers: seed ``_ref_gap_fill_staging`` for an ontology corpus."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)


async def seed_corpus(
    db_session: AsyncSession,
    *,
    source: str,
    rows: list[dict],
    status: StagingStatus = StagingStatus.PENDING,
) -> None:
    """Persist staging rows for a corpus (identified by ``source``).

    Each row dict accepts: element_system, property_name, value, unit, and
    optional method/phase. ``source`` is set on every row (= corpus_id).
    """
    for index, row in enumerate(rows):
        record = RefGapFillStaging(
            element_system=row["element_system"],
            property_name=row["property_name"],
            value=row["value"],
            unit=row["unit"],
            source=source,
            method=row.get("method"),
            phase=row.get("phase"),
            confidence=Confidence.MEDIUM,
            dedup_hash=(
                f"{source}:{index}:{row['element_system']}:{row['property_name']}"
            ),
            range_validated=True,
            status=status,
        )
        db_session.add(record)
    await db_session.commit()
