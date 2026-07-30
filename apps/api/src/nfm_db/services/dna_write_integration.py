"""DNA-aware write-path wrappers (NFM-2025).

Thin wrappers around the existing create_* service functions that
auto-generate and persist a DNA record before committing.

This is the service-layer gate mandated by Contract §3.1.2.
"""
from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.schemas.material import MaterialCreate, MaterialResponse
from nfm_db.schemas.property import (
    PropertyMeasurementCreate,
    PropertyMeasurementResponse,
)
from nfm_db.schemas.source import DataSourceCreate, DataSourceResponse
from nfm_db.services.dna_service import DNAService
from nfm_db.services.material_service import create_material as _raw_create_material
from nfm_db.services.property_service import (
    create_measurement as _raw_create_measurement,
)
from nfm_db.services.source_service import create_source as _raw_create_source

logger = logging.getLogger(__name__)


async def create_source_with_dna(
    db: AsyncSession,
    data: DataSourceCreate,
    classification_level: uuid.UUID,
) -> DataSourceResponse:
    """Create a data source and auto-bind a DNA record (AC-2)."""
    result = await _raw_create_source(db, data)
    await _persist_dna(
        db,
        record_type="data_source",
        record_id=result.id,
        content=data.model_dump_json().encode(),
        classification_level=classification_level,
    )
    return result


async def create_measurement_with_dna(
    db: AsyncSession,
    data: PropertyMeasurementCreate,
    classification_level: uuid.UUID,
) -> PropertyMeasurementResponse:
    """Create a property measurement and auto-bind a DNA record (AC-2)."""
    result = await _raw_create_measurement(db, data)
    await _persist_dna(
        db,
        record_type="property_measurement",
        record_id=result.id,
        content=data.model_dump_json().encode(),
        classification_level=classification_level,
    )
    return result


async def create_material_with_dna(
    db: AsyncSession,
    data: MaterialCreate,
    classification_level: uuid.UUID,
) -> MaterialResponse:
    """Create a material and auto-bind a DNA record (AC-2)."""
    result = await _raw_create_material(db, data)
    await _persist_dna(
        db,
        record_type="material",
        record_id=result.id,
        content=data.model_dump_json().encode(),
        classification_level=classification_level,
    )
    return result


async def _persist_dna(
    db: AsyncSession,
    *,
    record_type: str,
    record_id: uuid.UUID,
    content: bytes,
    classification_level: uuid.UUID,
) -> None:
    """Generate and persist a DNA record for the given entity."""
    dna = DNAService.generate_dna(record_type, record_id, content)
    await DNAService.persist_dna(db, dna, classification_level)
    logger.info(
        "DNA bound: type=%s record=%s dna=%s",
        record_type,
        record_id,
        dna.dna_uuid,
    )


__all__ = [
    "create_source_with_dna",
    "create_measurement_with_dna",
    "create_material_with_dna",
]
