"""Per-job step lifecycle API (NFM-3543 Phase 1.5 / 1.6 / 1.7).

This router hosts the per-extraction-job step endpoints introduced by
NFM-3543 Phase 1. The paths deliberately mirror the resource hierarchy
(``/jobs/{id}/steps/{name}``) rather than the legacy extraction-action
paths (``/extraction/jobs/{id}/steps/{name}`` from NFM-2883/NFM-2884)
so clients can transition cleanly to the V2 contract.

Routes:
- ``GET  /jobs/{id}/steps/{name}``        — Sibling C (NFM-3597) owns this.
- ``POST /jobs/{id}/steps/{name}/rerun``  — NFM-3543-D (this issue, NFM-3598).

The rerun endpoint is idempotent on duplicate requests with the same
``Idempotency-Key`` header (preferred) or ``client_request_id`` body
field within a 24h window. See ``docs/api/jobs.md`` for the contract.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.jobs import (
    RerunStepRequest,
    RerunStepResponse,
)
from nfm_db.services.extraction_pipeline import (
    StepRerunInFlightError,
    StepRerunJobNotFoundError,
    StepRerunSucceededError,
    StepRerunUnknownStepError,
    trigger_step_rerun,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs-steps"])

# Header name per the issue contract (NFM-3543-D AC-1).
IDEMPOTENCY_HEADER = "Idempotency-Key"
# Header surfaced on replays per AC-3.
REPLAYED_HEADER = "Idempotent-Replayed"


@router.post(
    "/jobs/{job_id}/steps/{step_name}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-execute a single extraction pipeline step (idempotent).",
    description=(
        "Mint a fresh ``track_id``, persist a new ``ExtractionStep`` row, "
        "and re-run one named pipeline step on the given job.\n\n"
        "The historical step's ``track_id`` is preserved on the original "
        "row. Duplicate requests with the same ``Idempotency-Key`` header "
        "(preferred) or ``client_request_id`` body field within a 24h "
        "window replay the original 202 with ``Idempotent-Replayed: true``.\n\n"
        "- **202** — new ``track_id`` returned in the body, plus "
        "``Idempotent-Replayed: true`` on replays.\n"
        "- **404** — unknown ``job_id`` or ``step_name`` "
        "(``step_not_found``).\n"
        "- **409** — another rerun is already in flight for the same "
        "(job, step) (``step_in_flight``).\n"
        "- **422** — the latest step is in a terminal-success state "
        "and ``force`` is false (``step_succeeded``)."
    ),
)
async def rerun_step(
    job_id: UUID,
    step_name: str,
    response: Response,
    payload: RerunStepRequest | None = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias=IDEMPOTENCY_HEADER, description=(
            "Idempotency token. If both this header and "
            "``client_request_id`` are present, the header wins."
        )),
    ] = None,
    session: AsyncSession = Depends(get_db),
) -> RerunStepResponse:
    """``POST /jobs/{id}/steps/{name}/rerun`` — NFM-3543-D (NFM-3598).

    See module docstring for the full contract. Maps
    :class:`nfm_db.services.extraction_pipeline.StepRerunError`
    subclasses to their HTTP status codes per the issue's AC.
    """
    body = payload or RerunStepRequest()
    effective_key = idempotency_key or body.client_request_id

    try:
        job, step, replayed, original_track_id = await trigger_step_rerun(
            session,
            job_id=job_id,
            step_name=step_name,
            idempotency_key=effective_key,
            force=body.force,
        )
    except StepRerunJobNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "step_not_found",
                "message": str(exc),
            },
        )
    except StepRerunUnknownStepError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "step_not_found",
                "message": str(exc),
            },
        )
    except StepRerunInFlightError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "step_in_flight",
                "message": str(exc),
            },
        )
    except StepRerunSucceededError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "step_succeeded",
                "message": str(exc),
            },
        )

    if response is not None:
        response.headers[REPLAYED_HEADER] = "true" if replayed else "false"

    # The new track_id lives in the step's metadata_ JSONB (written by
    # the orchestrator's ``rerun_step``). Fall back to a fresh UUID if
    # the column happened to not be persisted (defensive — should never
    # happen in practice).
    meta = step.metadata_ or {}
    new_track_str = meta.get("track_id") or str(step.id)
    try:
        new_track_id = UUID(new_track_str)
    except (TypeError, ValueError):
        new_track_id = step.id

    return RerunStepResponse(
        job_id=job.id,
        step_name=step_name,
        track_id=new_track_id,
        original_track_id=original_track_id,
        status=step.status,
        accepted_at=datetime.now(UTC),
    )
