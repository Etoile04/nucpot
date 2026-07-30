"""NFM-2102: Customer-case blog post PoC tests.

Verifies that the blog API can store and surface a customer-case scenario
(ICP-1: nuclear-research institute using NFM-DB for UO2 thermal_conductivity
research) without breaking the existing blog workflow.

SLOs from the issue:
- 0 mypy error
- 0 pydantic ValidationError
- Backward compatible — existing endpoints must keep working.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient

BASE = "/api/v1/admin/blog"

CUSTOMER_CASE_PAYLOAD = {
    "title": "ICP-1 客户案例：中核研究院使用 NFM-DB 进行 UO2 热导率研究",
    "summary": "ICP-1 客户案例：中核研究院采用 NFM-DB 平台建立 UO2 (二氧化铀) 热导率数据库，"
    "覆盖 300–1600 K 实验数据与第一性原理计算结果，支持反应堆燃料元件热工设计。",
    "content": (
        "# 客户案例：核燃料材料研究中的 UO2 热导率数据治理\n\n"
        "## 场景\n\n"
        "中核研究院 (ICP-1) 在新一代反应堆燃料元件研发中，需要建立标准化的 "
        "UO2 (二氧化铀) 热导率数据库，覆盖常压 / 高温 / 高 burnup 工况。\n\n"
        "## 痛点\n\n"
        "- 文献中分散的 `thermal_conductivity` 测量数据缺乏统一 schema;\n"
        "- 客户使用 Fink 2000、Rest 2005、Hyland 1983 等多源数据；\n"
        "- 缺乏 Material UO2 实体与 Property `thermal_conductivity` 的稳定关联。\n\n"
        "## 方案\n\n"
        "通过 NFM-DB 平台的 Blog 客户案例模块，发布 ICP-1 案例报告：\n\n"
        "- **Material**: UO2\n"
        "- **Property**: thermal_conductivity\n"
        "- **数据来源**: 73 篇文献，3,200+ 测量点\n"
        "- **发布状态**: published\n\n"
        "## 成效\n\n"
        "数据库引用次数 +42%，下游热工仿真查询延时 -18%。\n"
    ),
    "tags": [
        "customer-case",
        "icp-1",
        "uo2",
        "thermal_conductivity",
        "nuclear-fuel",
    ],
    "author_name": "NFMD Content Team",
}


@pytest.mark.asyncio
async def test_create_customer_case_blog_post(
    async_client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    """Customer-case payload must round-trip through the admin blog API.

    The post is identified by the ``customer-case`` tag and references the
    UO2 material and ``thermal_conductivity`` property via additional tags.
    """
    with patch(
        "nfm_db.services.blog_post.get_content_dir",
        return_value=Path(str(tmp_path)),
    ):
        resp = await async_client.post(
            BASE + "/posts",
            json=CUSTOMER_CASE_PAYLOAD,
            headers=admin_headers,
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Schema fields populated from DB metadata + markdown frontmatter
    assert body["title"] == CUSTOMER_CASE_PAYLOAD["title"]
    assert body["summary"] == CUSTOMER_CASE_PAYLOAD["summary"]
    assert body["author_name"] == CUSTOMER_CASE_PAYLOAD["author_name"]
    assert body["status"] == "draft"
    assert uuid.UUID(body["id"])  # parses as a valid UUID
    assert body["slug"]  # non-empty slug assigned by the service

    # Tags round-trip including customer-case + entity anchors
    assert "customer-case" in body["tags"]
    assert "icp-1" in body["tags"]
    assert "uo2" in body["tags"]
    assert "thermal_conductivity" in body["tags"]


@pytest.mark.asyncio
async def test_customer_case_blog_post_get_by_slug(
    async_client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    """A created customer-case post must be retrievable by its slug."""
    with patch(
        "nfm_db.services.blog_post.get_content_dir",
        return_value=Path(str(tmp_path)),
    ):
        create_resp = await async_client.post(
            BASE + "/posts",
            json=CUSTOMER_CASE_PAYLOAD,
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        slug = create_resp.json()["slug"]

        get_resp = await async_client.get(
            f"{BASE}/posts/{slug}",
            headers=admin_headers,
        )
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["slug"] == slug
    assert "customer-case" in body["tags"]
    assert body["title"] == CUSTOMER_CASE_PAYLOAD["title"]


@pytest.mark.asyncio
async def test_customer_case_blog_post_schema_validation(
    async_client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    """Pydantic must accept the customer-case payload without ValidationError."""
    from nfm_db.schemas.blog_post import BlogPostCreate, BlogPostResponse

    # Input-side validation
    parsed = BlogPostCreate.model_validate(CUSTOMER_CASE_PAYLOAD)
    assert parsed.title == CUSTOMER_CASE_PAYLOAD["title"]
    assert "customer-case" in parsed.tags

    # End-to-end output-side validation via the API
    with patch(
        "nfm_db.services.blog_post.get_content_dir",
        return_value=Path(str(tmp_path)),
    ):
        resp = await async_client.post(
            BASE + "/posts",
            json=CUSTOMER_CASE_PAYLOAD,
            headers=admin_headers,
        )
    assert resp.status_code == 201, resp.text

    body = resp.json()
    # If the response shape is malformed this raises ValidationError
    parsed_response = BlogPostResponse.model_validate(body)
    assert "customer-case" in parsed_response.tags
    assert parsed_response.title == CUSTOMER_CASE_PAYLOAD["title"]


@pytest.mark.asyncio
async def test_existing_blog_endpoints_unaffected(
    async_client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    """Existing blog endpoints continue to work after the PoC.

    Regression guard: posts that do NOT carry the ``customer-case`` tag
    must round-trip exactly as before (no shape drift, no missing fields).
    """
    plain_payload = {
        "title": "Plain Internal Update",
        "content": "Routine internal post.",
        "summary": "Routine internal post summary.",
        "tags": ["internal", "release-notes"],
        "author_name": "Release Manager",
    }

    with patch(
        "nfm_db.services.blog_post.get_content_dir",
        return_value=Path(str(tmp_path)),
    ):
        resp = await async_client.post(
            BASE + "/posts",
            json=plain_payload,
            headers=admin_headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == plain_payload["title"]
    assert body["summary"] == plain_payload["summary"]
    assert "customer-case" not in body["tags"]
    assert body["status"] == "draft"
