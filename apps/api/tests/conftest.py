"""Shared test fixtures for API integration tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models import Base, BlogRole, User
from nfm_db.services.auth_service import create_access_token

# Deterministic user IDs for FK seed data (matched by test_blog_post_service,
# test_blog_auth, test_md_verification_service_edge_cases).
_SEED_AUTHOR_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")
_SEED_REVIEWER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000002")
_SEED_OTHER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000003")


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
    """Replace JSONB/ARRAY columns with TEXT/JSON for SQLite compat."""
    from sqlalchemy import ARRAY as SA_ARRAY
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY

    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
            if isinstance(col.type, (PG_ARRAY, SA_ARRAY)):
                col.type = JSON()


def _safe_create_all(sync_conn, metadata) -> None:
    """Create all tables, stripping dangling FKs first.

    ``conn.run_sync`` passes ``(sync_connection, *args)`` as the callable's
    positional arguments.
    """
    _replace_jsonb(metadata)
    _strip_dangling_fks(metadata)
    metadata.create_all(sync_conn)


@pytest.fixture(autouse=True, scope="session")
def _disable_global_rate_limiting() -> None:
    """Strip all rate-limiting from the test app.

    Two layers exist:
    1. **Middleware** — a global ``slowapi`` burst limiter (20/second).  Removed
       from ``app.user_middleware`` entirely; ``limiter.reset()`` alone is
       insufficient because the counter accumulates mid-test.
    2. **Per-endpoint dependencies** — ``InProcessRateLimiter`` instances bound
       via ``Depends()`` in ontology and md-verification routes.  Overridden
       with no-ops so the 429 gate never fires during the test suite.
    """
    from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware
    from nfm_db.services.rate_limit import (
        md_verification_rate_limit,
        ontology_rate_limit,
    )

    # 1. Remove global middleware.
    app.user_middleware = [
        mw for mw in app.user_middleware
        if mw.cls is not NFMRateLimitMiddleware
    ]

    # 2. Override per-endpoint rate-limit dependencies with no-ops.
    async def _noop() -> None:  # pragma: no cover
        pass

    app.dependency_overrides[ontology_rate_limit] = _noop
    app.dependency_overrides[md_verification_rate_limit] = _noop


@pytest.fixture
async def db_session() -> AsyncSession:
    """Create an in-memory SQLite async session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(_safe_create_all, Base.metadata)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with session_factory() as session:
        # Seed deterministic users so FK references in blog/md_verification tests resolve.
        for uid, name, email in [
            (_SEED_AUTHOR_ID, "_fk_seed_author", "_fk_author@test.com"),
            (_SEED_REVIEWER_ID, "_fk_seed_reviewer", "_fk_reviewer@test.com"),
            (_SEED_OTHER_ID, "_fk_seed_other", "_fk_other@test.com"),
        ]:
            existing = await session.get(User, uid)
            if existing is None:
                session.add(User(
                    id=uid, username=name, email=email,
                    hashed_password="hashed",
                ))
        await session.flush()
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

    # Clean up only the DB override — not the session-scoped rate-limit overrides.
    app.dependency_overrides.pop(get_db, None)


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

    app.dependency_overrides.pop(get_db, None)
