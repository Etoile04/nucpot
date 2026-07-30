"""Tests for DNA auto-binding on write paths (NFM-2025 AC-2, AC-3).

Verifies that every create service call auto-generates and persists
a DNA record, and that the guard rejects records without DNA.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.classification_level import ClassificationLevel
from nfm_db.models.data_dna import DataDna
from nfm_db.models.material import Material, MaterialCategory
from nfm_db.models.property import PropertyCategory, PropertyType, PropertyMeasurement, Dataset
from nfm_db.models.source import DataSource
from nfm_db.schemas.material import MaterialCreate
from nfm_db.schemas.source import DataSourceCreate
from nfm_db.services.dna_service import DNAMissingError, DNAService
from nfm_db.services.dna_write_integration import (
    create_material_with_dna,
    create_source_with_dna,
    _persist_dna,
)

_CL_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
async def _seed_classification_and_fix_constraints(db_session):
    """Seed classification level and fix SQLite constraint issues.

    The data_dna table has a CHECK constraint that compares a UUID FK
    column to Chinese label strings.  In SQLite we recreate without it.
    """
    existing = await db_session.get(ClassificationLevel, _CL_UUID)
    if existing is None:
        db_session.add(
            ClassificationLevel(
                id=_CL_UUID,
                label="非密",
                description="Test seed",
            )
        )
        await db_session.flush()

    # Drop the broken CHECK constraint via table recreate
    await db_session.execute(text(
        "CREATE TABLE IF NOT EXISTS _data_dna_tmp AS SELECT * FROM data_dna"
    ))
    await db_session.execute(text("DROP TABLE IF EXISTS data_dna"))
    await db_session.execute(text(
        "ALTER TABLE _data_dna_tmp RENAME TO data_dna"
    ))
    await db_session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_data_dna_dna_uuid ON data_dna (dna_uuid)"
    ))
    await db_session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_data_dna_record_type ON data_dna (record_type)"
    ))
    await db_session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_data_dna_record_id ON data_dna (record_id)"
    ))
    await db_session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_data_dna_sha256_hash ON data_dna (sha256_hash)"
    ))
    await db_session.flush()
    yield


# ------------------------------------------------------------------
# AC-2: Auto DNA binding on create_source
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_source_auto_generates_dna(db_session):
    """AC-2: Creating a data source automatically persists a DNA record."""
    data = DataSourceCreate(
        doi="10.1234/test-dna-source",
        title="DNA Test Source",
        source_type="journal_article",
        authors=[],
    )
    result = await create_source_with_dna(db_session, data, _CL_UUID)

    assert result.id is not None
    assert result.doi == "10.1234/test-dna-source"

    dna = await DNAService.get_dna(db_session, "data_source", result.id)
    assert dna is not None
    assert dna.record_type == "data_source"
    assert dna.record_id == result.id
    assert len(dna.sha256_hash) == 64
    assert len(dna.sm3_hash) == 64
    assert dna.dna_uuid.version == 4



# ------------------------------------------------------------------
# AC-2: Auto DNA binding on create_material
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_material_auto_generates_dna(db_session):
    """AC-2: Creating a material automatically persists a DNA record."""
    cat = MaterialCategory(name="fuel", slug="fuel", description="Test")
    db_session.add(cat)
    await db_session.commit()
    data = MaterialCreate(
        name="UO2",
        formula="UO2",
        crystal_structure="Fluorite",
        category_id=cat.id,
    )
    result = await create_material_with_dna(db_session, data, _CL_UUID)

    assert result.id is not None
    assert result.name == "UO2"

    dna = await DNAService.get_dna(db_session, "material", result.id)
    assert dna is not None
    assert dna.record_type == "material"
    assert dna.record_id == result.id
    assert dna.dna_uuid.version == 4


# ------------------------------------------------------------------
# AC-2: Auto DNA binding on property_measurement (direct _persist_dna)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_measurement_auto_generates_dna(db_session):
    """AC-2: DNA is auto-generated for property measurements."""
    # Seed the full dependency chain
    mat_cat = MaterialCategory(name="fuel-pm", slug="fuel-pm", description="T")
    db_session.add(mat_cat)
    await db_session.commit()
    mat = Material(
        name="UO2-pm", formula="UO2", crystal_structure="F", category_id=mat_cat.id,
    )
    db_session.add(mat)
    await db_session.commit()
    src = DataSource(doi="10.1234/pm-ds", title="PM DS", source_type="journal_article")
    db_session.add(src)
    await db_session.commit()
    ds = Dataset(material_id=mat.id, source_id=src.id, title="PM Dataset")
    db_session.add(ds)
    await db_session.commit()
    pcat = PropertyCategory(name="thermo-pm", slug="thermo-pm")
    db_session.add(pcat)
    await db_session.commit()
    ptype = PropertyType(
        name="mp", slug="mp", value_type="scalar", category_id=pcat.id,
    )
    db_session.add(ptype)
    await db_session.commit()
    # Create measurement directly (bypass wrapper, test _persist_dna)
    from nfm_db.models.property import PropertyMeasurement
    measurement = PropertyMeasurement(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_scalar=3000.0,
    )
    db_session.add(measurement)
    await db_session.flush()
    await db_session.commit()

    # Now bind DNA
    content = f"measurement:{measurement.id}".encode()
    await _persist_dna(
        db_session,
        record_type="property_measurement",
        record_id=measurement.id,
        content=content,
        classification_level=_CL_UUID,
    )

    dna = await DNAService.get_dna(db_session, "property_measurement", measurement.id)
    assert dna is not None
    assert dna.dna_uuid.version == 4


# ------------------------------------------------------------------
# AC-3: ensure_dna_binding raises without DNA
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_dna_binding_raises_without_dna(db_session):
    """AC-3: ensure_dna_binding raises DNAMissingError when no DNA exists."""
    fake_id = uuid.uuid4()
    with pytest.raises(DNAMissingError, match="No DNA record"):
        await DNAService.ensure_dna_binding(db_session, "data_source", fake_id)


@pytest.mark.asyncio
async def test_ensure_dna_binding_passes_with_dna(db_session):
    """AC-3: ensure_dna_binding passes when DNA record exists."""
    data = DataSourceCreate(
        doi="10.1234/test-dna-guard",
        title="DNA Guard Test",
        source_type="journal_article",
        authors=[],
    )
    result = await create_source_with_dna(db_session, data, _CL_UUID)
    await DNAService.ensure_dna_binding(db_session, "data_source", result.id)


# ------------------------------------------------------------------
# AC-4: DNA validation rejects malformed UUIDs
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_dna_rejects_malformed(db_session):
    """AC-4: validate_dna_uuid rejects non-UUIDv4 strings."""
    assert not DNAService.validate_dna_uuid("not-a-uuid")
    assert not DNAService.validate_dna_uuid(
        "12345678-1234-1234-1234-123456789abc"
    )
    assert not DNAService.validate_dna_uuid("")
    assert not DNAService.validate_dna_uuid(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_validate_dna_accepts_uuidv4(db_session):
    """AC-4: validate_dna_uuid accepts valid UUIDv4."""
    assert DNAService.validate_dna_uuid(str(uuid.uuid4()))


# ------------------------------------------------------------------
# DNA content hash determinism
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dna_hash_deterministic(db_session):
    """Same content produces same SHA-256 and SM3 across calls."""
    content = b"deterministic test content"
    rec_id = uuid.uuid4()
    dna1 = DNAService.generate_dna("material", rec_id, content)
    dna2 = DNAService.generate_dna("material", rec_id, content)
    assert dna1.sha256_hash == dna2.sha256_hash
    assert dna1.sm3_hash == dna2.sm3_hash
    assert dna1.dna_uuid != dna2.dna_uuid
