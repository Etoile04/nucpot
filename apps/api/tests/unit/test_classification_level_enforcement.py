"""Unit tests for classification level enforcement (NFM-2026).

Covers:
  - ClassificationLevelEnum values and validation
  - Pydantic pre-write validation (missing + invalid labels)
  - DB CHECK constraint on data_dna and upload_sessions
  - Service-level guard enforcement
  - DNA + classification_level linkage (AC-4)

TDD RED phase — these tests define the acceptance criteria.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from nfm_db.models.classification_level import ClassificationLevelEnum
from nfm_db.schemas.classification_level import (
    ClassifiedDataDnaCreate,
    ClassifiedUploadSessionCreate,
)

# ========================================================================
# 1. ClassificationLevelEnum
# ========================================================================


class TestClassificationLevelEnum:
    """AC-2 foundation: enum must carry exact Chinese labels."""

    def test_enum_has_three_members(self) -> None:
        assert len(ClassificationLevelEnum) == 3

    def test_enum_unclassified_value(self) -> None:
        assert ClassificationLevelEnum.UNCLASSIFIED.value == "非密"

    def test_enum_internal_value(self) -> None:
        assert ClassificationLevelEnum.INTERNAL.value == "内部"

    def test_enum_secret_value(self) -> None:
        assert ClassificationLevelEnum.SECRET.value == "秘密"

    def test_enum_values_are_strings(self) -> None:
        for member in ClassificationLevelEnum:
            assert isinstance(member.value, str)

    def test_enum_label_set(self) -> None:
        """Convenience set for CHECK constraint and validator."""
        labels = ClassificationLevelEnum.labels()
        assert labels == {"非密", "内部", "秘密"}


# ========================================================================
# 2. Pydantic pre-write validation
# ========================================================================


class TestPydanticPreWriteValidation:
    """AC-1: missing classification_level fails.
    AC-2: invalid labels rejected at Pydantic layer."""

    def test_create_dna_without_classification_level_rejected(self) -> None:
        """AC-1: Writing data without classification_level fails."""
        with pytest.raises(ValidationError, match="classification_level"):
            ClassifiedDataDnaCreate(
                record_type="material",
                record_id=uuid.uuid4(),
                dna_uuid=uuid.uuid4(),
                sha256_hash="a" * 64,
            )

    def test_create_dna_with_invalid_label_rejected(self) -> None:
        """AC-2: Invalid classification labels rejected at Pydantic layer."""
        with pytest.raises(ValidationError, match="classification_level"):
            ClassifiedDataDnaCreate(
                record_type="material",
                record_id=uuid.uuid4(),
                dna_uuid=uuid.uuid4(),
                sha256_hash="a" * 64,
                classification_level="top_secret",
            )

    def test_create_dna_with_valid_label_accepted(self) -> None:
        """Valid label passes validation."""
        obj = ClassifiedDataDnaCreate(
            record_type="material",
            record_id=uuid.uuid4(),
            dna_uuid=uuid.uuid4(),
            sha256_hash="a" * 64,
            classification_level="非密",
        )
        assert obj.classification_level == "非密"

    def test_create_dna_all_valid_labels_accepted(self) -> None:
        """All three valid labels pass."""
        for label in ClassificationLevelEnum.labels():
            obj = ClassifiedDataDnaCreate(
                record_type="material",
                record_id=uuid.uuid4(),
                dna_uuid=uuid.uuid4(),
                sha256_hash="a" * 64,
                classification_level=label,
            )
            assert obj.classification_level == label

    def test_create_upload_without_classification_level_rejected(self) -> None:
        """AC-1: Upload session also requires classification_level."""
        with pytest.raises(ValidationError, match="classification_level"):
            ClassifiedUploadSessionCreate(
                resource_node_id=uuid.uuid4(),
                file_name="test.csv",
                total_size=1024,
                chunk_size=512,
                total_chunks=2,
            )

    def test_create_upload_with_invalid_label_rejected(self) -> None:
        """AC-2: Invalid label on upload session."""
        with pytest.raises(ValidationError, match="classification_level"):
            ClassifiedUploadSessionCreate(
                resource_node_id=uuid.uuid4(),
                file_name="test.csv",
                total_size=1024,
                chunk_size=512,
                total_chunks=2,
                classification_level="机密",
            )

    def test_empty_string_label_rejected(self) -> None:
        """Empty string is not a valid label."""
        with pytest.raises(ValidationError, match="classification_level"):
            ClassifiedDataDnaCreate(
                record_type="material",
                record_id=uuid.uuid4(),
                dna_uuid=uuid.uuid4(),
                sha256_hash="a" * 64,
                classification_level="",
            )


# ========================================================================
# 3. DB CHECK constraint (post-write safety net)
# ========================================================================


class TestDbCheckConstraint:
    """AC-3: DB CHECK constraint prevents invalid values even if Pydantic bypassed.

    Uses a raw SQL INSERT to bypass the ORM layer entirely.
    """

    @pytest.fixture()
    def db_engine(self):
        """SQLite in-memory engine for CHECK constraint testing.

        The constraint is attached via __table_args__ so SQLAlchemy
        compiles it to proper SQL during CREATE TABLE.
        """
        from sqlalchemy import Column, String, Uuid, create_engine
        from sqlalchemy.orm import declarative_base

        from nfm_db.models.classification_level import (
            classification_check_constraint,
        )

        engine = create_engine("sqlite:///:memory:")
        base = declarative_base()

        class TestTable(base):
            __tablename__ = "test_cl"
            __table_args__ = (classification_check_constraint("cl"),)
            id = Column(Uuid(as_uuid=True), primary_key=True)
            cl = Column(String(50), nullable=False)

        with engine.begin() as conn:
            base.metadata.create_all(conn)

        yield engine
        engine.dispose()

    def test_valid_label_passes_check(self, db_engine) -> None:
        """Valid Chinese labels pass the CHECK constraint."""
        from sqlalchemy import text
        with db_engine.begin() as conn:
            for label in ClassificationLevelEnum.labels():
                conn.execute(
                    text("INSERT INTO test_cl (id, cl) VALUES (:id, :cl)"),
                    {"id": str(uuid.uuid4()), "cl": label},
                )

    def test_invalid_label_fails_check(self, db_engine) -> None:
        """Invalid label is rejected by DB CHECK constraint."""
        from sqlalchemy import text
        with db_engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text("INSERT INTO test_cl (id, cl) VALUES (:id, :cl)"),
                    {"id": str(uuid.uuid4()), "cl": "机密"},
                )

    def test_null_label_fails_check(self, db_engine) -> None:
        """NULL classification_level rejected by NOT NULL + CHECK."""
        from sqlalchemy import text
        with db_engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text("INSERT INTO test_cl (id, cl) VALUES (:id, NULL)"),
                    {"id": str(uuid.uuid4())},
                )


# ========================================================================
# 4. Service-level guard
# ========================================================================


class TestServiceGuard:
    """Service-level enforcement that classification_level is present."""

    def test_guard_rejects_missing(self) -> None:
        from nfm_db.services.classification_guard import require_classification_level

        with pytest.raises(ValueError, match="classification_level"):
            require_classification_level(None)

    def test_guard_rejects_invalid(self) -> None:
        from nfm_db.services.classification_guard import require_classification_level

        with pytest.raises(ValueError, match="classification_level"):
            require_classification_level("机密")

    def test_guard_accepts_valid(self) -> None:
        from nfm_db.services.classification_guard import require_classification_level

        for label in ClassificationLevelEnum.labels():
            result = require_classification_level(label)
            assert result == label

    def test_guard_accepts_enum_member(self) -> None:
        from nfm_db.services.classification_guard import require_classification_level

        result = require_classification_level(ClassificationLevelEnum.UNCLASSIFIED)
        assert result == "非密"
