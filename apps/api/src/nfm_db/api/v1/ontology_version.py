"""Ontology Version CRUD API with role-based access (NFM-2580).

Endpoints for managing versioned ontology schemas with domain_expert
role restriction, auto-versioning on publish, and upload validation.

Lifecycle: draft → published → deprecated.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import (
    get_current_active_user,
    require_domain_expert,
)
from nfm_db.database import get_db
from nfm_db.models.ontology_version import (
    ONTOLOGY_VERSION_STATUSES,
    OntologyVersion,
)
from nfm_db.models.user import User
from nfm_db.schemas.common import PaginatedResponse, PaginationParams
from nfm_db.schemas.ontology_version import (
    OntologyDataUpload,
    OntologyVersionCreate,
    OntologyVersionRead,
    OntologyVersionUpdate,
    PublishRequest,
)

router = APIRouter(tags=["本体版本管理"])

# Default initial semver when no published version exists yet.
_INITIAL_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bump_semver(current: str, level: str) -> str:
    """Increment a semver string by the given level (major|minor|patch).

    ``current`` must be ``X.Y.Z`` format.  Returns the bumped string.
    """
    parts = current.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver: {current!r}")

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    # patch (default)
    return f"{major}.{minor}.{patch + 1}"


def _validate_ontology_data(data: dict) -> None:
    """Validate that ontology JSON has required top-level keys.

    Returns 422-compatible error detail if validation fails.
    """
    required_keys = {"entity_types", "relation_types"}
    missing = required_keys - set(data.keys())
    if missing:
        raise ValueError(
            f"Ontology JSON missing required keys: {', '.join(sorted(missing))}"
        )


# ---------------------------------------------------------------------------
# Read endpoints (any authenticated user)
# ---------------------------------------------------------------------------


@router.get(
    "/ontology/versions",
    response_model=PaginatedResponse[OntologyVersionRead],
    summary="List ontology versions",
    description="Paginated list of all ontology versions. Any authenticated user can access.",
)
async def list_versions(
    _current_user: Annotated[User, Depends(get_current_active_user)],
    pagination: PaginationParams = Depends(PaginationParams),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Optional status filter (draft|published|deprecated).",
    ),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[OntologyVersionRead]:
    """Return a paginated list of ontology versions."""
    query = select(OntologyVersion).order_by(OntologyVersion.created_at.desc())

    if status_filter is not None:
        if status_filter not in ONTOLOGY_VERSION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid status: {status_filter!r}. "
                    f"Must be one of {ONTOLOGY_VERSION_STATUSES}."
                ),
            )
        query = query.where(OntologyVersion.status == status_filter)

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    # Paginated items
    paginated_query = query.offset(pagination.offset).limit(pagination.per_page)
    result = await session.execute(paginated_query)
    versions = result.scalars().all()

    items = [OntologyVersionRead.model_validate(v) for v in versions]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        limit=pagination.per_page,
        pages=pagination.pages(total),
    )


@router.get(
    "/ontology/versions/latest/download",
    summary="Download latest published ontology",
    description="Download the latest published ontology version as a JSON file with Content-Disposition header.",
)
async def download_latest(
    _current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Return the latest published ontology version as a downloadable JSON file."""
    query = (
        select(OntologyVersion)
        .where(OntologyVersion.status == "published")
        .order_by(OntologyVersion.created_at.desc())
        .limit(1)
    )
    result = await session.execute(query)
    latest = result.scalar_one_or_none()

    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published ontology version found.",
        )

    content = json.dumps(latest.ontology_data, ensure_ascii=False, indent=2)
    filename = f"ontology-v{latest.version}.json"

    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Write endpoints (domain_expert only)
# ---------------------------------------------------------------------------


