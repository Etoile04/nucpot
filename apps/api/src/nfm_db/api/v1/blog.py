"""Blog admin API endpoints: CRUD and review workflow."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import (
    get_current_active_user,
    require_blog_role,
    require_editor,
    require_reviewer,
)
from nfm_db.database import get_db
from nfm_db.models.blog_post import PostStatus
from nfm_db.models.user import BlogRole, User
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

# Admin + reviewer role dependency (shared by list/get endpoints)
require_admin_or_reviewer = require_blog_role(
    BlogRole.ADMIN,
    BlogRole.EDITOR,
    BlogRole.REVIEWER,
)


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
    _current_user: Annotated[User, Depends(require_admin_or_reviewer)],
    session: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[BlogPostResponse]:
    """List blog posts with filtering (admin/editor/reviewer only)."""
    import uuid

    post_status = None
    if status is not None:
        try:
            post_status = PostStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid status: {status}"
            )

    parsed_author_id = None
    if author_id is not None:
        try:
            parsed_author_id = uuid.UUID(author_id)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid author_id: {author_id}"
            )

    posts = await list_blog_posts(
        session,
        status=post_status,
        author_id=parsed_author_id,
        limit=limit,
        offset=offset,
    )

    return [BlogPostResponse.model_validate(post) for post in posts]


@router.get("/admin/blog/posts/{slug}", response_model=BlogPostResponse)
async def get_post(
    slug: str,
    _current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    """Get a single blog post by slug (editor/admin only)."""
    post = await get_blog_post_by_slug(session, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    return BlogPostResponse.model_validate(post)


@router.delete("/admin/blog/posts/{slug}", status_code=204)
async def delete_post(
    slug: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a blog post (author or admin only)."""
    post = await get_blog_post_by_slug(session, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    is_author = post.author_id == current_user.id
    is_admin = current_user.blog_role == BlogRole.ADMIN

    if not is_author and not is_admin:
        raise HTTPException(
            status_code=403, detail="Only the author or an admin can delete this post"
        )

    await delete_blog_post(session, slug, current_user.id, is_admin=is_admin)


@router.post("/admin/blog/posts/{slug}/workflow", response_model=WorkflowActionResponse)
async def workflow_action(
    slug: str,
    payload: WorkflowActionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> WorkflowActionResponse:
    """Execute workflow action on a blog post."""
    # Convert Permission enum set to string set for the state machine
    user_permissions = {p.value for p in current_user.permissions}

    if payload.action == "submit":
        post = await submit_for_review(session, slug, current_user.id, user_permissions)
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
            session, slug, current_user.id, payload.rejection_reason, user_permissions
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
