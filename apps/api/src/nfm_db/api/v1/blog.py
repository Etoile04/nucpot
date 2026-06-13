"""Blog admin API endpoints: CRUD and review workflow."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.blog_post import (
    BlogPostCreate,
    BlogPostListQuery,
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


@router.post("/admin/blog/posts", response_model=BlogPostResponse, status_code=201)
async def create_post(
    payload: BlogPostCreate,
    author_id: uuid.UUID,  # TODO: Get from authenticated user
    session: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    """Create a new blog post (editor/admin only)."""
    # TODO: Verify user has create_post permission
    metadata, slug = await create_blog_post(
        session,
        author_id=author_id,
        title=payload.title,
        content=payload.content,
        summary=payload.summary,
        tags=payload.tags,
        author_name=payload.author_name,
    )

    return BlogPostResponse.model_validate(metadata)


@router.get("/admin/blog/posts", response_model=list[BlogPostResponse])
async def list_posts(
    status: str | None = Query(default=None),
    author_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> list[BlogPostResponse]:
    """List blog posts with filtering (admin/reviewer only)."""
    # TODO: Verify user has appropriate permission
    posts = await list_blog_posts(
        session,
        status=status,  # TODO: Convert to PostStatus enum
        author_id=author_id,
        limit=limit,
        offset=offset,
    )

    return [BlogPostResponse.model_validate(post) for post in posts]


@router.get("/admin/blog/posts/{slug}", response_model=BlogPostResponse)
async def get_post(
    slug: str,
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
    author_id: uuid.UUID,  # TODO: Get from authenticated user
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a blog post (author/admin only)."""
    # TODO: Verify user has delete_post permission or is author
    await delete_blog_post(session, slug, author_id)


@router.post("/admin/blog/posts/{slug}/workflow", response_model=WorkflowActionResponse)
async def workflow_action(
    slug: str,
    payload: WorkflowActionRequest,
    user_id: uuid.UUID,  # TODO: Get from authenticated user
    user_permissions: set[str],  # TODO: Get from authenticated user
    session: AsyncSession = Depends(get_db),
) -> WorkflowActionResponse:
    """Execute workflow action on a blog post."""

    if payload.action == "submit":
        post = await submit_for_review(session, slug, user_id, user_permissions)
        message = "Post submitted for review"

    elif payload.action == "approve":
        post = await approve_post(session, slug, user_id, user_permissions)
        message = "Post approved"

    elif payload.action == "reject":
        if not payload.rejection_reason:
            raise HTTPException(
                status_code=400, detail="rejection_reason required for reject action"
            )
        post = await reject_post(
            session, slug, user_id, payload.rejection_reason, user_permissions
        )
        message = "Post rejected"

    elif payload.action == "publish":
        post = await publish_post(session, slug, user_permissions)
        message = "Post published"

    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    return WorkflowActionResponse(
        id=post.id,
        slug=post.slug,
        status=post.status,
        message=message,
    )
