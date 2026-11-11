"""providers-cluster fixtures (C2 step 3 pilot, NFM-5951).

Cluster conftest pattern: re-export the core-layer fixtures so this tree is
self-sufficient (portable to the steps-4+ split without the root
conftest's app-cluster fixtures), and provide a cluster-level client built
on ``create_app`` with a router subset — NOT the full module-level
singleton.

Gate (NFM-5951 acceptance): this tree must carry zero literal references
to the app entrypoint module — the dependency is mediated by
``tests.support.app_factory`` (grep the tree for the entrypoint import
path; the hit count must be zero, docstrings included).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1 import potentials
from nfm_db.database import get_db
from tests.support.app_factory import ALL_ROUTER_MOUNTS, create_app, install_standard_handlers

# Core-layer re-exports (fixture registration for this cluster).
from tests.support.db_fixtures import db, db_session, pg_session  # noqa: F401
from tests.support.role_fixtures import (  # noqa: F401
    admin_headers,
    admin_user,
    domain_expert_headers,
    domain_expert_user,
    editor_headers,
    editor_user,
    reviewer_headers,
    reviewer_user,
)

# The potentials router is the providers domain surface (potential records
# are the objects the external data providers supply); picked by router
# identity so this stays correct if the mount metadata moves.
_PROVIDERS_MOUNTS = [mount for mount in ALL_ROUTER_MOUNTS if mount[0] is potentials.router]


# The ``db_session`` / ``admin_user`` parameters intentionally shadow the
# re-exported fixture names (pytest resolves fixtures by parameter name).
@pytest.fixture
async def cluster_client(db_session: AsyncSession):  # noqa: F811
    """Cluster-level client on a ``create_app`` router subset (not the singleton).

    ``middleware=[]`` — ASGITransport exercises no CORS/proxy chain, and the
    rate-limit strips in the root conftest's autouse fixtures target the
    singleton only; a bare middleware stack keeps the cluster app
    deterministic.  The standard NFM-1090 error envelope stays installed so
    error-response shapes match production.
    """
    cluster_app = create_app(
        routers=_PROVIDERS_MOUNTS,
        middleware=[],
        handlers=[install_standard_handlers],
    )

    async def override_get_db():
        yield db_session

    cluster_app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=cluster_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    cluster_app.dependency_overrides.pop(get_db, None)
