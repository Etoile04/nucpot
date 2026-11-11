"""Shared test fixtures for API integration tests.

C2 three-layer assembly (NFM-5951 step 1): the reusable core primitives
(env pins, SQLite-compat schema helpers, db/pg session fixtures, role
fixtures) live in ``tests/support/``.  This root conftest shrinks to:

- re-exports of the core layer (the 432 flat test files keep their
  current fixture/import surface unchanged — ``scripts/tests`` and
  ``test_migration_080_kg_orphan_bridge.py`` import helpers via
  ``tests.conftest``);
- ``pytest_configure`` — single registration point for the
  ``no_auto_auth`` marker;
- the three autouse fixtures, kept as-is.  Ownership is explicit: the two
  rate-limit / auto-auth fixtures follow the full-app cluster in the
  step-4+ split; ``_clear_v2_flag_cache`` stays in the core layer;
- the ``scripts/`` sys.path injection, kept for now (consumer:
  ``test_phase_gate.py`` 15-line import + batch3-ci.yml PR-level job;
  its migration is step 5b, out of this ticket's scope).
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Ensure repo-root scripts/ is importable for phase gate and eval scripts.
# 暂保留 (NFM-5951 step 1): consumer test_phase_gate.py 15-line import +
# batch3-ci.yml:105 PR-level job are running against it; migration is step 5b.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# --- 环境钉子 must run before any numpy/xgboost-backed import below. -------
# The pins live in tests/support/env.py (NFM-5058 / PR #1400 run 2); this
# side-effect import must stay above the ``nfm_db`` imports — isort would
# hoist it into the first-party section below them (t > n), which would let
# threaded BLAS load before the pins (see tests/support/env.py for why the
# order is load-bearing).  ``setdefault`` keeps caller overrides authoritative.
import tests.support.env  # noqa: E402,F401,I001

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

# ---------------------------------------------------------------------------
# Core-layer re-exports (NFM-5951 step 1).  Fixture registration + import-
# surface compatibility: fixtures re-exported into this namespace are
# registered for the whole suite exactly as before the move.
# ---------------------------------------------------------------------------
from nfm_db.database import get_db  # noqa: E402
from nfm_db.main import app  # noqa: E402
from nfm_db.models import BlogRole, User  # noqa: E402
from tests.support.db_fixtures import (  # noqa: E402,F401
    _SEED_AUTHOR_ID,
    db,
    db_session,
    pg_session,
)
from tests.support.role_fixtures import (  # noqa: E402,F401
    admin_headers,
    admin_user,
    domain_expert_headers,
    domain_expert_user,
    editor_headers,
    editor_user,
    reviewer_headers,
    reviewer_user,
)
from tests.support.sqlite_compat import (  # noqa: E402,F401
    _ATTRIBUTION_VIEW_DDL,
    _create_attribution_view,
    _replace_jsonb,
    _safe_create_all,
    _strip_dangling_fks,
)


@pytest.fixture(autouse=True, scope="session")
def _disable_global_rate_limiting() -> None:
    """Strip all rate-limiting from the test app.

    Three layers exist:
    0. **slowapi Limiter** — ``@limiter.limit()`` decorators on auth endpoints
       (register/login).  ``limiter.enabled = False`` disables the entire
       slowapi engine; middleware removal alone leaves decorators active.
    1. **Middleware** — a global ``slowapi`` burst limiter (20/second).  Removed
       from ``app.user_middleware`` entirely; ``limiter.reset()`` alone is
       insufficient because the counter accumulates mid-test.
    2. **Per-endpoint dependencies** — ``InProcessRateLimiter`` instances bound
       via ``Depends()`` in ontology and md-verification routes.  Overridden
       with no-ops so the 429 gate never fires during the test suite.
    """
    from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware, limiter
    from nfm_db.services.rate_limit import (
        md_verification_rate_limit,
        ontology_rate_limit,
    )

    # 0. Disable slowapi limiter entirely (covers @limiter.limit() decorators
    #    on auth endpoints — the middleware removal alone is insufficient).
    limiter.enabled = False

    # 1. Remove global middleware.
    app.user_middleware = [mw for mw in app.user_middleware if mw.cls is not NFMRateLimitMiddleware]

    # 2. Override per-endpoint rate-limit dependencies with no-ops.
    async def _noop() -> None:  # pragma: no cover
        pass

    app.dependency_overrides[ontology_rate_limit] = _noop
    app.dependency_overrides[md_verification_rate_limit] = _noop

    # 3. Auto-authenticate as admin for all tests.
    #    Sprint 3 added ``require_editor`` / ``require_reviewer`` dependencies
    #    to many endpoints.  Overriding ``get_current_active_user`` at the
    #    FastAPI level lets legacy tests (that never set Authorization
    #    headers) pass without modification.  We override the *active-user*
    #    gate (not ``get_current_user``) because every role-check flows
    #    through it, and it needs no DB / token / request deps.
    #
    #    Two auth modules exist — ``api.v1.auth`` (Sprint 1-3 JWT+cookie)
    #    and ``core.auth`` (legacy).  md_verification and verification
    #    endpoints still import from ``core.auth``, so both must be
    #    overridden.
    from nfm_db.api.v1.auth import get_current_active_user as _api_get_active_user
    from nfm_db.core.auth import get_current_user as _core_get_user

    _auto_user = User(
        id=_SEED_AUTHOR_ID,
        username="auto_admin",
        email="auto_admin@test.com",
        hashed_password="hashed",
        blog_role=BlogRole.ADMIN,
        is_active=True,
    )

    async def _auto_active_user() -> User:
        return _auto_user

    app.dependency_overrides[_api_get_active_user] = _auto_active_user
    app.dependency_overrides[_core_get_user] = _auto_active_user


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "no_auto_auth: disable auto-auth override for tests that verify auth behavior"
    )


# Marker for tests that deliberately exercise the real auth chain
# (e.g. unauthenticated -> 401, wrong role -> 403).
no_auto_auth = pytest.mark.no_auto_auth


@pytest.fixture(autouse=True)
def _reenable_rate_limit_overrides(request) -> None:
    """Re-apply rate-limit no-op and auto-auth overrides before each test.

    Many existing test files on main call ``dependency_overrides.clear()``
    in their teardown, which wipes the session-scoped overrides above.
    This function-scoped guard re-applies the no-ops before every test
    so that stray 429s or 401s never leak into unrelated tests.

    Rate-limit-specific tests (e.g. ``test_ontology_rate_limit_headers``)
    deliberately set tighter overrides in their test bodies *after* this
    fixture runs, so their assertions are unaffected.

    Tests marked ``@pytest.mark.no_auto_auth`` deliberately exercise
    the real auth chain (e.g. 401 for missing credentials, 403 for wrong role).
    """
    from nfm_db.api.v1.auth import get_current_active_user as _api_get_active_user
    from nfm_db.core.auth import get_current_user as _core_get_user
    from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware, limiter
    from nfm_db.services.rate_limit import (
        md_verification_rate_limit,
        ontology_rate_limit,
    )

    # Re-disable slowapi limiter (may be re-enabled by teardown of prior tests).
    limiter.enabled = False

    # Re-strip rate-limit middleware if re-added by prior test teardown.
    app.user_middleware = [mw for mw in app.user_middleware if mw.cls is not NFMRateLimitMiddleware]

    async def _noop() -> None:  # pragma: no cover
        pass

    app.dependency_overrides[ontology_rate_limit] = _noop
    app.dependency_overrides[md_verification_rate_limit] = _noop

    if "no_auto_auth" not in request.keywords:
        # Re-apply auto-auth overrides (survives dependency_overrides.clear()).
        _auto_user = User(
            id=_SEED_AUTHOR_ID,
            username="auto_admin",
            email="auto_admin@test.com",
            hashed_password="hashed",
            blog_role=BlogRole.ADMIN,
            is_active=True,
        )

        async def _auto_active_user() -> User:
            return _auto_user

        app.dependency_overrides[_api_get_active_user] = _auto_active_user
        app.dependency_overrides[_core_get_user] = _auto_active_user
    else:
        # Clear the session-scoped auto-auth so the real auth chain runs.
        app.dependency_overrides.pop(_api_get_active_user, None)
        app.dependency_overrides.pop(_core_get_user, None)

    yield


@pytest.fixture(autouse=True)
def _clear_v2_flag_cache() -> AsyncIterator[None]:
    """Prevent EXTRACTION_PIPELINE_V2 lru_cache from leaking across tests.

    Tests that set ``NFM_EXTRACTION_V2_ENABLED=true`` (e.g.
    ``test_extraction_v2_flag_verification``) can poison the cache for
    subsequent V4 API tests that route through the dispatcher.

    Core-layer owned (NFM-5951 step 1): stays with the core layer in the
    step-4+ split (the two rate-limit / auto-auth fixtures above follow
    the full-app cluster instead).
    """
    try:
        from nfm_db.services.extraction_pipeline_dispatch import (
            is_extraction_v2_enabled,
        )

        is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
        yield
        is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        # NFM-3008: is_extraction_v2_enabled was removed.
        yield


# The ``db_session`` / ``admin_user`` parameters below intentionally shadow
# the re-exported fixture names (pytest resolves fixtures by parameter name).
@pytest.fixture
async def async_client(db_session: AsyncSession):  # noqa: F811
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
async def authenticated_client(db_session: AsyncSession, admin_user: User):  # noqa: F811
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
