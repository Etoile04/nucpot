"""Integration tests for blog post service and API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.blog_state import PostStatus
from nfm_db.models import BlogRole, User
from nfm_db.services.blog_post import (
    approve_post,
    create_blog_post,
    list_blog_posts,
    publish_post,
    reject_post,
    submit_for_review,
)


@pytest.mark.asyncio
async def test_create_blog_post(db_session: AsyncSession):
    """Test creating a blog post."""
    author = User(
        username="author",
        email="author@example.com",
        hashed_password="hash",
        blog_role=BlogRole.EDITOR,
    )
    db_session.add(author)
    await db_session.commit()
    await db_session.refresh(author)

    metadata, slug = await create_blog_post(
        session=db_session,
        author_id=author.id,
        title="Test Post",
        content="# Test Content\n\nThis is test content.",
        summary="Test summary",
        tags=["test", "example"],
        author_name="Test Author",
    )

    assert metadata.slug == slug
    assert metadata.status == "draft"
    assert metadata.author_id == author.id


@pytest.mark.asyncio
async def test_submit_for_review(db_session: AsyncSession):
    """Test submitting a post for review."""
    editor = User(
        username="editor",
        email="editor@example.com",
        hashed_password="hash",
        blog_role=BlogRole.EDITOR,
    )
    db_session.add(editor)
    await db_session.commit()
    await db_session.refresh(editor)

    # Create a post
    metadata, _ = await create_blog_post(
        session=db_session,
        author_id=editor.id,
        title="Test Post",
        content="# Test",
        summary="Test summary",
        tags=["test"],
        author_name="Editor",
    )

    # Submit for review
    updated = await submit_for_review(
        session=db_session,
        slug=metadata.slug,
        author_id=editor.id,
        user_permissions={"submit_for_review"},
    )

    assert updated.status == "under_review"


@pytest.mark.asyncio
async def test_approve_post(db_session: AsyncSession):
    """Test approving a post."""
    editor = User(
        username="editor",
        email="editor@example.com",
        hashed_password="hash",
        blog_role=BlogRole.EDITOR,
    )
    reviewer = User(
        username="reviewer",
        email="reviewer@example.com",
        hashed_password="hash",
        blog_role=BlogRole.REVIEWER,
    )
    db_session.add_all([editor, reviewer])
    await db_session.commit()
    await db_session.refresh(editor)
    await db_session.refresh(reviewer)

    # Create and submit post
    metadata, _ = await create_blog_post(
        session=db_session,
        author_id=editor.id,
        title="Test Post",
        content="# Test",
        summary="Test summary",
        tags=["test"],
        author_name="Editor",
    )

    await submit_for_review(
        session=db_session,
        slug=metadata.slug,
        author_id=editor.id,
        user_permissions={"submit_for_review"},
    )

    # Approve
    updated = await approve_post(
        session=db_session,
        slug=metadata.slug,
        reviewer_id=reviewer.id,
        user_permissions={"review_post"},
    )

    assert updated.status == "approved"
    assert updated.reviewer_id == reviewer.id
    assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_reject_post(db_session: AsyncSession):
    """Test rejecting a post."""
    editor = User(
        username="editor",
        email="editor@example.com",
        hashed_password="hash",
        blog_role=BlogRole.EDITOR,
    )
    reviewer = User(
        username="reviewer",
        email="reviewer@example.com",
        hashed_password="hash",
        blog_role=BlogRole.REVIEWER,
    )
    db_session.add_all([editor, reviewer])
    await db_session.commit()
    await db_session.refresh(editor)
    await db_session.refresh(reviewer)

    # Create and submit post
    metadata, _ = await create_blog_post(
        session=db_session,
        author_id=editor.id,
        title="Test Post",
        content="# Test",
        summary="Test summary",
        tags=["test"],
        author_name="Editor",
    )

    await submit_for_review(
        session=db_session,
        slug=metadata.slug,
        author_id=editor.id,
        user_permissions={"submit_for_review"},
    )

    # Reject with reason
    updated = await reject_post(
        session=db_session,
        slug=metadata.slug,
        reviewer_id=reviewer.id,
        rejection_reason="Needs more work",
        user_permissions={"review_post"},
    )

    assert updated.status == "rejected"
    assert updated.reviewer_id == reviewer.id
    assert updated.rejection_reason == "Needs more work"


@pytest.mark.asyncio
async def test_publish_post(db_session: AsyncSession):
    """Test publishing an approved post."""
    editor = User(
        username="editor",
        email="editor@example.com",
        hashed_password="hash",
        blog_role=BlogRole.EDITOR,
    )
    reviewer = User(
        username="reviewer",
        email="reviewer@example.com",
        hashed_password="hash",
        blog_role=BlogRole.REVIEWER,
    )
    admin = User(
        username="admin",
        email="admin@example.com",
        hashed_password="hash",
        blog_role=BlogRole.ADMIN,
    )
    db_session.add_all([editor, reviewer, admin])
    await db_session.commit()
    await db_session.refresh(editor)
    await db_session.refresh(reviewer)
    await db_session.refresh(admin)

    # Create, submit, and approve post
    metadata, _ = await create_blog_post(
        session=db_session,
        author_id=editor.id,
        title="Test Post",
        content="# Test",
        summary="Test summary",
        tags=["test"],
        author_name="Editor",
    )

    await submit_for_review(
        session=db_session,
        slug=metadata.slug,
        author_id=editor.id,
        user_permissions={"submit_for_review"},
    )

    await approve_post(
        session=db_session,
        slug=metadata.slug,
        reviewer_id=reviewer.id,
        user_permissions={"review_post"},
    )

    # Publish
    updated = await publish_post(
        session=db_session,
        slug=metadata.slug,
        user_permissions={"publish_post"},
    )

    assert updated.status == "published"
    assert updated.published_at is not None


@pytest.mark.asyncio
async def test_list_blog_posts_filters_by_status(db_session: AsyncSession):
    """Test listing blog posts with status filter."""
    editor = User(
        username="editor",
        email="editor@example.com",
        hashed_password="hash",
        blog_role=BlogRole.EDITOR,
    )
    db_session.add(editor)
    await db_session.commit()
    await db_session.refresh(editor)

    # Create multiple posts
    for i in range(3):
        metadata, _ = await create_blog_post(
            session=db_session,
            author_id=editor.id,
            title=f"Post {i}",
            content=f"# Post {i}",
            summary=f"Summary {i}",
            tags=["test"],
            author_name="Editor",
        )

    # Submit one for review
    await submit_for_review(
        session=db_session,
        slug=metadata.slug,
        author_id=editor.id,
        user_permissions={"submit_for_review"},
    )

    # List all posts
    all_posts = await list_blog_posts(session=db_session)
    assert len(all_posts) == 3

    # List only draft posts
    draft_posts = await list_blog_posts(
        session=db_session,
        status=PostStatus.DRAFT,
    )
    assert len(draft_posts) == 2


@pytest.mark.integration
async def test_workflow_api_endpoint(async_client: AsyncClient, admin_headers):
    """Test the workflow API endpoint."""
    # This would require full integration with auth middleware
    # For now, this is a placeholder
    pass
