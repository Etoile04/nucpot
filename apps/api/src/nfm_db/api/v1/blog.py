"""Blog admin API endpoints: CRUD and review workflow."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import (
    get_current_active_user,
    require_editor,
    require_reviewer,
)
from nfm_db.core.blog_state import PermissionError as StatePermissionError
from nfm_db.core.blog_state import PostStatus
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.blog_post import (
    BlogPostCreate,
    BlogPostResponse,
    WorkflowActionRequest,
    WorkflowActionResponse,
)
from nfm_db.services.blog_post import (
    approve_post,
    create_blog_post,
    delete_blog_post,
    get_blog_post_by_slug,
    list_blog_posts,
    publish_post,
    reject_post,
    submit_for_review,
)

router = APIRouter()


class ReviewCountResponse(BaseModel):
    """Response schema for review queue count."""

    count: int


@router.post("/admin/blog/posts", response_model=BlogPostResponse, status_code=201)
async def create_post(
    payload: BlogPostCreate,
    current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    """Create a new blog post (editor/admin only)."""
    metadata, _ = await create_blog_post(
        session,
        author_id=current_user.id,
        title=payload.title,
        content=payload.content,
        summary=payload.summary,
        tags=payload.tags,
        author_name=payload.author_name,
    )

    return BlogPostResponse.model_validate(metadata)


@router.get("/admin/blog/posts", response_model=list[BlogPostResponse])
async def list_posts(
    _current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[BlogPostResponse]:
    """List blog posts with filtering (authenticated users only)."""
    status_enum = PostStatus(status) if status else None
    posts = await list_blog_posts(
        session,
        status=status_enum,
        author_id=author_id,
        limit=limit,
        offset=offset,
    )

    return [BlogPostResponse.model_validate(post) for post in posts]


@router.get(
    "/admin/blog/posts/review-count",
    response_model=ReviewCountResponse,
)
async def review_count(
    _current_user: Annotated[User, Depends(require_reviewer)],
    session: AsyncSession = Depends(get_db),
) -> ReviewCountResponse:
    """Get count of posts currently under review (reviewer/admin only)."""
    posts = await list_blog_posts(
        session,
        status=PostStatus.UNDER_REVIEW,
        limit=1000,
        offset=0,
    )
    return ReviewCountResponse(count=len(posts))


@router.get("/admin/blog/posts/{slug}", response_model=BlogPostResponse)
async def get_post(
    slug: str,
    _current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    """Get a single blog post by slug."""
    post = await get_blog_post_by_slug(session, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    return BlogPostResponse.model_validate(post)


@router.delete("/admin/blog/posts/{slug}", status_code=204)
async def delete_post(
    slug: str,
    current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a blog post (author/admin only)."""
    await delete_blog_post(session, slug, current_user.id)


@router.post("/admin/blog/posts/{slug}/workflow", response_model=WorkflowActionResponse)
async def workflow_action(
    slug: str,
    payload: WorkflowActionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> WorkflowActionResponse:
    """Execute workflow action on a blog post."""
    user_permissions = {p.value for p in current_user.permissions}

    try:
        if payload.action == "submit":
            post = await submit_for_review(
                session, slug, current_user.id, user_permissions
            )
            message = "Post submitted for review"

        elif payload.action == "approve":
            post = await approve_post(session, slug, current_user.id, user_permissions)
            message = "Post approved"

        elif payload.action == "reject":
            if not payload.rejection_reason:
                raise HTTPException(
                    status_code=400, detail="rejection_reason required for reject action"
                )
            post = await reject_post(
                session,
                slug,
                current_user.id,
                payload.rejection_reason,
                user_permissions,
            )
            message = "Post rejected"

        elif payload.action == "publish":
            post = await publish_post(session, slug, user_permissions)
            message = "Post published"

        else:
            raise HTTPException(status_code=400, detail="Invalid action")

    except StatePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return WorkflowActionResponse(
        id=post.id,
        slug=post.slug,
        status=post.status,
        message=message,
    )
