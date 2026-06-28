"""Integration tests for /api/v1/potentials endpoints."""

import io

import pytest

from nfm_db.models import Potential


async def _seed(db_session, **overrides):
    defaults = dict(
        name="EAM_U_Zhou_2004",
        type="EAM",
        elements=["U"],
        status="published",
        lammps_config={},
        applicability={},
        description="EAM for uranium",
    )
    defaults.update(overrides)
    p = Potential(**defaults)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.mark.asyncio
async def test_list_endpoint_returns_paginated(async_client, db_session) -> None:
    await _seed(db_session)
    response = await async_client.get("/api/v1/potentials")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    payload = data["data"]
    assert "potentials" in payload
    assert payload["page"] == 1


@pytest.mark.asyncio
async def test_list_endpoint_with_type_filter(async_client, db_session) -> None:
    await _seed(db_session, name="eam1", type="EAM")
    await _seed(db_session, name="mtp1", type="MTP")
    response = await async_client.get("/api/v1/potentials?type=EAM")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    payload = data["data"]
    assert all(p["type"] == "EAM" for p in payload["potentials"])


@pytest.mark.asyncio
async def test_detail_endpoint_returns_full_record(async_client, db_session) -> None:
    p = await _seed(db_session, name="detail1", verified_props={"lattice": 3.5})
    response = await async_client.get(f"/api/v1/potentials/{p.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    payload = data["data"]
    assert payload["name"] == "detail1"
    assert payload["verified_props"] == {"lattice": 3.5}


@pytest.mark.asyncio
async def test_detail_endpoint_404_for_missing(async_client) -> None:
    import uuid

    response = await async_client.get(f"/api/v1/potentials/{uuid.uuid4()}")
    assert response.status_code == 404


# ── write path (NFM-299) ──────────────────────────────────────────────────────


def _valid_metadata(overrides=None):
    data = {
        "name": "Test Potential 001",
        "type": "EAM",
        "elements": ["U", "Mo"],
        "system_name": "U-Mo test",
        "description": "A test potential for upload",
        "license_type": "own_work",
    }
    if overrides:
        data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_create_potential_valid(async_client) -> None:
    payload = _valid_metadata()
    response = await async_client.post("/api/v1/potentials", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["success"] is True
    returned = data["data"]
    assert returned["name"] == "Test Potential 001"


@pytest.mark.asyncio
async def test_create_potential_missing_required(async_client) -> None:
    response = await async_client.post("/api/v1/potentials", json={})
    # Pydantic 422 for missing required fields
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_create_potential_empty_elements(async_client) -> None:
    payload = _valid_metadata({"elements": []})
    response = await async_client.post("/api/v1/potentials", json=payload)
    assert response.status_code == 400, response.text
    assert "elements" in response.json()["error"].lower()


@pytest.mark.asyncio
async def test_create_potential_invalid_license_type(async_client) -> None:
    payload = _valid_metadata({"license_type": "invalid_license"})
    response = await async_client.post("/api/v1/potentials", json=payload)
    assert response.status_code == 400, response.text
    data = response.json()
    assert "license_type" in data["error"].lower() or "own_work" in data["error"]


@pytest.mark.asyncio
async def test_create_potential_author_permission_needs_proof(async_client) -> None:
    payload = _valid_metadata({"license_type": "author_permission"})
    response = await async_client.post("/api/v1/potentials", json=payload)
    assert response.status_code == 400, response.text
    data = response.json()
    assert "proof" in data["error"].lower() or "auth" in data["error"].lower()


@pytest.mark.asyncio
async def test_create_potential_open_license_needs_detail(async_client) -> None:
    payload = _valid_metadata({"license_type": "open_license"})
    response = await async_client.post("/api/v1/potentials", json=payload)
    assert response.status_code == 400, response.text
    data = response.json()
    assert "license name" in data["error"].lower() or "open_license" in data["error"].lower()


@pytest.mark.asyncio
async def test_create_potential_duplicate_name(async_client, db_session) -> None:
    await _seed(db_session, name="duplicate_name")
    payload = _valid_metadata({"name": "duplicate_name"})
    response = await async_client.post("/api/v1/potentials", json=payload)
    assert response.status_code == 409, response.text
    data = response.json()
    assert "already exists" in data["error"].lower() or "duplicate" in data["error"].lower()


# ── file upload ───────────────────────────────────────────────────────────────


@pytest.fixture
async def upload_test_potential(db_session):
    """Create a real potential row for file-attach tests."""
    return await _seed(db_session, name="file-target", status="published")


@pytest.fixture
def upload_dir_override(tmp_path, monkeypatch):
    """Override upload dir dependency to use tmp_path."""
    import nfm_db.services.upload_service as mod

    mod._UPLOAD_DIR_OVERRIDE = tmp_path
    yield tmp_path
    mod._UPLOAD_DIR_OVERRIDE = None


@pytest.mark.asyncio
async def test_upload_file_valid(
    async_client, db_session, upload_test_potential, upload_dir_override
) -> None:
    import hashlib

    pid = str(upload_test_potential.id)
    content = b"LAMMPS potential data\n"
    response = await async_client.post(
        f"/api/v1/potentials/{pid}/file",
        files={"file": ("test.eam.alloy", io.BytesIO(content))},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    result = data["data"]
    assert result["file_name"] == "test.eam.alloy"
    assert result["file_size"] == len(content)
    assert result["file_hash"] == hashlib.sha256(content).hexdigest()
    assert result["file_url"].startswith("/uploads/")

    # Verify file written to disk
    url_path = result["file_url"]
    rel = url_path.lstrip("/")  # uploads/{pid}/{name}
    written = upload_dir_override / "/".join(rel.split("/")[1:])
    assert written.exists()
    assert written.read_bytes() == content

    # Verify DB row updated
    from sqlalchemy import select

    from nfm_db.models import Potential

    stmt = select(Potential).where(Potential.id == upload_test_potential.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.file_url == result["file_url"]
    assert row.file_size == len(content)
    assert row.file_hash == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_upload_file_missing(async_client, upload_test_potential) -> None:
    pid = str(upload_test_potential.id)
    response = await async_client.post(f"/api/v1/potentials/{pid}/file", data={})
    assert response.status_code in (400, 422), response.text


@pytest.mark.asyncio
async def test_upload_file_bad_extension(
    async_client, upload_test_potential, upload_dir_override
) -> None:
    pid = str(upload_test_potential.id)
    response = await async_client.post(
        f"/api/v1/potentials/{pid}/file",
        files={"file": ("bad.exe", io.BytesIO(b"malicious"))},
    )
    assert response.status_code == 400, response.text
    data = response.json()
    error_msg = data["error"].lower()
    assert "extension" in error_msg or "unsupported" in error_msg or "acceptable" in error_msg


@pytest.mark.asyncio
async def test_upload_file_too_large(
    async_client, upload_test_potential, upload_dir_override, monkeypatch
) -> None:
    monkeypatch.setattr("nfm_db.services.upload_service.MAX_FILE_SIZE", 100)
    pid = str(upload_test_potential.id)
    big = b"x" * 200
    response = await async_client.post(
        f"/api/v1/potentials/{pid}/file",
        files={"file": ("big.setfl", io.BytesIO(big))},
    )
    assert response.status_code == 400, response.text
    data = response.json()
    assert "large" in data["error"].lower() or "size" in data["error"].lower()


@pytest.mark.asyncio
async def test_upload_file_nonexistent_potential(async_client, upload_dir_override) -> None:
    import uuid

    fake_id = str(uuid.uuid4())
    response = await async_client.post(
        f"/api/v1/potentials/{fake_id}/file",
        files={"file": ("test.eam.alloy", io.BytesIO(b"data"))},
    )
    assert response.status_code == 404, response.text
