"""Integration tests for durable Hub sync-data endpoints (NFM-2029)."""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import HubNode


@pytest.mark.asyncio
async def test_sync_data_is_idempotent_and_incremental(async_client, db_session) -> None:
    hub = HubNode(
        id=uuid.UUID("b1000000-0000-0000-0000-000000000001"),
        name="sync-test-hub",
        api_endpoint="http://hub:8000",
        status="active",
    )
    db_session.add(hub)
    await db_session.commit()

    register = await async_client.post(
        "/api/v1/hub/nodes/register",
        json={
            "hub_node_id": str(hub.id),
            "name": "sync-test-resource",
            "node_type": "computing",
            "api_endpoint": "http://resource:8080",
        },
    )
    assert register.status_code == 201
    node_id = register.json()["data"]["id"]
    operation_id = str(uuid.uuid4())
    operation = {
        "operation_id": operation_id,
        "op_type": "create",
        "entity_type": "material",
        "entity_id": "UO2",
        "payload": {"formula": "UO2"},
        "vector_clock": {"clocks": {node_id: 1}},
    }

    first = await async_client.post(
        f"/api/v1/hub/nodes/{node_id}/sync-data", json=operation
    )
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["duplicate"] is False
    assert first_data["watermark"] == 1

    duplicate = await async_client.post(
        f"/api/v1/hub/nodes/{node_id}/sync-data", json=operation
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"] == {
        "operation_id": operation_id,
        "watermark": 1,
        "duplicate": True,
    }

    pulled = await async_client.get(
        f"/api/v1/hub/nodes/{node_id}/sync-data", params={"since": 0}
    )
    assert pulled.status_code == 200
    pulled_data = pulled.json()["data"]
    assert pulled_data["watermark"] == 1
    assert len(pulled_data["items"]) == 1
    assert pulled_data["items"][0]["entity_id"] == "UO2"

    after = await async_client.get(
        f"/api/v1/hub/nodes/{node_id}/sync-data", params={"since": 1}
    )
    assert after.status_code == 200
    assert after.json()["data"] == {"items": [], "watermark": 1}
