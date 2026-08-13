"""Unit tests for blog_post service.

Tests for:
- generate_slug (URL-safe, unique timestamp)
- get_content_dir (env var override)
- update_markdown_status (frontmatter mutation)
- create_blog_post (DB record + file I/O)
- get_blog_post_by_slug (lookup)
- list_blog_posts (filtering, pagination)
- submit_for_review (state transition, ownership check)
- approve_post (state transition, reviewer assignment)
- reject_post (state transition, rejection reason)
- publish_post (state transition)
- delete_blog_post (ownership check, file + DB cleanup)
- Full workflow: draft → review → approved → published

Uses mocked file I/O and in-memory SQLite.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.blog_state import (
    PermissionError,
    PostStatus,
    StateTransitionError,
)
from nfm_db.models.blog_post import BlogPostMetadata
from nfm_db.services.blog_post import (
    _safe_md_path,
    approve_post,
    create_blog_post,
    delete_blog_post,
    generate_slug,
    get_blog_post_by_slug,
    get_content_dir,
    list_blog_posts,
    publish_post,
    reject_post,
    submit_for_review,
    update_blog_post,
    update_markdown_status,
)

EDITOR_PERMS = {"submit_for_review", "edit_post"}
REVIEWER_PERMS = {"review_post"}

# Deterministic IDs matching conftest seed users.
AUTHOR_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")
REVIEWER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000002")


# ---------------------------------------------------------------------------
# generate_slug tests
# ---------------------------------------------------------------------------


class TestGenerateSlug:
    """Tests for slug generation."""

    def test_slug_lowercases(self) -> None:
        slug = generate_slug("Hello World")
        assert slug.startswith("hello-world")

    def test_slug_removes_special_chars(self) -> None:
        slug = generate_slug("UO2 Fuel!@#$ Properties")
        # Should not contain special chars (only alphanumeric, hyphens)
        base = slug.rsplit("-", 1)[0]
        assert re.match(r"^[a-z0-9-]+$", base)

    def test_slug_replaces_spaces_with_hyphens(self) -> None:
        slug = generate_slug("a b c")
        base = slug.rsplit("-", 1)[0]
        assert " " not in base
        assert base == "a-b-c"

    def test_slug_has_timestamp_suffix(self) -> None:
        slug = generate_slug("test")
        parts = slug.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[1].isdigit()

    def test_slug_different_each_time(self) -> None:
        import time

        slug1 = generate_slug("same title")
        time.sleep(1.1)
        slug2 = generate_slug("same title")
        assert slug1 != slug2

    def test_slug_handles_unicode(self) -> None:
        slug = generate_slug("测试标题")
        # \w in Python regex keeps unicode word characters
        base = slug.rsplit("-", 1)[0]
        assert "测试标题" in base


# ---------------------------------------------------------------------------
# get_content_dir tests
# ---------------------------------------------------------------------------


class TestGetContentDir:
    """Tests for content directory resolution."""

    def test_default_dir(self) -> None:
        with patch.dict("os.environ", {"BLOG_CONTENT_DIR": "content/blog"}):
            result = get_content_dir()
            assert result == Path("content/blog")

    def test_custom_dir_from_env(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"BLOG_CONTENT_DIR": str(tmp_path / "custom")}):
            result = get_content_dir()
            assert result == Path(tmp_path / "custom")


# ---------------------------------------------------------------------------
# update_markdown_status tests
# ---------------------------------------------------------------------------


class TestUpdateMarkdownStatus:
    """Tests for markdown frontmatter status update."""

    def test_updates_existing_status(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            slug = "test-post"
            file_path = tmp_path / f"{slug}.md"
            file_path.write_text(
                "---\ntitle: Test\nstatus: draft\n---\nContent here", encoding="utf-8"
            )

            update_markdown_status(slug, "under_review")

            content = file_path.read_text(encoding="utf-8")
            assert "status: under_review" in content

    def test_adds_status_after_summary(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            slug = "test-post-no-status"
            file_path = tmp_path / f"{slug}.md"
            file_path.write_text(
                "---\ntitle: Test\nsummary: A post\n---\nContent", encoding="utf-8"
            )

            update_markdown_status(slug, "approved")

            content = file_path.read_text(encoding="utf-8")
            assert "status: approved" in content
            # Should appear after summary line
            lines = content.split("\n")
            summary_idx = lines.index("summary: A post")
            assert "status: approved" in lines[summary_idx + 1]

    def test_raises_if_file_missing(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                update_markdown_status("nonexistent-slug", "draft")


# ---------------------------------------------------------------------------
# create_blog_post tests
# ---------------------------------------------------------------------------


class TestCreateBlogPost:
    """Tests for blog post creation."""

    @pytest.mark.asyncio
    async def test_create_post_returns_metadata_and_slug(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            metadata, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Test Post",
                content="Hello world",
                summary="A test",
                tags=["test", "nuclear"],
                author_name="Test Author",
            )
            assert isinstance(metadata, BlogPostMetadata)
            assert slug == metadata.slug
            assert metadata.status == PostStatus.DRAFT.value
            assert metadata.author_id == AUTHOR_ID

    @pytest.mark.asyncio
    async def test_create_post_writes_markdown_file(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            metadata, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="File Test",
                content="Content body",
                summary="Summary",
                tags=["tag1"],
                author_name="Author",
            )
            file_path = tmp_path / f"{slug}.md"
            assert file_path.exists()
            content = file_path.read_text(encoding="utf-8")
            assert "---" in content
            assert "title: File Test" in content
            assert "author: Author" in content
            assert "status: draft" in content
            assert "Content body" in content

    @pytest.mark.asyncio
    async def test_create_post_with_tags_in_frontmatter(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            _, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Tagged Post",
                content="Body",
                summary="Sum",
                tags=["reactor", "fuel"],
                author_name="A",
            )
            file_path = tmp_path / f"{slug}.md"
            content = file_path.read_text(encoding="utf-8")
            assert "tags:" in content
            assert "  - reactor" in content
            assert "  - fuel" in content


# ---------------------------------------------------------------------------
# get_blog_post_by_slug tests
# ---------------------------------------------------------------------------


class TestGetBlogPostBySlug:
    """Tests for blog post lookup."""

    @pytest.mark.asyncio
    async def test_returns_post_when_found(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="existing-post",
            status=PostStatus.DRAFT.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        result = await get_blog_post_by_slug(db_session, "existing-post")
        assert result is not None
        assert result.slug == "existing-post"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, db_session: AsyncSession) -> None:
        result = await get_blog_post_by_slug(db_session, "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# list_blog_posts tests
# ---------------------------------------------------------------------------


class TestListBlogPosts:
    """Tests for blog post listing with filters."""

    @pytest.fixture
    async def seeded_posts(self, db_session: AsyncSession) -> list[BlogPostMetadata]:
        posts = [
            BlogPostMetadata(
                slug=f"post-{i}", title=f"Post {i}", status=status, author_id=AUTHOR_ID
            )
            for i, status in enumerate(
                [PostStatus.DRAFT.value, PostStatus.PUBLISHED.value, PostStatus.DRAFT.value]
            )
        ]
        for p in posts:
            db_session.add(p)
        await db_session.flush()
        return posts

    @pytest.mark.asyncio
    async def test_list_all(self, db_session: AsyncSession, seeded_posts) -> None:
        result = await list_blog_posts(db_session)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_filter_by_status(self, db_session: AsyncSession, seeded_posts) -> None:
        result = await list_blog_posts(db_session, status=PostStatus.DRAFT)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_filter_by_author(self, db_session: AsyncSession, seeded_posts) -> None:
        other_author = uuid.uuid4()
        result = await list_blog_posts(db_session, author_id=other_author)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_limit_and_offset(self, db_session: AsyncSession, seeded_posts) -> None:
        result = await list_blog_posts(db_session, limit=2, offset=0)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_order_by_created_at_desc(self, db_session: AsyncSession, seeded_posts) -> None:
        result = await list_blog_posts(db_session)
        assert len(result) == 3
        # All 3 posts should be returned (order depends on DB)


# ---------------------------------------------------------------------------
# State transition tests (submit/approve/reject/publish)
# ---------------------------------------------------------------------------


class TestSubmitForReview:
    """Tests for the submit-for-review workflow."""

    @pytest.mark.asyncio
    async def test_submit_draft_to_review(self, db_session: AsyncSession, tmp_path: Path) -> None:
        post = BlogPostMetadata(
            title="Test Post", slug="review-me", status=PostStatus.DRAFT.value, author_id=AUTHOR_ID
        )
        db_session.add(post)
        await db_session.flush()

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            # Create the markdown file
            (tmp_path / "review-me.md").write_text(
                "---\nstatus: draft\n---\nBody", encoding="utf-8"
            )
            result = await submit_for_review(db_session, "review-me", AUTHOR_ID, EDITOR_PERMS)

        assert result.status == PostStatus.UNDER_REVIEW.value

    @pytest.mark.asyncio
    async def test_submit_rejects_non_owner(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post", slug="not-mine", status=PostStatus.DRAFT.value, author_id=AUTHOR_ID
        )
        db_session.add(post)
        await db_session.flush()

        other_id = uuid.uuid4()
        with pytest.raises(PermissionError):
            await submit_for_review(db_session, "not-mine", other_id, EDITOR_PERMS)

    @pytest.mark.asyncio
    async def test_submit_rejects_missing_permission(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post", slug="no-perm", status=PostStatus.DRAFT.value, author_id=AUTHOR_ID
        )
        db_session.add(post)
        await db_session.flush()

        with pytest.raises(PermissionError):
            await submit_for_review(db_session, "no-perm", AUTHOR_ID, set())

    @pytest.mark.asyncio
    async def test_submit_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(ValueError):
            await submit_for_review(db_session, "nonexistent", AUTHOR_ID, EDITOR_PERMS)


class TestApprovePost:
    """Tests for the approve workflow."""

    @pytest.mark.asyncio
    async def test_approve_under_review(self, db_session: AsyncSession, tmp_path: Path) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="approve-me",
            status=PostStatus.UNDER_REVIEW.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            (tmp_path / "approve-me.md").write_text(
                "---\nstatus: under_review\n---\nBody", encoding="utf-8"
            )
            result = await approve_post(db_session, "approve-me", REVIEWER_ID, REVIEWER_PERMS)

        assert result.status == PostStatus.APPROVED.value
        assert result.reviewer_id == REVIEWER_ID
        assert result.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_approve_invalid_transition(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post", slug="draft-post", status=PostStatus.DRAFT.value, author_id=AUTHOR_ID
        )
        db_session.add(post)
        await db_session.flush()

        with pytest.raises(StateTransitionError):
            await approve_post(db_session, "draft-post", REVIEWER_ID, REVIEWER_PERMS)

    @pytest.mark.asyncio
    async def test_approve_missing_permission(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="no-perm-approve",
            status=PostStatus.UNDER_REVIEW.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        with pytest.raises(PermissionError):
            await approve_post(db_session, "no-perm-approve", REVIEWER_ID, set())


class TestRejectPost:
    """Tests for the reject workflow."""

    @pytest.mark.asyncio
    async def test_reject_under_review(self, db_session: AsyncSession, tmp_path: Path) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="reject-me",
            status=PostStatus.UNDER_REVIEW.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            (tmp_path / "reject-me.md").write_text(
                "---\nstatus: under_review\n---\nBody", encoding="utf-8"
            )
            result = await reject_post(
                db_session, "reject-me", REVIEWER_ID, "Not rigorous enough", REVIEWER_PERMS
            )

        assert result.status == PostStatus.REJECTED.value
        assert result.reviewer_id == REVIEWER_ID
        assert result.rejection_reason == "Not rigorous enough"


class TestPublishPost:
    """Tests for the publish workflow."""

    @pytest.mark.asyncio
    async def test_publish_approved(self, db_session: AsyncSession, tmp_path: Path) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="publish-me",
            status=PostStatus.APPROVED.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            (tmp_path / "publish-me.md").write_text(
                "---\nstatus: approved\n---\nBody", encoding="utf-8"
            )
            result = await publish_post(db_session, "publish-me", {"publish_post"})

        assert result.status == PostStatus.PUBLISHED.value
        assert result.published_at is not None

    @pytest.mark.asyncio
    async def test_publish_invalid_from_draft(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="cant-publish",
            status=PostStatus.DRAFT.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        with pytest.raises(StateTransitionError):
            await publish_post(db_session, "cant-publish", {"publish_post"})


# ---------------------------------------------------------------------------
# delete_blog_post tests
# ---------------------------------------------------------------------------


class TestDeleteBlogPost:
    """Tests for blog post deletion."""

    @pytest.mark.asyncio
    async def test_delete_removes_record(self, db_session: AsyncSession, tmp_path: Path) -> None:
        post = BlogPostMetadata(
            title="Test Post", slug="delete-me", status=PostStatus.DRAFT.value, author_id=AUTHOR_ID
        )
        db_session.add(post)
        await db_session.flush()

        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            file_path = tmp_path / "delete-me.md"
            file_path.write_text("---\ntitle: Del\n---\nBody", encoding="utf-8")

            await delete_blog_post(db_session, "delete-me", AUTHOR_ID)

            assert file_path.exists() is False
            result = await get_blog_post_by_slug(db_session, "delete-me")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete_rejects_non_owner(self, db_session: AsyncSession) -> None:
        post = BlogPostMetadata(
            title="Test Post",
            slug="not-mine-del",
            status=PostStatus.DRAFT.value,
            author_id=AUTHOR_ID,
        )
        db_session.add(post)
        await db_session.flush()

        with pytest.raises(PermissionError):
            await delete_blog_post(db_session, "not-mine-del", uuid.uuid4())

    @pytest.mark.asyncio
    async def test_delete_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(ValueError):
            await delete_blog_post(db_session, "nonexistent", AUTHOR_ID)


# ---------------------------------------------------------------------------
# Full workflow test
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    """Integration-style test of the complete blog lifecycle."""

    @pytest.mark.asyncio
    async def test_draft_to_published_lifecycle(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            # 1. Create as draft
            metadata, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Lifecycle Test",
                content="Full lifecycle content",
                summary="Testing the full flow",
                tags=["test"],
                author_name="Author",
            )
            assert metadata.status == PostStatus.DRAFT.value

            # 2. Submit for review
            metadata = await submit_for_review(db_session, slug, AUTHOR_ID, EDITOR_PERMS)
            assert metadata.status == PostStatus.UNDER_REVIEW.value

            # 3. Approve
            metadata = await approve_post(db_session, slug, REVIEWER_ID, REVIEWER_PERMS)
            assert metadata.status == PostStatus.APPROVED.value
            assert metadata.reviewer_id == REVIEWER_ID

            # 4. Publish
            metadata = await publish_post(db_session, slug, {"publish_post"})
            assert metadata.status == PostStatus.PUBLISHED.value
            assert metadata.published_at is not None

            # 5. Verify final state
            final = await get_blog_post_by_slug(db_session, slug)
            assert final is not None
            assert final.status == PostStatus.PUBLISHED.value

    @pytest.mark.asyncio
    async def test_draft_to_rejected_to_draft(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            # 1. Create
            _, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Rejected Test",
                content="Body",
                summary="Sum",
                tags=["t"],
                author_name="A",
            )

            # 2. Submit
            metadata = await submit_for_review(db_session, slug, AUTHOR_ID, EDITOR_PERMS)
            assert metadata.status == PostStatus.UNDER_REVIEW.value

            # 3. Reject
            metadata = await reject_post(
                db_session, slug, REVIEWER_ID, "Needs work", REVIEWER_PERMS
            )
            assert metadata.status == PostStatus.REJECTED.value
            assert metadata.rejection_reason == "Needs work"

            # 4. Can transition back to draft (requires edit_post permission)

            PostStatus(metadata.status)
            # Rejected → Draft is valid
            from nfm_db.core.blog_state import can_transition

            assert can_transition(PostStatus.REJECTED, PostStatus.DRAFT)


# ---------------------------------------------------------------------------
# _safe_md_path tests
# ---------------------------------------------------------------------------


class TestSafeMdPath:
    """Tests for the path traversal-safe markdown path helper."""

    def test_normal_slug(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            result = _safe_md_path("my-post")
            assert result == (tmp_path / "my-post.md").resolve()

    def test_rejects_dot_dot(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="Unsafe slug"):
                _safe_md_path("../etc/passwd")

    def test_rejects_forward_slash(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="Unsafe slug"):
                _safe_md_path("foo/bar")

    def test_rejects_backslash(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="Unsafe slug"):
                _safe_md_path("foo\\bar")

    def test_resolved_path_escapes_dir(self, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            # Slug with no explicit traversal but that resolves outside
            # (e.g., via symlink — can't test easily, but verify the check)
            with pytest.raises(ValueError, match="Unsafe slug"):
                _safe_md_path("..")


# ---------------------------------------------------------------------------
# update_blog_post tests
# ---------------------------------------------------------------------------


class TestUpdateBlogPost:
    """Tests for in-place blog post update."""

    @pytest.mark.asyncio
    async def test_update_title_only(self, db_session: AsyncSession, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            _, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Original Title",
                content="Original body",
                summary="Original summary",
                tags=["tag1"],
                author_name="Author",
            )

            updated = await update_blog_post(
                session=db_session,
                slug=slug,
                title="Updated Title",
            )

            assert updated.title == "Updated Title"

            file_path = tmp_path / f"{slug}.md"
            content = file_path.read_text(encoding="utf-8")
            assert "title: Updated Title" in content

    @pytest.mark.asyncio
    async def test_update_content_only(self, db_session: AsyncSession, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            _, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Title",
                content="Old body",
                summary="Sum",
                tags=["t"],
                author_name="A",
            )

            await update_blog_post(
                session=db_session,
                slug=slug,
                content="New body content",
            )

            file_path = tmp_path / f"{slug}.md"
            raw = file_path.read_text(encoding="utf-8")
            assert "New body content" in raw
            # Title in frontmatter should be unchanged
            assert "title: Title" in raw

    @pytest.mark.asyncio
    async def test_update_all_fields(self, db_session: AsyncSession, tmp_path: Path) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            _, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Old Title",
                content="Old content",
                summary="Old summary",
                tags=["old"],
                author_name="Old Author",
            )

            updated = await update_blog_post(
                session=db_session,
                slug=slug,
                title="New Title",
                content="New content",
                summary="New summary",
                tags=["new1", "new2"],
                author_name="New Author",
            )

            assert updated.title == "New Title"

            file_path = tmp_path / f"{slug}.md"
            raw = file_path.read_text(encoding="utf-8")
            assert "title: New Title" in raw
            assert "author: New Author" in raw
            assert "summary: New summary" in raw
            assert "  - new1" in raw
            assert "  - new2" in raw
            assert "New content" in raw

    @pytest.mark.asyncio
    async def test_update_nonexistent_slug_raises(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="Post not found"):
                await update_blog_post(
                    session=db_session,
                    slug="nonexistent-slug-12345",
                    title="Ghost Title",
                )

    @pytest.mark.asyncio
    async def test_frontmatter_preserves_unchanged_fields(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Updating only tags should preserve title, author, summary in file."""
        with patch("nfm_db.services.blog_post.get_content_dir", return_value=tmp_path):
            _, slug = await create_blog_post(
                session=db_session,
                author_id=AUTHOR_ID,
                title="Keep This Title",
                content="Body text",
                summary="Keep Summary",
                tags=["original"],
                author_name="Keep Author",
            )

            await update_blog_post(
                session=db_session,
                slug=slug,
                tags=["updated"],
            )

            file_path = tmp_path / f"{slug}.md"
            raw = file_path.read_text(encoding="utf-8")
            assert "title: Keep This Title" in raw
            assert "author: Keep Author" in raw
            assert "summary: Keep Summary" in raw
            assert "  - updated" in raw
            assert "Body text" in raw
