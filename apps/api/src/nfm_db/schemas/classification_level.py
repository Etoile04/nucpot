"""Pydantic schemas with classification_level enforcement (NFM-2026).

Adds classified variants of DataDnaCreate and UploadSessionCreate
that require a valid classification_level field.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nfm_db.models.classification_level import ClassificationLevelEnum

_VALID_LABELS = ClassificationLevelEnum.labels()
_CLASSIFICATION_ERROR = (
    f"classification_level must be one of {{'，'.join(sorted(_VALID_LABELS))}}"
)


def _validate_classification_level(v: str | None) -> str:
    """Validator: reject None and invalid labels."""
    if v is None or v not in _VALID_LABELS:
        raise ValueError(_CLASSIFICATION_ERROR)
    return v


class ClassifiedDataDnaCreate(BaseModel):
    """Payload for creating a data DNA record WITH required classification.

    AC-1: classification_level is required.
    AC-2: Only valid labels accepted.
    AC-4: classification level stored alongside DNA.
    """

    model_config = ConfigDict(extra="forbid")

    record_type: str = Field(min_length=1, max_length=100)
    record_id: uuid.UUID
    dna_uuid: uuid.UUID
    sha256_hash: str = Field(min_length=64, max_length=64)
    sm3_hash: str | None = Field(default=None, max_length=64)
    classification_level: str = Field(
        min_length=1,
        description="Contract security label (非密, 内部, 秘密).",
    )

    validate_classification_level_ = field_validator("classification_level")(
        _validate_classification_level
    )


class ClassifiedUploadSessionCreate(BaseModel):
    """Payload for creating an upload session WITH required classification.

    AC-1: classification_level is required.
    AC-2: Only valid labels accepted.
    """

    model_config = ConfigDict(extra="forbid")

    resource_node_id: uuid.UUID
    file_name: str = Field(min_length=1, max_length=500)
    total_size: int = Field(gt=0)
    chunk_size: int = Field(gt=0)
    total_chunks: int = Field(gt=0)
    classification_level: str = Field(
        min_length=1,
        description="Contract security label (非密, 内部, 秘密).",
    )

    validate_classification_level_ = field_validator("classification_level")(
        _validate_classification_level
    )


__all__ = [
    "ClassifiedDataDnaCreate",
    "ClassifiedUploadSessionCreate",
]
