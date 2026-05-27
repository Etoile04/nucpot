"""Tests for the grading engine."""

import pytest
from verify_service.core.grading import grade_property, grade_results, _worst_grade


class TestGradeProperty:
    def test_grade_a(self):
        grade, err = grade_property(3.47, 3.47)
        assert grade == "A"
        assert err == 0.0

    def test_grade_a_near_boundary(self):
        grade, err = grade_property(3.50, 3.47)
        # rel_err = 0.03/3.47 = 0.00865 → A (<1%)
        assert grade == "A"

    def test_grade_b(self):
        grade, err = grade_property(3.55, 3.47)
        # rel_err = 0.08/3.47 = 0.023 → B (<3%)
        assert grade == "B"

    def test_grade_c(self):
        grade, err = grade_property(3.60, 3.47)
        # rel_err = 0.13/3.47 = 0.0375 → C (<5%)
        assert grade == "C"

    def test_grade_d(self):
        grade, err = grade_property(3.70, 3.47)
        # rel_err = 0.23/3.47 = 0.066 → D (<10%)
        assert grade == "D"

    def test_grade_f(self):
        grade, err = grade_property(4.0, 3.47)
        # rel_err = 0.53/3.47 = 0.153 → F (>10%)
        assert grade == "F"

    def test_zero_reference_zero_computed(self):
        grade, err = grade_property(0.0, 0.0)
        assert grade == "A"

    def test_zero_reference_nonzero_computed(self):
        grade, err = grade_property(1.0, 0.0)
        assert grade == "F"


class TestGradeResults:
    def test_all_match(self):
        computed = {
            "lattice_constant": {"value": 3.47, "unit": "angstrom"},
            "bulk_modulus": {"value": 58.7, "unit": "GPa"},
        }
        reference = {
            "lattice_constant": {"value": 3.47, "unit": "angstrom"},
            "bulk_modulus": {"value": 58.7, "unit": "GPa"},
        }
        result = grade_results(computed, reference)
        assert result["overall_grade"] == "A"

    def test_worst_grade_wins(self):
        computed = {
            "lattice_constant": {"value": 3.47, "unit": "angstrom"},  # A
            "bulk_modulus": {"value": 70.0, "unit": "GPa"},  # ~19% error → F
        }
        reference = {
            "lattice_constant": {"value": 3.47, "unit": "angstrom"},
            "bulk_modulus": {"value": 58.7, "unit": "GPa"},
        }
        result = grade_results(computed, reference)
        assert result["overall_grade"] == "F"

    def test_error_in_computed(self):
        computed = {
            "lattice_constant": {"error": "calculation failed"},
        }
        reference = {
            "lattice_constant": {"value": 3.47},
        }
        result = grade_results(computed, reference)
        assert result["results"]["lattice_constant"]["grade"] == "F"


class TestWorstGrade:
    def test_all_a(self):
        assert _worst_grade(["A", "A", "A"]) == "A"

    def test_mixed(self):
        assert _worst_grade(["A", "C", "B"]) == "C"

    def test_single_f(self):
        assert _worst_grade(["A", "A", "F"]) == "F"
