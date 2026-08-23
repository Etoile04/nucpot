"""POST /api/ontology/versions registration endpoint (NFM-3591).

Thin route layer that delegates to
:mod:`nfm_db.services.ontology_register`.  The handler:

* Validates the request shape via Pydantic.
* Resolves the authenticated user via the standard
  ``get_current_active_user`` dependency.
* Calls the service which performs the SHA-256 fetch-and-compare and
  the single-row insert in a single transaction.
* Maps service-layer domain errors to the HTTP responses described in
  the issue description.

This route is intentionally mounted at ``/api/ontology/versions`` (no
``/v1`` prefix) — it is a parallel contract to the existing
``/api/v1/ontology/versions`` CRUD surface, with a different focus
(SHA-256 validation, single-row insert, no destructive mutation).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.ontology_register import (
    OntologyVersionRegisterError,
    OntologyVersionRegisterRequest,
    OntologyVersionRegisterResponse,
    extract_display_created_by,
)
from nfm_db.services import ontology_register as register_service

router = APIRouter(tags=["本体版本注册"])


def _bad_request(error_code: str, detail: str) -> HTTPException:
    """Build a structured 400 envelope matching the spec's error shape."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=OntologyVersionRegisterError(
            error=error_code,
            detail=detail,
        ).model_dump(),
    )


@router.post(
    "/ontology/versions",
    response_model=OntologyVersionRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new ontology version with SHA-256 source validation",
    description=(
        "Register a new ``OntologyVersion`` row after verifying the SHA-256 "
        "checksum of the supplied ``source_url`` body.  The endpoint "
        "intentionally does not mutate existing ``k_entity_types`` or "
        "``k_relation_types`` rows — only inserts a single new version row. "
        "(NFM-3591)"
    ),
)
async def register_ontology_version(
    body: OntologyVersionRegisterRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OntologyVersionRegisterResponse:
    """Register a new ontology version with SHA-256 source validation.

    Errors:
        400 ``source_url_required`` — ``source_url`` was null/empty.
        400 ``checksum_mismatch`` — computed SHA-256 did not match.
        409 ``version_tag_exists`` — ``version_tag`` collides with an
            existing row (mapped from IntegrityError on the UNIQUE
            constraint).
    """
    if not body.source_url:
        # Fail fast at the API boundary so the service can assume a
        # non-empty string.  The service has its own guard as a
        # defense-in-depth measure for direct service callers.
        raise _bad_request(
            error_code="source_url_required",
            detail="source_url required for checksum validation",
        )

    # The service stores the request's display ``created_by`` string
    # alongside ``source_url`` and ``checksum`` inside ``ontology_data``
    # so the response can echo it without a second transaction (the
    # row's FK column carries the authenticated user's UUID, not the
    # request string).
    raw_display = body.created_by

    try:
        ov = await register_service.register_ontology_version(
            session,
            version_tag=body.version_tag,
            created_by_user_id=current_user.id,
            created_by_display=raw_display,
            source_url=body.source_url,
            checksum=body.checksum,
        )
    except register_service.SourceUrlRequiredError as exc:
        raise _bad_request(
            error_code="source_url_required",
            detail=str(exc),
        ) from exc
    except register_service.ChecksumMismatchError as exc:
        raise _bad_request(
            error_code="checksum_mismatch",
            detail=f"expected {exc.expected}, got {exc.observed}",
        ) from exc
    except register_service.VersionTagExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=OntologyVersionRegisterError(
                error="version_tag_exists",
                detail=str(exc),
            ).model_dump(),
        ) from exc
    except register_service.SourceFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=OntologyVersionRegisterError(
                error="source_fetch_failed",
                detail=str(exc),
            ).model_dump(),
        ) from exc

    # The service committed the row in a single transaction; just read
    # back the display string for the response.
    display = extract_display_created_by(ov.ontology_data)
    if not display:
        # Defensive fallback: echo the authenticated user's email.
        display = current_user.email or str(current_user.id)

    return OntologyVersionRegisterResponse(
        id=ov.id,
        version_tag=ov.version,
        created_at=ov.created_at,
        created_by=display,
        source_url=(ov.ontology_data or {}).get("source_url"),
        checksum=(ov.ontology_data or {}).get("checksum", ""),
    )


__all__ = ["router"]


#: Re-exported so ``main.py`` can ``include_router(ontology_router, prefix="/api")``.
ontology_router = router