@router.post(
    "/ontology/versions",
    response_model=OntologyVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create draft ontology version",
    description="Create a new draft ontology version. Domain expert only.",
)
async def create_draft(
    body: OntologyVersionCreate,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> OntologyVersionRead:
    """Create a new draft ontology version."""
    version = OntologyVersion(
        version=_INITIAL_VERSION,
        status="draft",
        changelog=body.changelog,
        ontology_data=body.ontology_data,
        created_by=_current_user.id,
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return OntologyVersionRead.model_validate(version)


@router.put(
    "/ontology/versions/{version_id}",
    response_model=OntologyVersionRead,
    summary="Update draft ontology version",
    description="Update a draft ontology version's data and/or changelog. Only drafts can be updated. Domain expert only.",
)
async def update_draft(
    version_id: uuid.UUID,
    body: OntologyVersionUpdate,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> OntologyVersionRead:
    """Update a draft ontology version."""
    result = await session.execute(
        select(OntologyVersion).where(OntologyVersion.id == version_id)
    )
    version = result.scalar_one_or_none()

    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ontology version {version_id} not found.",
        )

    if version.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only draft versions can be updated.",
        )

    if body.ontology_data is not None:
        version.ontology_data = body.ontology_data
    if body.changelog is not None:
        version.changelog = body.changelog
    if body.status is not None:
        if body.status not in ("draft", "published", "deprecated"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status: {body.status!r}.",
            )
        version.status = body.status

    await session.commit()
    await session.refresh(version)
    return OntologyVersionRead.model_validate(version)


@router.post(
    "/ontology/versions/{version_id}/publish",
    response_model=OntologyVersionRead,
    summary="Publish draft ontology version",
    description="Publish a draft version, auto-incrementing semver. Changelog is mandatory. Domain expert only.",
)
async def publish_version(
    version_id: uuid.UUID,
    body: PublishRequest,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> OntologyVersionRead:
    """Publish a draft ontology version with auto-semver bump."""
    result = await session.execute(
        select(OntologyVersion).where(OntologyVersion.id == version_id)
    )
    version = result.scalar_one_or_none()

    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ontology version {version_id} not found.",
        )

    if version.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only draft versions can be published.",
        )

    # Find latest published version to determine next version number
    latest_published_query = (
        select(OntologyVersion)
        .where(OntologyVersion.status == "published")
        .order_by(OntologyVersion.created_at.desc())
        .limit(1)
    )
    latest_result = await session.execute(latest_published_query)
    latest_published = latest_result.scalar_one_or_none()

    if latest_published is None:
        new_version = _INITIAL_VERSION
    else:
        new_version = _bump_semver(latest_published.version, body.bump)

    version.version = new_version
    version.status = "published"
    version.changelog = body.changelog

    await session.commit()
    await session.refresh(version)
    return OntologyVersionRead.model_validate(version)


@router.post(
    "/ontology/versions/upload",
    response_model=OntologyVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload ontology JSON",
    description="Upload ontology JSON with validation. Must contain entity_types and relation_types. Creates a new draft. Domain expert only.",
)
async def upload_ontology(
    body: OntologyDataUpload,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> OntologyVersionRead:
    """Upload ontology JSON, validate structure, create as draft."""
    try:
        _validate_ontology_data(body.ontology_data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    version = OntologyVersion(
        version=_INITIAL_VERSION,
        status="draft",
        changelog=body.changelog,
        ontology_data=body.ontology_data,
        created_by=_current_user.id,
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return OntologyVersionRead.model_validate(version)


@router.post(
    "/ontology/versions/{version_id}/deprecate",
    response_model=OntologyVersionRead,
    summary="Deprecate published ontology version",
    description="Deprecate a published ontology version. Domain expert only.",
)
async def deprecate_version(
    version_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> OntologyVersionRead:
    """Deprecate a published ontology version."""
    result = await session.execute(
        select(OntologyVersion).where(OntologyVersion.id == version_id)
    )
    version = result.scalar_one_or_none()

    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ontology version {version_id} not found.",
        )

    if version.status != "published":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only published versions can be deprecated.",
        )

    version.status = "deprecated"

    await session.commit()
    await session.refresh(version)
    return OntologyVersionRead.model_validate(version)
