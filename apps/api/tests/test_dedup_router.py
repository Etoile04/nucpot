"""End-to-end smoke tests for the dedup router (NFM-1391).

Uses FastAPI's ``TestClient`` with an in-memory SQLite database so the
full request/response cycle (Pydantic validation + DB round-trip +
response envelopes) is exercised without external dependencies.

The fixture swaps in a fresh SQLite-backed async engine and exposes
``app.state._sync_session`` so tests can seed rows synchronously
without juggling an event loop.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker

from nfm_db.main import app
from nfm_db.models import Base
from nfm_db.models.material import Material, MaterialCategory


def _replace_jsonb(metadata) -> None:
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient + a shared file-backed SQLite DB.

    Uses a temp file (not :memory:) so a sync engine (for seeding
    fixtures) and an async engine (for the FastAPI dependency) point
    at the same database.  Tests seed via the sync engine exposed on
    ``app.state._sync_session``.
    """
    import nfm_db.database as db_module

    _replace_jsonb(Base.metadata)

    db_file = Path(tempfile.mkstemp(suffix=".sqlite3")[1])
    sync_url = f"sqlite:///{db_file}"
    async_url = f"sqlite+aiosqlite:///{db_file}"

    sync_engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    async_engine = create_async_engine(async_url, echo=False)

    @event.listens_for(sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        dbapi_conn.commit()

    Base.metadata.create_all(sync_engine)
    SyncSession = sessionmaker(sync_engine, expire_on_commit=False)
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    db_module.get_session_factory = lambda: factory
    app.dependency_overrides[db_module.get_db] = _override_get_db
    app.state._sync_session = SyncSession  # type: ignore[attr-defined]

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    db_file.unlink(missing_ok=True)


def _seed_category(client: TestClient, name: str = "Fuel") -> str:
    SyncSession: sessionmaker = client.app.state._sync_session  # type: ignore[attr-defined]
    with SyncSession() as s:
        cat = MaterialCategory(
            name=name, slug=name.lower().replace(" ", "-")
        )
        s.add(cat)
        s.commit()
        return str(cat.id)


def _seed_material(
    client: TestClient, name: str, formula: str, category_id: str
) -> str:
    SyncSession: sessionmaker = client.app.state._sync_session  # type: ignore[attr-defined]
    with SyncSession() as s:
        m = Material(
            name=name,
            formula=formula,
            category_id=uuid.UUID(category_id),
        )
        s.add(m)
        s.commit()
        return str(m.id)


class TestDedupRouter:
    def test_duplicates_finds_exact_formula_pair(self, client: TestClient) -> None:
        cat_id = _seed_category(client)
        _seed_material(client, "UO2 primary", "UO2", cat_id)
        _seed_material(client, "UO2 duplicate", "uo2", cat_id)

        resp = client.get("/api/v1/dedup/duplicates")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["success"] is True
        items = payload["data"]
        assert len(items) == 1
        assert items[0]["match_method"] == "exact"
        assert items[0]["match_score"] == 1.0

    def test_merge_records_audit_log(self, client: TestClient) -> None:
        cat_id = _seed_category(client)
        canonical_id = _seed_material(client, "UO2 canonical", "UO2", cat_id)
        duplicate_id = _seed_material(client, "UO2 dup", "UO2", cat_id)

        resp = client.post(
            "/api/v1/dedup/merge",
            json={
                "canonical_id": canonical_id,
                "duplicate_id": duplicate_id,
                "match_score": 1.0,
                "match_method": "exact",
                "matched_aliases": ["UO2 dup alias"],
            },
        )
        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["success"] is True
        log = payload["data"]
        assert log["canonical_id"] == canonical_id
        assert log["merged_id"] == duplicate_id
        assert log["match_method"] == "exact"

        list_resp = client.get("/api/v1/dedup/logs")
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["id"] == log["id"]

    def test_merge_404_for_missing_material(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/dedup/merge",
            json={
                "canonical_id": str(uuid.uuid4()),
                "duplicate_id": str(uuid.uuid4()),
                "match_score": 1.0,
                "match_method": "exact",
            },
        )
        assert resp.status_code == 404

    def test_get_log_by_id(self, client: TestClient) -> None:
        cat_id = _seed_category(client)
        canonical_id = _seed_material(client, "A", "AB", cat_id)
        duplicate_id = _seed_material(client, "A dup", "AB", cat_id)

        merge_resp = client.post(
            "/api/v1/dedup/merge",
            json={
                "canonical_id": canonical_id,
                "duplicate_id": duplicate_id,
                "match_score": 0.9,
                "match_method": "fuzzy",
            },
        )
        log_id = merge_resp.json()["data"]["id"]

        resp = client.get(f"/api/v1/dedup/logs/{log_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == log_id

    def test_get_log_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/dedup/logs/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_logs_pagination(self, client: TestClient) -> None:
        cat_id = _seed_category(client)
        for i in range(3):
            a = _seed_material(client, f"A{i}", "X", cat_id)
            b = _seed_material(client, f"A{i} dup", "X", cat_id)
            client.post(
                "/api/v1/dedup/merge",
                json={
                    "canonical_id": a,
                    "duplicate_id": b,
                    "match_score": 1.0,
                    "match_method": "exact",
                },
            )

        resp = client.get("/api/v1/dedup/logs?page=1&per_page=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 3
        assert data["pages"] == 2
        assert len(data["items"]) == 2
