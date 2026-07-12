"""Property catalog: categories, standard name mapping, and unit normalization.

Ported from v4 property_catalog.md (NFM-524).

Exports:
    PropertyCategory  - 11-category enum for property classification
    STANDARD_PROPERTIES - dict of alias (lowered) → standard Chinese name
    UnitNormalizer   - class that normalizes unit strings from JSON config
"""

from __future__ import annotations

import json
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
# STANDARD_PROPERTIES Mapping (v4 §4)
# ---------------------------------------------------------------------------

_raw_aliases: dict[str, str] = _load_config()["property_aliases"]


def _build_case_insensitive_mapping(
    raw: dict[str, str],
) -> dict[str, str]:
    """Create a case-insensitive alias→standard_name dict."""
    return {alias.lower(): name for alias, name in raw.items()}


STANDARD_PROPERTIES: dict[str, str] = _build_case_insensitive_mapping(_raw_aliases)

STANDARD_PROPERTIES


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
            key.lower(): value
            for key, value in config.get("unit_normalization", {}).items()
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
