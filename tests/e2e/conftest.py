"""Shared fixtures for E2E integration tests.

Provides:
  * Real FastAPI app with in-memory SQLite (ASGI transport)
  * Deterministic hub node and resource node seeds
  * SyncEngine + OfflineQueue instances per node
  * PartitionSimulator for network partition scenarios
  * Structured test report collection (AC-7)

Usage::

    pytest tests/e2e/ -v --tb=short
    pytest tests/e2e/ -v --tb=short -k "plan_b"  # Plan B only
    pytest tests/e2e/ -v --tb=short -k "partition"  # Partition tests only
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure project packages are importable.
# conftest lives at tests/e2e/conftest.py → parent.parent.parent = repo root.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
_API_SRC = str(Path(__file__).resolve().parent.parent.parent / "apps" / "api" / "src")
_NODE_CLIENT_SRC = str(Path(__file__).resolve().parent.parent.parent / "apps" / "nfm-node-client" / "src")
for _path in (_REPO_ROOT, _API_SRC, _NODE_CLIENT_SRC):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from nfm_db.database import get_db  # noqa: E402
from nfm_db.main import app  # noqa: E402
from nfm_db.models import Base, HubNode  # noqa: E402
from nfm_node_client.offline_queue import (  # noqa: E402
    OfflineQueue,
    OperationType,
    PendingOperation,
)
from nfm_node_client.sync_engine import SyncEngine  # noqa: E402
from nfm_node_client.vector_clock import VectorClock  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic IDs
# ---------------------------------------------------------------------------

SEED_HUB_ID = uuid.UUID("b1000000-0000-0000-0000-000000000001")

SEED_RESOURCE_IDS = [
    uuid.UUID("b2000000-0000-0000-0000-000000000001"),
    uuid.UUID("b2000000-0000-0000-0000-000000000002"),
    uuid.UUID("b2000000-0000-0000-0000-000000000003"),
    uuid.UUID("b2000000-0000-0000-0000-000000000004"),
]

RESOURCE_NODE_NAMES = ["resource-alpha", "resource-beta", "resource-gamma", "resource-delta"]
RESOURCE_NODE_TYPES = ["computing", "storage", "observatory", "computing"]


# ---------------------------------------------------------------------------
# SQLite compatibility (mirrors apps/api/tests/conftest.py)
# ---------------------------------------------------------------------------


def _strip_dangling_fks(metadata) -> None:
    registered = set(metadata.tables.keys())
    for table in metadata.tables.values():
        for col in table.columns:
            dangling = [
                fk
                for fk in list(col.foreign_keys)
                if fk._colspec.split(".")[0].strip('"') not in registered
            ]
            for fk in dangling:
                col.foreign_keys.discard(fk)


def _replace_jsonb(metadata) -> None:
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
    from sqlalchemy import ARRAY as SA_ARRAY

    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
            if isinstance(col.type, (PG_ARRAY, SA_ARRAY)):
                col.type = JSON()


def _safe_create_all(sync_conn, metadata) -> None:
    _replace_jsonb(metadata)
    _strip_dangling_fks(metadata)
    metadata.create_all(sync_conn)


# ---------------------------------------------------------------------------
# Rate-limit / auth overrides
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _disable_rate_limiting_and_auto_auth() -> None:
    from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware, limiter
    from nfm_db.services.rate_limit import (
        md_verification_rate_limit,
        ontology_rate_limit,
    )
    from nfm_db.api.v1.auth import get_current_active_user as _api_get_active_user
    from nfm_db.core.auth import get_current_user as _core_get_user
    from nfm_db.models import User, BlogRole

    limiter.enabled = False
    app.user_middleware = [mw for mw in app.user_middleware if mw.cls is not NFMRateLimitMiddleware]

    async def _noop() -> None:
        pass

    app.dependency_overrides[ontology_rate_limit] = _noop
    app.dependency_overrides[md_verification_rate_limit] = _noop

    _auto_user = User(
        id=uuid.UUID("a0000000-0000-0000-0000-000000000001"),
        username="e2e_admin",
        email="e2e@test.com",
        hashed_password="hashed",
        blog_role=BlogRole.ADMIN,
        is_active=True,
    )

    async def _auto_active_user() -> User:
        return _auto_user

    app.dependency_overrides[_api_get_active_user] = _auto_active_user
    app.dependency_overrides[_core_get_user] = _auto_active_user


@pytest.fixture(autouse=True)
def _reenable_overrides() -> None:
    from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware, limiter
    from nfm_db.services.rate_limit import (
        md_verification_rate_limit,
        ontology_rate_limit,
    )
    from nfm_db.api.v1.auth import get_current_active_user as _api_get_active_user
    from nfm_db.core.auth import get_current_user as _core_get_user
    from nfm_db.models import User, BlogRole

    limiter.enabled = False
    app.user_middleware = [mw for mw in app.user_middleware if mw.cls is not NFMRateLimitMiddleware]

    async def _noop() -> None:
        pass

    app.dependency_overrides[ontology_rate_limit] = _noop
    app.dependency_overrides[md_verification_rate_limit] = _noop

    _auto_user = User(
        id=uuid.UUID("a0000000-0000-0000-0000-000000000001"),
        username="e2e_admin",
        email="e2e@test.com",
        hashed_password="hashed",
        blog_role=BlogRole.ADMIN,
        is_active=True,
    )

    async def _auto_active_user() -> User:
        return _auto_user

    app.dependency_overrides[_api_get_active_user] = _auto_active_user
    app.dependency_overrides[_core_get_user] = _auto_active_user

    yield


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite async session for E2E tests."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy import event

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
async def async_client(db_session):
    """Async HTTP client against the real FastAPI app."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def seed_hub_node(db_session):
    """Create a deterministic HubNode in the DB."""
    hub = HubNode(
        id=SEED_HUB_ID,
        name="e2e-hub",
        api_endpoint="http://hub:8000",
        status="active",
    )
    db_session.add(hub)
    await db_session.commit()
    await db_session.refresh(hub)
    return hub


