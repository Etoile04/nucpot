"""Service-level guard for classification_level enforcement (NFM-2026).

Provides ``require_classification_level`` which validates the label
before any write operation.  This is the service-layer safety net
that runs *after* Pydantic validation.
"""
from __future__ import annotations

from nfm_db.models.classification_level import ClassificationLevelEnum

_VALID_LABELS = ClassificationLevelEnum.labels()


def require_classification_level(value: str | ClassificationLevelEnum | None) -> str:
    """Validate and return the classification level string.

    Raises:
        ValueError: If value is None or not a valid label.

    This function accepts both the enum member and raw string
    so callers can pass either form.
    """
    if value is None:
        raise ValueError(
            "classification_level is required for all write operations. "
            f"Valid values: {sorted(_VALID_LABELS)}"
        )

    label = value.value if isinstance(value, ClassificationLevelEnum) else value
    if label not in _VALID_LABELS:
        raise ValueError(
            f"Invalid classification_level: {label!r}. "
            f"Valid values: {sorted(_VALID_LABELS)}"
        )
    return label


__all__ = ["require_classification_level"]
