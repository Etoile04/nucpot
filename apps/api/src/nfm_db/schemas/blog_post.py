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


class BlogPostUpdate(BaseModel):
    """Schema for updating an existing blog post.

    All fields are optional — only provided fields are updated.
    """

    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    summary: str | None = Field(None, min_length=1, max_length=500)
    tags: list[str] | None = Field(None, max_length=10)
    author_name: str | None = Field(None, min_length=1, max_length=100)


class BlogPostResponse(BaseModel):
    """Schema for blog post response.

    Includes both DB metadata and content fields populated from the
    markdown file frontmatter so the frontend has everything it needs.
    """

    id: uuid.UUID
    slug: str
    title: str
    status: str
    author_id: uuid.UUID
    reviewer_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    published_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    # Content fields (populated from markdown file frontmatter)
    content: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    author_name: str | None = None

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
