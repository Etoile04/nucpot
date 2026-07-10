"""Tests for POST /api/v1/materials/batch-import (NFM-1141).

Covers CSV parsing (BOM, empty rows, encoding), JSON parsing, partial
success, file size limit enforcement, duplicate upsert, concurrency
control, and error reporting.
"""

from __future__ import annotations

import asyncio
import csv
import io

import pytest
from httpx import AsyncClient

from nfm_db.models import Material
from nfm_db.services.batch_import_service import (
    BATCH_IMPORT_MAX_SIZE_MB,
    _parse_csv_content,
    _parse_json_content,
    _row_to_material_create,
    _validate_row,
    get_import_lock,
)

# ---------------------------------------------------------------------------
# Unit tests: parsing helpers
# ---------------------------------------------------------------------------


class TestParseCsvContent:
    """Unit tests for _parse_csv_content."""

    def test_basic_csv(self) -> None:
        content = b"name,formula\ncarbon,CO2\n"
        rows = _parse_csv_content(content)
        assert len(rows) == 1
        assert rows[0]["name"] == "carbon"
        assert rows[0]["formula"] == "CO2"

    def test_utf8_bom_stripped(self) -> None:
        content = b"\xef\xbb\xbfname,formula\nUO2,Uranium Dioxide\n"
        rows = _parse_csv_content(content)
        assert len(rows) == 1
        assert rows[0]["name"] == "UO2"

    def test_empty_rows_skipped(self) -> None:
        content = b"name,formula\nUO2,UO2\n\n,\n"
        rows = _parse_csv_content(content)
        assert len(rows) == 1

    def test_whitespace_trimmed(self) -> None:
        content = b"name,formula\n  carbon  ,  CO2  \n"
        rows = _parse_csv_content(content)
        assert rows[0]["name"] == "carbon"
        assert rows[0]["formula"] == "CO2"

    def test_all_empty_file(self) -> None:
        content = b"name,formula\n\n\n"
        rows = _parse_csv_content(content)
        assert rows == []

    def test_case_insensitive_columns(self) -> None:
        content = b"Name,Formula\nUO2,UO2\n"
        rows = _parse_csv_content(content)
        assert len(rows) == 1
        assert "Name" in rows[0]

    def test_extra_columns_preserved(self) -> None:
        content = b"name,formula,extra_col\nUO2,UO2,value\n"
        rows = _parse_csv_content(content)
        assert rows[0]["extra_col"] == "value"


class TestParseJsonContent:
    """Unit tests for _parse_json_content."""

    def test_valid_json_array(self) -> None:
        content = b'[{"name": "UO2", "formula": "UO2"}]'
        rows = _parse_json_content(content)
        assert len(rows) == 1
        assert rows[0]["name"] == "UO2"

    def test_json_not_array_raises(self) -> None:
        content = b'{"name": "UO2"}'
        with pytest.raises(ValueError, match="array"):
            _parse_json_content(content)

    def test_json_empty_array(self) -> None:
        content = b"[]"
        rows = _parse_json_content(content)
        assert rows == []

    def test_json_multiple_objects(self) -> None:
        content = b'[{"name": "A"}, {"name": "B"}, {"name": "C"}]'
        rows = _parse_json_content(content)
        assert len(rows) == 3


class TestRowToMaterialCreate:
    """Unit tests for _row_to_material_create."""

    def test_valid_row(self) -> None:
        row = {"name": "UO2", "formula": "UO2"}
        data, err = _row_to_material_create(row, row_index=1)
        assert err is None
        assert data["name"] == "UO2"

    def test_missing_name_returns_error(self) -> None:
        row = {"formula": "UO2"}
        data, err = _row_to_material_create(row, row_index=1)
        assert data is None
        assert err is not None
        assert err.field == "name"

    def test_invalid_boolean_returns_error(self) -> None:
        row = {"name": "UO2", "is_active": "maybe"}
        data, err = _row_to_material_create(row, row_index=1)
        assert data is None
        assert err is not None
        assert "boolean" in err.message.lower()

    def test_boolean_true_variants(self) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "Yes"):
            data, err = _row_to_material_create(
                {"name": "UO2", "is_active": val}, row_index=1
            )
            assert err is None
            assert data["is_active"] is True

    def test_boolean_false_variants(self) -> None:
        for val in ("false", "False", "FALSE", "0", "no", "No"):
            data, err = _row_to_material_create(
                {"name": "UO2", "is_active": val}, row_index=1
            )
            assert err is None
            assert data["is_active"] is False


class TestValidateRow:
    """Unit tests for _validate_row."""

    def test_valid_data(self) -> None:
        data = {"name": "UO2", "formula": "UO2"}
        validated, err = _validate_row(data, row_index=1)
        assert err is None
        assert validated is not None
        assert validated.name == "UO2"

    def test_invalid_name_too_long(self) -> None:
        data = {"name": "x" * 501}
        validated, err = _validate_row(data, row_index=1)
        assert validated is None
        assert err is not None


# ---------------------------------------------------------------------------
# Integration tests: endpoint
# ---------------------------------------------------------------------------


def _make_csv(content_rows: list[dict]) -> bytes:
    """Build a CSV bytes object from a list of row dicts."""
    if not content_rows:
        return b"name,formula\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=content_rows[0].keys())
    writer.writeheader()
    for row in content_rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


