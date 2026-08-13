"""Tests for nfm_db.api.v1.properties — Properties CRUD router stub."""

from __future__ import annotations

from fastapi import APIRouter

from nfm_db.api.v1.properties import router


class TestPropertiesRouter:
    """Contract tests for the properties APIRouter stub."""

    def test_router_is_api_router_instance(self) -> None:
        """Router must be a FastAPI APIRouter."""
        assert isinstance(router, APIRouter)

    def test_router_prefix(self) -> None:
        """Router prefix must be /properties."""
        assert router.prefix == "/properties"

    def test_router_tags(self) -> None:
        """Router must carry the properties tag for OpenAPI grouping."""
        assert router.tags == ["properties"]

    def test_router_has_no_routes(self) -> None:
        """Stub router must not register any routes."""
        assert len(router.routes) == 0
