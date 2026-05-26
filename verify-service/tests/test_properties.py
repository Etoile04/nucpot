"""Tests for property calculations using ASE EMT calculator."""

import pytest
from ase.calculators.emt import EMT
from verify_service.core.properties import PropertyCalculator


@pytest.fixture
def calc():
    """EMT calculator — fast, built into ASE, good for testing."""
    return EMT()


@pytest.fixture
def pc():
    return PropertyCalculator()


class TestLatticeConstant:
    def test_compute_al_bcc(self, pc, calc):
        """Test lattice constant for Al (FCC) with EMT."""
        result = pc.compute_lattice_constant(calc, species="Al", structure="FCC")
        assert result["property"] == "lattice_constant"
        assert result["unit"] == "angstrom"
        # EMT Al FCC lattice constant is approximately 4.05 Å
        assert 3.5 < result["value"] < 5.0

    def test_compute_cu_bcc(self, pc, calc):
        """Test lattice constant for Cu (FCC) with EMT."""
        result = pc.compute_lattice_constant(calc, species="Cu", structure="FCC")
        assert result["property"] == "lattice_constant"
        assert 3.0 < result["value"] < 4.5


class TestBulkModulus:
    def test_compute_al(self, pc, calc):
        result = pc.compute_bulk_modulus(calc, species="Al", structure="FCC")
        assert result["property"] == "bulk_modulus"
        assert result["unit"] == "GPa"
        # EMT gives reasonable bulk modulus
        assert 10 < result["value"] < 300


class TestCohesiveEnergy:
    def test_compute_al(self, pc, calc):
        result = pc.compute_cohesive_energy(calc, species="Al", structure="FCC")
        assert result["property"] == "cohesive_energy"
        assert result["unit"] == "eV/atom"
        # Cohesive energy should be positive (convention: E_atom > E_bulk)
        assert result["value"] > 0


class TestVacancyFormationEnergy:
    def test_compute_al(self, pc, calc):
        result = pc.compute_vacancy_formation_energy(
            calc, species="Al", structure="FCC"
        )
        assert result["property"] == "vacancy_formation_energy"
        assert result["unit"] == "eV"
        # Vacancy formation energy should be positive
        assert result["value"] > 0


class TestComputeAll:
    def test_all_properties(self, pc, calc):
        results = pc.compute_all(calc, species="Al", structure="FCC")
        assert "lattice_constant" in results
        assert "bulk_modulus" in results
        assert "cohesive_energy" in results
        assert "vacancy_formation_energy" in results

    def test_selected_properties(self, pc, calc):
        results = pc.compute_all(
            calc,
            properties=["lattice_constant", "bulk_modulus"],
            species="Al",
            structure="FCC",
        )
        assert len(results) == 2
        assert "lattice_constant" in results
        assert "bulk_modulus" in results