@pytest.mark.asyncio
async def test_batch_import_csv_valid(async_client: AsyncClient) -> None:
    """POST a valid CSV file and verify import succeeds."""
    csv_bytes = _make_csv([
        {"name": "UO2", "formula": "UO2", "crystal_structure": "Fluorite"},
        {"name": "UN", "formula": "UN", "crystal_structure": "NaCl"},
    ])

    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("test.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["imported"] == 2
    assert data["failed"] == 0
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_batch_import_csv_with_bom(async_client: AsyncClient) -> None:
    """CSV with UTF-8 BOM should import successfully."""
    content = b"\xef\xbb\xbfname,formula\nUO2,UO2\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("bom.csv", content, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 1


@pytest.mark.asyncio
async def test_batch_import_csv_empty_rows(async_client: AsyncClient) -> None:
    """Empty CSV rows should be skipped without error."""
    csv_bytes = b"name,formula\nUO2,UO2\n\n,\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("test.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 1
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_batch_import_json_valid(async_client: AsyncClient) -> None:
    """POST a valid JSON file and verify import succeeds."""
    json_bytes = b'[{"name": "UO2", "formula": "UO2"}, {"name": "UN"}]'
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("test.json", json_bytes, "application/json")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_batch_import_json_not_array(async_client: AsyncClient) -> None:
    """JSON file that is not an array should return 400."""
    json_bytes = b'{"name": "UO2"}'
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("bad.json", json_bytes, "application/json")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_batch_import_unsupported_type(async_client: AsyncClient) -> None:
    """Non-CSV/JSON file type should return 400."""
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("data.xlsx", b"fake", "application/octet-stream")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_batch_import_file_size_limit(async_client: AsyncClient) -> None:
    """File exceeding size limit should return 413."""
    large_content = b"x" * (BATCH_IMPORT_MAX_SIZE_MB * 1024 * 1024 + 1)
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("big.csv", large_content, "text/csv")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_batch_import_partial_success(async_client: AsyncClient) -> None:
    """Valid rows imported; invalid rows reported in errors."""
    csv_bytes = b"name,formula,is_active\nUO2,UO2,true\n,missing_name,true\nBadBool,X,maybe\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("mixed.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 1
    assert data["failed"] == 2
    assert len(data["errors"]) == 2


@pytest.mark.asyncio
async def test_batch_import_upsert_duplicate(
    async_client: AsyncClient, db_session
) -> None:
    """Importing the same name+formula should update the existing record."""
    mat = Material(name="UO2", formula="UO2", crystal_structure="OldStructure")
    db_session.add(mat)
    await db_session.commit()

    csv_bytes = b"name,formula,crystal_structure\nUO2,UO2,Fluorite\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("update.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 1

    await db_session.refresh(mat)
    assert mat.crystal_structure == "Fluorite"


@pytest.mark.asyncio
async def test_batch_import_upsert_new_if_formula_differs(
    async_client: AsyncClient, db_session
) -> None:
    """Same name but different formula creates a new record (not update)."""
    mat = Material(name="UO2", formula="UO2")
    db_session.add(mat)
    await db_session.commit()

    csv_bytes = b"name,formula\nUO2,UN\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("new.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 1

    from sqlalchemy import select

    stmt = select(Material)
    result = (await db_session.execute(stmt)).scalars().all()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_batch_import_empty_csv(async_client: AsyncClient) -> None:
    """CSV with only a header row should import 0 rows."""
    csv_bytes = b"name,formula\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("empty.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 0
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_batch_import_empty_json(async_client: AsyncClient) -> None:
    """Empty JSON array should import 0 rows."""
    json_bytes = b"[]"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("empty.json", json_bytes, "application/json")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["imported"] == 0


@pytest.mark.asyncio
async def test_batch_import_result_persisted(
    async_client: AsyncClient, db_session
) -> None:
    """Imported materials should be queryable after import."""
    csv_bytes = b"name,formula\nPersistedMat,PM1\n"
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("persist.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200

    list_resp = await async_client.get("/api/v1/materials")
    items = list_resp.json()["data"]["items"]
    assert any(m["name"] == "PersistedMat" for m in items)


@pytest.mark.asyncio
async def test_batch_import_concurrency_rejects_when_busy(
    async_client: AsyncClient,
) -> None:
    """Concurrent batch import should return 409 Conflict."""
    csv_bytes = b"name,formula\nUO2,UO2\n"

    # ASGITransport sets scope["client"] = ("127.0.0.1", 123) by default
    test_ip = "127.0.0.1"
    lock = await get_import_lock(test_ip)
    await lock.acquire()

    try:
        response = await async_client.post(
            "/api/v1/materials/batch-import",
            files={"file": ("test.csv", csv_bytes, "text/csv")},
        )
        assert response.status_code == 409, (
            f"Expected 409 but got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["error_code"] == "CONFLICT"
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_batch_import_413_has_error_code(async_client: AsyncClient) -> None:
    """File too large should return 413 with REQUEST_ENTITY_TOO_LARGE code."""
    large_content = b"x" * (BATCH_IMPORT_MAX_SIZE_MB * 1024 * 1024 + 1)
    response = await async_client.post(
        "/api/v1/materials/batch-import",
        files={"file": ("big.csv", large_content, "text/csv")},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error_code"] == "REQUEST_ENTITY_TOO_LARGE"
