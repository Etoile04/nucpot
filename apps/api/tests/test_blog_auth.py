"""Tests for blog API auth wiring and title column.

Tests for NFM-604:
- Blog API endpoints use real auth dependencies from api/v1/auth.py
- No raw author_id/user_id/user_permissions query/body params
- BlogPostResponse.model_validate() succeeds (title field populated)
- Create requires editor+ role, delete checks ownership or admin
- Workflow enforces role per state machine
- Title column populated in create_blog_post service
- Admin can delete any post (is_admin override)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.blog_state import PermissionError
from nfm_db.models.blog_post import BlogPostMetadata, PostStatus
from nfm_db.models.user import User
from nfm_db.schemas.blog_post import BlogPostResponse
from nfm_db.services.blog_post import (
    create_blog_post,
    delete_blog_post,
)

AUTHOR_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")
EDITOR_PERMS = {"submit_for_review", "edit_post"}
REVIEWER_PERMS = {"review_post"}


# ---------------------------------------------------------------------------
# Model: title column exists and is populated
# ---------------------------------------------------------------------------


class TestBlogPostTitleColumn:
    """Tests for the title column on BlogPostMetadata."""

    @pytest.mark.asyncio
    async def test_create_populates_title(self, db_session: AsyncSession, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            metadata, _ = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Nuclear Fuel Analysis",
                content="Body text",
                summary="A summary",
                tags=["nuclear"],
                author_name="Dr. Smith",
            )

        assert metadata.title == "Nuclear Fuel Analysis"

    @pytest.mark.asyncio
    async def test_title_required_for_model_validate(self, db_session: AsyncSession) -> None:
        """BlogPostResponse.model_validate() succeeds when title is present."""
        post = BlogPostMetadata(
            slug="test-title",
            title="Test Post Title",
            status=PostStatus.DRAFT.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        response = BlogPostResponse.model_validate(post)
        assert response.title == "Test Post Title"
        assert response.slug == "test-title"
        assert response.status == PostStatus.DRAFT.value

    @pytest.mark.asyncio
    async def test_create_without_title_raises(self, db_session: AsyncSession) -> None:
        """BlogPostMetadata requires title — SQLAlchemy NOT NULL constraint."""
        with pytest.raises(Exception):
            post = BlogPostMetadata(
                slug="no-title",
                status=PostStatus.DRAFT.value,
                author_id=AUTHOR_ID,
            )
            db_session.add(post)
            await db_session.flush()


# ---------------------------------------------------------------------------
# Service: delete with admin override
# ---------------------------------------------------------------------------


class TestDeleteAdminOverride:
    """Tests for admin override in delete_blog_post."""

    @pytest.mark.asyncio
    async def test_admin_can_delete_any_post(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        post = BlogPostMetadata(
            slug="admin-delete-me",
            title="Admin Delete Test",
            status=PostStatus.DRAFT.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        admin_id = uuid.uuid4()

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            await delete_blog_post(db_session, "admin-delete-me", admin_id, is_admin=True)

        result = await db_session.get(BlogPostMetadata, post.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_admin_cannot_delete_others_post(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            slug="not-yours",
            title="Not Yours",
            status=PostStatus.DRAFT.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        other_id = uuid.uuid4()
        with pytest.raises(PermissionError):
            await delete_blog_post(db_session, "not-yours", other_id, is_admin=False)


# ---------------------------------------------------------------------------
# API: Auth dependency wiring (integration tests via httpx)
# ---------------------------------------------------------------------------


class TestBlogAPIAuthWiring:
    """Integration tests for blog API auth dependencies."""

    @pytest.mark.asyncio
    async def test_create_post_requires_auth(self, db_session: AsyncSession) -> None:
        """POST /admin/blog/posts returns 401 without auth token."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/blog/posts",
                json={
                    "title": "Test",
                    "content": "Body",
                    "summary": "Sum",
                    "author_name": "A",
                },
            )

        assert resp.status_code == 401
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_post_with_editor_auth(
        self,
        db_session: AsyncSession,
        editor_user: User,
        editor_headers: dict,
        tmp_path: Path,
    ) -> None:
        """POST /admin/blog/posts succeeds with editor auth."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            async with AsyncClient(
                transport=transport, base_url="http://test", headers=editor_headers
            ) as client:
                resp = await client.post(
                    "/api/v1/admin/blog/posts",
                    json={
                        "title": "Editor Post",
                        "content": "Content body",
                        "summary": "A summary",
                        "author_name": editor_user.username,
                    },
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Editor Post"
        assert data["status"] == "draft"
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_post_rejected_for_reviewer(
        self,
        db_session: AsyncSession,
        reviewer_user: User,
        reviewer_headers: dict,
    ) -> None:
        """POST /admin/blog/posts returns 403 for reviewer (not editor/admin)."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport, base_url="http://test", headers=reviewer_headers
        ) as client:
            resp = await client.post(
                "/api/v1/admin/blog/posts",
                json={
                    "title": "Should Fail",
                    "content": "Body",
                    "summary": "Sum",
                    "author_name": "A",
                },
            )

        assert resp.status_code == 403
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_posts_requires_auth(self, db_session: AsyncSession) -> None:
        """GET /admin/blog/posts returns 401 without auth."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/blog/posts")

        assert resp.status_code == 401
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_posts_with_admin_auth(
        self,
        db_session: AsyncSession,
        admin_user: User,
        admin_headers: dict,
    ) -> None:
        """GET /admin/blog/posts succeeds with admin auth."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport, base_url="http://test", headers=admin_headers
        ) as client:
            resp = await client.get("/api/v1/admin/blog/posts")

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_posts_status_validation(
        self,
        db_session: AsyncSession,
        admin_user: User,
        admin_headers: dict,
    ) -> None:
        """GET /admin/blog/posts returns 400 for invalid status query param."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport, base_url="http://test", headers=admin_headers
        ) as client:
            resp = await client.get("/api/v1/admin/blog/posts", params={"status": "invalid"})

        assert resp.status_code == 400
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_no_raw_author_id_param(
        self,
        db_session: AsyncSession,
        editor_user: User,
        editor_headers: dict,
        tmp_path: Path,
    ) -> None:
        """Create endpoint ignores raw author_id — it comes from JWT."""
        from nfm_db.database import get_db
        from nfm_db.main import app

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            async with AsyncClient(
                transport=transport, base_url="http://test", headers=editor_headers
            ) as client:
                # Do NOT pass author_id — the endpoint should use JWT user.id
                resp = await client.post(
                    "/api/v1/admin/blog/posts",
                    json={
                        "title": "No Raw Param",
                        "content": "Body",
                        "summary": "Sum",
                        "author_name": "Editor",
                    },
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["author_id"] == str(editor_user.id)
        app.dependency_overrides.clear()
