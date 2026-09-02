"""Dataset service for NFM-4159 — ``GET /api/v1/datasets/{id}``.

The dataset routes previously did not exist; only dataset references
embedded inside ``PropertyMeasurementDetailResponse``.  This module
adds the missing read-only dataset endpoint per the §5.2 contract.

No UI affordance is attached to the returned ``attribution`` block —
the placeholder title is itself the disclosure (§4.2 / CEO directive).
The block exists so the frontend can assert the negative in its
regression tests (§7c backstop).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Dataset
from nfm_db.schemas.property import (
    DatasetAttributionBlock,
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


__all__ = ["get_dataset_with_attribution"]
