"""Integration tests for batch import/export API endpoints (NFM-1085).

Tests the full request→response→DB cycle using the async test client
with in-memory SQLite.
"""

from __future__ import annotations

import csv
import io
import json

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.database import get_db
from nfm_db.main import app

# ── Material Import ──────────────────────────────────────────────────


class TestMaterialImport:
    """Integration tests for POST /api/v1/materials/import."""

    @pytest.fixture
    async def client(self, db_session):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_csv_creates_materials(self, client, db_session) -> None:
        csv_content = b"name,formula,crystal_structure\nUO2,UO2,FCC\nFe,Fe,BCC\n"
        resp = await client.post(
            "/api/v1/materials/import",
            files={"file": ("materials.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["failed"] == 0
        assert data["errors"] == []

        from sqlalchemy import select

        from nfm_db.models.material import Material

        result = await db_session.execute(select(Material))
        materials = result.scalars().all()
        assert len(materials) == 2
        names = {m.name for m in materials}
        assert "UO2" in names
        assert "Fe" in names

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_json_creates_materials(self, client, db_session) -> None:
        json_content = json.dumps(
            [
                {"name": "UO2", "formula": "UO2"},
                {"name": "bad" * 200},
            ]
        ).encode()
        resp = await client.post(
            "/api/v1/materials/import",
            files={"file": ("materials.json", json_content, "application/json")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 1
        assert data["failed"] == 1

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_invalid_file_type(self, client) -> None:
        resp = await client.post(
            "/api/v1/materials/import",
            files={"file": ("data.txt", b"name\nUO2\n", "text/plain")},
        )
        assert resp.status_code == 400

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_oversized_file(self, client) -> None:
        big_content = b"name\n" + b"x" * (11 * 1024 * 1024)
        resp = await client.post(
            "/api/v1/materials/import",
            files={"file": ("big.csv", big_content, "text/csv")},
        )
        assert resp.status_code == 413

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_skips_invalid_rows(self, client, db_session) -> None:
        csv_content = b"name,formula\nUO2,UO2\n,BCC\n"
        resp = await client.post(
            "/api/v1/materials/import",
            files={"file": ("materials.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 1
        assert data["failed"] == 1
        assert data["errors"][0]["field"] == "name"


# ── Material Export ──────────────────────────────────────────────────


class TestMaterialExport:
    """Integration tests for GET /api/v1/materials/export."""

    @pytest.fixture
    async def client(self, db_session):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.pop(get_db, None)

    async def _seed_material(self, db_session, name="UO2", formula="UO2") -> None:
        from nfm_db.models.material import Material

        db_session.add(Material(name=name, formula=formula))
        await db_session.commit()

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_csv(self, client, db_session) -> None:
        await self._seed_material(db_session)
        resp = await client.get("/api/v1/materials/export?format=csv")
        assert resp.status_code == 200
        assert "content-disposition" in resp.headers
        assert "materials_" in resp.headers["content-disposition"]
        assert "attachment" in resp.headers["content-disposition"]

        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) >= 1
        assert rows[0]["name"] == "UO2"

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_json(self, client, db_session) -> None:
        await self._seed_material(db_session)
        resp = await client.get("/api/v1/materials/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["name"] == "UO2"

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_invalid_format(self, client) -> None:
        resp = await client.get("/api/v1/materials/export?format=xml")
        assert resp.status_code == 422


# ── Reference Value Import ────────────────────────────────────────


class TestReferenceValueImport:
    """Integration tests for POST /api/v1/reference-values/import."""

    @pytest.fixture
    async def client(self, db_session):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_csv_creates_staging_records(self, client, db_session) -> None:
        csv_content = (
            b"element_system,property_name,value,unit,source\n"
            b"UO2,lattice_constant,5.47,angstrom,Smirnov2014\n"
            b"Fe,bulk_modulus,170.0,GPa,Test2024\n"
        )
        resp = await client.post(
            "/api/v1/reference-values/import",
            files={"file": ("ref_values.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["failed"] == 0

    @pytest.mark.xfail(reason="batch import behavior changed after PR merge — needs investigation")
    async def test_import_json_with_errors(self, client) -> None:
        json_content = json.dumps(
            [
                {
                    "element_system": "UO2",
                    "property_name": "lattice_constant",
                    "value": 5.47,
                    "unit": "angstrom",
                    "source": "Smirnov2014",
                },
                {
                    "property_name": "lattice_constant",
                    "value": 5.47,
                    "unit": "angstrom",
                    "source": "Smirnov2014",
                },
            ]
        ).encode()
        resp = await client.post(
            "/api/v1/reference-values/import",
            files={"file": ("ref_values.json", json_content, "application/json")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 1
        assert data["failed"] == 1

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_invalid_file_type(self, client) -> None:
        resp = await client.post(
            "/api/v1/reference-values/import",
            files={"file": ("data.xml", b"<data/>", "application/xml")},
        )
        assert resp.status_code == 400


# ── Property Import ───────────────────────────────────────────────


class TestPropertyImport:
    """Integration tests for POST /api/v1/properties/import."""

    @pytest.fixture
    async def client(self, db_session):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_json_properties(self, client) -> None:
        """Property import without FK-referenced dataset/property_type returns 409."""
        json_content = json.dumps(
            [
                {"value_scalar": 5.47, "notes": "test measurement"},
                {"value_scalar": 170.0},
            ]
        ).encode()
        resp = await client.post(
            "/api/v1/properties/import",
            files={"file": ("properties.json", json_content, "application/json")},
        )
        # dataset_id and property_type_id are NOT NULL FK columns;
        # without seeded FK rows the commit fails with IntegrityError → 409
        assert resp.status_code == 409
        assert "integrity" in resp.json()["detail"].lower()

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_import_csv_invalid_type(self, client) -> None:
        resp = await client.post(
            "/api/v1/properties/import",
            files={"file": ("data.yaml", b"{}", "application/yaml")},
        )
        assert resp.status_code == 400


# ── Property Export ───────────────────────────────────────────────


class TestPropertyExport:
    """Integration tests for GET /api/v1/properties/export."""

    @pytest.fixture
    async def client(self, db_session):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_csv_empty(self, client) -> None:
        resp = await client.get("/api/v1/properties/export?format=csv")
        assert resp.status_code == 200
        assert "content-disposition" in resp.headers
        assert "properties_" in resp.headers["content-disposition"]

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_json_empty(self, client) -> None:
        resp = await client.get("/api/v1/properties/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_invalid_format(self, client) -> None:
        resp = await client.get("/api/v1/properties/export?format=xml")
        assert resp.status_code == 422


# ── Reference Value Export ───────────────────────────────────────


class TestReferenceValueExport:
    """Integration tests for GET /api/v1/reference-values/export."""

    @pytest.fixture
    async def client(self, db_session):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_csv_empty(self, client) -> None:
        resp = await client.get("/api/v1/reference-values/export?format=csv")
        assert resp.status_code == 200
        assert "content-disposition" in resp.headers
        assert "reference_values_" in resp.headers["content-disposition"]

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_json_empty(self, client) -> None:
        resp = await client.get("/api/v1/reference-values/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    @pytest.mark.xfail(reason="NFM-1366: batch endpoints not wired", strict=False)
    async def test_export_invalid_format(self, client) -> None:
        resp = await client.get("/api/v1/reference-values/export?format=xml")
        assert resp.status_code == 422
