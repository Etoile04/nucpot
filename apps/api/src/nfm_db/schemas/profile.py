"""User profile and aggregate-stats response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nfm_db.models.user import BlogRole


class ProfileResponse(BaseModel):
    """Full user profile — all user fields including profile extensions."""

    id: uuid.UUID
    username: str
    email: str
    full_name: str | None = None
    affiliation: str | None = None
    title: str | None = None
    phone: str | None = None
    blog_role: BlogRole | None = None
    is_active: bool
    last_login: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileUpdate(BaseModel):
    """Partial-update payload for PATCH /api/v1/auth/profile."""

    full_name: str | None = Field(default=None, max_length=255)
    affiliation: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)


class RecentPotentialItem(BaseModel):
    """Lightweight summary of a recently added potential."""

    id: str
    name: str
    display_name: str | None = None
    type: str
    elements: list[Any] = Field(default_factory=list)
    created_at: str | None = None


class StatsResponse(BaseModel):
    """Aggregate database statistics."""

    total_potentials: int
    total_types: int
    total_elements: int
    recent_potentials: list[RecentPotentialItem] = Field(default_factory=list)


class ContributionItem(BaseModel):
    """Lightweight summary of a user-uploaded potential."""

    id: str
    name: str
    display_name: str | None = None
    type: str
    elements: list[Any] = Field(default_factory=list)
    created_at: str | None = None


__all__ = [
    "ContributionItem",
    "ProfileResponse",
    "ProfileUpdate",
    "RecentPotentialItem",
    "StatsResponse",
]
