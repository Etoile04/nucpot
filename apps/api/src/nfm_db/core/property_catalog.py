"""Property catalog: categories, standard name mapping, and unit normalization.

Ported from v4 property_catalog.md (NFM-524).

Exports:
    PropertyCategory          - 11-category enum for property classification
    load_standard_properties  - canonical loader for the alias→standard_name
                                mapping (ontology-driven migration target).
    STANDARD_PROPERTIES       - DEPRECATED shim that resolves through
                                ``load_standard_properties`` and emits a
                                ``DeprecationWarning`` pointing callers at
                                the ontology loader. Kept for backward
                                compatibility with legacy imports
                                (``v4_mapper.py``, ``extraction_pipeline.py``,
                                ``ontology_coverage_report.py``) until each
                                migrates. See NFM-3537.
    UnitNormalizer            - class that normalizes unit strings from JSON config
"""

from __future__ import annotations

import json
import warnings
from enum import Enum
from pathlib import Path
from typing import Any, Final

# ---------------------------------------------------------------------------
# PropertyCategory Enum (v4 §3)
# ---------------------------------------------------------------------------


class PropertyCategory(str, Enum):
    """11 fixed property categories from v4 property_catalog.md §3-§5.

    First 9 are core performance categories; last 2 are supporting.
    """

    DENSITY = "密度"
    SPECIFIC_HEAT = "比热容"
    THERMAL_CONDUCTIVITY = "热传导率"
    ELASTOPLASTIC = "弹塑性模型"
    THERMAL_EXPANSION = "热膨胀"
    IRRADIATION_CREEP = "辐照蠕变"
    IRRADIATION_SWELLING = "辐照肿胀"
    CORROSION = "腐蚀"
    HARDENING = "硬化性能"
    MATERIAL_SPEC = "材料规格/组织信息"
    OTHER = "其他性能"


# ---------------------------------------------------------------------------
# Config Loading
# ---------------------------------------------------------------------------


_CONFIG_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent / "config" / "property_mapping.json"
)


def _load_config() -> dict[str, Any]:
    """Load property mapping JSON config from disk (hot-reloadable)."""
    with open(_CONFIG_PATH) as f:
        data: dict[str, Any] = json.load(f)
        return data


# ---------------------------------------------------------------------------
# STANDARD_PROPERTIES Mapping (v4 §4) — deprecated shim
# ---------------------------------------------------------------------------
#
# The hardcoded alias→standard_name mapping is preserved as a module-level
# ``__getattr__`` shim for backward compatibility (NFM-3537). New code MUST
# call ``load_standard_properties()`` (the ontology-driven canonical loader)
# directly. The shim issues a ``DeprecationWarning`` per access; legacy
# importers (``v4_mapper.py``, ``extraction_pipeline.py``,
# ``ontology_coverage_report.py``) should be migrated off this name.


def _build_case_insensitive_mapping(
    raw: dict[str, str],
) -> dict[str, str]:
    """Create a case-insensitive alias→standard_name dict."""
    return {alias.lower(): name for alias, name in raw.items()}


def load_standard_properties() -> dict[str, str]:
    """Canonical loader for the standard-properties alias mapping.

    Returns the alias (lowered) → standard Chinese name dict sourced from
    the v4 property catalog config. This is the migration target the
    NFM-3531 (NFM-2868-P0-2) effort funnels callers onto — NFM-3531-C
    will swap the underlying source to the canonical ontology payload
    without changing this signature.

    Returns:
        A fresh ``dict[str, str]`` copy of the alias mapping. A new dict
        is returned on every call so callers cannot accidentally share
        state; the cost is negligible (≤113 entries today).

    Note:
        This function does NOT emit a ``DeprecationWarning`` — it is the
        supported path. The deprecated name ``STANDARD_PROPERTIES`` is
        the one that warns (via the module-level ``__getattr__``).
    """
    raw_aliases: dict[str, str] = _load_config()["property_aliases"]
    return _build_case_insensitive_mapping(raw_aliases)


_DEPRECATION_MSG = (
    "nfm_db.core.property_catalog.STANDARD_PROPERTIES is deprecated; "
    "use the ontology loader (`load_standard_properties()`) instead."
)


def __getattr__(name: str) -> Any:
    """Module-level ``__getattr__`` that backs the deprecated shim.

    Resolves ``STANDARD_PROPERTIES`` to a fresh dict from
    ``load_standard_properties()`` while emitting a
    ``DeprecationWarning`` that names the canonical migration target.
    Any other attribute raises ``AttributeError`` so accidental typos
    surface immediately rather than silently returning ``None``.
    """
    if name == "STANDARD_PROPERTIES":
        warnings.warn(
            _DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        return load_standard_properties()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "STANDARD_PROPERTIES",  # noqa: F822 — resolved lazily via __getattr__
    "PropertyCategory",
    "UnitNormalizer",
    "load_standard_properties",
]


# ---------------------------------------------------------------------------
# UnitNormalizer (v4 §7)
# ---------------------------------------------------------------------------


class UnitNormalizer:
    """Normalizes unit strings according to v4 §7 rules.

    Rules are loaded from property_mapping.json at construction time,
    enabling hot-reload without code changes.
    """

    def __init__(self) -> None:
        self._rules: dict[str, str] = {}
        self._load_rules()

    def _load_rules(self) -> None:
        """Load unit normalization rules from JSON config."""
        config = _load_config()
        self._rules = {
            key.lower(): value for key, value in config.get("unit_normalization", {}).items()
        }

    def normalize(self, unit: str) -> str:
        """Normalize a unit string using config-driven rules.

        Args:
            unit: Raw unit string from extracted data.

        Returns:
            Normalized unit string. Unrecognized units pass through unchanged.
        """
        stripped = unit.strip()
        if not stripped:
            return stripped

        key = stripped.lower()

        # Check for multi-token patterns (e.g. "deg c", "degrees c")
        normalized = self._rules.get(key)
        if normalized is not None:
            return normalized

        # For units containing ^ (e.g. "m^2"), try matching the base pattern
        # "m2" → "m²" is already covered by single-token rules above

        return stripped
