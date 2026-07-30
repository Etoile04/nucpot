"""DNA identification service (NFM-2025).

Generates, validates, and persists data DNA records — the
cryptographic identity (UUIDv4 + SHA-256 + SM3) for data
deduplication in the 1+N architecture.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_dna import DataDna
from nfm_db.utils.sm3 import sm3 as sm3_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DNARecord:
    """Immutable DNA fingerprint result."""

    record_type: str
    record_id: uuid.UUID
    dna_uuid: uuid.UUID
    sha256_hash: str
    sm3_hash: str


class DNAMissingError(Exception):
    """Raised when a required DNA binding does not exist."""


class DNAService:
    """Generates and manages data DNA records."""

    @staticmethod
    def generate_dna(record_type: str, record_id: uuid.UUID, content: bytes) -> DNARecord:
        """Generate a DNA fingerprint for the given content.

        Returns an immutable DNARecord with UUIDv4, SHA-256, and SM3.
        """
        dna_uuid = uuid.uuid4()
        sha256 = hashlib.sha256(content).hexdigest()
        sm3 = sm3_hash(content)
        return DNARecord(
            record_type=record_type,
            record_id=record_id,
            dna_uuid=dna_uuid,
            sha256_hash=sha256,
            sm3_hash=sm3,
        )

    @staticmethod
    def validate_dna_uuid(dna_uuid: str) -> bool:
        """Check whether a string is a valid UUIDv4."""
        try:
            val = uuid.UUID(dna_uuid)
            return val.version == 4
        except (ValueError, AttributeError, TypeError):
            return False

    @staticmethod
    async def get_dna(
        db: AsyncSession,
        record_type: str,
        record_id: uuid.UUID,
    ) -> DataDna | None:
        """Look up an existing DNA record by record type and ID."""
        stmt = select(DataDna).where(
            DataDna.record_type == record_type,
            DataDna.record_id == record_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def persist_dna(
        db: AsyncSession,
        dna: DNARecord,
        classification_level: uuid.UUID,
    ) -> DataDna:
        """Persist a DNA record to the database.

        Raises IntegrityError on duplicate dna_uuid.
        """
        record = DataDna(
            record_type=dna.record_type,
            record_id=dna.record_id,
            dna_uuid=dna.dna_uuid,
            sha256_hash=dna.sha256_hash,
            sm3_hash=dna.sm3_hash,
            classification_level=classification_level,
        )
        db.add(record)
        await db.flush()
        return record

    @staticmethod
    async def ensure_dna_binding(
        db: AsyncSession,
        record_type: str,
        record_id: uuid.UUID,
    ) -> None:
        """Service-layer gate: raise DNAMissingError if no DNA exists."""
        existing = await DNAService.get_dna(db, record_type, record_id)
        if existing is None:
            raise DNAMissingError(
                f"No DNA record for {record_type!r}/{record_id}"
            )


__all__ = ["DNARecord", "DNAMissingError", "DNAService"]
