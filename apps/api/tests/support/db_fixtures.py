"""Session primitives: ``db_session`` / ``db`` / ``pg_session`` (NFM-5951 core layer).

Moved verbatim from the root conftest (C2 step 1).  ``db_session`` is the
function-scoped in-memory SQLite workhorse; ``db`` is its NFM-4159 alias;
``pg_session`` is the NFM-2032 opt-in real-Postgres probe.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.models import Base, User
from tests.support.sqlite_compat import _create_attribution_view, _safe_create_all

# Deterministic user IDs for FK seed data (matched by test_blog_post_service,
# test_blog_auth, test_md_verification_service_edge_cases).
_SEED_AUTHOR_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")
_SEED_REVIEWER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000002")
_SEED_OTHER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000003")


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
        await conn.run_sync(_create_attribution_view)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
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
                session.add(
                    User(
                        id=uid,
                        username=name,
                        email=email,
                        hashed_password="hashed",
                    )
                )
        await session.flush()
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# ---------------------------------------------------------------------------
# NFM-4159 — ``db`` fixture alias.
# ---------------------------------------------------------------------------
# New test files pin ``db: AsyncSession`` as the per-test session fixture.
# The long-standing shared fixture is ``db_session``; alias here so test
# code reads naturally without rewriting shared infrastructure.
@pytest.fixture
async def db(db_session: AsyncSession) -> AsyncSession:
    """Per-test async session — alias for ``db_session`` (NFM-4159)."""
    return db_session


# ---------------------------------------------------------------------------
# NFM-2032: real-Postgres integration fixture (opt-in)
# ---------------------------------------------------------------------------
# Activated by setting NFM_TEST_DATABASE_URL to a disposable asyncpg
# URL (e.g. ``postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_test_nfm2032``).
# When the env var is unset the fixture is auto-skipped, so the regular
# SQLite ``db_session`` continues to drive the rest of the test suite.
# This is the only piece of the test stack that exercises the real
# Postgres dialect against the 5-tuple composite UNIQUE INDEX introduced
# by migration 033; SQLAlchemy's SQLite dialect silently accepts
# duplicate UNIQUE rows in some configurations, so the SQLite suite
# cannot be trusted on its own for race detection.


_NFM_TEST_PG_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()


@pytest.fixture
async def pg_session() -> AsyncSession:
    """Create a disposable Postgres async session for NFM-2032 SQL probes.

    Skips when ``NFM_TEST_DATABASE_URL`` is unset; the test calling
    this fixture must therefore be decorated with
    ``@pytest.mark.skipif(not _NFM_TEST_PG_URL, ...)`` or equivalent
    so the suite stays green on CI without Postgres.

    Schema is created from the application metadata (no migrations
    applied); the dedup tests assert the composite UNIQUE INDEX is
    enforced by the DB regardless of how the schema was built.
    """
    if not _NFM_TEST_PG_URL:
        pytest.skip("NFM_TEST_DATABASE_URL is not set; skipping real-Postgres test")

    engine = create_async_engine(_NFM_TEST_PG_URL, echo=False)
    # Replace JSONB/ARRAY with JSON because the test metadata is
    # written once for both SQLite and Postgres; the prod sessions
    # use the JSONB/ARRAY types so the schema matches production.
    async with engine.begin() as conn:
        await conn.run_sync(_safe_create_all, Base.metadata)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
