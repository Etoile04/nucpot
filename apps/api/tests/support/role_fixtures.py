"""Role fixtures: users and auth headers for the four blog roles (NFM-5951 core layer).

Moved verbatim from the root conftest (C2 step 1): admin/editor/reviewer/
domain_expert user fixtures plus the matching Bearer-header fixtures.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import BlogRole, User
from nfm_db.services.auth_service import create_access_token


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
async def domain_expert_user(db_session: AsyncSession):
    """Create a domain_expert user for testing."""
    user = User(
        username="domain_expert",
        email="domain_expert@example.com",
        hashed_password="hashed_password_here",
        blog_role=BlogRole.DOMAIN_EXPERT,
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
async def domain_expert_headers(domain_expert_user: User):
    """Create headers with domain_expert authentication token."""
    token = create_access_token(data={"sub": str(domain_expert_user.id)})
    return {"Authorization": f"Bearer {token}"}
