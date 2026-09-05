"""NFM-4308 ⑤ — tests for scripts/nfm_4308_test_material_cleanup.py.

The script is the operational half of the test-data cleanup: it finds
production materials whose name matches the test-data pattern and (with
``--execute``) deletes them. Rows carrying real data (datasets / DFT
calculations) are never auto-deleted — they are reported for manual
review.
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ lives at the project root (three levels up from tests/ file).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest
from scripts.nfm_4308_test_material_cleanup import (
    TEST_MATERIAL_NAME_PATTERN,
    delete_test_materials,
    find_test_materials,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Dataset, Material
from tests.test_materials_router import _seed_material

pytestmark = pytest.mark.asyncio


async def _seed_junk(db: AsyncSession) -> list[Material]:
    return [
        await _seed_material(db, name="Test"),
        await _seed_material(db, name="E2E-Test-Novel-Alloy-X7"),
    ]


class TestPattern:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Test", True),
            ("E2E-Test-Novel-Alloy-X7", True),
            ("e2e_test 1", True),
            ("UO2", False),
            ("Testerite", False),
            ("Contest Sample", False),
        ],
    )
    def test_pattern_classification(self, name: str, expected: bool) -> None:
        assert bool(TEST_MATERIAL_NAME_PATTERN.match(name.strip())) is expected


class TestFind:
    async def test_find_returns_only_test_named_materials(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_junk(db_session)
        await _seed_material(db_session, name="UO2")

        candidates = await find_test_materials(db_session)

        assert sorted(c.name for c in candidates) == [
            "E2E-Test-Novel-Alloy-X7",
            "Test",
        ]

    async def test_find_flags_rows_with_dependent_data(
        self, db_session: AsyncSession
    ) -> None:
        junk = await _seed_junk(db_session)
        db_session.add(
            Dataset(title="real-dataset", material_id=junk[0].id)
        )
        await db_session.commit()

        candidates = await find_test_materials(db_session)

        by_name = {c.name: c for c in candidates}
        assert by_name["Test"].deletable is False
        assert by_name["Test"].dataset_count == 1
        assert by_name["E2E-Test-Novel-Alloy-X7"].deletable is True


class TestDelete:
    async def test_delete_removes_junk_and_keeps_real(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_junk(db_session)
        await _seed_material(db_session, name="UO2")

        report = await delete_test_materials(db_session)

        assert report.deleted_count == 2
        assert report.skipped_count == 0
        remaining = (
            (await db_session.execute(select(Material.name))).scalars().all()
        )
        assert sorted(remaining) == ["UO2"]

    async def test_delete_skips_rows_with_dependent_data(
        self, db_session: AsyncSession
    ) -> None:
        junk = await _seed_junk(db_session)
        db_session.add(
            Dataset(title="real-dataset", material_id=junk[0].id)
        )
        await db_session.commit()

        report = await delete_test_materials(db_session)

        assert report.deleted_count == 1
        assert report.skipped_count == 1
        names = set(
            (await db_session.execute(select(Material.name))).scalars().all()
        )
        assert "Test" in names  # kept for manual review
        assert "E2E-Test-Novel-Alloy-X7" not in names