@pytest.fixture
async def seed_resource_nodes(async_client, seed_hub_node):
    """Register 4 resource nodes via the hub API (AC-1 prerequisite)."""
    nodes = []
    for i in range(4):
        resp = await async_client.post(
            "/api/v1/hub/nodes/register",
            json={
                "hub_node_id": str(SEED_HUB_ID),
                "name": RESOURCE_NODE_NAMES[i],
                "node_type": RESOURCE_NODE_TYPES[i],
                "api_endpoint": f"http://resource-{i+1}:8000",
            },
        )
        assert resp.status_code == 201, f"Failed to register node {i}: {resp.text}"
        nodes.append(resp.json()["data"])
    return nodes


@pytest.fixture
def tmp_dir():
    """Temp directory for per-node SQLite databases, cleaned up after test."""
    with tempfile.TemporaryDirectory(prefix="e2e_nodes_") as d:
        yield Path(d)


@pytest.fixture
def offline_queues(tmp_dir):
    """Create 4 OfflineQueue instances (one per resource node)."""
    queues = []
    for i in range(4):
        db_path = str(tmp_dir / f"node_{i+1}.db")
        queues.append(OfflineQueue(db_path=db_path))
    yield queues
    for q in queues:
        q.close()


@pytest.fixture
def sync_engines(offline_queues):
    """Create 4 SyncEngine instances (one per resource node)."""
    engines = []
    for i in range(4):
        engine = SyncEngine(
            queue=offline_queues[i],
            node_id=str(SEED_RESOURCE_IDS[i]),
            hub_url="http://hub:8000",
            watermark=0,
            auto_resolve=True,
        )
        engines.append(engine)
    yield engines
    for e in engines:
        e.close()


# ---------------------------------------------------------------------------
# Plan B fixtures (1 hub + 2 resource nodes)
# ---------------------------------------------------------------------------

PLAN_B_RESOURCE_IDS = [
    uuid.UUID("b3000000-0000-0000-0000-000000000001"),
    uuid.UUID("b3000000-0000-0000-0000-000000000002"),
]

