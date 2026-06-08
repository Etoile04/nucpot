"""Integration tests for feedback API endpoints using test database."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.schemas.feedback import FeedbackCreate
from nfm_db.services.feedback import create_feedback


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


@pytest.mark.asyncio
async def test_submit_feedback_returns_201(db_session: AsyncSession) -> None:
    """POST /api/v1/feedback creates a new feedback and returns 201."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/feedback",
            json={
                "feedback_type": "bug_report",
                "title": "页面无法加载",
                "description": "点击查询后页面显示 500 错误",
                "page_url": "https://nucpot.example.com/potentials/ceo2",
                "contact_email": "user@example.com",
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["data"]["feedback_type"] == "bug_report"
    assert data["data"]["priority"] == "high"  # escalated due to "500"
    assert data["data"]["status"] == "open"
    assert "id" in data["data"]
    assert "created_at" in data["data"]


@pytest.mark.asyncio
async def test_submit_feedback_minimal_fields(db_session: AsyncSession) -> None:
    """POST /api/v1/feedback with only required fields succeeds."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/feedback",
            json={
                "feedback_type": "feature_request",
                "title": "希望增加导出功能",
                "description": "能否支持将数据导出为 Excel 格式",
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["data"]["priority"] == "low"
    assert data["data"]["feedback_type"] == "feature_request"


@pytest.mark.asyncio
async def test_submit_feedback_validates_required_fields(db_session: AsyncSession) -> None:
    """POST /api/v1/feedback rejects missing required fields with 422."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/feedback",
            json={"feedback_type": "bug_report"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_feedback_validates_title_length(db_session: AsyncSession) -> None:
    """POST /api/v1/feedback rejects title exceeding 100 characters."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/feedback",
            json={
                "feedback_type": "bug_report",
                "title": "x" * 101,
                "description": "valid description",
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_feedback_returns_empty(db_session: AsyncSession) -> None:
    """GET /api/v1/feedback returns empty list when no feedback exists."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/feedback")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["items"] == []
    assert data["data"]["total"] == 0


@pytest.mark.asyncio
async def test_list_feedback_returns_created_items(db_session: AsyncSession) -> None:
    """GET /api/v1/feedback returns previously submitted feedback."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    # Create feedback directly via service
    fb1 = await create_feedback(
        db_session,
        FeedbackCreate(
            feedback_type="bug_report",
            title="Bug 1",
            description="Description 1",
        ),
    )
    fb2 = await create_feedback(
        db_session,
        FeedbackCreate(
            feedback_type="feature_request",
            title="Feature 1",
            description="Description 2",
        ),
    )
    await db_session.commit()

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/feedback")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["total"] == 2
    assert len(data["data"]["items"]) == 2


@pytest.mark.asyncio
async def test_list_feedback_filters_by_type(db_session: AsyncSession) -> None:
    """GET /api/v1/feedback?feedback_type= filters results."""
    from nfm_db.database import get_db
    from nfm_db.main import app

    await create_feedback(
        db_session,
        FeedbackCreate(
            feedback_type="bug_report",
            title="Bug",
            description="Bug desc",
        ),
    )
    await create_feedback(
        db_session,
        FeedbackCreate(
            feedback_type="feature_request",
            title="Feature",
            description="Feature desc",
        ),
    )
    await db_session.commit()

    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/feedback",
            params={"feedback_type": "bug_report"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["total"] == 1
    assert data["data"]["items"][0]["feedback_type"] == "bug_report"
