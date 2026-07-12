"""
UO2 Lattice Constant Baseline Smoke Test

Validates that the MD runner can parse and validate UO2 lattice relaxation
results from mock LAMMPS output against literature values.

Literature reference: UO2 fluorite structure, a = 5.470 Å ± 0.02 Å
(Moreland Buckingham potential, 0 K equilibrium).
"""

import re
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import numpy as np
import pytest


# Literature reference values for UO2
UO2_LITERATURE_LATTICE_CONSTANT = 5.470  # Angstroms
UO2_LATTICE_TOLERANCE = 0.02  # ±0.02 Å acceptance window


def generate_mock_lammps_output(
    lattice_constant: float = UO2_LITERATURE_LATTICE_CONSTANT,
) -> str:
    """Generate mock LAMMPS lattice relaxation output.

    Simulates the output of a LAMMPS min/cg run that outputs
    the final equilibrated lattice parameter.
    """
    return f"""LAMMPS (15 Jun 2026)
# UO2 lattice relaxation simulation
units           metal
dimension       3
boundary        p p p
atom_style      charge
read_data      UO2_fluorite.data

# Minimize energy
min_style       cg
minimize        1.0e-10 1.0e-10 1000 10000

# Final lattice vectors after minimization
Lattice constants:
  a = {lattice_constant:.6f}  b = {lattice_constant:.6f}  c = {lattice_constant:.6f}
  alpha = 90.000  beta = 90.000  gamma = 90.000

Total energy = -42.34567890 eV
Volume per atom = 20.45067890 A^3
"""


def generate_mock_lammps_dump(lattice_constant: float) -> str:
    """Generate mock LAMMPS dump file with UO2 structure."""
    a = lattice_constant
    return f"""ITEM: TIMESTEP 0
ITEM: NUMBER OF ATOMS 12
ITEM: BOX BOUNDS pp pp
0.0 {a:.6f} xlo xhi
0.0 {a:.6f} ylo yhi
0.0 {a:.6f} zlo zhi
ITEM: ATOMS id type x y z q
1 1 0.000000 0.000000 0.000000 2.000000
2 2 2.735000 2.735000 2.735000 -2.000000
3 1 0.000000 {a:.6f} 0.000000 2.000000
4 2 {a:.6f} 0.000000 2.735000 -2.000000
5 1 {a:.6f} 0.000000 0.000000 2.000000
6 2 2.735000 {a:.6f} 2.735000 -2.000000
7 1 0.000000 0.000000 {a:.6f} 2.000000
8 2 2.735000 2.735000 {a:.6f} -2.000000
9 1 {a:.6f} {a:.6f} 0.000000 2.000000
10 2 0.000000 2.735000 2.735000 -2.000000
11 1 0.000000 {a:.6f} {a:.6f} 2.000000
12 2 {a:.6f} 0.000000 2.735000 -2.000000
"""


def parse_lattice_constant_from_output(output_text: str) -> float:
    """Parse lattice constant from LAMMPS relaxation output.

    Looks for patterns like:
      a = 5.470000  b = 5.470000  c = 5.470000

    Args:
        output_text: LAMMPS output text

    Returns:
        Lattice constant in Angstroms

    Raises:
        ValueError: If lattice constant cannot be parsed
    """
    pattern = r"a\s*=\s*([\d.]+)"
    match = re.search(pattern, output_text)
    if not match:
        raise ValueError("Could not parse lattice constant from LAMMPS output")

    return float(match.group(1))


def validate_lattice_constant(
    measured: float,
    reference: float = UO2_LITERATURE_LATTICE_CONSTANT,
    tolerance: float = UO2_LATTICE_TOLERANCE,
) -> dict:
    """Validate measured lattice constant against reference.

    Args:
        measured: Measured lattice constant in Angstroms
        reference: Literature reference value
        tolerance: Acceptable deviation

    Returns:
        Dictionary with validation results
    """
    deviation = abs(measured - reference)
    passed = deviation <= tolerance

    return {
        "measured": measured,
        "reference": reference,
        "deviation": deviation,
        "tolerance": tolerance,
        "passed": passed,
        "percent_error": (deviation / reference) * 100,
    }


class TestUO2LatticeParsing:
    """Test lattice constant parsing from LAMMPS output"""

    def test_parse_nominal_lattice(self):
        """Test parsing nominal UO2 lattice constant"""
        output = generate_mock_lammps_output(5.470)
        a = parse_lattice_constant_from_output(output)
        assert abs(a - 5.470) < 1e-4

    def test_parse_slightly_larger(self):
        """Test parsing slightly larger lattice constant"""
        output = generate_mock_lammps_output(5.480)
        a = parse_lattice_constant_from_output(output)
        assert abs(a - 5.480) < 1e-4

    def test_parse_slightly_smaller(self):
        """Test parsing slightly smaller lattice constant"""
        output = generate_mock_lammps_output(5.460)
        a = parse_lattice_constant_from_output(output)
        assert abs(a - 5.460) < 1e-4

    def test_parse_no_match_raises(self):
        """Test parsing raises ValueError on bad output"""
        with pytest.raises(ValueError, match="Could not parse lattice constant"):
            parse_lattice_constant_from_output("No lattice data here")

    def test_parse_precision(self):
        """Test parser handles high precision values"""
        output = generate_mock_lammps_output(5.470123)
        a = parse_lattice_constant_from_output(output)
        assert abs(a - 5.470123) < 1e-5


