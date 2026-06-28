"""Pydantic schemas for feedback API request/response validation."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from nfm_db.models.feedback import FeedbackStatus, FeedbackType, Priority
from nfm_db.schemas.common import ApiResponse, PaginatedResponse


class FeedbackCreate(BaseModel):
    """Schema for creating a new feedback entry."""

    feedback_type: FeedbackType
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    page_url: str | None = Field(default=None, max_length=500)
    contact_email: EmailStr | None = None


class FeedbackResponse(BaseModel):
    """Schema for a single feedback item in API responses."""

    id: UUID
    feedback_type: FeedbackType
    title: str
    description: str
    page_url: str | None
    contact_email: str | None
    priority: Priority
    status: FeedbackStatus
    assignee: str | None
    resolution: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class FeedbackCreateResult(BaseModel):
    """Schema returned after successful feedback creation."""

    id: UUID
    feedback_type: FeedbackType
    priority: Priority
    status: FeedbackStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedbackListQuery(BaseModel):
    """Query parameters for listing feedback."""

    status: FeedbackStatus | None = None
    priority: Priority | None = None
    feedback_type: FeedbackType | None = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


# Re-export generic envelopes specialised for feedback, so existing
# imports like ``from nfm_db.schemas.feedback import ApiResponse`` keep
# working.  The concrete aliases below are zero-cost wrappers — they are
# *not* new classes, just type aliases.
FeedbackPaginatedResponse = PaginatedResponse[FeedbackResponse]
FeedbackApiResponse = ApiResponse[FeedbackCreateResult | FeedbackPaginatedResponse]
