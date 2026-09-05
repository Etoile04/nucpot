"""Tests for the potential service layer."""

import pytest

from nfm_db.models import Potential
from nfm_db.services.potential_service import (
    get_potential_by_id,
    list_potentials,
    update_potential_verification,
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
async def test_list_query_treats_like_wildcards_literally(db_session) -> None:
    """Legacy BFF parity: ``q`` is a literal substring match, so ``_`` and
    ``%`` from the caller must not act as SQL LIKE wildcards."""
    await _seed(db_session, name="EAM_U_Zhou")
    await _seed(db_session, name="EAMXU_Zhou")
    await _seed(db_session, name="alpha_beta")

    underscore = await list_potentials(db_session, page=1, limit=20, query="EAM_U")
    assert {p.name for p in underscore.potentials} == {"EAM_U_Zhou"}

    percent = await list_potentials(db_session, page=1, limit=20, query="a%b")
    assert percent.total == 0

    backslash = await list_potentials(db_session, page=1, limit=20, query="a\\b")
    assert backslash.total == 0


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


@pytest.mark.asyncio
async def test_list_elements_filter_before_pagination(db_session) -> None:
    """Elements filter is applied BEFORE pagination — correct total/pages.

    Reproduces NFM-286 HIGH: the old code filtered elements on a single
    paginated page-slice, so ``total`` and ``total_pages`` were wrong when
    elements + page + limit interacted.
    """
    # 10 Zr + 10 Mo, updated_at desc so newly-seeded rows sort last (Zr last)
    for i in range(1, 11):
        await _seed(db_session, name=f"mo_{i}", type="EAM", elements=["Mo"])
    for i in range(1, 11):
        await _seed(db_session, name=f"zr_{i}", type="EAM", elements=["Zr"])

    # Page 1 of 2 (limit=5, 10 Zr total)
    page1 = await list_potentials(db_session, page=1, limit=5, elements=["Zr"])
    assert page1.total == 10, f"total should be 10, got {page1.total}"
    assert page1.total_pages == 2, f"total_pages should be 2, got {page1.total_pages}"
    assert len(page1.potentials) == 5
    assert all("Zr" in p.elements for p in page1.potentials)

    # Page 2 should have the remaining 5 Zr
    page2 = await list_potentials(db_session, page=2, limit=5, elements=["Zr"])
    assert page2.total == 10
    assert page2.total_pages == 2
    assert len(page2.potentials) == 5
    assert all("Zr" in p.elements for p in page2.potentials)

    # All 10 Zr names recovered across two pages
    all_names = {p.name for p in page1.potentials} | {p.name for p in page2.potentials}
    assert all_names == {f"zr_{i}" for i in range(1, 11)}

    # Page 3 (past end) should be empty but still report correct total
    page3 = await list_potentials(db_session, page=3, limit=5, elements=["Zr"])
    assert page3.total == 10
    assert page3.total_pages == 2
    assert page3.potentials == []


@pytest.mark.asyncio
async def test_update_verification_status_sets_pending(db_session) -> None:
    """update_potential_verification flips status and persists the change."""
    p = Potential(name="verify-me", type="EAM", elements=["U"], status="published")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.verification_status == "unverified"

    updated = await update_potential_verification(
        db_session, p.id, "pending", message="autovc queued"
    )
    assert updated is not None
    assert updated.verification_status == "pending"
    assert updated.extra["verification_message"] == "autovc queued"


@pytest.mark.asyncio
async def test_update_verification_status_stores_evidence_url(db_session) -> None:
    """The helper records the evidence URL in the extra audit blob."""
    p = Potential(name="verify-evidence", type="EAM", elements=["U"], status="published")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    updated = await update_potential_verification(
        db_session,
        p.id,
        "verified",
        message="all props within tolerance",
        evidence_url="https://example.org/report/1",
    )
    assert updated is not None
    assert updated.verification_status == "verified"
    assert updated.extra["verification_evidence_url"] == "https://example.org/report/1"


@pytest.mark.asyncio
async def test_update_verification_status_returns_none_for_missing(db_session) -> None:
    """Updating a non-existent potential returns None (no raise)."""
    import uuid

    result = await update_potential_verification(db_session, uuid.uuid4(), "pending")
    assert result is None


@pytest.mark.asyncio
async def test_update_verification_status_preserves_existing_extra(db_session) -> None:
    """Writing verification audit data must not clobber other extra fields."""
    p = Potential(
        name="verify-preserve",
        type="EAM",
        elements=["U"],
        status="published",
        extra={"custom_note": "keep me"},
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    updated = await update_potential_verification(
        db_session, p.id, "failed", message="divergence in lattice"
    )
    assert updated is not None
    assert updated.verification_status == "failed"
    assert updated.extra["custom_note"] == "keep me"
    assert updated.extra["verification_message"] == "divergence in lattice"


# ---------------------------------------------------------------------------
# NFM-4311 — advanced filters (ported from the legacy Supabase BFF route so
# the list can be served from the local stack without contract loss) and the
# comma-separated type filter the browse UI already sends ("EAM,MEAM").
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_type_filter_accepts_comma_separated(db_session) -> None:
    await _seed(db_session, name="eam1", type="EAM")
    await _seed(db_session, name="meam1", type="MEAM")
    await _seed(db_session, name="mtp1", type="MTP")
    result = await list_potentials(db_session, page=1, limit=20, type_filter="EAM,MEAM")
    names = {p.name for p in result.potentials}
    assert names == {"eam1", "meam1"}
    assert result.total == 2


@pytest.mark.asyncio
async def test_list_filters_by_extra_boolean(db_session) -> None:
    await _seed(db_session, name="irrad", extra={"irradiationRelevant": True})
    await _seed(db_session, name="not_irrad", extra={"irradiationRelevant": False})
    await _seed(db_session, name="no_extra")
    result = await list_potentials(
        db_session, page=1, limit=20, extra_filters={"irradiationRelevant": True}
    )
    names = [p.name for p in result.potentials]
    assert names == ["irrad"]
    assert result.total == 1


@pytest.mark.asyncio
async def test_list_filters_by_validation_level(db_session) -> None:
    await _seed(db_session, name="bench", extra={"validationLevel": "benchmarked"})
    await _seed(db_session, name="basic", extra={"validationLevel": "basic"})
    result = await list_potentials(
        db_session, page=1, limit=20, extra_filters={"validationLevel": "benchmarked"}
    )
    assert [p.name for p in result.potentials] == ["bench"]


@pytest.mark.asyncio
async def test_list_temp_min_uses_range_upper_bound(db_session) -> None:
    # temperatureRange is [min, max]; PostgREST filtered on ->>1 >= tempMin.
    await _seed(
        db_session,
        name="wide",
        applicability={"temperatureRange": [300, 3000]},
    )
    await _seed(db_session, name="narrow", applicability={"temperatureRange": [300, 900]})
    await _seed(db_session, name="no_range", applicability={})

    hot = await list_potentials(db_session, page=1, limit=20, temp_min=2000.0)
    assert {p.name for p in hot.potentials} == {"wide"}

    all_rows = await list_potentials(db_session, page=1, limit=20, temp_min=100.0)
    assert {p.name for p in all_rows.potentials} == {"wide", "narrow"}


@pytest.mark.asyncio
async def test_list_temp_max_uses_range_lower_bound(db_session) -> None:
    await _seed(db_session, name="hot_only", applicability={"temperatureRange": [1500, 3000]})
    await _seed(db_session, name="cold", applicability={"temperatureRange": [100, 500]})

    cold = await list_potentials(db_session, page=1, limit=20, temp_max=600.0)
    assert {p.name for p in cold.potentials} == {"cold"}

    everything = await list_potentials(db_session, page=1, limit=20, temp_max=5000.0)
    assert {p.name for p in everything.potentials} == {"hot_only", "cold"}


@pytest.mark.asyncio
async def test_list_combined_advanced_filters(db_session) -> None:
    await _seed(
        db_session,
        name="match",
        extra={"irradiationRelevant": True, "validationLevel": "production"},
        applicability={"temperatureRange": [300, 2500]},
    )
    await _seed(
        db_session,
        name="wrong_level",
        extra={"irradiationRelevant": True, "validationLevel": "basic"},
        applicability={"temperatureRange": [300, 2500]},
    )
    result = await list_potentials(
        db_session,
        page=1,
        limit=20,
        extra_filters={"irradiationRelevant": True, "validationLevel": "production"},
        temp_min=2000.0,
    )
    assert [p.name for p in result.potentials] == ["match"]


@pytest.mark.asyncio
async def test_list_advanced_filters_paginate_after_filter(db_session) -> None:
    for i in range(5):
        await _seed(db_session, name=f"irrad_{i}", extra={"irradiationRelevant": True})
    await _seed(db_session, name="plain")
    page1 = await list_potentials(
        db_session, page=1, limit=2, extra_filters={"irradiationRelevant": True}
    )
    page2 = await list_potentials(
        db_session, page=2, limit=2, extra_filters={"irradiationRelevant": True}
    )
    page3 = await list_potentials(
        db_session, page=3, limit=2, extra_filters={"irradiationRelevant": True}
    )
    assert page1.total == 5 and page1.total_pages == 3
    # SQLite second-precision timestamps make intra-second order undefined —
    # assert coverage across pages instead of exact ordering.
    names1 = {p.name for p in page1.potentials}
    names2 = {p.name for p in page2.potentials}
    assert len(names1) == 2 and len(names2) == 2
    assert not names1 & names2
    assert names1 | names2 | {p.name for p in page3.potentials} == {
        f"irrad_{i}" for i in range(5)
    }


@pytest.mark.asyncio
async def test_list_pagination_is_deterministic_on_tied_sort_keys(db_session) -> None:
    """NFM-4311: the BFF stitches multiple backend pages into one response;
    rows sharing a sort key (bulk import → identical updated_at) must page
    in a stable order so stitched windows neither overlap nor skip rows."""
    from datetime import datetime, timezone

    tied = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    seeded_ids = []
    for i in range(6):
        p = await _seed(db_session, name=f"bulk_{i}", updated_at=tied)
        seeded_ids.append(p.id)

    expected = sorted(seeded_ids)  # desc(updated_at) tie → id ascending
    seen: list = []
    for page in range(1, 4):
        result = await list_potentials(db_session, page=page, limit=2)
        seen.extend(p.id for p in result.potentials)
    assert seen == expected


@pytest.mark.asyncio
async def test_list_latency_guard_under_500ms(db_session) -> None:
    """NFM-4311 latency regression guard: the local list path must stay far
    below the 500ms design target at production corpus scale (65 rows)."""
    import time

    for i in range(65):
        await _seed(
            db_session,
            name=f"guard_{i:03d}",
            type="EAM" if i % 2 else "MEAM",
            elements=["U", "Mo"],
            extra={"validationLevel": "basic", "irradiationRelevant": i % 3 == 0},
            applicability={"temperatureRange": [300, 2000 + i]},
            description="x" * 200,
        )
    start = time.perf_counter()
    result = await list_potentials(db_session, page=1, limit=12, type_filter="EAM,MEAM")
    elapsed = time.perf_counter() - start
    assert result.total == 65
    assert elapsed < 0.5, f"list_potentials took {elapsed * 1000:.1f}ms (>500ms budget)"
