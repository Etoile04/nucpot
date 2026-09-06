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
    assert payload["verification_status"] == "unverified"


@pytest.mark.asyncio
async def test_detail_endpoint_404_for_missing(async_client) -> None:
    import uuid

    response = await async_client.get(f"/api/v1/potentials/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_verification_sets_status(async_client, db_session) -> None:
    p = await _seed(db_session, name="patch-me")
    response = await async_client.patch(
        f"/api/v1/potentials/{p.id}/verification",
        json={"verification_status": "pending"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["verification_status"] == "pending"


@pytest.mark.asyncio
async def test_patch_verification_404_for_missing(async_client) -> None:
    import uuid

    response = await async_client.patch(
        f"/api/v1/potentials/{uuid.uuid4()}/verification",
        json={"verification_status": "pending"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_verification_rejects_invalid_status(async_client, db_session) -> None:
    p = await _seed(db_session, name="reject-me")
    response = await async_client.patch(
        f"/api/v1/potentials/{p.id}/verification",
        json={"verification_status": "bogus"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_verification_accepts_message_and_evidence(async_client, db_session) -> None:
    p = await _seed(db_session, name="ev-me")
    payload = {
        "verification_status": "verified",
        "message": "all clear",
        "evidence_url": "https://example.org/ev",
    }
    response = await async_client.patch(f"/api/v1/potentials/{p.id}/verification", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["verification_status"] == "verified"
    assert data["data"]["extra"]["verification_message"] == "all clear"
    assert data["data"]["extra"]["verification_evidence_url"] == "https://example.org/ev"


# ---------------------------------------------------------------------------
# NFM-4343 F3 — references widening (bare-string & dict+string mix).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_endpoint_handles_bare_string_references(async_client, db_session) -> None:
    """F3 / NFM-4343 — three Hunan University potentials store references
    as bare citation strings rather than the canonical dict list. Before
    this fix, the FastAPI detail endpoint 500'd on those rows because
    PotentialDetail typed the field as list[dict]. The BFF retarget
    (PR #1184) routed the FE at FastAPI, so without this widening those
    three detail pages regress from working to 500.

    This pins the end-to-end behavior: seed a row with a bare-string
    references list, fetch via /api/v1/potentials/{id}, expect HTTP 200
    with the bare string preserved in the response payload.
    """
    bare_refs = [
        "J. Nucl. Mater. 541 (2020) 152421",
        "Phys. Rev. B 102 (2020) 014101",
    ]
    p = await _seed(
        db_session,
        name="EAM_Fe_Hnu_2020",
        references=bare_refs,
    )
    response = await async_client.get(f"/api/v1/potentials/{p.id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["data"]["references"] == bare_refs


@pytest.mark.asyncio
async def test_detail_endpoint_handles_mixed_references(async_client, db_session) -> None:
    """Dict + bare-string mix — legacy rows migrated one entry at a time
    must still serialize cleanly."""
    mixed = [
        {"doi": "10.1234/canonical", "citation": "Canonical ref"},
        "J. Nucl. Mater. 541 (2020) 152421",
    ]
    p = await _seed(
        db_session,
        name="EAM_Fe_Hnu_Mixed",
        references=mixed,
    )
    response = await async_client.get(f"/api/v1/potentials/{p.id}")
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["references"] == mixed


# ---------------------------------------------------------------------------
# NFM-4311 — advanced filter query params (contract parity with the legacy
# Supabase BFF route) and comma-separated type filter.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_endpoint_comma_separated_type(async_client, db_session) -> None:
    await _seed(db_session, name="eam1", type="EAM")
    await _seed(db_session, name="meam1", type="MEAM")
    await _seed(db_session, name="mtp1", type="MTP")
    response = await async_client.get("/api/v1/potentials?type=EAM,MEAM")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert {p["type"] for p in payload["potentials"]} == {"EAM", "MEAM"}


@pytest.mark.asyncio
async def test_list_endpoint_advanced_filters(async_client, db_session) -> None:
    await _seed(
        db_session,
        name="match",
        extra={"irradiationRelevant": True, "validationLevel": "production"},
        applicability={"temperatureRange": [300, 2500]},
    )
    await _seed(
        db_session,
        name="too_cold",
        extra={"irradiationRelevant": True, "validationLevel": "production"},
        applicability={"temperatureRange": [300, 900]},
    )
    await _seed(db_session, name="not_irrad", extra={"irradiationRelevant": False})

    response = await async_client.get(
        "/api/v1/potentials?irradiation=true&validationLevel=production&tempMin=2000"
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert [p["name"] for p in payload["potentials"]] == ["match"]
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_list_endpoint_has_defect_and_has_liquid_params(
    async_client, db_session
) -> None:
    await _seed(db_session, name="defect", extra={"hasDefectData": True})
    await _seed(db_session, name="liquid", extra={"hasLiquidPhase": True})
    response = await async_client.get("/api/v1/potentials?hasDefect=true&hasLiquid=true")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["total"] == 0


@pytest.mark.asyncio
async def test_list_endpoint_temp_max_param(async_client, db_session) -> None:
    await _seed(db_session, name="cold", applicability={"temperatureRange": [100, 500]})
    await _seed(db_session, name="hot", applicability={"temperatureRange": [1500, 3000]})
    response = await async_client.get("/api/v1/potentials?tempMax=600")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert [p["name"] for p in payload["potentials"]] == ["cold"]


@pytest.mark.asyncio
async def test_list_endpoint_validation_level_all_returns_everything(
    async_client, db_session
) -> None:
    await _seed(db_session, name="prod", extra={"validationLevel": "production"})
    await _seed(db_session, name="unlevelled")
    response = await async_client.get("/api/v1/potentials?validationLevel=all")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["total"] == 2
    assert {p["name"] for p in payload["potentials"]} == {"prod", "unlevelled"}
