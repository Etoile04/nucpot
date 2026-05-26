"""Tests for elastic constants and vacancy formation energy."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ase.calculators.lj import LennardJones


def test_compute_elastic_mo():
    """Compute elastic constants for Mo using LJ calculator."""
    from workers.elastic import compute_elastic_constants

    calc = LennardJones()
    result = compute_elastic_constants("Mo", calc, phase="BCC")

    assert "C11" in result
    assert "C12" in result
    assert "C44" in result
    assert abs(result["C11"]) > 0
    assert abs(result["C12"]) > 0
    assert abs(result["C44"]) > 0


def test_compute_elastic_nb():
    """Compute elastic constants for Nb."""
    from workers.elastic import compute_elastic_constants

    calc = LennardJones()
    result = compute_elastic_constants("Nb", calc, phase="BCC")
    assert abs(result["C11"]) > 0
    assert abs(result["C12"]) > 0
    assert abs(result["C44"]) > 0


def test_compute_vacancy_formation_energy():
    """Compute vacancy formation energy for Mo using LJ calculator."""
    from workers.vacancy import compute_vacancy_formation_energy

    calc = LennardJones()
    e_vac = compute_vacancy_formation_energy(
        "Mo", calc, phase="BCC", supercell_size=(2, 2, 2)
    )
    assert abs(e_vac) < 50, f"Vacancy formation energy unreasonably large: {e_vac}"


def test_returns_gpa():
    """Elastic constants should be in GPa range."""
    from workers.elastic import compute_elastic_constants

    calc = LennardJones()
    result = compute_elastic_constants("Mo", calc, phase="BCC")
    # Even with LJ, values should be in 0-1000 GPa range
    for key in ("C11", "C12", "C44"):
        assert -500 < result[key] < 500, f"{key}={result[key]} out of range"
