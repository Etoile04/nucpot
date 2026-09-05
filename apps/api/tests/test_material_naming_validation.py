"""NFM-4308 ⑤ — material name validation blocking test-data names.

Minimal entry validation so junk like ``Test`` / ``E2E-Test-*`` can no
longer reach the production materials table (BUG-36 ⑤). Applies to
create, rename (PATCH) and batch import (which validates rows through
``MaterialCreate``).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.schemas.material import MaterialCreate, MaterialUpdate
from tests.test_materials_router import _seed_material

REJECTED_NAMES = [
    "Test",
    "test",
    "  Test  ",  # leading/trailing whitespace must not smuggle it through
    "TEST",  # case-insensitive
    "E2E-Test-Novel-Alloy-X7",
    "e2e_test_material",
    "E2E Test Alloy",
    "Test Alloy",
    "test-material-42",
]

ACCEPTED_NAMES = [
    "UO2",
    "Uranium Dioxide",
    "Testerite",  # 'test' prefix without word boundary stays legal
    "Contest Sample",
    "Latest Zr Alloy",
]


class TestSchemaValidation:
    @pytest.mark.parametrize("name", REJECTED_NAMES)
    def test_create_rejects_test_names(self, name: str) -> None:
        with pytest.raises(ValidationError, match="(?i)test"):
            MaterialCreate(name=name)

    @pytest.mark.parametrize("name", ACCEPTED_NAMES)
    def test_create_accepts_real_names(self, name: str) -> None:
        assert MaterialCreate(name=name).name == name

    @pytest.mark.parametrize("name", REJECTED_NAMES)
    def test_update_renames_to_test_name_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError, match="(?i)test"):
            MaterialUpdate(name=name)

    def test_update_none_name_passes(self) -> None:
        # PATCH without renaming must stay valid.
        update = MaterialUpdate(description="updated")
        assert update.name is None


class TestEndpointValidation:
    """POST /materials and PATCH /materials/{id} must 422 on test names."""

    @pytest.mark.asyncio
    async def test_post_material_with_test_name_returns_422(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.post(
            "/api/v1/materials", json={"name": "E2E-Test-Novel-Alloy-X7"}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_rename_to_test_name_returns_422(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        mat = await _seed_material(db_session, name="UO2")

        resp = await async_client.patch(
            f"/api/v1/materials/{mat.id}", json={"name": "Test"}
        )
        assert resp.status_code == 422
