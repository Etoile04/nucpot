"""Tests for lattice constant calculation."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ase.calculators.lj import LennardJones


def test_build_bulk_metal():
    """ASE can build bulk structures for nuclear metals."""
    from runners.ase_runner import build_crystal

    for element, phase in [("Mo", "BCC"), ("U", "BCC"), ("Nb", "BCC")]:
        atoms = build_crystal(element, phase)
        assert len(atoms) > 0
        assert atoms.get_volume() > 0

    atoms = build_crystal("Zr", "HCP")
    assert len(atoms) > 0


def test_compute_lattice_metal():
    """Compute lattice constant for Mo using LJ calculator."""
    from workers.lattice import compute_lattice_constant

    calc = LennardJones()
    a_mo = compute_lattice_constant("Mo", calc, phase="BCC")
    assert 2.0 < a_mo < 6.0, f"Mo lattice constant {a_mo} out of reasonable range"


def test_compute_lattice_nb():
    """Compute lattice constant for Nb using LJ."""
    from workers.lattice import compute_lattice_constant

    calc = LennardJones()
    a_nb = compute_lattice_constant("Nb", calc, phase="BCC")
    assert 2.0 < a_nb < 6.0, f"Nb lattice constant {a_nb} out of reasonable range"


def test_compute_lattice_zr():
    """Compute lattice constant for Zr (HCP)."""
    from workers.lattice import compute_lattice_constant

    calc = LennardJones()
    a_zr = compute_lattice_constant("Zr", calc, phase="HCP")
    assert 2.0 < a_zr < 6.0, f"Zr lattice constant {a_zr} out of reasonable range"


def test_compute_returns_float():
    """Lattice constant computation returns a float."""
    from workers.lattice import compute_lattice_constant

    calc = LennardJones()
    result = compute_lattice_constant("Mo", calc, phase="BCC")
    assert isinstance(result, float)
