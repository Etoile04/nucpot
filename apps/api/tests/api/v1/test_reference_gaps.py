"""Integration tests for /api/v1/reference-gaps endpoints.

Tests all 4 routes:
- GET  /api/v1/reference-gaps        — list gaps with filtering + pagination
- GET  /api/v1/reference-gaps/summary — coverage statistics
- POST /api/v1/reference-gaps/fill   — trigger fill (202)
- POST /api/v1/reference-gaps/scan   — manual gap scan
"""

from __future__ import annotations

import pytest

from nfm_db.models.ref_gap_fill import RefGapFillStaging, StagingStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_staging(db_session, *, element_system="U", phase="BCC",
                        property_name="lattice_constant", status=StagingStatus.PENDING):
    """Insert a minimal staging record to represent a covered tuple."""
    record = RefGapFillStaging(
        element_system=element_system,
        phase=phase,
        property_name=property_name,
        value=3.47,
        unit="angstrom",
        source="test_source",
        confidence="high",
        dedup_hash="a" * 64,
        status=status,
        range_validated=True,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# GET /api/v1/reference-gaps — list gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_gaps_default_params(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert "gaps" in data
    assert "total" in data
    assert data["page"] == 1
    assert data["per_page"] == 20


@pytest.mark.asyncio
async def test_list_gaps_all_gaps_without_seed(async_client) -> None:
    """With no staging records, every target tuple is a gap."""
    response = await async_client.get("/api/v1/reference-gaps?per_page=100")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] > 0
    for gap in data["gaps"]:
        assert "element_system" in gap
        assert "property_name" in gap
        assert "priority" in gap
        assert isinstance(gap["priority"], int)


@pytest.mark.asyncio
async def test_list_gaps_shrink_after_seeding(async_client, db_session) -> None:
    """Covering a tuple should reduce the gap count."""
    # Count gaps before seeding
    resp_before = await async_client.get("/api/v1/reference-gaps?per_page=100")
    total_before = resp_before.json()["data"]["total"]

    await _seed_staging(db_session)

    resp_after = await async_client.get("/api/v1/reference-gaps?per_page=100")
    total_after = resp_after.json()["data"]["total"]

    assert total_after == total_before - 1


