"""Tests for nfm_db.api.v1.materials — Materials CRUD router stub."""

from __future__ import annotations

from fastapi import APIRouter

from nfm_db.api.v1.materials import router


class TestMaterialsRouter:
    """Contract tests for the materials APIRouter stub."""

    def test_router_is_api_router_instance(self) -> None:
        """Router must be a FastAPI APIRouter."""
        assert isinstance(router, APIRouter)

    def test_router_prefix(self) -> None:
        """Router prefix must be /materials."""
        assert router.prefix == "/materials"

    def test_router_tags(self) -> None:
        """Router must carry the materials tag for OpenAPI grouping."""
        assert router.tags == ["materials"]

    def test_router_has_no_routes(self) -> None:
        """Stub router must not register any routes."""
        assert len(router.routes) == 0
