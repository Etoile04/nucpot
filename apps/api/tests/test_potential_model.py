"""Tests for the Potential ORM model."""

import pytest

from nfm_db.models import Potential


def test_potential_model_importable() -> None:
    """Potential model can be imported and has expected columns."""
    assert Potential.__tablename__ == "potentials"
    columns = {c.name for c in Potential.__table__.columns}
    expected = {
        "id",
        "name",
        "display_name",
        "type",
        "format",
        "elements",
        "description",
        "applicability",
        "lammps_config",
        "verified_props",
        "file_url",
        "source",
        "version",
        "status",
        "verification_status",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(columns), f"missing: {expected - columns}"


@pytest.mark.asyncio
async def test_potential_can_be_created(db_session) -> None:
    """A Potential row can be inserted and queried back."""
    p = Potential(
        name="EAM_U_Zhou_2004",
        type="EAM",
        elements=["U"],
        description="EAM potential for U by Zhou (2004)",
        lammps_config={"pair_style": "eam/alloy"},
        status="published",
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.id is not None
    assert p.elements == ["U"]
    assert p.status == "published"


@pytest.mark.asyncio
async def test_potential_verification_status_default(db_session) -> None:
    """verification_status defaults to 'unverified' on insert (model column default)."""
    p = Potential(name="test-default", type="EAM")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.verification_status == "unverified"


@pytest.mark.asyncio
async def test_potential_verification_status_custom(db_session) -> None:
    """verification_status can be set explicitly at construction and persists to DB."""
    p = Potential(name="test-custom", type="EAM", verification_status="verified")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.verification_status == "verified"