@pytest.mark.asyncio
async def test_list_gaps_filter_element_system(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?element_system=UO2&per_page=100")
    assert response.status_code == 200
    for gap in response.json()["data"]["gaps"]:
        assert gap["element_system"] == "UO2"


@pytest.mark.asyncio
async def test_list_gaps_filter_phase(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?phase=FCC&per_page=100")
    assert response.status_code == 200
    for gap in response.json()["data"]["gaps"]:
        assert gap["phase"] == "FCC"


@pytest.mark.asyncio
async def test_list_gaps_filter_property(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?property=bulk_modulus&per_page=100")
    assert response.status_code == 200
    for gap in response.json()["data"]["gaps"]:
        assert gap["property_name"] == "bulk_modulus"


@pytest.mark.asyncio
async def test_list_gaps_sort_by_priority(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?sort_by=priority&per_page=100")
    priorities = [g["priority"] for g in response.json()["data"]["gaps"]]
    assert priorities == sorted(priorities)


@pytest.mark.asyncio
async def test_list_gaps_sort_by_element_system(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?sort_by=element_system&per_page=100")
    names = [g["element_system"] for g in response.json()["data"]["gaps"]]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_list_gaps_pagination(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?per_page=2&page=2")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page"] == 2
    assert data["per_page"] == 2
    assert len(data["gaps"]) <= 2


@pytest.mark.asyncio
async def test_list_gaps_pagination_beyond_range(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?page=999&per_page=100")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["gaps"] == []
    assert data["total"] > 0


@pytest.mark.asyncio
async def test_list_gaps_combined_filters(async_client) -> None:
    response = await async_client.get(
        "/api/v1/reference-gaps?element_system=U&phase=BCC&property=lattice_constant",
    )
    assert response.status_code == 200
    for gap in response.json()["data"]["gaps"]:
        assert gap["element_system"] == "U"
        assert gap["phase"] == "BCC"
        assert gap["property_name"] == "lattice_constant"


@pytest.mark.asyncio
async def test_list_gaps_filter_no_match(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps?element_system=NONEXISTENT")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/v1/reference-gaps/summary — coverage statistics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_shape(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    summary = body["data"]
    assert "total_target_tuples" in summary
    assert "covered" in summary
    assert "gaps" in summary
    assert "coverage_percent" in summary
    assert "by_system" in summary
    assert "staging_pending" in summary
    assert "staging_approved" in summary


@pytest.mark.asyncio
async def test_summary_total_equals_covered_plus_gaps(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps/summary")
    summary = response.json()["data"]
    assert summary["total_target_tuples"] == summary["covered"] + summary["gaps"]


@pytest.mark.asyncio
async def test_summary_coverage_percent_range(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps/summary")
    pct = response.json()["data"]["coverage_percent"]
    assert isinstance(pct, (int, float))
    assert 0 <= pct <= 100


@pytest.mark.asyncio
async def test_summary_by_system_breakdown(async_client) -> None:
    response = await async_client.get("/api/v1/reference-gaps/summary")
    by_system = response.json()["data"]["by_system"]
    assert isinstance(by_system, list)
    assert len(by_system) > 0
    for entry in by_system:
        assert "element_system" in entry
        assert "total" in entry
        assert "covered" in entry
        assert "gaps" in entry


@pytest.mark.asyncio
async def test_summary_increases_on_seed(async_client, db_session) -> None:
    resp_before = await async_client.get("/api/v1/reference-gaps/summary")
    covered_before = resp_before.json()["data"]["covered"]

    await _seed_staging(db_session)

    resp_after = await async_client.get("/api/v1/reference-gaps/summary")
    covered_after = resp_after.json()["data"]["covered"]
    assert covered_after == covered_before + 1


@pytest.mark.asyncio
async def test_summary_staging_counts(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.PENDING)
    await _seed_staging(
        db_session, element_system="UO2", phase="FCC",
        property_name="bulk_modulus", status=StagingStatus.APPROVED,
    )

    response = await async_client.get("/api/v1/reference-gaps/summary")
    summary = response.json()["data"]
    assert summary["staging_pending"] == 1
    assert summary["staging_approved"] == 1


# ---------------------------------------------------------------------------
# POST /api/v1/reference-gaps/fill — trigger fill operation (202)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_returns_202(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "dry_run": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_fill_response_shape(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "dry_run": True,
        },
    )
    fill = response.json()["data"]
    assert "batch_id" in fill
    assert "gaps_targeted" in fill
    assert "values_found" in fill
    assert "staged" in fill
    assert "duplicates" in fill
    assert "results" in fill
    assert fill["gaps_targeted"] == 1


@pytest.mark.asyncio
async def test_fill_dry_run_no_batch_id(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "dry_run": True,
        },
    )
    fill = response.json()["data"]
    assert fill["batch_id"] is None
    assert fill["staged"] == 0


@pytest.mark.asyncio
async def test_fill_dry_run_found_not_staged(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "dry_run": True,
        },
    )
    fill = response.json()["data"]
    assert fill["values_found"] > 0
    assert fill["staged"] == 0
    for item in fill["results"]:
        assert item["status"] == "found"


@pytest.mark.asyncio
async def test_fill_nonexistent_property(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "nonexistent_property_xyz",
            "dry_run": True,
        },
    )
    assert response.status_code == 202
    fill = response.json()["data"]
    assert fill["values_found"] == 0
    assert fill["results"] == []


@pytest.mark.asyncio
async def test_fill_wet_run_stages_values(async_client, db_session) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "lattice_constant",
            "dry_run": False,
        },
    )
    assert response.status_code == 202
    fill = response.json()["data"]
    assert fill["batch_id"] is not None
    assert fill["staged"] > 0


@pytest.mark.asyncio
async def test_fill_lattice_constant_finds_multiple(async_client) -> None:
    """lattice_constant has 2 simulated cache entries."""
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "lattice_constant",
            "dry_run": True,
        },
    )
    fill = response.json()["data"]
    assert fill["values_found"] == 2


@pytest.mark.asyncio
async def test_fill_invalid_missing_required_fields(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/fill",
        json={"phase": "BCC"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/reference-gaps/scan — manual gap scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_returns_200(async_client) -> None:
    response = await async_client.post("/api/v1/reference-gaps/scan", json={})
    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_scan_response_shape(async_client) -> None:
    response = await async_client.post("/api/v1/reference-gaps/scan", json={})
    scan = response.json()["data"]
    assert "total_gaps_found" in scan
    assert "systems_scanned" in scan
    assert "results" in scan
    assert isinstance(scan["results"], list)


@pytest.mark.asyncio
async def test_scan_filter_by_element_systems(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/scan",
        json={"element_systems": ["U"]},
    )
    scan = response.json()["data"]
    for item in scan["results"]:
        assert item["element_system"] == "U"


@pytest.mark.asyncio
async def test_scan_multiple_systems(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/scan",
        json={"element_systems": ["U", "Zr"]},
    )
    scan = response.json()["data"]
    scanned_systems = {item["element_system"] for item in scan["results"]}
    assert "U" in scanned_systems
    assert "Zr" in scanned_systems


@pytest.mark.asyncio
async def test_scan_none_payload_scans_all(async_client) -> None:
    response = await async_client.post("/api/v1/reference-gaps/scan")
    assert response.status_code == 200
    scan = response.json()["data"]
    assert scan["total_gaps_found"] > 0


@pytest.mark.asyncio
async def test_scan_empty_list_scans_none(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-gaps/scan",
        json={"element_systems": []},
    )
    assert response.status_code == 200
    scan = response.json()["data"]
    assert scan["total_gaps_found"] == 0


@pytest.mark.asyncio
async def test_scan_gaps_found_matches_list(async_client) -> None:
    resp_scan = await async_client.post("/api/v1/reference-gaps/scan", json={})
    scan_total = resp_scan.json()["data"]["total_gaps_found"]

    resp_list = await async_client.get("/api/v1/reference-gaps?per_page=100")
    list_total = resp_list.json()["data"]["total"]

    assert scan_total == list_total
