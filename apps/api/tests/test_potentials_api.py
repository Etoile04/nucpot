"""Integration tests for /api/v1/potentials endpoints."""

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
