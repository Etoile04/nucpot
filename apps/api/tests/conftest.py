"""Shared test fixtures for API integration tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models import Base, BlogRole, User
from nfm_db.services.auth_service import create_access_token


# ---------------------------------------------------------------------------
# SQLite compatibility: JSONB is PG-only — fall back to TEXT for test DDL.
# This must run before the first ``create_all`` in any fixture.
# ---------------------------------------------------------------------------
@event.listens_for(Base.metadata, "before_create")
def _map_jsonb_to_text_for_sqlite(
    target,
    connection,
    **_kw,
) -> None:
    """Replace JSONB columns with TEXT so ``create_all`` succeeds on SQLite.

    Runs on every ``before_create`` event (not once-only) because each test
    re-creates the database from scratch.
    """
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
    from sqlalchemy.dialects.sqlite.base import SQLiteDialect

    if not isinstance(connection.dialect, SQLiteDialect):
        return

    for table in target.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = Text()


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> None:
    """Isolate all in-process rate limiters between tests (no cross-test 429)."""
    from nfm_db.services.rate_limit import md_verification_limiter, ontology_limiter

    ontology_limiter.reset()
    md_verification_limiter.reset()
    yield
    ontology_limiter.reset()
    md_verification_limiter.reset()


@pytest.fixture
async def db_session() -> AsyncSession:
    """Create an in-memory SQLite async session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def async_client(db_session: AsyncSession):
    """Create an async test client for the FastAPI app with database override."""

    # Override the database dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # Clean up override
    app.dependency_overrides.clear()


@pytest.fixture
async def admin_user(db_session: AsyncSession):
    """Create an admin user for testing."""
    user = User(
        username="admin",
        email="admin@example.com",
        hashed_password="hashed_password_here",
        blog_role=BlogRole.ADMIN,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def editor_user(db_session: AsyncSession):
    """Create an editor user for testing."""
    user = User(
        username="editor",
        email="editor@example.com",
        hashed_password="hashed_password_here",
        blog_role=BlogRole.EDITOR,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def reviewer_user(db_session: AsyncSession):
    """Create a reviewer user for testing."""
    user = User(
        username="reviewer",
        email="reviewer@example.com",
        hashed_password="hashed_password_here",
        blog_role=BlogRole.REVIEWER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def admin_headers(admin_user: User):
    """Create headers with admin authentication token."""
    token = create_access_token(data={"sub": str(admin_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def editor_headers(editor_user: User):
    """Create headers with editor authentication token."""
    token = create_access_token(data={"sub": str(editor_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def reviewer_headers(reviewer_user: User):
    """Create headers with reviewer authentication token."""
    token = create_access_token(data={"sub": str(reviewer_user.id)})
    return {"Authorization": f"Bearer {token}"}
