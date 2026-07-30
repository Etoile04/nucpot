"""Tests for the Hub Node Management API (NFM-2022).

Covers AC-1 (status codes), AC-2 (heartbeat writes timestamp), AC-3
(unique-name validation), AC-4 (pagination defaults + max), and the
deregister path.  Uses the project-wide ``db_session`` + ``async_client``
fixtures so the test exercises the same ASGI stack + DB session the
production app uses.

Coverage target: >= 80% on ``nfm_db.api.v1.hub_nodes`` and
``nfm_db.schemas.hub_nodes``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import HubNode, ResourceNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_hub(db_session: AsyncSession) -> HubNode:
    """Seed a hub for FK targets; commit so the FK is resolvable across
    requests (the test client shares the same session)."""
    hub = HubNode(
        name=f"Hub-{uuid.uuid4().hex[:8]}",
        api_endpoint="https://hub.example.com/api",
    )
    db_session.add(hub)
    await db_session.commit()
    await db_session.refresh(hub)
    return hub


def _register_payload(hub_id: uuid.UUID, *, name: str | None = None) -> dict:
    """Build a valid registration body; ``name`` defaults to a UUID-derived
    unique value so successive calls don't collide (AC-3 separate test)."""
    return {
        "hub_node_id": str(hub_id),
        "name": name or f"node-{uuid.uuid4().hex[:8]}",
        "node_type": "computing",
        "api_endpoint": f"https://{uuid.uuid4().hex[:8]}.example.com/api",
        "public_key": "ssh-rsa AAAA",
    }


# ---------------------------------------------------------------------------
# AC-1 + AC-3: POST /register
# ---------------------------------------------------------------------------


