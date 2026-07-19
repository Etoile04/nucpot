"""Tests for nfm_db.api.v1.properties — Properties CRUD router."""

from __future__ import annotations

from fastapi import APIRouter

from nfm_db.api.v1.properties import router


class TestPropertiesRouter:
    """Contract tests for the properties APIRouter."""

    def test_router_is_api_router_instance(self) -> None:
        """Router must be a FastAPI APIRouter."""
        assert isinstance(router, APIRouter)

    def test_router_prefix(self) -> None:
        """Router uses empty prefix — routes carry full path segments."""
        assert router.prefix == ""

    def test_router_tags(self) -> None:
        """Router must carry the properties tag for OpenAPI grouping."""
        assert router.tags == ["属性管理"]

    def test_router_has_crud_routes(self) -> None:
        """Router must register CRUD + stats routes."""
        assert len(router.routes) == 5
