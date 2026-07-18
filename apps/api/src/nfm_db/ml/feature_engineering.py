"""Physical feature engineering for NFMD ML pipeline (§5).

This module implements material science feature calculations used as inputs
to ML models for phase stability prediction, temperature prediction, and
binding energy prediction.

References:
    - 技术路线图 §5.1: Physical Feature Engineering Pipeline
    - NFM-1523: Mo equivalent calculation (pilot implementation)
"""

# Mo equivalent coefficients (atomic percent basis)
# Formula: Mo_eq = 1.0*Mo + 1.13*Nb + 2.42*V + 1.86*Ti + 1.1*Zr
MO_EQUIVALENT_COEFFICIENTS: dict[str, float] = {
    "Mo": 1.0,
    "Nb": 1.13,
    "V": 2.42,
    "Ti": 1.86,
    "Zr": 1.1,
}


def calculate_mo_equivalent(composition: dict[str, float]) -> float:
    """Calculate Mo equivalent (Mo_eq) for a given composition.

    The Mo equivalent parameter is widely used in nuclear fuel materials
    design to characterize the solid-solution strengthening effect of
    alloying elements relative to Mo.

    Formula:
        Mo_eq = 1.0 × Mo + 1.13 × Nb + 2.42 × V + 1.86 × Ti + 1.1 × Zr

    Args:
        composition: Element name to atomic percent (at.%) mapping.
            Example: {"U": 88.2, "Mo": 8.4, "Ti": 0.6, "V": 2.8}

    Returns:
        Mo equivalent value as a float. Elements not in the coefficient
        table contribute 0.0 to the sum.

    Examples:
        >>> calculate_mo_equivalent({"U": 90.0, "Mo": 10.0})
        10.0
        >>> calculate_mo_equivalent({"U": 100.0})
        0.0
        >>> round(calculate_mo_equivalent({"U": 88.2, "Mo": 8.4, "Ti": 0.6, "V": 2.8}), 3)
        16.292
    """
    return sum(
        composition.get(element, 0.0) * coefficient
        for element, coefficient in MO_EQUIVALENT_COEFFICIENTS.items()
    )
