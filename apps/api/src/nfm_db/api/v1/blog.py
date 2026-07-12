"""Blog admin API endpoints: CRUD and review workflow."""

import logging
import uuid
from pathlib import Path
from typing import Annotated

import frontmatter as matter
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import (
    get_current_active_user,
    require_blog_role,
    require_editor,
)
from nfm_db.config import get_settings
from nfm_db.database import get_db
from nfm_db.models.blog_post import PostStatus
from nfm_db.models.user import BlogRole, User
from nfm_db.schemas.blog_post import (
    BlogPostCreate,
    BlogPostResponse,
    BlogPostUpdate,
    WorkflowActionRequest,
    WorkflowActionResponse,
)
from nfm_db.schemas.common import PaginationParams
from nfm_db.services.blog_post import (
    approve_post,
    create_blog_post,
    delete_blog_post,
    get_blog_post_by_slug,
    list_blog_posts,
    publish_post,
    reject_post,
    submit_for_review,
    update_blog_post,
)

router = APIRouter(tags=["博客管理"])
settings = get_settings()
logger = logging.getLogger(__name__)


def _content_dir() -> Path:
    return Path(settings.blog_content_dir)


def _read_markdown(slug: str) -> dict | None:
    """Read markdown file for a post and return frontmatter + content.

    Rejects slugs containing path traversal sequences.
    """
    if ".." in slug or "/" in slug or "\\" in slug:
        logger.warning("Rejected path traversal attempt in slug=%r", slug)
        return None

    safe_name = slug.split("/")[-1]
    md_path = (_content_dir() / f"{safe_name}.md").resolve()

    if not md_path.is_relative_to(_content_dir().resolve()):
        logger.warning("Resolved path escapes content dir: slug=%r", slug)
        return None

    if not md_path.exists():
        return None
    try:
        raw = md_path.read_text(encoding="utf-8")
        parsed = matter.loads(raw)
        return {
            "content": parsed.content,
            "summary": parsed.metadata.get("summary", ""),
            "tags": parsed.metadata.get("tags", []),
            "author_name": parsed.metadata.get("author", ""),
        }
    except Exception:
        logger.warning("Failed to read markdown for slug=%s", slug, exc_info=True)
        return None


def _enrich_response(post) -> BlogPostResponse:
    """Build a BlogPostResponse with markdown content fields merged in."""
    data = {}
    if hasattr(post, "title") and not hasattr(post, "content"):
        # ORM model — extract fields
        data = {k: getattr(post, k) for k in BlogPostResponse.model_fields if hasattr(post, k)}
    else:
        data = BlogPostResponse.model_validate(post).model_dump()

    file_data = _read_markdown(data.get("slug", ""))
    if file_data is not None:
        data.update(file_data)

    return BlogPostResponse(**data)


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
    """创建博客文章（仅编辑/管理员）。

    Create a new blog post (editor/admin only)."""
    metadata, _ = await create_blog_post(
        session,
        author_id=current_user.id,
        title=payload.title,
        content=payload.content,
        summary=payload.summary,
        tags=payload.tags,
        author_name=payload.author_name,
    )

    return _enrich_response(metadata)


@router.get("/admin/blog/posts", response_model=list[BlogPostResponse])
async def list_posts(
    _current_user: Annotated[User, Depends(require_admin_or_reviewer)],
    session: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    pagination: PaginationParams = Depends(PaginationParams),
    _offset: int | None = Query(default=None, ge=0, alias="offset", deprecated=True, description="已弃用: 请使用 page 参数"),
    _limit: int | None = Query(default=None, ge=1, le=100, alias="limit", deprecated=True, description="已弃用: 请使用 per_page 参数"),
) -> list[BlogPostResponse]:
    """List blog posts with filtering (admin/editor/reviewer only).

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100 (已弃用 limit/offset 参数)
    """
    if _limit is not None:
        effective_page = ((_offset or 0) // _limit) + 1
        pagination = PaginationParams(page=effective_page, per_page=_limit)
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
        limit=pagination.per_page,
        offset=pagination.offset,
    )

    return [_enrich_response(post) for post in posts]


@router.get("/admin/blog/posts/{slug}", response_model=BlogPostResponse)
async def get_post(
    slug: str,
    _current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    """按slug获取单篇博客文章（仅编辑/管理员）。

    Get a single blog post by slug (editor/admin only)."""
    post = await get_blog_post_by_slug(session, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    return _enrich_response(post)


@router.delete("/admin/blog/posts/{slug}", status_code=204)
async def delete_post(
    slug: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> None:
    """删除博客文章（仅作者或管理员）。

    Delete a blog post (author or admin only)."""
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


@router.put("/admin/blog/posts/{slug}", response_model=BlogPostResponse)
async def update_post(
    slug: str,
    payload: BlogPostUpdate,
    current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    """更新博客文章（保留原slug）。

    Update an existing blog post in place (preserves slug)."""
    try:
        updated = await update_blog_post(
            session,
            slug=slug,
            title=payload.title,
            content=payload.content,
            summary=payload.summary,
            tags=payload.tags,
            author_name=payload.author_name,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Post not found")

    return _enrich_response(updated)


@router.post("/admin/blog/posts/{slug}/workflow", response_model=WorkflowActionResponse)
async def workflow_action(
    slug: str,
    payload: WorkflowActionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> WorkflowActionResponse:
    """执行博客文章工作流操作（提交/批准/拒绝/发布）。

    Execute workflow action on a blog post."""
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
