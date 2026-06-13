"""Blog post service: manages blog posts with review workflow."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.blog_state import (
    PermissionError,
    PostStatus,
    StateTransitionError,
    can_transition,
    get_auto_fields,
    get_required_permission,
    validate_transition,
)
from nfm_db.models.blog_post import BlogPostMetadata
from nfm_db.models.user import User


def get_content_dir() -> Path:
    """Get the blog content directory."""
    import os

    return Path(os.environ.get("BLOG_CONTENT_DIR", "content/blog"))


def generate_slug(title: str) -> str:
    """Generate a URL-safe slug from a title."""
    import re

    # Remove special characters, convert to lowercase
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    # Add timestamp for uniqueness
    timestamp = int(datetime.now().timestamp())
    return f"{slug}-{timestamp}"


def update_markdown_status(slug: str, status: str) -> None:
    """Update the status field in a markdown file's frontmatter.

    Args:
        slug: Post slug
        status: New status value
    """
    import re

    content_dir = get_content_dir()
    file_path = content_dir / f"{slug}.md"

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update or add status field in frontmatter
    if "status:" in content:
        # Update existing status
        content = re.sub(
            r"^status:.*$",
            f"status: {status}",
            content,
            flags=re.MULTILINE,
        )
    else:
        # Add status after summary field
        content = re.sub(
            r"^(summary:.*)$",
            r"\1\nstatus: " + status,
            content,
            flags=re.MULTILINE,
        )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


async def create_blog_post(
    session: AsyncSession,
    author_id: uuid.UUID,
    title: str,
    content: str,
    summary: str,
    tags: list[str],
    author_name: str,
) -> tuple[BlogPostMetadata, str]:
    """Create a new blog post with markdown file and metadata.

    Args:
        session: Database session
        author_id: UUID of the author user
        title: Post title
        content: Markdown content
        summary: Post summary
        tags: List of tags
        author_name: Author's display name

    Returns:
        Tuple of (BlogPostMetadata, slug)
    """
    slug = generate_slug(title)

    # Create markdown file
    content_dir = get_content_dir()
    content_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "title": title,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "author": author_name,
        "tags": tags,
        "summary": summary,
        "status": "draft",
    }

    # Build markdown with frontmatter
    frontmatter_str = "---\n"
    for key, value in frontmatter.items():
        if isinstance(value, list):
            frontmatter_str += f"{key}:\n"
            for item in value:
                frontmatter_str += f"  - {item}\n"
        else:
            frontmatter_str += f"{key}: {value}\n"
    frontmatter_str += "---\n\n" + content

    # Write file
    file_path = content_dir / f"{slug}.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(frontmatter_str)

    # Create metadata record
    metadata = BlogPostMetadata(
        slug=slug,
        status=PostStatus.DRAFT.value,
        author_id=author_id,
    )
    session.add(metadata)
    await session.flush()
    await session.refresh(metadata)

    return metadata, slug


async def get_blog_post_by_slug(
    session: AsyncSession,
    slug: str,
) -> BlogPostMetadata | None:
    """Get blog post metadata by slug.

    Args:
        session: Database session
        slug: Post slug

    Returns:
        BlogPostMetadata or None
    """
    stmt = select(BlogPostMetadata).where(BlogPostMetadata.slug == slug)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_blog_posts(
    session: AsyncSession,
    status: PostStatus | None = None,
    author_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BlogPostMetadata]:
    """List blog posts with optional filters.

    Args:
        session: Database session
        status: Filter by status
        author_id: Filter by author
        limit: Max results
        offset: Pagination offset

    Returns:
        List of BlogPostMetadata
    """
    stmt = select(BlogPostMetadata)

    if status is not None:
        stmt = stmt.where(BlogPostMetadata.status == status.value)
    if author_id is not None:
        stmt = stmt.where(BlogPostMetadata.author_id == author_id)

    stmt = stmt.order_by(BlogPostMetadata.created_at.desc())
    stmt = stmt.limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def submit_for_review(
    session: AsyncSession,
    slug: str,
    author_id: uuid.UUID,
    user_permissions: set[str],
) -> BlogPostMetadata:
    """Submit a blog post for review.

    Args:
        session: Database session
        slug: Post slug
        author_id: User requesting the submission
        user_permissions: Set of user's permissions

    Returns:
        Updated BlogPostMetadata

    Raises:
        StateTransitionError: If post cannot be submitted
        PermissionError: If user lacks required permission
    """
    metadata = await get_blog_post_by_slug(session, slug)
    if not metadata:
        raise ValueError(f"Post not found: {slug}")

    # Verify ownership
    if metadata.author_id != author_id:
        raise PermissionError("edit_post")

    # Validate transition
    current_status = PostStatus(metadata.status)
    validate_transition(current_status, PostStatus.UNDER_REVIEW, user_permissions)

    # Update status
    metadata.status = PostStatus.UNDER_REVIEW.value
    await session.flush()
    await session.refresh(metadata)

    # Update markdown file
    update_markdown_status(slug, PostStatus.UNDER_REVIEW.value)

    return metadata


async def approve_post(
    session: AsyncSession,
    slug: str,
    reviewer_id: uuid.UUID,
    user_permissions: set[str],
) -> BlogPostMetadata:
    """Approve a blog post.

    Args:
        session: Database session
        slug: Post slug
        reviewer_id: User approving the post
        user_permissions: Set of user's permissions

    Returns:
        Updated BlogPostMetadata

    Raises:
        StateTransitionError: If post cannot be approved
        PermissionError: If user lacks required permission
    """
    metadata = await get_blog_post_by_slug(session, slug)
    if not metadata:
        raise ValueError(f"Post not found: {slug}")

    # Validate transition
    current_status = PostStatus(metadata.status)
    validate_transition(current_status, PostStatus.APPROVED, user_permissions)

    # Update status and reviewer
    metadata.status = PostStatus.APPROVED.value
    metadata.reviewer_id = reviewer_id
    metadata.reviewed_at = datetime.now()
    await session.flush()
    await session.refresh(metadata)

    # Update markdown file
    update_markdown_status(slug, PostStatus.APPROVED.value)

    return metadata


async def reject_post(
    session: AsyncSession,
    slug: str,
    reviewer_id: uuid.UUID,
    rejection_reason: str,
    user_permissions: set[str],
) -> BlogPostMetadata:
    """Reject a blog post.

    Args:
        session: Database session
        slug: Post slug
        reviewer_id: User rejecting the post
        rejection_reason: Reason for rejection
        user_permissions: Set of user's permissions

    Returns:
        Updated BlogPostMetadata

    Raises:
        StateTransitionError: If post cannot be rejected
        PermissionError: If user lacks required permission
    """
    metadata = await get_blog_post_by_slug(session, slug)
    if not metadata:
        raise ValueError(f"Post not found: {slug}")

    # Validate transition
    current_status = PostStatus(metadata.status)
    validate_transition(current_status, PostStatus.REJECTED, user_permissions)

    # Update status, reviewer, and reason
    metadata.status = PostStatus.REJECTED.value
    metadata.reviewer_id = reviewer_id
    metadata.reviewed_at = datetime.now()
    metadata.rejection_reason = rejection_reason
    await session.flush()
    await session.refresh(metadata)

    # Update markdown file
    update_markdown_status(slug, PostStatus.REJECTED.value)

    return metadata


async def publish_post(
    session: AsyncSession,
    slug: str,
    user_permissions: set[str],
) -> BlogPostMetadata:
    """Publish an approved blog post.

    Args:
        session: Database session
        slug: Post slug
        user_permissions: Set of user's permissions

    Returns:
        Updated BlogPostMetadata

    Raises:
        StateTransitionError: If post cannot be published
        PermissionError: If user lacks required permission
    """
    metadata = await get_blog_post_by_slug(session, slug)
    if not metadata:
        raise ValueError(f"Post not found: {slug}")

    # Validate transition
    current_status = PostStatus(metadata.status)
    validate_transition(current_status, PostStatus.PUBLISHED, user_permissions)

    # Update status and publish time
    metadata.status = PostStatus.PUBLISHED.value
    metadata.published_at = datetime.now()
    await session.flush()
    await session.refresh(metadata)

    # Update markdown file
    update_markdown_status(slug, PostStatus.PUBLISHED.value)

    return metadata


async def delete_blog_post(
    session: AsyncSession,
    slug: str,
    author_id: uuid.UUID,
) -> None:
    """Delete a blog post (file and metadata).

    Args:
        session: Database session
        slug: Post slug
        author_id: User requesting deletion
    """
    metadata = await get_blog_post_by_slug(session, slug)
    if not metadata:
        raise ValueError(f"Post not found: {slug}")

    # Verify ownership (or admin could override)
    if metadata.author_id != author_id:
        raise PermissionError("delete_post")

    # Delete markdown file
    content_dir = get_content_dir()
    file_path = content_dir / f"{slug}.md"
    if file_path.exists():
        file_path.unlink()

    # Delete metadata
    await session.delete(metadata)
    await session.flush()
