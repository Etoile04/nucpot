"""Integration tests for /api/v1/admin/blog endpoints."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from nfm_db.models.blog_post import BlogPostMetadata

BASE = "/api/v1/admin/blog"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_PAYLOAD = {
    "title": "Test Post Title",
    "content": "Some markdown content here.",
    "summary": "A short summary of the post.",
    "tags": ["nuclear", "fuel"],
    "author_name": "Jane Doe",
}


async def _seed_post(
    db_session,
    *,
    slug: str | None = None,
    title: str = "Seeded Post",
    status: str = "draft",
    author_id: uuid.UUID | None = None,
) -> BlogPostMetadata:
    """Create a BlogPostMetadata row directly (no markdown file needed)."""
    if slug is None:
        slug = f"seeded-post-{uuid.uuid4().hex[:8]}"
    post = BlogPostMetadata(
        slug=slug,
        title=title,
        status=status,
        author_id=author_id or uuid.uuid4(),
    )
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    return post


# ---------------------------------------------------------------------------
# POST /admin/blog/posts — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_post_success(async_client, admin_headers, tmp_path) -> None:
    """Editor/admin can create a blog post; returns 201 with post data."""
    from pathlib import Path

    with patch("nfm_db.services.blog_post.get_content_dir", return_value=Path(str(tmp_path))):
        resp = await async_client.post(
            BASE + "/posts",
            json=_CREATE_PAYLOAD,
            headers=admin_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Post Title"
    assert data["status"] == "draft"
    assert "id" in data
    assert "slug" in data


@pytest.mark.asyncio
async def test_create_post_with_editor(async_client, editor_headers, tmp_path) -> None:
    """An editor (not admin) can also create posts."""
    from pathlib import Path

    with patch("nfm_db.services.blog_post.get_content_dir", return_value=Path(str(tmp_path))):
        resp = await async_client.post(
            BASE + "/posts",
            json=_CREATE_PAYLOAD,
            headers=editor_headers,
        )

    assert resp.status_code == 201
    assert resp.json()["title"] == "Test Post Title"


@pytest.mark.asyncio
async def test_create_post_unauthenticated(async_client) -> None:
    """Unauthenticated requests receive 401 (HTTPBearer missing)."""
    resp = await async_client.post(BASE + "/posts", json=_CREATE_PAYLOAD)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_post_reviewer_forbidden(async_client, reviewer_headers) -> None:
    """A reviewer (no create_post permission) cannot create posts."""
    resp = await async_client.post(
        BASE + "/posts",
        json=_CREATE_PAYLOAD,
        headers=reviewer_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_post_validation_empty_title(async_client, admin_headers) -> None:
    """Empty title violates min_length=1, returns 422."""
    payload = {**_CREATE_PAYLOAD, "title": ""}
    resp = await async_client.post(BASE + "/posts", json=payload, headers=admin_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_post_validation_empty_content(async_client, admin_headers) -> None:
    """Empty content violates min_length=1, returns 422."""
    payload = {**_CREATE_PAYLOAD, "content": ""}
    resp = await async_client.post(BASE + "/posts", json=payload, headers=admin_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_post_validation_too_many_tags(async_client, admin_headers) -> None:
    """More than 10 tags violates max_length=10, returns 422."""
    payload = {**_CREATE_PAYLOAD, "tags": [f"tag-{i}" for i in range(11)]}
    resp = await async_client.post(BASE + "/posts", json=payload, headers=admin_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/blog/posts — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_posts_empty(async_client, admin_headers) -> None:
    """Returns 200 with empty list when no posts exist."""
    resp = await async_client.get(BASE + "/posts", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_posts_with_data(async_client, admin_headers, db_session, admin_user) -> None:
    """Returns seeded posts in descending created_at order."""
    await _seed_post(db_session, title="Alpha", author_id=admin_user.id)
    await _seed_post(db_session, title="Beta", author_id=admin_user.id)

    resp = await async_client.get(BASE + "/posts", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_list_posts_filter_by_status(
    async_client, admin_headers, db_session, admin_user
) -> None:
    """Filtering by status=draft returns only draft posts."""
    await _seed_post(db_session, title="Draft-A", status="draft", author_id=admin_user.id)
    await _seed_post(db_session, title="Published-B", status="published", author_id=admin_user.id)

    resp = await async_client.get(
        BASE + "/posts",
        params={"status": "draft"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(p["status"] == "draft" for p in data)
    assert len(data) == 1


@pytest.mark.asyncio
async def test_list_posts_filter_invalid_status(async_client, admin_headers) -> None:
    """Invalid status query param returns 400."""
    resp = await async_client.get(
        BASE + "/posts",
        params={"status": "not_a_real_status"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_posts_filter_by_author(
    async_client, admin_headers, db_session, admin_user, editor_user
) -> None:
    """Filtering by author_id returns posts for that author only."""
    await _seed_post(db_session, title="Admin-Post", author_id=admin_user.id)
    await _seed_post(db_session, title="Editor-Post", author_id=editor_user.id)

    resp = await async_client.get(
        BASE + "/posts",
        params={"author_id": str(editor_user.id)},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Editor-Post"


@pytest.mark.asyncio
async def test_list_posts_filter_invalid_author_id(async_client, admin_headers) -> None:
    """Non-UUID author_id returns 400."""
    resp = await async_client.get(
        BASE + "/posts",
        params={"author_id": "not-a-uuid"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_posts_pagination(async_client, admin_headers, db_session, admin_user) -> None:
    """limit and offset params control pagination."""
    for i in range(5):
        await _seed_post(db_session, title=f"Post-{i}", author_id=admin_user.id)

    resp = await async_client.get(
        BASE + "/posts",
        params={"limit": 2, "offset": 0},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_list_posts_reviewer_allowed(
    async_client, reviewer_headers, db_session, admin_user
) -> None:
    """Reviewer role can access the list endpoint."""
    await _seed_post(db_session, title="Visible", author_id=admin_user.id)

    resp = await async_client.get(BASE + "/posts", headers=reviewer_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_posts_unauthenticated(async_client) -> None:
    """Unauthenticated requests receive 401 (HTTPBearer missing)."""
    resp = await async_client.get(BASE + "/posts")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /admin/blog/posts/{slug} — get by slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_post_by_slug(async_client, admin_headers, db_session, admin_user) -> None:
    """Returns 200 with the matching post."""
    post = await _seed_post(db_session, title="Get Me", author_id=admin_user.id)

    resp = await async_client.get(
        f"{BASE}/posts/{post.slug}",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == post.slug
    assert data["title"] == "Get Me"


@pytest.mark.asyncio
async def test_get_post_not_found(async_client, admin_headers) -> None:
    """Nonexistent slug returns 404."""
    resp = await async_client.get(
        f"{BASE}/posts/nonexistent-slug-xyz",
        headers=admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_post_unauthenticated(async_client) -> None:
    """Unauthenticated requests receive 401."""
    resp = await async_client.get(f"{BASE}/posts/hidden-slug-xyz")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /admin/blog/posts/{slug} — update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_post_success(
    async_client, admin_headers, db_session, admin_user, tmp_path
) -> None:
    """Editor/admin can update a post's title and content."""
    from pathlib import Path

    slug = f"update-me-{uuid.uuid4().hex[:8]}"
    await _seed_post(db_session, slug=slug, title="Old Title", author_id=admin_user.id)

    with patch("nfm_db.services.blog_post.get_content_dir", return_value=Path(str(tmp_path))):
        resp = await async_client.put(
            f"{BASE}/posts/{slug}",
            json={"title": "New Title"},
            headers=admin_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "New Title"


@pytest.mark.asyncio
async def test_update_post_not_found(async_client, admin_headers) -> None:
    """Nonexistent slug returns 404."""
    resp = await async_client.put(
        f"{BASE}/posts/ghost-slug",
        json={"title": "Does not matter"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_post_unauthenticated(async_client) -> None:
    """Unauthenticated requests receive 401."""
    resp = await async_client.put(
        f"{BASE}/posts/some-slug",
        json={"title": "Nope"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /admin/blog/posts/{slug} — delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_post_as_admin(async_client, admin_headers, db_session, admin_user) -> None:
    """Admin can delete any post."""
    slug = f"delete-admin-{uuid.uuid4().hex[:8]}"
    await _seed_post(db_session, slug=slug, title="Delete Me", author_id=admin_user.id)

    resp = await async_client.delete(
        f"{BASE}/posts/{slug}",
        headers=admin_headers,
    )
    assert resp.status_code == 204

    # Verify it is gone
    resp2 = await async_client.get(
        f"{BASE}/posts/{slug}",
        headers=admin_headers,
    )
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_post_as_author(async_client, editor_headers, db_session, editor_user) -> None:
    """Author can delete their own post."""
    slug = f"delete-author-{uuid.uuid4().hex[:8]}"
    await _seed_post(db_session, slug=slug, title="My Post", author_id=editor_user.id)

    resp = await async_client.delete(
        f"{BASE}/posts/{slug}",
        headers=editor_headers,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_post_other_author_forbidden(
    async_client, editor_user, db_session, admin_user
) -> None:
    """A non-author, non-admin user receives 403."""
    from nfm_db.services.auth_service import create_access_token

    slug = f"delete-other-{uuid.uuid4().hex[:8]}"
    await _seed_post(db_session, slug=slug, title="Not Mine", author_id=admin_user.id)

    token = create_access_token(data={"sub": str(editor_user.id)})
    headers = {"Authorization": f"Bearer {token}"}

    resp = await async_client.delete(
        f"{BASE}/posts/{slug}",
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_post_not_found(async_client, admin_headers) -> None:
    """Nonexistent slug returns 404."""
    resp = await async_client.delete(
        f"{BASE}/posts/ghost-slug-delete",
        headers=admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_post_unauthenticated(async_client) -> None:
    """Unauthenticated requests receive 401."""
    resp = await async_client.delete(f"{BASE}/posts/some-slug")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/blog/posts/{slug}/workflow — workflow actions
#
# The workflow endpoint uses get_current_active_user (any active user), but the
# underlying state machine checks permissions. Admin has ALL permissions so we
# use admin_headers for all workflow happy-path tests. We patch
# update_markdown_status so the tests do not need markdown files on disk.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.services.blog_post.update_markdown_status")
@patch("nfm_db.services.blog_post.validate_transition")
async def test_workflow_submit(
    mock_validate, mock_md, async_client, admin_headers, db_session, admin_user
) -> None:
    """Admin can submit a draft post for review."""
    slug = f"wf-submit-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="Submit Me",
        status="draft",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "submit"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "under_review"
    assert "submitted" in data["message"].lower()
    mock_validate.assert_called_once()


@pytest.mark.asyncio
@patch("nfm_db.services.blog_post.update_markdown_status")
async def test_workflow_approve(
    mock_md, async_client, admin_headers, db_session, admin_user
) -> None:
    """Admin can approve a post under review."""
    slug = f"wf-approve-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="Approve Me",
        status="under_review",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "approve"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"


@pytest.mark.asyncio
@patch("nfm_db.services.blog_post.update_markdown_status")
async def test_workflow_reject(
    mock_md, async_client, admin_headers, db_session, admin_user
) -> None:
    """Admin can reject a post under review with a reason."""
    slug = f"wf-reject-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="Reject Me",
        status="under_review",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "reject", "rejection_reason": "Needs more data"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"


@pytest.mark.asyncio
async def test_workflow_reject_without_reason(
    async_client, admin_headers, db_session, admin_user
) -> None:
    """Reject without rejection_reason returns 400."""
    slug = f"wf-reject-nr-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="Reject No Reason",
        status="under_review",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "reject"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@patch("nfm_db.services.blog_post.update_markdown_status")
async def test_workflow_publish(
    mock_md, async_client, admin_headers, db_session, admin_user
) -> None:
    """Admin can publish an approved post."""
    slug = f"wf-publish-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="Publish Me",
        status="approved",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "publish"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "published"


@pytest.mark.asyncio
async def test_workflow_invalid_action(async_client, admin_headers, db_session, admin_user) -> None:
    """An unrecognized action is rejected by Pydantic validation (422)."""
    slug = f"wf-invalid-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="Invalid Action",
        status="draft",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "explode"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_workflow_unauthenticated(async_client, db_session, admin_user) -> None:
    """Unauthenticated requests receive 401."""
    slug = f"wf-noauth-{uuid.uuid4().hex[:8]}"
    await _seed_post(
        db_session,
        slug=slug,
        title="No Auth",
        status="draft",
        author_id=admin_user.id,
    )

    resp = await async_client.post(
        f"{BASE}/posts/{slug}/workflow",
        json={"action": "submit"},
    )
    assert resp.status_code == 401