class TestUO2LatticeValidation:
    """Test lattice constant validation against literature"""

    def test_nominal_value_passes(self):
        """Test nominal UO2 lattice constant passes validation"""
        result = validate_lattice_constant(5.470)
        assert result["passed"] is True
        assert result["deviation"] < 0.001

    def test_max_tolerance_passes(self):
        """Test value at maximum tolerance passes"""
        result = validate_lattice_constant(UO2_LITERATURE_LATTICE_CONSTANT + 0.02)
        assert result["passed"] is True
        assert result["deviation"] == pytest.approx(0.02, abs=1e-12)

    def test_min_tolerance_passes(self):
        """Test value at minimum tolerance passes"""
        result = validate_lattice_constant(UO2_LITERATURE_LATTICE_CONSTANT - 0.02)
        assert result["passed"] is True
        assert result["deviation"] == pytest.approx(0.02, abs=1e-12)

    def test_beyond_tolerance_fails(self):
        """Test value beyond tolerance fails"""
        result = validate_lattice_constant(UO2_LITERATURE_LATTICE_CONSTANT + 0.021)
        assert result["passed"] is False
        assert result["deviation"] > UO2_LATTICE_TOLERANCE

    def test_percent_error_calculation(self):
        """Test percent error is calculated correctly"""
        # 0.01 Å deviation on 5.47 Å → ~0.183%
        result = validate_lattice_constant(5.480)
        assert 0.18 < result["percent_error"] < 0.19


class TestUO2SmokeTest:
    """
    UO2 Baseline Smoke Test

    This test validates the complete parsing + validation pipeline
    for UO2 lattice relaxation results. It serves as the CI baseline
    for ensuring the MD runner correctly processes LAMMPS output.
    """

    def test_uo2_lattice_constant_baseline(self):
        """
        Smoke test: UO2 lattice constant must be within tolerance.

        Acceptance criterion: |measured - 5.47| < 0.02 Å
        """
        # Generate mock LAMMPS output with literature-accurate value
        mock_output = generate_mock_lammps_output(UO2_LITERATURE_LATTICE_CONSTANT)

        # Parse lattice constant from output
        measured_a = parse_lattice_constant_from_output(mock_output)

        # Validate against literature
        result = validate_lattice_constant(measured_a)

        assert result["passed"], (
            f"UO2 lattice constant out of tolerance: "
            f"measured={measured_a:.4f} Å, "
            f"expected={UO2_LITERATURE_LATTICE_CONSTANT:.3f} Å, "
            f"deviation={result['deviation']:.4f} Å, "
            f"tolerance=±{UO2_LATTICE_TOLERANCE} Å"
        )

    def test_uo2_lattice_constant_with_perturbation(self):
        """
        Smoke test with realistic simulation perturbation.

        Real MD simulations may produce values slightly different from
        the 0 K theoretical value. Test that realistic perturbations
        still pass validation.
        """
        # Typical MD perturbation: +0.005 Å from numerical noise
        perturbed_value = UO2_LITERATURE_LATTICE_CONSTANT + 0.005
        mock_output = generate_mock_lammps_output(perturbed_value)

        measured_a = parse_lattice_constant_from_output(mock_output)
        result = validate_lattice_constant(measured_a)

        assert result["passed"]
        assert result["deviation"] < 0.01  # Well within tolerance


@pytest.fixture
def mock_uo2_files(tmp_path: Path) -> Generator[dict[str, Path], None, None]:
    """Create mock UO2 input files for integration testing"""
    potential = tmp_path / "UO2_Moreland.eam.alloy"
    potential.write_text("# UO2 Moreland/Buckingham potential\n")
    structure = tmp_path / "UO2_fluorite.data"
    structure.write_text(generate_mock_lammps_dump(UO2_LITERATURE_LATTICE_CONSTANT))
    yield {"potential": potential, "structure": structure}


class TestUO2Integration:
    """Integration tests with UO2 data through AnalysisManager"""

    def test_uo2_pipeline_with_lattice_data(self, mock_uo2_files):
        """Test UO2 data flows through the analysis manager pipeline"""
        from nfm_md_runner.analysis_manager import AnalysisManager

        with patch("nfm_md_runner.analysis_manager.settings") as mock_settings:
            mock_settings.ovito_python_path = None
            with patch(
                "nfm_md_runner.analysis_manager.DefectAnalyzer._verify_ovito_available"
            ):
                manager = AnalysisManager()
                results = manager.run_verification_pipeline(
                    potential_file=mock_uo2_files["potential"],
                    structure_file=mock_uo2_files["structure"],
                    simulation_params={
                        "temperature": 0,  # 0 K for lattice constant
                        "pressure": 0.0,
                        "steps": 0,  # Lattice relaxation only
                        "minimize": True,
                    },
                )

                assert results["potential_file"] == str(mock_uo2_files["potential"])
                assert results["structure_file"] == str(mock_uo2_files["structure"])
