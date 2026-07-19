"""Tests for nfm_db.api.v1.materials — Materials CRUD router."""

from __future__ import annotations

from fastapi import APIRouter

from nfm_db.api.v1.materials import router


class TestMaterialsRouter:
    """Contract tests for the materials APIRouter."""

    def test_router_is_api_router_instance(self) -> None:
        """Router must be a FastAPI APIRouter."""
        assert isinstance(router, APIRouter)

    def test_router_prefix(self) -> None:
        """Router uses empty prefix — routes carry full path segments."""
        assert router.prefix == ""

    def test_router_tags(self) -> None:
        """Router must carry the materials tag for OpenAPI grouping."""
        assert router.tags == ["材料管理"]

    def test_router_has_crud_routes(self) -> None:
        """Router must register CRUD + search + batch-import routes."""
        assert len(router.routes) == 7
