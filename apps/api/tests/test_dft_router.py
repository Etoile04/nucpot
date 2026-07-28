"""End-to-end smoke tests for the DFT router (NFM-1678).

Covers the list/get/stats endpoints and the import path (which writes
real rows via ``dft_import.run_import``).
"""

from __future__ import annotations

import json
import tempfile
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

from nfm_db.main import app
from nfm_db.models import Base


def _replace_jsonb(metadata) -> None:
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient + shared file-backed SQLite."""
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

    db_module.engine = async_engine
    db_module.async_session_factory = factory
    app.dependency_overrides[db_module.get_db] = _override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    db_file.unlink(missing_ok=True)


def _sample_records() -> list[dict]:
    return [
        {
            "composition": {"U": 0.5, "Mo": 0.5},
            "functional": "PBE",
            "cutoff_energy": 520.0,
            "kpoints": "8x8x8",
            "formation_energy": -0.5,
            "binding_energy": -4.2,
            "lattice_distortion": 0.01,
        },
        {
            "composition": {"Zr": 0.5, "Nb": 0.5},
            "functional": "PBE",
            "cutoff_energy": 510.0,
            "kpoints": "8x8x8",
            "formation_energy": -0.6,
            "binding_energy": -4.3,
            "lattice_distortion": 0.02,
        },
    ]


class TestDFTRouter:
    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/dft/calculations")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is True
        assert payload["data"]["total"] == 0
        assert payload["data"]["items"] == []

    def test_import_json_writes_rows(self, client: TestClient) -> None:
        json_bytes = json.dumps(_sample_records()).encode()
        resp = client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "test-source"},
            files={"file": ("dft.json", json_bytes, "application/json")},
        )
        assert resp.status_code == 201, resp.text
        report = resp.json()["data"]
        assert report["inserted"] == 2
        assert report["skipped"] == 0
        assert report["failed"] == 0
        assert report["source"] == "test-source"

    def test_import_csv_writes_rows(self, client: TestClient) -> None:
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "composition",
                "functional",
                "cutoff_energy",
                "kpoints",
                "formation_energy",
                "binding_energy",
                "lattice_distortion",
            ]
        )
        for r in _sample_records():
            writer.writerow(
                [
                    json.dumps(r["composition"]),
                    r["functional"],
                    r["cutoff_energy"],
                    r["kpoints"],
                    r["formation_energy"],
                    r["binding_energy"],
                    r["lattice_distortion"],
                ]
            )
        csv_bytes = buf.getvalue().encode()

        resp = client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "csv-source"},
            files={"file": ("dft.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 201, resp.text
        report = resp.json()["data"]
        assert report["inserted"] == 2

    def test_list_after_import_returns_rows(self, client: TestClient) -> None:
        json_bytes = json.dumps(_sample_records()).encode()
        client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "list-source"},
            files={"file": ("dft.json", json_bytes, "application/json")},
        )

        resp = client.get("/api/v1/dft/calculations?source=list-source")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 2
        # Filter by functional -> still both
        resp2 = client.get("/api/v1/dft/calculations?functional=PBE")
        assert resp2.json()["data"]["total"] == 2

    def test_get_by_calc_id(self, client: TestClient) -> None:
        json_bytes = json.dumps(_sample_records()).encode()
        client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "by-calc-source"},
            files={"file": ("dft.json", json_bytes, "application/json")},
        )

        # Fetch one via the listing endpoint to discover its calc_id.
        listing = client.get("/api/v1/dft/calculations?source=by-calc-source")
        calc_id = listing.json()["data"]["items"][0]["calculation_id"]

        resp = client.get(f"/api/v1/dft/calculations/{calc_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["calculation_id"] == calc_id

    def test_get_by_calc_id_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/dft/calculations/DFT-nonexistent")
        assert resp.status_code == 404

    def test_stats(self, client: TestClient) -> None:
        json_bytes = json.dumps(_sample_records()).encode()
        client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "stats-source"},
            files={"file": ("dft.json", json_bytes, "application/json")},
        )

        resp = client.get("/api/v1/dft/stats")
        assert resp.status_code == 200
        stats = resp.json()["data"]
        assert stats["total"] == 2
        # by_source should contain our source
        source_keys = {b["key"] for b in stats["by_source"]}
        assert "stats-source" in source_keys

    def test_import_rejects_unknown_extension(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "bad"},
            files={"file": ("dft.txt", b"garbage", "text/plain")},
        )
        assert resp.status_code == 400

    def test_import_rejects_empty_file(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/dft/calculations/import",
            params={"source": "empty"},
            files={"file": ("dft.json", b"", "application/json")},
        )
        assert resp.status_code == 400
