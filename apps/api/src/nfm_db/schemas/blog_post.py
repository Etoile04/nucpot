"""Blog post API schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BlogPostCreate(BaseModel):
    """Schema for creating a new blog post."""

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=10)
    author_name: str = Field(..., min_length=1, max_length=100)


class BlogPostResponse(BaseModel):
    """Schema for blog post response.

    Note: title is stored in the markdown file, not in the DB metadata
    model, so it is not included here. The frontend reads title from the
    markdown frontmatter when rendering.
    """

    id: uuid.UUID
    slug: str
    status: str
    author_id: uuid.UUID
    reviewer_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    published_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BlogPostListQuery(BaseModel):
    """Schema for blog post list query parameters."""

    status: str | None = None
    author_id: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkflowActionRequest(BaseModel):
    """Schema for workflow action requests."""

    action: str = Field(..., pattern=r"^(submit|approve|reject|publish)$")
    rejection_reason: str | None = Field(None, max_length=1000)


class WorkflowActionResponse(BaseModel):
    """Schema for workflow action response."""

    id: uuid.UUID
    slug: str
    status: str
    message: str
