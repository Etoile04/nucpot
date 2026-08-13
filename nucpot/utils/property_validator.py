"""Property name validator for the nucpot data ingestion pipeline."""

from __future__ import annotations

_DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "density",
        "thermal_conductivity",
        "melting_point",
        "tensile_strength",
        "youngs_modulus",
        "poissons_ratio",
        "specific_heat",
    }
)


def validate_property_name(
    name: str,
    allowlist: set[str] | None = None,
) -> bool:
    """Check whether *name* is an accepted property name.

    Args:
        name: Property name to validate. Must be a non-empty string.
        allowlist: Optional set of allowed names. When provided it
            **replaces** the built-in default allowlist entirely.

    Returns:
        ``True`` if *name* (lowercased) is present in the active allowlist.

    Raises:
        ValueError: If *name* is empty or not a string.
    """
    if not isinstance(name, str):
        raise ValueError(f"Property name must be a string, got {type(name).__name__}")

    if not name:
        raise ValueError("Property name must not be empty")

    effective = frozenset(allowlist) if allowlist is not None else _DEFAULT_ALLOWLIST
    return name.lower() in effective
