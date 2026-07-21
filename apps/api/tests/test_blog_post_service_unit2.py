"""Unit tests for nfm_db.services.blog_post — no conftest.py fixtures."""

import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.core.blog_state import PermissionError as BlogPermissionError
from nfm_db.core.blog_state import PostStatus, StateTransitionError, validate_transition
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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestGetContentDir:
    def test_default_dir(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = get_content_dir()
        assert result == Path("content/blog")

    def test_env_override(self) -> None:
        with patch.dict(os.environ, {"BLOG_CONTENT_DIR": "/tmp/blog"}):
            result = get_content_dir()
        assert result == Path("/tmp/blog")


class TestSafeMdPath:
    @patch("nfm_db.services.blog_post.get_content_dir")
    def test_valid_slug(self, mock_dir: MagicMock) -> None:
        mock_dir.return_value = Path("/safe/content")
        result = _safe_md_path("my-post")
        assert result.name == "my-post.md"
        assert result.is_relative_to(Path("/safe/content"))

    def test_reject_dotdot(self) -> None:
        with pytest.raises(ValueError, match="Unsafe slug"):
            _safe_md_path("../etc/passwd")

    def test_reject_forward_slash(self) -> None:
        with pytest.raises(ValueError, match="Unsafe slug"):
            _safe_md_path("foo/bar")

    def test_reject_backslash(self) -> None:
        with pytest.raises(ValueError, match="Unsafe slug"):
            _safe_md_path("foo\\bar")

    @patch("nfm_db.services.blog_post.get_content_dir")
    def test_reject_path_escape(self, mock_dir: MagicMock) -> None:
        # Use a slug that passes the literal ".." check but escapes via resolve
        # Since _safe_md_path checks ".." literally, we can't test the
        # is_relative_to branch with just slug manipulation — it's a defense-
        # in-depth. Test the first guard covers ".." directly instead.
        with pytest.raises(ValueError, match="Unsafe slug"):
            _safe_md_path("..")


class TestGenerateSlug:
    def test_basic(self) -> None:
        slug = generate_slug("Hello World")
        assert slug.startswith("hello-world-")
        # trailing part should be a numeric timestamp
        parts = slug.rsplit("-", 1)
        assert parts[1].isdigit()

    def test_special_chars_removed(self) -> None:
        slug = generate_slug("Hello, World! @2024")
        assert "," not in slug
        assert "!" not in slug
        assert "@" not in slug

    def test_multiple_spaces_to_dash(self) -> None:
        slug = generate_slug("a   b")
        assert "a-b-" in slug


# ---------------------------------------------------------------------------
# update_markdown_status (writes to filesystem)
# ---------------------------------------------------------------------------


class TestUpdateMarkdownStatus:
    @patch("builtins.open", create=True)
    @patch("nfm_db.services.blog_post._safe_md_path")
    def test_updates_existing_status(self, mock_path: MagicMock, mock_open: MagicMock) -> None:
        mock_path.return_value = Path("/safe/content/test-post.md")
        mock_file = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        mock_file.read.return_value = "---\ntitle: Test\nstatus: draft\n---\n\nBody"
        mock_file.write = MagicMock()

        update_markdown_status("test-post", "published")

        written = mock_file.write.call_args[0][0]
        assert "status: published" in written
        assert "status: draft" not in written

    @patch("builtins.open", create=True)
    @patch("nfm_db.services.blog_post._safe_md_path")
    def test_adds_status_after_summary(self, mock_path: MagicMock, mock_open: MagicMock) -> None:
        mock_path.return_value = Path("/safe/content/test-post.md")
        mock_file = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        mock_file.read.return_value = "---\ntitle: Test\nsummary: A post\n---\n\nBody"
        mock_file.write = MagicMock()

        update_markdown_status("test-post", "under_review")

        written = mock_file.write.call_args[0][0]
        assert "status: under_review" in written
        assert "summary: A post\nstatus: under_review" in written


# ---------------------------------------------------------------------------
# get_blog_post_by_slug
# ---------------------------------------------------------------------------


class TestGetBlogPostBySlug:
    async def test_returns_post(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        expected = MagicMock()
        mock_result.scalar_one_or_none.return_value = expected
        session.execute.return_value = mock_result

        result = await get_blog_post_by_slug(session, "my-slug")
        assert result is expected

    async def test_returns_none_when_missing(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await get_blog_post_by_slug(session, "no-such")
        assert result is None


# ---------------------------------------------------------------------------
# create_blog_post
# ---------------------------------------------------------------------------


class TestCreateBlogPost:
    @patch("nfm_db.services.blog_post.generate_slug", return_value="test-slug-123")
    @patch("nfm_db.services.blog_post.get_content_dir")
    async def test_creates_post(self, mock_dir: MagicMock, mock_slug: MagicMock) -> None:
        tmp = MagicMock(spec=Path)
        tmp.mkdir = MagicMock()
        tmp.__truediv__ = MagicMock(return_value=MagicMock(spec=Path))
        mock_dir.return_value = tmp

        session = AsyncMock()
        author_id = uuid.uuid4()

        with patch("builtins.open", create=True):
            metadata, slug = await create_blog_post(
                session=session,
                author_id=author_id,
                title="Test Title",
                content="Body text",
                summary="A summary",
                tags=["python", "testing"],
                author_name="Test Author",
            )

        assert slug == "test-slug-123"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()

    @patch("nfm_db.services.blog_post.generate_slug", return_value="slug-with-tags")
    @patch("nfm_db.services.blog_post.get_content_dir")
    async def test_frontmatter_includes_tags_as_list(
        self, mock_dir: MagicMock, mock_slug: MagicMock
    ) -> None:
        tmp = MagicMock(spec=Path)
        tmp.mkdir = MagicMock()
        written_content = ""
        fake_file = MagicMock()
        fake_file.write = MagicMock(side_effect=lambda c: written_content.__class__.__setitem__(written_content, "data", c) or None)

        # simpler approach: capture what's written
        captured = {"data": ""}

        def capture_write(c: str) -> None:
            captured["data"] = c

        fake_file.write = MagicMock(side_effect=capture_write)

        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=fake_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        tmp.__truediv__ = MagicMock(return_value=MagicMock(spec=Path))
        mock_dir.return_value = tmp

        session = AsyncMock()

        with patch("builtins.open", mock_open, create=True):
            await create_blog_post(
                session=session,
                author_id=uuid.uuid4(),
                title="Tagged Post",
                content="Content",
                summary="Summary",
                tags=["a", "b"],
                author_name="Author",
            )

        assert "  - a\n" in captured["data"]
        assert "  - b\n" in captured["data"]


# ---------------------------------------------------------------------------
# update_blog_post
# ---------------------------------------------------------------------------


class TestUpdateBlogPost:
    @patch("nfm_db.services.blog_post._safe_md_path")
    async def test_raises_when_post_not_found(self, mock_path: MagicMock) -> None:
        session = AsyncMock()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Post not found"):
                await update_blog_post(session, "missing-slug", title="New Title")

    async def test_updates_title_and_content(self) -> None:
        fake_path = MagicMock()
        fake_path.exists.return_value = True
        fake_path.read_text.return_value = "---\ntitle: Old\n---\n\nOld body"

        captured = {"data": ""}

        fake_file = MagicMock()
        fake_file.write = MagicMock(side_effect=lambda c: captured.update({"data": c}))
        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=fake_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        post = MagicMock()
        post.title = "Old"

        session = AsyncMock()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with patch("nfm_db.services.blog_post._safe_md_path", return_value=fake_path):
                with patch("builtins.open", mock_open, create=True):
                    result = await update_blog_post(
                        session,
                        "existing",
                        title="New Title",
                        content="New body",
                    )

        assert post.title == "New Title"
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()
        assert "New body" in captured["data"]

    async def test_no_file_uses_empty_metadata(self) -> None:
        fake_path = MagicMock()
        fake_path.exists.return_value = False
        fake_path.parent = MagicMock()

        captured = {"data": ""}

        fake_file = MagicMock()
        fake_file.write = MagicMock(side_effect=lambda c: captured.update({"data": c}))
        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=fake_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        post = MagicMock()
        post.title = "Original"

        session = AsyncMock()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with patch("nfm_db.services.blog_post._safe_md_path", return_value=fake_path):
                with patch("builtins.open", mock_open, create=True):
                    result = await update_blog_post(
                        session,
                        "nofile",
                        title="Updated",
                    )

        assert post.title == "Updated"
        session.flush.assert_awaited_once()

    async def test_merges_author_name_tags_and_summary(self) -> None:
        fake_path = MagicMock()
        fake_path.exists.return_value = True
        fake_path.read_text.return_value = "---\ntitle: Old\n---\n\nOld body"
        fake_path.parent = MagicMock()

        captured = {"data": ""}

        fake_file = MagicMock()
        fake_file.write = MagicMock(side_effect=lambda c: captured.update({"data": c}))
        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=fake_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        post = MagicMock()
        post.title = "Old"

        session = AsyncMock()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with patch("nfm_db.services.blog_post._safe_md_path", return_value=fake_path):
                with patch("builtins.open", mock_open, create=True):
                    with patch("yaml.safe_load", return_value={"title": "Old"}):
                        result = await update_blog_post(
                            session,
                            "existing",
                            author_name="New Author",
                            tags=["x", "y"],
                            summary="New summary",
                            content="Fresh body",
                        )

        assert post.title == "Old"  # title not passed
        written = captured["data"]
        assert "New Author" in written
        assert "  - x\n" in written
        assert "  - y\n" in written
        assert "New summary" in written
        assert "Fresh body" in written
        session.flush.assert_awaited_once()

    async def test_preserves_existing_content_when_no_content_arg(self) -> None:
        fake_path = MagicMock()
        fake_path.exists.return_value = True
        fake_path.read_text.return_value = "---\ntitle: Keep\nsummary: Sum\n---\n\nPreserved body"
        fake_path.parent = MagicMock()

        captured = {"data": ""}

        fake_file = MagicMock()
        fake_file.write = MagicMock(side_effect=lambda c: captured.update({"data": c}))
        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(return_value=fake_file)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        post = MagicMock()
        post.title = "Keep"

        session = AsyncMock()
        yaml_data = {"title": "Keep", "summary": "Sum"}

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with patch("nfm_db.services.blog_post._safe_md_path", return_value=fake_path):
                with patch("builtins.open", mock_open, create=True):
                    with patch("yaml.safe_load", return_value=yaml_data):
                        result = await update_blog_post(
                            session,
                            "preserve",
                        )
                        assert "Preserved body" in captured["data"]


# ---------------------------------------------------------------------------
# list_blog_posts
# ---------------------------------------------------------------------------


class TestListBlogPosts:
    async def test_no_filters(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock(), MagicMock()]
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        posts = await list_blog_posts(session)
        assert len(posts) == 2
        session.execute.assert_awaited_once()

    async def test_with_status_filter(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        posts = await list_blog_posts(session, status=PostStatus.PUBLISHED)
        assert posts == []

    async def test_with_author_filter(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        author = uuid.uuid4()
        posts = await list_blog_posts(session, author_id=author)
        assert posts == []

    async def test_with_limit_and_offset(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        posts = await list_blog_posts(session, limit=10, offset=20)
        assert posts == []

    async def test_with_all_filters(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        posts = await list_blog_posts(
            session,
            status=PostStatus.DRAFT,
            author_id=uuid.uuid4(),
            limit=5,
            offset=10,
        )
        assert len(posts) == 1


# ---------------------------------------------------------------------------
# submit_for_review
# ---------------------------------------------------------------------------


class TestSubmitForReview:
    async def test_post_not_found(self) -> None:
        session = AsyncMock()
        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Post not found"):
                await submit_for_review(
                    session, "missing", uuid.uuid4(), set()
                )

    async def test_wrong_author(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.author_id = uuid.uuid4()
        other_author = uuid.uuid4()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(BlogPermissionError, match="edit_post"):
                await submit_for_review(
                    session, "slug", other_author, {"submit_for_review"}
                )

    async def test_invalid_transition(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        author_id = uuid.uuid4()
        post.author_id = author_id
        post.status = PostStatus.PUBLISHED.value  # can't submit from published

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(StateTransitionError):
                await submit_for_review(
                    session, "slug", author_id, {"submit_for_review"}
                )

    async def test_permission_denied(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        author_id = uuid.uuid4()
        post.author_id = author_id
        post.status = PostStatus.DRAFT.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(BlogPermissionError):
                await submit_for_review(
                    session, "slug", author_id, {"wrong_permission"}
                )

    @patch("nfm_db.services.blog_post.update_markdown_status")
    async def test_successful_submit(self, mock_md: MagicMock) -> None:
        session = AsyncMock()
        post = MagicMock()
        author_id = uuid.uuid4()
        post.author_id = author_id
        post.status = PostStatus.DRAFT.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            result = await submit_for_review(
                session, "slug", author_id, {"submit_for_review"}
            )

        assert post.status == PostStatus.UNDER_REVIEW.value
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()
        mock_md.assert_called_once_with("slug", PostStatus.UNDER_REVIEW.value)


# ---------------------------------------------------------------------------
# approve_post
# ---------------------------------------------------------------------------


class TestApprovePost:
    async def test_post_not_found(self) -> None:
        session = AsyncMock()
        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Post not found"):
                await approve_post(session, "missing", uuid.uuid4(), set())

    async def test_invalid_transition(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.DRAFT.value  # can't approve draft

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(StateTransitionError):
                await approve_post(
                    session, "slug", uuid.uuid4(), {"review_post"}
                )

    async def test_permission_denied(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.UNDER_REVIEW.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(BlogPermissionError):
                await approve_post(session, "slug", uuid.uuid4(), {"no_perm"})

    @patch("nfm_db.services.blog_post.update_markdown_status")
    async def test_successful_approve(self, mock_md: MagicMock) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.UNDER_REVIEW.value
        reviewer_id = uuid.uuid4()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            result = await approve_post(
                session, "slug", reviewer_id, {"review_post"}
            )

        assert post.status == PostStatus.APPROVED.value
        assert post.reviewer_id == reviewer_id
        assert post.reviewed_at is not None
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()
        mock_md.assert_called_once_with("slug", PostStatus.APPROVED.value)


# ---------------------------------------------------------------------------
# reject_post
# ---------------------------------------------------------------------------


class TestRejectPost:
    async def test_post_not_found(self) -> None:
        session = AsyncMock()
        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Post not found"):
                await reject_post(
                    session, "missing", uuid.uuid4(), "bad", set()
                )

    async def test_invalid_transition(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.DRAFT.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(StateTransitionError):
                await reject_post(
                    session, "slug", uuid.uuid4(), "bad", {"review_post"}
                )

    async def test_permission_denied(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.UNDER_REVIEW.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(BlogPermissionError):
                await reject_post(
                    session, "slug", uuid.uuid4(), "bad", {"nope"}
                )

    @patch("nfm_db.services.blog_post.update_markdown_status")
    async def test_successful_reject(self, mock_md: MagicMock) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.UNDER_REVIEW.value
        reviewer_id = uuid.uuid4()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            result = await reject_post(
                session, "slug", reviewer_id, "Needs work", {"review_post"}
            )

        assert post.status == PostStatus.REJECTED.value
        assert post.reviewer_id == reviewer_id
        assert post.reviewed_at is not None
        assert post.rejection_reason == "Needs work"
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()
        mock_md.assert_called_once_with("slug", PostStatus.REJECTED.value)


# ---------------------------------------------------------------------------
# publish_post
# ---------------------------------------------------------------------------


class TestPublishPost:
    async def test_post_not_found(self) -> None:
        session = AsyncMock()
        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Post not found"):
                await publish_post(session, "missing", set())

    async def test_invalid_transition(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.DRAFT.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(StateTransitionError):
                await publish_post(session, "slug", {"publish_post"})

    async def test_permission_denied(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.APPROVED.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(BlogPermissionError):
                await publish_post(session, "slug", {"nope"})

    @patch("nfm_db.services.blog_post.update_markdown_status")
    async def test_successful_publish(self, mock_md: MagicMock) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.status = PostStatus.APPROVED.value

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            result = await publish_post(session, "slug", {"publish_post"})

        assert post.status == PostStatus.PUBLISHED.value
        assert post.published_at is not None
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()
        mock_md.assert_called_once_with("slug", PostStatus.PUBLISHED.value)


# ---------------------------------------------------------------------------
# delete_blog_post
# ---------------------------------------------------------------------------


class TestDeleteBlogPost:
    async def test_post_not_found(self) -> None:
        session = AsyncMock()
        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Post not found"):
                await delete_blog_post(session, "missing", uuid.uuid4())

    async def test_wrong_author_not_admin(self) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.author_id = uuid.uuid4()
        other = uuid.uuid4()

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            with pytest.raises(BlogPermissionError, match="delete_post"):
                await delete_blog_post(session, "slug", other)

    @patch("nfm_db.services.blog_post._safe_md_path")
    async def test_admin_can_delete_any_post(self, mock_path: MagicMock) -> None:
        session = AsyncMock()
        post = MagicMock()
        post.author_id = uuid.uuid4()

        file_path = MagicMock(spec=Path)
        file_path.exists.return_value = True
        mock_path.return_value = file_path

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            await delete_blog_post(
                session, "slug", uuid.uuid4(), is_admin=True
            )

        file_path.unlink.assert_called_once()
        session.delete.assert_called_once_with(post)
        session.flush.assert_awaited_once()

    @patch("nfm_db.services.blog_post._safe_md_path")
    async def test_author_can_delete_own_post(self, mock_path: MagicMock) -> None:
        session = AsyncMock()
        author_id = uuid.uuid4()
        post = MagicMock()
        post.author_id = author_id

        file_path = MagicMock(spec=Path)
        file_path.exists.return_value = False
        mock_path.return_value = file_path

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            await delete_blog_post(session, "slug", author_id)

        # file doesn't exist so unlink should NOT be called
        file_path.unlink.assert_not_called()
        session.delete.assert_called_once_with(post)
        session.flush.assert_awaited_once()

    @patch("nfm_db.services.blog_post._safe_md_path")
    async def test_deletes_file_when_exists(self, mock_path: MagicMock) -> None:
        session = AsyncMock()
        author_id = uuid.uuid4()
        post = MagicMock()
        post.author_id = author_id

        file_path = MagicMock(spec=Path)
        file_path.exists.return_value = True
        mock_path.return_value = file_path

        with patch(
            "nfm_db.services.blog_post.get_blog_post_by_slug",
            new_callable=AsyncMock,
            return_value=post,
        ):
            await delete_blog_post(session, "slug", author_id)

        file_path.unlink.assert_called_once()
        session.delete.assert_called_once_with(post)