class TestRegisterNode:
    """POST /api/v1/hub/nodes/register — register a resource node."""

    @pytest.mark.asyncio
    async def test_register_returns_201_with_body(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        payload = _register_payload(hub.id)

        response = await async_client.post(
            "/api/v1/hub/nodes/register", json=payload
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["success"] is True
        node = body["data"]
        assert uuid.UUID(node["id"])  # parses
        assert node["hub_node_id"] == str(hub.id)
        assert node["name"] == payload["name"]
        assert node["node_type"] == "computing"
        assert node["api_endpoint"] == payload["api_endpoint"]
        assert node["public_key"] == "ssh-rsa AAAA"
        assert node["status"] == "active"
        assert node["last_heartbeat"] is None

    @pytest.mark.asyncio
    async def test_register_minimal_payload(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """public_key is optional."""
        hub = await _create_hub(db_session)
        payload = _register_payload(hub.id)
        payload.pop("public_key")

        response = await async_client.post(
            "/api/v1/hub/nodes/register", json=payload
        )

        assert response.status_code == 201, response.text
        assert response.json()["data"]["public_key"] is None

    @pytest.mark.asyncio
    async def test_register_unknown_hub_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        """FK target must exist — 404 (not 422/500)."""
        payload = _register_payload(uuid.uuid4())  # random, doesn't exist

        response = await async_client.post(
            "/api/v1/hub/nodes/register", json=payload
        )

        assert response.status_code == 404, response.text

    @pytest.mark.asyncio
    async def test_register_missing_field_returns_422(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        payload = _register_payload(hub.id)
        payload.pop("node_type")

        response = await async_client.post(
            "/api/v1/hub/nodes/register", json=payload
        )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_register_invalid_node_type_returns_422(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        payload = _register_payload(hub.id)
        payload["node_type"] = "rogue"

        response = await async_client.post(
            "/api/v1/hub/nodes/register", json=payload
        )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_register_duplicate_name_returns_409(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """AC-3: duplicate name → 409."""
        hub = await _create_hub(db_session)
        shared_name = f"dup-{uuid.uuid4().hex[:8]}"

        first = await async_client.post(
            "/api/v1/hub/nodes/register",
            json=_register_payload(hub.id, name=shared_name),
        )
        assert first.status_code == 201, first.text

        # Second registration with the same name under any hub → 409.
        second = await async_client.post(
            "/api/v1/hub/nodes/register",
            json=_register_payload(hub.id, name=shared_name),
        )
        assert second.status_code == 409, second.text
        # The global HTTPException handler wraps the error string in
        # an i18n message ("资源冲突，操作无法完成"); the original  # noqa: RUF003
        # 409 detail is preserved as the structured "detail" field.
        body = second.json()
        assert "already exists" in (body.get("detail") or body.get("error", "")).lower()


# ---------------------------------------------------------------------------
# AC-1 + AC-4: GET / (list, paginated)
# ---------------------------------------------------------------------------


class TestListNodes:
    """GET /api/v1/hub/nodes/ — paginated list."""

    @pytest.mark.asyncio
    async def test_list_empty_returns_paginated_envelope(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.get("/api/v1/hub/nodes/")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        page = body["data"]
        assert page["items"] == []
        assert page["total"] == 0
        assert page["page"] == 1
        assert page["limit"] == 20  # AC-4 default
        assert page["pages"] == 0

    @pytest.mark.asyncio
    async def test_list_returns_200_with_all_nodes(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        for _ in range(3):
            resp = await async_client.post(
                "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
            )
            assert resp.status_code == 201, resp.text

        response = await async_client.get("/api/v1/hub/nodes/")

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["total"] == 3
        assert len(body["items"]) == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_hub(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub_a = await _create_hub(db_session)
        hub_b = await _create_hub(db_session)
        for hub in (hub_a, hub_b):
            for _ in range(2):
                resp = await async_client.post(
                    "/api/v1/hub/nodes/register",
                    json=_register_payload(hub.id),
                )
                assert resp.status_code == 201, resp.text

        response = await async_client.get(
            f"/api/v1/hub/nodes/?hub_node_id={hub_a.id}"
        )

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["total"] == 2
        assert all(item["hub_node_id"] == str(hub_a.id) for item in body["items"])

    @pytest.mark.asyncio
    async def test_list_pagination_limit_max_100(
        self,
        async_client: AsyncClient,
    ) -> None:
        """AC-4: limit > 100 → 422."""
        response = await async_client.get("/api/v1/hub/nodes/?limit=101")

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_list_pagination_limit_0_returns_422(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.get("/api/v1/hub/nodes/?limit=0")

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_list_pagination_custom_limit(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        for _ in range(5):
            resp = await async_client.post(
                "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
            )
            assert resp.status_code == 201, resp.text

        response = await async_client.get("/api/v1/hub/nodes/?limit=2&page=2")

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["page"] == 2
        assert body["pages"] == 3  # ceil(5/2)
        assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# AC-1: GET /{node_id} (detail)
# ---------------------------------------------------------------------------


class TestGetNode:
    """GET /api/v1/hub/nodes/{node_id} — node detail."""

    @pytest.mark.asyncio
    async def test_get_returns_200_when_found(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        reg = await async_client.post(
            "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
        )
        node_id = reg.json()["data"]["id"]

        response = await async_client.get(f"/api/v1/hub/nodes/{node_id}")

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["id"] == node_id

    @pytest.mark.asyncio
    async def test_get_returns_404_when_missing(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.get(
            f"/api/v1/hub/nodes/{uuid.uuid4()}"
        )

        assert response.status_code == 404, response.text

    @pytest.mark.asyncio
    async def test_get_invalid_uuid_returns_422(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.get("/api/v1/hub/nodes/not-a-uuid")

        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# AC-1 + AC-2: PUT /{node_id}/status
# ---------------------------------------------------------------------------


class TestUpdateNodeStatus:
    """PUT /api/v1/hub/nodes/{node_id}/status — update status."""

    @pytest.mark.asyncio
    async def test_update_status_returns_200(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        reg = await async_client.post(
            "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
        )
        node_id = reg.json()["data"]["id"]

        response = await async_client.put(
            f"/api/v1/hub/nodes/{node_id}/status",
            json={"status": "suspended"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "suspended"

    @pytest.mark.asyncio
    async def test_update_status_invalid_value_returns_422(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        reg = await async_client.post(
            "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
        )
        node_id = reg.json()["data"]["id"]

        response = await async_client.put(
            f"/api/v1/hub/nodes/{node_id}/status",
            json={"status": "deliberately-invalid"},
        )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_update_status_missing_node_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.put(
            f"/api/v1/hub/nodes/{uuid.uuid4()}/status",
            json={"status": "active"},
        )

        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# AC-1 + AC-2: POST /{node_id}/heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    """POST /api/v1/hub/nodes/{node_id}/heartbeat — update last_heartbeat."""

    @pytest.mark.asyncio
    async def test_heartbeat_writes_iso_timestamp(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        reg = await async_client.post(
            "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
        )
        node_id = reg.json()["data"]["id"]
        assert reg.json()["data"]["last_heartbeat"] is None

        before = datetime.now(UTC)
        response = await async_client.post(
            f"/api/v1/hub/nodes/{node_id}/heartbeat", json={}
        )
        after = datetime.now(UTC)

        assert response.status_code == 200, response.text
        ts = response.json()["data"]["last_heartbeat"]
        assert ts is not None
        # The handler must have written an ISO-8601 string parseable by
        # datetime.fromisoformat — exactly the contract format.
        parsed = datetime.fromisoformat(ts)
        assert before <= parsed <= after

        # Verify the persisted DB row also reflects the heartbeat
        # (AC-2: "updates last_heartbeat timestamp in DB").
        node = await db_session.get(ResourceNode, uuid.UUID(node_id))
        assert node is not None
        assert node.last_heartbeat == ts

    @pytest.mark.asyncio
    async def test_heartbeat_missing_node_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.post(
            f"/api/v1/hub/nodes/{uuid.uuid4()}/heartbeat", json={}
        )

        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# AC-1: DELETE /{node_id}
# ---------------------------------------------------------------------------


class TestDeregisterNode:
    """DELETE /api/v1/hub/nodes/{node_id} — deregister (hard delete)."""

    @pytest.mark.asyncio
    async def test_delete_returns_204(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        hub = await _create_hub(db_session)
        reg = await async_client.post(
            "/api/v1/hub/nodes/register", json=_register_payload(hub.id)
        )
        node_id = reg.json()["data"]["id"]

        response = await async_client.delete(f"/api/v1/hub/nodes/{node_id}")

        assert response.status_code == 204, response.text

        # Verify it's gone.
        follow = await async_client.get(f"/api/v1/hub/nodes/{node_id}")
        assert follow.status_code == 404, follow.text

    @pytest.mark.asyncio
    async def test_delete_missing_node_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.delete(
            f"/api/v1/hub/nodes/{uuid.uuid4()}"
        )

        assert response.status_code == 404, response.text
