"""Tests for the potential service layer."""

import pytest

from nfm_db.models import Potential
from nfm_db.services.potential_service import (
    get_potential_by_id,
    list_potentials,
)


async def _seed(db_session, **overrides):
    defaults = dict(
        name="EAM_U_Zhou_2004",
        type="EAM",
        elements=["U"],
        status="published",
        lammps_config={},
        applicability={},
    )
    defaults.update(overrides)
    p = Potential(**defaults)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.mark.asyncio
async def test_list_returns_only_published(db_session) -> None:
    await _seed(db_session, name="pub1", status="published")
    await _seed(db_session, name="draft1", status="draft")
    result = await list_potentials(db_session, page=1, limit=20)
    names = [p.name for p in result.potentials]
    assert "pub1" in names
    assert "draft1" not in names
    assert result.total == 1


@pytest.mark.asyncio
async def test_list_filters_by_type(db_session) -> None:
    await _seed(db_session, name="eam1", type="EAM")
    await _seed(db_session, name="mtp1", type="MTP")
    result = await list_potentials(db_session, page=1, limit=20, type_filter="EAM")
    assert all(p.type == "EAM" for p in result.potentials)
    assert result.total == 1


@pytest.mark.asyncio
async def test_list_filters_by_elements_overlap(db_session) -> None:
    await _seed(db_session, name="umo", elements=["U", "Mo"])
    await _seed(db_session, name="zr", elements=["Zr"])
    result = await list_potentials(db_session, page=1, limit=20, elements=["Mo"])
    assert result.total == 1
    assert result.potentials[0].name == "umo"


@pytest.mark.asyncio
async def test_list_searches_by_query(db_session) -> None:
    await _seed(db_session, name="EAM_U_Zhou", description="EAM potential for uranium")
    await _seed(db_session, name="EAM_Mo_Ack", description="molybdenum")
    result = await list_potentials(db_session, page=1, limit=20, query="uranium")
    assert result.total == 1


@pytest.mark.asyncio
async def test_get_by_id_returns_detail(db_session) -> None:
    seeded = await _seed(db_session, name="detail1", verified_props={"lattice": 3.5})
    detail = await get_potential_by_id(db_session, seeded.id)
    assert detail is not None
    assert detail.name == "detail1"
    assert detail.verified_props == {"lattice": 3.5}


@pytest.mark.asyncio
async def test_get_by_id_returns_none_for_missing(db_session) -> None:
    import uuid

    assert await get_potential_by_id(db_session, uuid.uuid4()) is None
