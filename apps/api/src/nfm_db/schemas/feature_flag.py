"""Pydantic schemas for the internal feature-flag service (NFM-4180)."""

from datetime import datetime

from pydantic import BaseModel, Field

from nfm_db.schemas.common import ApiResponse


class FeatureFlagResponse(BaseModel):
    """Stored state of one flag (admin view, no per-subject evaluation)."""

    key: str
    enabled: bool
    rollout_percentage: int = Field(ge=0, le=100)
    description: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class FeatureFlagUpdate(BaseModel):
    """Admin payload to toggle a flag or change its rollout percentage."""

    enabled: bool | None = None
    rollout_percentage: int | None = Field(default=None, ge=0, le=100)
    description: str | None = Field(default=None, max_length=255)


class FeatureFlagEvaluation(BaseModel):
    """Per-subject evaluation result returned to the frontend client."""

    key: str
    enabled: bool
    rollout_percentage: int
    value: bool
    bucket: int = Field(ge=0, le=99)


__all__ = [
    "ApiResponse",
    "FeatureFlagEvaluation",
    "FeatureFlagResponse",
    "FeatureFlagUpdate",
]
