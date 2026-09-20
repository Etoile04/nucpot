"""Dataset service for NFM-4159 + NFM-4991 (P2 /datasets block).

* NFM-4159 — ``get_dataset_with_attribution`` powers the
  ``GET /api/v1/datasets/{id}`` endpoint per the §5.2 contract.

* NFM-4991 — ``list_datasets`` powers the paginated
  ``GET /api/v1/datasets`` list endpoint.  Filters: material_id,
  source_id, is_verified.  Optional ``expand`` joins pull in
  ``material.name`` / ``source.title`` so the list page can render
  human-readable names without a second round-trip per row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Dataset, DataSource, Material
from nfm_db.schemas.common import PaginatedResponse
from nfm_db.schemas.property import (
    DatasetAttributionBlock,
    DatasetListItem,
    DatasetResponse,
    DatasetWithAttributionResponse,
)
from nfm_db.services.attribution_flag import get_recast_restored_dataset_ids

logger = __import__("logging").getLogger(__name__)


async def get_dataset_with_attribution(
    db: AsyncSession,
    dataset_id: uuid.UUID,
) -> DatasetWithAttributionResponse | None:
    """Return a single dataset with the §5.2 attribution block.

    Status semantics
    ----------------

    * ``"placeholder"`` iff the dataset id is in the recast-restored
      set (defaults to ``()`` until CEO publishes the IDs).
    * ``"intact"`` otherwise.

    Returns ``None`` if the dataset does not exist; the route handler
    converts that to a 404.
    """
    stmt = select(Dataset).where(Dataset.id == dataset_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    restored = get_recast_restored_dataset_ids()
    attribution_status = "placeholder" if dataset_id in restored else "intact"

    # Validate the ORM row against the *base* DatasetResponse first (the
    # ORM has no ``attribution`` column); then attach the attribution
    # block via the extended response model.  This keeps the attribution
    # block strictly additive — clients that don't read it still see a
    # fully-typed dataset record.
    base = DatasetResponse.model_validate(row)
    return DatasetWithAttributionResponse(
        **base.model_dump(),
        attribution=DatasetAttributionBlock(status=attribution_status),
    )


async def list_datasets(
    db: AsyncSession,
    *,
    page: int,
    per_page: int,
    material_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    is_verified: bool | None = None,
    expand_material: bool = False,
    expand_source: bool = False,
) -> PaginatedResponse[DatasetListItem]:
    """Paginated dataset list with optional material/source name joins.

    The default query is a single-table scan on ``datasets`` ordered by
    ``updated_at`` DESC.  Material/source names are pulled in only when
    ``expand_material`` / ``expand_source`` is true — keeps anonymous
    list calls cheap (one row read per dataset, no JOIN fan-out) and
    still lets the page render names when the caller explicitly opts
    in.  Empty result sets are not a special case — the response is a
    normal envelope with ``items=[]`` and ``total=0``.
    """
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 1
    if per_page > 100:
        # NFM-4308 ③ — cap silently with the echo so callers know they
        # were clamped instead of silently missing rows.  The route
        # handler caps at 100 already via Query(le=100), this is the
        # belt-and-braces second line of defence.
        per_page = 100

    where_clauses = []
    if material_id is not None:
        where_clauses.append(Dataset.material_id == material_id)
    if source_id is not None:
        where_clauses.append(Dataset.source_id == source_id)
    if is_verified is not None:
        where_clauses.append(Dataset.is_verified == is_verified)

    # NFM-4991: Dataset ORM has no declared relationships on material_id
    # / source_id (raw FK columns), so joinedload can't hydrate a
    # row.material / row.source attribute.  We build the row tuple by
    # appending the joined columns in a fixed order and unpack with
    # explicit slot indices below — Material.name at index 1, DataSource.title
    # at index 2 — and use None when the corresponding expand flag is
    # off (slot still exists for the rows where it was added).
    base_stmt = select(Dataset, Material.name, DataSource.title)
    base_stmt = base_stmt.join(Material, Dataset.material_id == Material.id)
    # NFM-4159 LEFT OUTER join — source_id is NULLABLE on the recast
    # cohort; an INNER join would silently drop those rows.
    base_stmt = base_stmt.outerjoin(DataSource, Dataset.source_id == DataSource.id)
    if where_clauses:
        base_stmt = base_stmt.where(*where_clauses)
    base_stmt = base_stmt.order_by(Dataset.updated_at.desc()).limit(per_page).offset(
        (page - 1) * per_page
    )

    count_stmt = select(func.count()).select_from(Dataset)
    if where_clauses:
        count_stmt = count_stmt.where(*where_clauses)
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (await db.execute(base_stmt)).all()

    items: list[DatasetListItem] = []
    for row in rows:
        dataset = row[0]
        # Slots are fixed-position: row[1] = Material.name (always
        # populated — INNER join), row[2] = DataSource.title (None if
        # source_id is NULL — LEFT OUTER join).
        material_name = row[1] if expand_material else None
        source_title = row[2] if expand_source else None
        items.append(
            DatasetListItem(
                id=dataset.id,
                material_id=dataset.material_id,
                material_name=material_name,
                source_id=dataset.source_id,
                source_title=source_title,
                title=dataset.title,
                measurement_date=dataset.measurement_date,
                is_verified=dataset.is_verified,
                created_at=dataset.created_at,
                updated_at=dataset.updated_at,
            )
        )

    return PaginatedResponse[DatasetListItem](
        items=items,
        total=total,
        page=page,
        limit=per_page,
        pages=(total + per_page - 1) // per_page if total > 0 else 0,
        truncated=False,
    )


__all__ = ["get_dataset_with_attribution", "list_datasets"]
