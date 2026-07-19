"""Profile, contributions, and aggregate-stats API endpoints.

These endpoints replace Supabase-dependent Next.js API routes for:
  - GET/PATCH  /api/v1/auth/profile      — user profile CRUD
  - GET        /api/v1/contributions/me  — potentials uploaded by current user
  - GET        /api/v1/stats             — aggregate database statistics
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.models.potential import Potential
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.profile import (
    ContributionItem,
    ProfileResponse,
    ProfileUpdate,
    RecentPotentialItem,
    StatsResponse,
)

# --- routers ---------------------------------------------------------------
# Three separate routers because the three endpoint groups live under
# different URL prefixes (/auth, /contributions, /stats).

profile_router = APIRouter(prefix="/auth", tags=["用户资料"])
contributions_router = APIRouter(prefix="/contributions", tags=["贡献"])
stats_router = APIRouter(prefix="/stats", tags=["统计"])


# --- profile endpoints -----------------------------------------------------


@profile_router.get("/profile", response_model=ApiResponse[ProfileResponse])
async def get_profile(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ApiResponse[ProfileResponse]:
    """Get the current user's full profile."""
    return ApiResponse(
        success=True,
        data=ProfileResponse.model_validate(current_user),
    )


@profile_router.patch("/profile", response_model=ApiResponse[ProfileResponse])
async def update_profile(
    update_data: ProfileUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[ProfileResponse]:
    """Update the current user's profile fields.

    Only the fields present in the request body are updated
    (``exclude_unset=True`` semantics), so partial PATCHes work.
    """
    for field, value in update_data.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)

    return ApiResponse(
        success=True,
        data=ProfileResponse.model_validate(current_user),
    )


# --- contributions endpoint ------------------------------------------------


@contributions_router.get(
    "/me",
    response_model=ApiResponse[list[ContributionItem]],
)
async def get_my_contributions(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[list[ContributionItem]]:
    """Get potentials uploaded by the current user.

    The Potential model does not yet have an uploader / created_by
    field, so for now we return an empty list.  Once a field like
    ``uploader_id`` is added, replace the body with::

        result = await db.execute(
            select(Potential)
            .where(Potential.uploader_id == current_user.id)
            .order_by(Potential.created_at.desc())
        )
        ...
    """
    # TODO: add uploader_id to Potential model, then filter by current_user.id
    return ApiResponse(success=True, data=[])


# --- stats endpoint --------------------------------------------------------


@stats_router.get("", response_model=ApiResponse[StatsResponse])
async def get_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[StatsResponse]:
    """Get aggregate database statistics.

    Returns total potential count, distinct potential types, distinct
    elements across all potentials, and the five most recently added
    potentials.
    """
    # Total potentials
    total_result = await db.execute(select(func.count(Potential.id)))
    total_potentials = total_result.scalar_one()

    # Distinct potential types
    types_result = await db.execute(select(func.count(func.distinct(Potential.type))))
    total_types = types_result.scalar_one()

    # Distinct elements (stored as JSON arrays — flatten in Python)
    elements_result = await db.execute(select(Potential.elements))
    element_set: set[str] = set()
    for elements_row in elements_result.scalars():
        if elements_row:
            element_set.update(elements_row)

    # Recent potentials (5 most recent)
    recent_result = await db.execute(
        select(Potential).order_by(Potential.created_at.desc()).limit(5),
    )
    recent_potentials = [
        RecentPotentialItem(
            id=str(p.id),
            name=p.name,
            display_name=p.display_name,
            type=p.type,
            elements=p.elements or [],
            created_at=p.created_at.isoformat() if p.created_at else None,
        )
        for p in recent_result.scalars()
    ]

    return ApiResponse(
        success=True,
        data=StatsResponse(
            total_potentials=total_potentials,
            total_types=total_types,
            total_elements=len(element_set),
            recent_potentials=recent_potentials,
        ),
    )


__all__ = [
    "contributions_router",
    "profile_router",
    "stats_router",
]
