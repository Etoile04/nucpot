"""Shared test fixtures for API integration tests."""

from __future__ import annotations

import sqlalchemy.exc as sa_exc
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON
from sqlalchemy.dialects.sqlite.base import SQLiteDialect
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models import Base, BlogRole, User
from nfm_db.services.auth_service import create_access_token


# ---------------------------------------------------------------------------
# SQLite compatibility helpers
# ---------------------------------------------------------------------------


def _strip_dangling_fks(metadata) -> None:
    """Remove FKs whose target table is absent from the metadata.

    ``extraction_figures.job_id`` references ``extraction_jobs`` which is
    not registered — SQLAlchemy's ``sort_tables_and_constraints`` raises
    ``NoReferencedTableError``.  We strip these before ``create_all``.
    Valid FKs (whose target *is* in the metadata) are kept so that ORM
    relationships can resolve their join conditions.
    """
    registered = set(metadata.tables.keys())
    for table in metadata.tables.values():
        for col in table.columns:
            dangling = [
                fk for fk in list(col.foreign_keys)
                if fk._colspec.split(".")[0].strip('"') not in registered
            ]
            for fk in dangling:
                col.foreign_keys.discard(fk)
        table_fks_to_remove = [
            fkc for fkc in list(table.constraints)
            if hasattr(fkc, "_colspec")
            and fkc._colspec.split(".")[0].strip('"') not in registered
        ]
        for fkc in table_fks_to_remove:
            table.constraints.discard(fkc)


def _replace_jsonb(metadata) -> None:
    """Replace JSONB columns with JSON for SQLite compat."""
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()


def _safe_create_all(sync_conn, metadata) -> None:
    """Create all tables, stripping dangling FKs first.

    ``conn.run_sync`` passes ``(sync_connection, *args)`` as the callable's
    positional arguments.
    """
    _replace_jsonb(metadata)
    _strip_dangling_fks(metadata)
    metadata.create_all(sync_conn)


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
        await conn.run_sync(_safe_create_all, Base.metadata)

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


@pytest.fixture
async def authenticated_client(db_session: AsyncSession, admin_user: User):
    """Create an async test client with admin authentication headers."""
    from nfm_db.services.auth_service import create_access_token

    token = create_access_token(data={"sub": str(admin_user.id)})
    headers = {"Authorization": f"Bearer {token}"}

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        yield client

    app.dependency_overrides.clear()