PLAN_B_NODE_NAMES = ["plan-b-alpha", "plan-b-beta"]
PLAN_B_NODE_TYPES = ["computing", "storage"]


@pytest.fixture
async def seed_plan_b_nodes(async_client, seed_hub_node):
    """Register 2 resource nodes for Plan B (AC-6)."""
    nodes = []
    for i in range(2):
        resp = await async_client.post(
            "/api/v1/hub/nodes/register",
            json={
                "hub_node_id": str(SEED_HUB_ID),
                "name": PLAN_B_NODE_NAMES[i],
                "node_type": PLAN_B_NODE_TYPES[i],
                "api_endpoint": f"http://plan-b-{i+1}:8000",
            },
        )
        assert resp.status_code == 201, f"Failed to register plan-b node {i}: {resp.text}"
        nodes.append(resp.json()["data"])
    return nodes


@pytest.fixture
def plan_b_offline_queues(tmp_dir):
    """Create 2 OfflineQueue instances for Plan B."""
    queues = []
    for i in range(2):
        db_path = str(tmp_dir / f"plan_b_node_{i+1}.db")
        queues.append(OfflineQueue(db_path=db_path))
    yield queues
    for q in queues:
        q.close()


@pytest.fixture
def plan_b_sync_engines(plan_b_offline_queues):
    """Create 2 SyncEngine instances for Plan B."""
    engines = []
    for i in range(2):
        engine = SyncEngine(
            queue=plan_b_offline_queues[i],
            node_id=str(PLAN_B_RESOURCE_IDS[i]),
            hub_url="http://hub:8000",
            watermark=0,
            auto_resolve=True,
        )
        engines.append(engine)
    yield engines
    for e in engines:
        e.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_remote_record(
    *,
    entity_id: str,
    source_node: str,
    counter: int,
    timestamp: float = 0.0,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a synthetic remote record with a vector clock."""
    vc = VectorClock(
        node_id=source_node,
        clocks={source_node: counter},
        timestamp=timestamp,
    )
    return {
        "entity_id": entity_id,
        "source_node": source_node,
        "vector_clock": vc.to_dict(),
        "updated_at": timestamp,
        **(data or {}),
    }


def make_local_operation(
    *,
    op_type: OperationType,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
) -> PendingOperation:
    """Create a PendingOperation for offline queue testing."""
    return PendingOperation(
        op_type=op_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
        priority=priority,
    )


# ---------------------------------------------------------------------------
# Structured test report (AC-7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioReport:
    """Structured report for a single E2E test scenario."""

    scenario: str
    acceptance_criteria: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "acceptance_criteria": self.acceptance_criteria,
            "passed": self.passed,
            "details": self.details,
            "error": self.error,
        }


@pytest.fixture
def report_collector():
    """Collect structured reports across test scenarios (AC-7)."""
    reports: list[TestScenarioReport] = []

    class ReportCollector:
        def add_report(self, report: TestScenarioReport) -> None:
            reports.append(report)

        def dump_json(self, output_dir: str | Path | None = None) -> str:
            data = {
                "total": len(reports),
                "passed": sum(1 for r in reports if r.passed),
                "failed": sum(1 for r in reports if not r.passed),
                "scenarios": [r.to_dict() for r in reports],
            }
            json_str = json.dumps(data, indent=2, ensure_ascii=False)
            if output_dir:
                output_path = Path(output_dir) / "e2e_report.json"
                output_path.write_text(json_str, encoding="utf-8")
            return json_str

    yield ReportCollector()


@pytest.fixture(scope="session")
def report_output_dir():
    """Directory for E2E test reports."""
    path = Path(_REPO_ROOT) / ".e2e-reports"
    path.mkdir(exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Register custom pytest markers
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """Register E2E-specific markers."""
    for marker in ("e2e", "plan_b", "ac1", "ac2", "ac3", "ac4", "ac5", "ac6", "ac7"):
        config.addinivalue_line("markers", f"{marker}: NFM-2029 acceptance criteria marker")
