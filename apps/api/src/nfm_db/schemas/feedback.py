"""Pydantic schemas for feedback API request/response validation."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from nfm_db.models.feedback import FeedbackStatus, FeedbackType, Priority


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


class PaginatedResponse(BaseModel):
    """Paginated response envelope for feedback list."""

    items: list[FeedbackResponse]
    total: int
    page: int
    limit: int
    pages: int


class ApiResponse(BaseModel):
    """Standard API response envelope."""

    success: bool
    data: FeedbackCreateResult | PaginatedResponse | None = None
    error: str | None = None
