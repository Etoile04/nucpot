"""Property catalog: categories, standard name mapping, and unit normalization.

Ported from v4 property_catalog.md (NFM-524).

Exports:
    PropertyCategory  - 11-category enum for property classification
    STANDARD_PROPERTIES - DEPRECATED shim returning ontology-derived list
                          (kept for backward compatibility; legacy callers
                          should migrate to the ontology loader).
    UnitNormalizer   - class that normalizes unit strings from JSON config

NFM-3580: STANDARD_PROPERTIES is now a thin shim. The ontology is the
single source of truth for property keys. The shim returns an empty
mapping until the ontology loader calls ``set_ontology_aliases()`` to
register the active ontology's alias→standard_name entries. All callers
should migrate to the ontology loader path; legacy imports issue a
DeprecationWarning.
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
# UnitNormalizer Config Loading (still JSON-driven — units are not ontology)
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
# NFM-3580: STANDARD_PROPERTIES backward-compat shim
# ---------------------------------------------------------------------------

_DEPRECATION_MSG = (
    "STANDARD_PROPERTIES is deprecated (NFM-3580). The ontology is the "
    "single source of truth for property keys. Migrate to the ontology "
    "loader path (e.g. extraction_prompt._build_ontology_standard_names_block) "
    "or call set_ontology_aliases() to register the active ontology's aliases. "
    "This shim will be removed in a future release."
)

# Module-level storage for the active ontology's alias→standard_name mapping.
# Populated by the ontology loader via set_ontology_aliases(); cleared by
# reset_ontology_state() for tests / hot-swap scenarios.
_active_aliases: dict[str, str] = {}


def set_ontology_aliases(aliases: dict[str, str]) -> None:
    """Register ontology-derived alias→standard_name mapping.

    The ontology loader calls this once per active ontology version to
    populate the backward-compat shim. Keys are lowercased to preserve
    the original case-insensitive lookup contract.

    Args:
        aliases: Mapping of alias (any case) → standard Chinese name.
    """
    global _active_aliases
    _active_aliases = {alias.lower(): name for alias, name in aliases.items()}


def reset_ontology_state() -> None:
    """Clear the active ontology alias mapping.

    Used by tests to isolate shim state. Production code should not
    call this; the ontology loader manages the lifecycle.
    """
    global _active_aliases
    _active_aliases = {}


# Emit the deprecation warning once at module-import time. Per PEP 565,
# this fires during interactive interpreter sessions and during pytest
# collection, alerting operators and CI to legacy callers.
warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)


class _StandardPropertiesShim(dict):
    """Read-only dict subclass over the active ontology's alias mapping.

    Issues DeprecationWarning on first access so legacy callers surface
    in logs. Returns an empty mapping until ``set_ontology_aliases()``
    registers an ontology. Inherits from ``dict`` so existing callers
    that do ``isinstance(STANDARD_PROPERTIES, dict)`` or compare to
    ``{}`` continue to function.

    Note: backing data is held in the module-level ``_active_aliases``;
    this subclass mirrors that mapping's current contents on each access
    via ``_sync()`` rather than maintaining its own state, ensuring
    ``set_ontology_aliases()`` is reflected immediately.
    """

    _warned: bool = False

    def _sync(self) -> None:
        """Mirror the active ontology alias mapping into this dict."""
        super().clear()
        super().update(_active_aliases)

    def _warn_once(self) -> None:
        # Per-class one-shot warning; avoids log spam during normal
        # dict-style iteration. Legacy callers see one warning per
        # process; tests that explicitly inspect catch_warnings get a
        # single emission on first access.
        if not _StandardPropertiesShim._warned:
            warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=3)
            _StandardPropertiesShim._warned = True

    def __getitem__(self, key: str) -> str:
        self._warn_once()
        self._sync()
        return super().__getitem__(key.lower())

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        self._warn_once()
        self._sync()
        return super().get(key.lower(), default)

    def __contains__(self, key: object) -> bool:
        self._warn_once()
        self._sync()
        if not isinstance(key, str):
            return False
        return super().__contains__(key.lower())

    def __iter__(self):
        self._warn_once()
        self._sync()
        return super().__iter__()

    def __len__(self) -> int:
        self._warn_once()
        self._sync()
        return super().__len__()

    def keys(self):  # type: ignore[override]
        self._warn_once()
        self._sync()
        return super().keys()

    def values(self):  # type: ignore[override]
        self._warn_once()
        self._sync()
        return super().values()

    def items(self):  # type: ignore[override]
        self._warn_once()
        self._sync()
        return super().items()

    def __eq__(self, other: object) -> bool:
        self._warn_once()
        self._sync()
        return super().__eq__(other)

    def __repr__(self) -> str:
        self._warn_once()
        self._sync()
        return super().__repr__()


STANDARD_PROPERTIES: _StandardPropertiesShim = _StandardPropertiesShim()


# ---------------------------------------------------------------------------
# UnitNormalizer (v4 §7) — units are NOT ontology-driven; JSON config OK
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
