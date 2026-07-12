"""Service layer for data source CRUD operations (NFM-698).

Provides async functions for listing, retrieving, and creating
data sources with eager-loaded author relationships.
"""

import logging
import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nfm_db.models import DataSource, DataSourceAuthor
from nfm_db.schemas.common import PaginatedResponse
from nfm_db.schemas.source import (
    AuthorResponse,
    DataSourceAuthorResponse,
    DataSourceCreate,
    DataSourceDetailResponse,
    DataSourceResponse,
)

logger = logging.getLogger(__name__)


async def list_sources(
    db: AsyncSession,
    *,
    year: int | None = None,
    source_type: str | None = None,
    page: int = 1,
    per_page: int = 20,
    sort: str = "created_at",
    order: Literal["asc", "desc"] = "desc",
) -> PaginatedResponse[DataSourceResponse]:
    """Return a paginated, filtered list of data sources."""

    stmt = select(DataSource)

    if year is not None:
        stmt = stmt.where(DataSource.year == year)

    if source_type is not None:
        stmt = stmt.where(DataSource.source_type == source_type)

    sort_column = {
        "created_at": DataSource.created_at,
        "updated_at": DataSource.updated_at,
        "title": DataSource.title,
        "year": DataSource.year,
    }.get(sort, DataSource.created_at)

    direction = sort_column.desc() if order == "desc" else sort_column.asc()
    stmt = stmt.order_by(direction)

    # Count total matching rows
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)
    rows = (await db.execute(stmt)).scalars().all()

    items = [DataSourceResponse.model_validate(r) for r in rows]
    pages = max(1, -(-total // per_page)) if total > 0 else 0  # ceil

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=per_page,
        pages=pages,
    )


async def get_source(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> DataSourceDetailResponse | None:
    """Return a single source with authors, ordered by author_order.

    Uses selectinload for the DataSource → DataSourceAuthor → Author chain.
    Returns None if not found.
    """

    stmt = (
        select(DataSource)
        .options(selectinload(DataSource.data_source_authors).selectinload(DataSourceAuthor.author))
        .where(DataSource.id == source_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()

    if row is None:
        return None

    # Build the flat source response from the row (no relationship access)
    source_resp = DataSourceResponse.model_validate(row)

    # Build author responses from eagerly-loaded junction entries
    sorted_links = sorted(row.data_source_authors, key=lambda x: x.author_order)
    authors = [
        DataSourceAuthorResponse(
            id=link.id,
            data_source_id=link.data_source_id,
            author_id=link.author_id,
            author_order=link.author_order,
            is_corresponding=link.is_corresponding,
            created_at=link.created_at,
            updated_at=link.updated_at,
            author=AuthorResponse.model_validate(link.author),
        )
        for link in sorted_links
    ]

    return DataSourceDetailResponse(
        **source_resp.model_dump(),
        authors=authors,
    )


async def create_source(
    db: AsyncSession,
    data: DataSourceCreate,
) -> DataSourceResponse:
    """Create a new data source and return the response."""

    source = DataSource(**data.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)

    return DataSourceResponse.model_validate(source)
