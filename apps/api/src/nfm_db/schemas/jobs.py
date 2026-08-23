"""Schemas for /jobs/* step API (NFM-3543 Phase 1.5/1.6/1.7).

Sibling C (NFM-3597) owns ``GET /jobs/{id}/steps/{name}``.
Sibling D (NFM-3598) owns ``POST /jobs/{id}/steps/{name}/rerun``.
Both endpoints live in ``nfm_db.api.v1.jobs``; this file collects their
shared request / response Pydantic models so the OpenAPI schema stays
in sync.

The rerun endpoint is idempotent on duplicate requests with the same
``Idempotency-Key`` header (or ``client_request_id`` body field)
within a 24h window. See ``docs/api/jobs.md`` for the contract.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# GET /jobs/{id}/steps/{name}  (NFM-3543-C)
# ---------------------------------------------------------------------------


class StepStatusResponse(BaseModel):
    """Status of a single pipeline step.

    Returned by ``GET /jobs/{id}/steps/{name}`` (NFM-3543-C). The
    ``track_id`` is the durable handle to the original execution;
    subsequent reruns (NFM-3543-D) mint a new ``track_id`` and preserve
    this one on the historical row.
    """

    job_id: UUID = Field(description="Parent extraction job id.")
    step_name: str = Field(description="Pipeline step name (chunk, extract, ...).")
    track_id: UUID = Field(description="Durable handle to this execution.")
    status: str = Field(description="Step status (pending|running|completed|failed|skipped).")
    input_hash: str | None = Field(
        default=None,
        description="Input fingerprint used for skip detection on reruns.",
    )
    output_id: UUID | None = Field(
        default=None,
        description="Reference to the product artifact this step produced.",
    )
    error_message: str | None = Field(
        default=None,
        description="Last failure reason when status='failed'.",
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# POST /jobs/{id}/steps/{name}/rerun  (NFM-3543-D)
# ---------------------------------------------------------------------------


class RerunStepRequest(BaseModel):
    """Request body for ``POST /jobs/{id}/steps/{name}/rerun``.

    Both fields are optional. The header ``Idempotency-Key`` (preferred)
    or ``client_request_id`` body field provide the idempotency token;
    if both are present, the header wins.
    """

    client_request_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Optional client-supplied idempotency token. Used only when "
            "the ``Idempotency-Key`` header is absent."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "If true, allow rerunning a step whose latest status is "
            "'succeeded'. Default false: re-running a succeeded step "
            "returns 422."
        ),
    )


class RerunStepResponse(BaseModel):
    """Response body for ``POST /jobs/{id}/steps/{name}/rerun``.

    The new ``track_id`` is the durable handle to the rerun execution;
    ``original_track_id`` preserves the historical step's track_id so
    callers can correlate the rerun back to the original execution.
    """

    job_id: UUID = Field(description="Parent extraction job id.")
    step_name: str = Field(description="Pipeline step name being rerun.")
    track_id: UUID = Field(
        description=(
            "NEW track_id for this rerun. Idempotent replays within 24h "
            "return the original track_id (with ``Idempotent-Replayed: true``)."
        ),
    )
    original_track_id: UUID = Field(
        description=(
            "track_id of the historical step being rerun. Preserved on the "
            "historical row; the new track_id above refers to the rerun."
        ),
    )
    status: str = Field(
        default="pending",
        description="Initial rerun status. Always 'pending' on 202.",
    )
    accepted_at: datetime = Field(
        description="Server-side timestamp when the rerun was accepted.",
    )
