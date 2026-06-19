"""Integration tests for /api/v1/potentials endpoints.

API tests patch ``build_composite_provider`` → local-only so the
HTTP-level tests are not affected by the OpenKIM mock transport.
"""

from unittest.mock import patch

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


@pytest.fixture(autouse=True)
def _local_only_async_client(async_client, db_session):
    from nfm_db.services.providers.local import LocalPotentialProvider

    with patch(
        "nfm_db.services.potential_service.build_composite_provider",
        return_value=LocalPotentialProvider(db_session),
    ):
        yield


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


# ── NFM-296 Task 8: dual-provider consistency (AC#1, #2, #3) ────────


@pytest.mark.asyncio
async def test_summary_provider_field_present(async_client, db_session) -> None:
    """AC#2: every returned summary has provider ∈ {local, openkim}."""
    await _seed(db_session, name="local1")
    response = await async_client.get("/api/v1/potentials")
    assert response.status_code == 200
    payload = response.json()["data"]
    for item in payload["potentials"]:
        assert item["provider"] in {"local", "openkim"}


@pytest.mark.asyncio
async def test_endpoint_does_not_500_on_openkim_outage(async_client, db_session) -> None:
    """AC#3: with OpenKIM mocked to fail, endpoint still 200 (local-only)."""
    from httpx import ConnectError, Request, Response

    class _FailingTransport:
        async def handle_async_request(self, request: Request) -> Response:
            raise ConnectError("connection refused")

    from nfm_db.services.providers.composite import CompositeProvider
    from nfm_db.services.providers.local import LocalPotentialProvider
    from nfm_db.services.providers.openkim import OpenKIMProvider

    await _seed(db_session, name="local-survivor")
    comp = CompositeProvider(
        LocalPotentialProvider(db_session),
        OpenKIMProvider(
            base_url="https://test.invalid/api",
            client_kwargs={"transport": _FailingTransport()},
        ),
    )
    with patch(
        "nfm_db.services.potential_service.build_composite_provider",
        return_value=comp,
    ):
        response = await async_client.get("/api/v1/potentials")
    assert response.status_code == 200
    payload = response.json()["data"]
    names = [p["name"] for p in payload["potentials"]]
    assert "local-survivor" in names
