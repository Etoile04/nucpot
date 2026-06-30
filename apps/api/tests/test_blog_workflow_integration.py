"""Integration tests covering the full blog workflow with mocked JWT auth.

Tests exercise the HTTP API endpoints (not service-layer functions) to
verify status transitions, role-based access, and authentication guards.

Depends on conftest.py fixtures:
  - db_session, async_client (SQLite in-memory + httpx AsyncClient)
  - admin_user / editor_user / reviewer_user
  - admin_headers / editor_headers / reviewer_headers
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# Test 1: Create post as editor → verify DRAFT status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_post_as_editor_returns_draft(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
) -> None:
    """Test 1: Editor creates a post → status is DRAFT in response."""
    resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        headers=editor_headers,
        json={
            "title": "Integration Test Post",
            "content": "# Hello\n\nThis is content.",
            "summary": "A test post",
            "tags": ["test"],
            "author_name": "Test Editor",
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert "slug" in data
    assert data["author_id"] is not None


# ---------------------------------------------------------------------------
# Test 2: Submit for review → verify UNDER_REVIEW status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_submit_for_review_transitions_status(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
) -> None:
    """Test 2: Editor submits draft → status becomes UNDER_REVIEW."""
    # Create a post first
    create_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        headers=editor_headers,
        json={
            "title": "Submit Test",
            "content": "# Submit\n\nContent here.",
            "summary": "Summary",
            "tags": ["test"],
            "author_name": "Editor",
        },
    )
    slug = create_resp.json()["slug"]

    # Submit for review
    wf_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )

    assert wf_resp.status_code == 200
    assert wf_resp.json()["status"] == "under_review"
    assert wf_resp.json()["slug"] == slug


# ---------------------------------------------------------------------------
# Test 3: Approve as reviewer → verify APPROVED status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_approve_post_as_reviewer(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
    reviewer_headers: dict[str, str],
) -> None:
    """Test 3: Reviewer approves under_review post → APPROVED."""
    # Editor creates and submits
    create_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        headers=editor_headers,
        json={
            "title": "Approve Test",
            "content": "# Approve\n\nContent.",
            "summary": "Summary",
            "tags": ["test"],
            "author_name": "Editor",
        },
    )
    slug = create_resp.json()["slug"]

    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )

    # Reviewer approves
    approve_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=reviewer_headers,
        json={"action": "approve"},
    )

    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"


# ---------------------------------------------------------------------------
# Test 4: Publish as admin → verify PUBLISHED status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_publish_approved_post_as_admin(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
    reviewer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """Test 4: Admin publishes approved post → PUBLISHED."""
    # Full pipeline: create → submit → approve → publish
    create_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        headers=editor_headers,
        json={
            "title": "Publish Test",
            "content": "# Publish\n\nContent.",
            "summary": "Summary",
            "tags": ["test"],
            "author_name": "Editor",
        },
    )
    slug = create_resp.json()["slug"]

    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )
    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=reviewer_headers,
        json={"action": "approve"},
    )

    # Admin publishes
    publish_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=admin_headers,
        json={"action": "publish"},
    )

    assert publish_resp.status_code == 200
    assert publish_resp.json()["status"] == "published"


# ---------------------------------------------------------------------------
# Test 5: Reject with reason → verify REJECTED status + reason stored
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_reject_post_stores_reason(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
    reviewer_headers: dict[str, str],
) -> None:
    """Test 5: Reviewer rejects with reason → REJECTED + reason persisted."""
    # Create and submit
    create_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        headers=editor_headers,
        json={
            "title": "Reject Test",
            "content": "# Reject\n\nContent.",
            "summary": "Summary",
            "tags": ["test"],
            "author_name": "Editor",
        },
    )
    slug = create_resp.json()["slug"]

    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )

    # Reviewer rejects with reason
    reject_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=reviewer_headers,
        json={"action": "reject", "rejection_reason": "Needs citations"},
    )

    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # Verify reason is stored by fetching the post
    get_resp = await async_client.get(
        f"{API_PREFIX}/admin/blog/posts/{slug}",
        headers=reviewer_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["rejection_reason"] == "Needs citations"


# ---------------------------------------------------------------------------
# Test 6: Unauthorized access (no token) → verify 401
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_unauthenticated_request_returns_401(
    async_client: AsyncClient,
) -> None:
    """Test 6: Request without auth token → 401 Unauthorized."""
    resp = await async_client.get(f"{API_PREFIX}/admin/blog/posts")
    assert resp.status_code == 401

    resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        json={
            "title": "No Auth",
            "content": "# No Auth\n\nContent.",
            "summary": "Summary",
            "tags": [],
            "author_name": "Anonymous",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 7: Role-based access (editor cannot publish) → verify 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_editor_cannot_publish_returns_403(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
    reviewer_headers: dict[str, str],
) -> None:
    """Test 7: Editor tries to publish → 403 Forbidden.

    Editors lack publish_post permission. The state machine will
    raise a PermissionError which the endpoint should surface as 403.
    """
    # Create → submit → approve (by reviewer)
    create_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts",
        headers=editor_headers,
        json={
            "title": "Editor Publish Test",
            "content": "# EP\n\nContent.",
            "summary": "Summary",
            "tags": ["test"],
            "author_name": "Editor",
        },
    )
    slug = create_resp.json()["slug"]

    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )
    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=reviewer_headers,
        json={"action": "approve"},
    )

    # Editor tries to publish → should fail
    publish_resp = await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
        headers=editor_headers,
        json={"action": "publish"},
    )

    assert publish_resp.status_code == 403


# ---------------------------------------------------------------------------
# Test 8: Public list only returns PUBLISHED posts
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_posts_status_filter_isolation(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
    reviewer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """Test 8: Filtering by status=published returns only published posts.

    Creates posts in various states and verifies the status filter works.
    """
    # Create 3 posts with different fates
    for title in ["Pub A", "Pub B", "Pub C"]:
        await async_client.post(
            f"{API_PREFIX}/admin/blog/posts",
            headers=editor_headers,
            json={
                "title": title,
                "content": f"# {title}\n\nContent.",
                "summary": f"Summary {title}",
                "tags": ["test"],
                "author_name": "Editor",
            },
        )

    # Get all posts to find slugs
    all_resp = await async_client.get(
        f"{API_PREFIX}/admin/blog/posts",
        headers=admin_headers,
    )
    assert all_resp.status_code == 200
    slugs = [p["slug"] for p in all_resp.json()]
    assert len(slugs) >= 3

    # Submit + approve first post → publish
    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slugs[0]}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )
    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slugs[0]}/workflow",
        headers=reviewer_headers,
        json={"action": "approve"},
    )
    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slugs[0]}/workflow",
        headers=admin_headers,
        json={"action": "publish"},
    )

    # Submit second post → leave under_review
    await async_client.post(
        f"{API_PREFIX}/admin/blog/posts/{slugs[1]}/workflow",
        headers=editor_headers,
        json={"action": "submit"},
    )

    # Filter by published
    pub_resp = await async_client.get(
        f"{API_PREFIX}/admin/blog/posts",
        params={"status": "published"},
        headers=admin_headers,
    )
    assert pub_resp.status_code == 200
    published = pub_resp.json()
    assert len(published) >= 1
    assert all(p["status"] == "published" for p in published)

    # Filter by draft — should have at least one remaining draft
    draft_resp = await async_client.get(
        f"{API_PREFIX}/admin/blog/posts",
        params={"status": "draft"},
        headers=admin_headers,
    )
    assert draft_resp.status_code == 200
    drafts = draft_resp.json()
    assert len(drafts) >= 1
    assert all(p["status"] == "draft" for p in drafts)


# ---------------------------------------------------------------------------
# Bonus: Review count endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_review_count_endpoint(
    async_client: AsyncClient,
    editor_headers: dict[str, str],
    reviewer_headers: dict[str, str],
) -> None:
    """Bonus: Review count endpoint returns correct under_review count."""
    # Submit two posts for review
    for title in ["Count A", "Count B"]:
        create_resp = await async_client.post(
            f"{API_PREFIX}/admin/blog/posts",
            headers=editor_headers,
            json={
                "title": title,
                "content": f"# {title}\n\nContent.",
                "summary": f"Summary {title}",
                "tags": ["test"],
                "author_name": "Editor",
            },
        )
        slug = create_resp.json()["slug"]
        await async_client.post(
            f"{API_PREFIX}/admin/blog/posts/{slug}/workflow",
            headers=editor_headers,
            json={"action": "submit"},
        )

    count_resp = await async_client.get(
        f"{API_PREFIX}/admin/blog/posts/review-count",
        headers=reviewer_headers,
    )
    assert count_resp.status_code == 200
    assert count_resp.json()["count"] == 2
