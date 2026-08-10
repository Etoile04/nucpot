"""PropertyNormalizer pipeline step — NFM-2684 / NFM-2677-B2.

Normalizes extracted material properties to standard SI units and
canonical term names.  Conforms to the
:class:`~nfm_db.pipeline.extraction_step.ExtractionStep` Protocol.

Accepts entity chunks from Step 3 (``entity_kind ==
"property_value"`` or ``"unit"``), converts values to SI, and emits
``chunk_type = "property_normalized"`` with full audit metadata.

Idempotent: normalizing an already-normalized chunk is a no-op.
Unknown units pass through unchanged with a warning — never silent
data loss.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from nfm_db.pipeline.extraction_step import (
    StepContext,
    StepResult,
    validate_step_type,
)
from nfm_db.services.extraction_normalizer import UnitConverter

logger = logging.getLogger(__name__)

__all__ = ["PropertyNormalizer"]

# ---------------------------------------------------------------------------
# Energy conversion (not yet in UnitConverter)
# ---------------------------------------------------------------------------

_EV_TO_JOULES: Final[float] = 1.602_176_634e-19

_ENERGY_CONVERSION_FACTORS: Final[dict[str, float]] = {
    "J": 1.0,
    "eV": _EV_TO_JOULES,
    "keV": _EV_TO_JOULES * 1e3,
    "MeV": _EV_TO_JOULES * 1e6,
    "GeV": _EV_TO_JOULES * 1e9,
}

_ENERGY_ALIASES: Final[dict[str, str]] = {
    "j": "J",
    "ev": "eV",
    "kev": "keV",
    "mev": "MeV",
    "gev": "GeV",
}


# ---------------------------------------------------------------------------
# Canonical SI units per property type
# ---------------------------------------------------------------------------

_PROPERTY_SI_UNITS: Final[dict[str, str]] = {
    "temperature": "K",
    "pressure": "Pa",
    "stress": "Pa",
    "energy": "J",
    "thermal_conductivity": "W/(m-K)",
    "diffusion_coefficient": "m2/s",
    "density": "kg/m3",
    "specific_heat": "J/(kg-K)",
    "thermal_expansion": "1/K",
    "youngs_modulus": "Pa",
    "shear_modulus": "Pa",
    "bulk_modulus": "Pa",
    "yield_strength": "Pa",
    "ultimate_tensile_strength": "Pa",
}


# ---------------------------------------------------------------------------
# PropertyNormalizer
# ---------------------------------------------------------------------------


class PropertyNormalizer:
    """Pipeline step that normalizes extracted property values.

    Conforms to the :class:`~nfm_db.pipeline.extraction_step.ExtractionStep`
    Protocol.  Takes ``mapped_chunks`` from Step 3 context, filters
    for property-related entity kinds, converts units to SI, and emits
    normalized chunks.

    Idempotent: already-normalized chunks (``chunk_type ==
    "property_normalized"``) pass through unchanged.
    Unknown units pass through with a warning metadata entry — the
    value is never silently discarded.
    """

    step_type: str = "map"
    input_keys: tuple[str, ...] = ("mapped_chunks",)

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        """Normalize property-value chunks from the pipeline context.

        Parameters
        ----------
        context:
            Immutable pipeline context; reads ``mapped_chunks`` key.
        **kwargs:
            Forward-compatible extensions (e.g. ``session``).
        """
        validate_step_type(self.step_type)

        raw_chunks: list[dict[str, Any]] = context.get("mapped_chunks", [])
        if not raw_chunks:
            return StepResult(
                produced_keys=(),
                outputs={"normalized_chunks": []},
            )

        normalized_chunks: list[dict[str, Any]] = []
        for chunk in raw_chunks:
            processed = _process_chunk(chunk)
            if processed is not None:
                normalized_chunks.append(processed)

        return StepResult(
            produced_keys=("normalized_chunks",),
            outputs={"normalized_chunks": normalized_chunks},
        )


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, easy to test)
# ---------------------------------------------------------------------------


def _process_chunk(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single chunk.

    Returns ``None`` for non-property entity kinds.
    Returns the chunk unchanged for already-normalized chunks
    (idempotency).
    """
    entity_kind = chunk.get("entity_kind", "")

    # Non-property chunks: skip
    if entity_kind not in ("property_value", "unit"):
        return None

    # Idempotency: already normalized
    if chunk.get("chunk_type") == "property_normalized":
        return chunk

    property_name = chunk.get("property_name", "")
    raw_value = chunk.get("value", 0.0)
    raw_unit = chunk.get("unit", "")

    normalized_value, normalized_unit, conversion_factor = _convert(
        property_name, raw_value, raw_unit
    )

    # Build audit metadata
    metadata: dict[str, Any] = {
        "original_unit": raw_unit,
        "normalized_unit": normalized_unit,
        "conversion_factor": conversion_factor,
    }

    if conversion_factor is None:
        metadata["warning"] = (
            f"Unknown unit '{raw_unit}' for property "
            f"'{property_name}'; value passed through unchanged."
        )
        logger.warning(
            "Unknown unit '%s' for property '%s'; passthrough",
            raw_unit,
            property_name,
        )

    return {
        **chunk,
        "chunk_type": "property_normalized",
        "step_order": 4,
        "value": normalized_value,
        "unit": normalized_unit,
        "original_value": raw_value,
        "original_unit": raw_unit,
        "metadata": metadata,
    }


def _convert(
    property_name: str,
    value: float,
    unit: str,
) -> tuple[float, str, float | None]:
    """Convert a property value to its canonical SI unit.

    Returns:
        ``(normalized_value, si_unit, conversion_factor)``.
        ``conversion_factor`` is ``None`` when the unit is unknown
        (passthrough with warning).
    """
    prop_key = property_name.strip().lower()

    # Energy conversion (not in existing UnitConverter)
    if prop_key == "energy":
        return _convert_energy(value, unit)

    # Delegate to existing UnitConverter for temperature, pressure,
    # stress, thermal_conductivity, diffusion_coefficient
    normalized_value, result_unit = UnitConverter.convert(
        prop_key, value, unit
    )

    # Same unit → passthrough, but check if it's actually the
    # canonical SI unit for this property.  If not, signal
    # unknown-unit so the caller can emit a warning.
    if result_unit == unit.strip() and normalized_value == value:
        canonical = _PROPERTY_SI_UNITS.get(prop_key)
        if canonical and result_unit.lower() != canonical.lower():
            return value, result_unit, None
        return value, result_unit, 1.0

    # Compute effective factor
    if unit != result_unit and value != 0:
        factor = normalized_value / value
    elif unit != result_unit:
        factor = 0.0  # zero-value with unit change
    else:
        factor = 1.0  # no change

    return normalized_value, result_unit, factor


def _convert_energy(
    value: float,
    unit: str,
) -> tuple[float, str, float | None]:
    """Convert energy to Joules.

    Supports: J, eV, keV, MeV, GeV (case-insensitive).
    """
    key = unit.strip().lower()
    resolved = _ENERGY_ALIASES.get(key, unit.strip())
    factor = _ENERGY_CONVERSION_FACTORS.get(resolved)

    if factor is None:
        return value, unit.strip(), None

    return value * factor, "J", factor
