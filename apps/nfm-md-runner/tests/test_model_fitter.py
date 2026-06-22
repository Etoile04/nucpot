"""
Unit tests for model fitter module

Tests potential function fitting methods and validation.
"""

from typing import Dict

import numpy as np
import pytest

from nfm_md_runner.model_fitter import DataPoint, FittingMethod, FittingResult, ModelFitter


def test_fitting_result_model():
    """Test FittingResult data model"""
    result = FittingResult(
        method=FittingMethod.ARC_DPA,
        converged=True,
        iterations=100,
        final_error=0.001,
        parameters={"param1": 1.0, "param2": 2.0},
        quality_metrics={"r_squared": 0.95, "rmse": 0.01},
    )

    assert result.method == FittingMethod.ARC_DPA
    assert result.converged is True
    assert result.iterations == 100
    assert result.final_error == 0.001


def test_fitting_result_defaults():
    """Test FittingResult default values"""
    result = FittingResult(method=FittingMethod.RPA)

    assert result.converged is False
    assert result.iterations == 0
    assert result.final_error == float("inf")
    assert result.parameters == {}
    assert result.quality_metrics == {}


def test_model_fitter_initialization():
    """Test ModelFitter initialization"""
    fitter = ModelFitter(method=FittingMethod.LEAST_SQUARES)

    assert fitter.method == FittingMethod.LEAST_SQUARES


def test_fit_potential_arc_dpa():
    """Test arc-dpa fitting method"""
    fitter = ModelFitter(method=FittingMethod.ARC_DPA)

    target_data = {"energies": np.array([1.0, 2.0, 3.0])}
    initial_params = {"param1": 1.0, "param2": 2.0}

    result = fitter.fit_potential(target_data, initial_params)

    assert result.method == FittingMethod.ARC_DPA
    assert isinstance(result, FittingResult)
    # Placeholder should return unconverged result
    assert result.converged is False


def test_fit_potential_rpa():
    """Test RPA fitting method"""
    fitter = ModelFitter(method=FittingMethod.RPA)

    target_data = {"energies": np.array([1.0, 2.0, 3.0])}
    initial_params = {"param1": 1.0}

    result = fitter.fit_potential(target_data, initial_params)

    assert result.method == FittingMethod.RPA
    assert isinstance(result, FittingResult)
    assert result.converged is False


def test_fit_potential_least_squares():
    """Test least squares fitting method"""
    fitter = ModelFitter(method=FittingMethod.LEAST_SQUARES)

    target_data = {"energies": np.array([1.0, 2.0, 3.0])}
    initial_params = {"param1": 1.0}

    result = fitter.fit_potential(target_data, initial_params)

    assert result.method == FittingMethod.LEAST_SQUARES
    assert isinstance(result, FittingResult)
    # Least squares should complete (even if placeholder)
    assert result.computation_time >= 0


def test_fit_potential_with_bounds():
    """Test fitting with parameter bounds"""
    fitter = ModelFitter(method=FittingMethod.ARC_DPA)

    target_data = {"energies": np.array([1.0, 2.0, 3.0])}
    initial_params = {"param1": 1.0}
    param_bounds = {"param1": (0.0, 10.0)}  # Min, max

    result = fitter.fit_potential(target_data, initial_params, param_bounds)

    assert isinstance(result, FittingResult)


def test_validate_fitting_not_converged():
    """Test validation fails for unconverged fitting"""
    fitter = ModelFitter()

    fitting_result = FittingResult(
        method=FittingMethod.ARC_DPA, converged=False
    )
    validation_data = {"energies": np.array([1.0, 2.0, 3.0])}

    result = fitter.validate_fitting(fitting_result, validation_data)

    assert result is False


def test_validate_fitting_converged():
    """Test validation for converged fitting (placeholder)"""
    fitter = ModelFitter()

    fitting_result = FittingResult(
        method=FittingMethod.ARC_DPA, converged=True
    )
    validation_data = {"energies": np.array([1.0, 2.0, 3.0])}

    result = fitter.validate_fitting(fitting_result, validation_data)

    # Placeholder returns False even when converged
    assert result is False


def test_compute_quality_metrics():
    """Test quality metrics computation"""
    fitter = ModelFitter()

    predictions = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    targets = np.array([1.1, 2.1, 2.9, 4.1, 4.9])

    metrics = fitter.compute_quality_metrics(predictions, targets)

    assert "r_squared" in metrics
    assert "rmse" in metrics
    assert "mae" in metrics
    assert "max_error" in metrics
    assert "mean_residual" in metrics
    assert "std_residual" in metrics

    # R² should be close to 1 for good fit
    assert metrics["r_squared"] > 0.9

    # RMSE should be reasonable
    assert 0.0 < metrics["rmse"] < 1.0


def test_compute_quality_metrics_perfect_fit():
    """Test quality metrics for perfect fit"""
    fitter = ModelFitter()

    predictions = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    targets = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    metrics = fitter.compute_quality_metrics(predictions, targets)

    # Perfect fit should have R² = 1
    assert abs(metrics["r_squared"] - 1.0) < 0.01

    # RMSE and MAE should be 0
    assert metrics["rmse"] == 0.0
    assert metrics["mae"] == 0.0


def test_compute_quality_metrics_poor_fit():
    """Test quality metrics for poor fit"""
    fitter = ModelFitter()

    predictions = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
    targets = np.array([10.0, 20.0, 30.0, 40.0, 50.0])

    metrics = fitter.compute_quality_metrics(predictions, targets)

    # Poor fit should have negative R² or close to 0
    assert metrics["r_squared"] < 0.1

    # High errors
    assert metrics["mae"] > 10.0
    assert metrics["max_error"] > 10.0


def test_fitting_result_serialization():
    """Test that FittingResult can be serialized"""
    result = FittingResult(
        method=FittingMethod.ARC_DPA,
        converged=True,
        iterations=100,
        final_error=0.001,
        parameters={"param1": 1.0},
        quality_metrics={"r_squared": 0.95},
    )

    # Should serialize without errors
    json_dict = result.model_dump()

    assert json_dict["method"] == "arc-dpa"  # Enum value uses hyphen
    assert json_dict["converged"] is True
    assert json_dict["iterations"] == 100


class TestDataPoint:
    """Test DataPoint model"""

    def test_data_point_creation(self):
        """Test DataPoint with energy and n_dpa"""
        dp = DataPoint(energy=1.5, n_dpa=0.8)
        assert dp.energy == 1.5
        assert dp.n_dpa == 0.8

    def test_data_point_json_serialization(self):
        """Test DataPoint serializes correctly"""
        dp = DataPoint(energy=500.0, n_dpa=25.0)
        d = dp.model_dump()
        assert d["energy"] == 500.0
        assert d["n_dpa"] == 25.0


class TestFitMethod:
    """Test the high-level fit() method"""

    def test_fit_empty_data(self):
        """Test fit raises on empty data"""
        fitter = ModelFitter(method=FittingMethod.ARC_DPA)
        with pytest.raises(ValueError, match="Energy data cannot be empty"):
            fitter.fit([], "arc-dpa")

    def test_fit_with_data_points(self):
        """Test fit with valid data points"""
        fitter = ModelFitter(method=FittingMethod.ARC_DPA)
        data_points = [
            DataPoint(energy=100.0, n_dpa=5.0),
            DataPoint(energy=500.0, n_dpa=25.0),
            DataPoint(energy=1000.0, n_dpa=50.0),
        ]
        result = fitter.fit(data_points, "arc-dpa")
        assert isinstance(result, FittingResult)
        assert result.method == FittingMethod.ARC_DPA
        assert result.converged is False
        assert "scale" in result.parameters

    def test_fit_rpa_method(self):
        """Test fit with RPA method"""
        fitter = ModelFitter(method=FittingMethod.RPA)
        data_points = [DataPoint(energy=100.0, n_dpa=5.0)]
        result = fitter.fit(data_points, "rpa")
        assert result.method == FittingMethod.RPA


class TestValidateFittingEdgeCases:
    """Test validate_fitting edge cases"""

    def test_validate_empty_data(self):
        """Test validate raises on empty data"""
        fitter = ModelFitter()
        with pytest.raises(ValueError, match="Validation data cannot be empty"):
            fitter.validate_fitting({}, {})

    def test_validate_with_data(self):
        """Test validate returns False for placeholder implementation"""
        fitter = ModelFitter()
        validation_data = {"energies": np.array([1.0, 2.0, 3.0])}
        result = fitter.validate_fitting({"param1": 1.0}, validation_data)
        assert result is False

    def test_validate_with_zero_variance(self):
        """Test compute_quality_metrics with zero variance in reference"""
        fitter = ModelFitter()
        predictions = np.array([5.0, 5.0, 5.0])
        reference = np.array([5.0, 5.0, 5.0])
        metrics = fitter.compute_quality_metrics(predictions, reference)
        assert metrics["r_squared"] == 0.0
        assert metrics["rmse"] == 0.0

    def test_compute_quality_metrics_shape_mismatch(self):
        """Test compute_quality_metrics raises on shape mismatch"""
        fitter = ModelFitter()
        with pytest.raises(ValueError, match="same shape"):
            fitter.compute_quality_metrics(
                np.array([1.0, 2.0]),
                np.array([1.0, 2.0, 3.0]),
            )


class TestOptimizeParameters:
    """Test optimize_parameters with scipy"""

    def test_optimize_quadratic(self):
        """Test optimize_parameters on a simple quadratic function"""
        fitter = ModelFitter()

        def quadratic(x):
            return float(np.sum(x**2))

        initial_guess = np.array([5.0, -3.0])
        params, info = fitter.optimize_parameters(quadratic, initial_guess)

        assert info["success"] is True
        assert abs(params[0]) < 0.01
        assert abs(params[1]) < 0.01
        assert info["nit"] > 0
        assert info["fun"] < 0.01

    def test_optimize_with_bounds(self):
        """Test optimize_parameters with parameter bounds"""
        fitter = ModelFitter()

        def quadratic(x):
            return float(np.sum((x - 2.0) ** 2))

        initial_guess = np.array([0.0])
        bounds = [(0.5, 10.0)]
        params, info = fitter.optimize_parameters(
            quadratic, initial_guess, bounds=bounds
        )

        assert info["success"] is True
        assert abs(params[0] - 2.0) < 0.01


class TestProtocolTypes:
    """Test Protocol type definitions"""

    def test_data_point_is_pydantic(self):
        """Test DataPoint is a Pydantic model"""
        assert hasattr(DataPoint, "model_dump")

    def test_fitting_method_enum_values(self):
        """Test FittingMethod enum has expected values"""
        assert FittingMethod.ARC_DPA.value == "arc-dpa"
        assert FittingMethod.RPA.value == "rpa"
        assert FittingMethod.LEAST_SQUARES.value == "least_squares"

    def test_invalid_method_string(self):
        """Test invalid method string raises ValueError"""
        with pytest.raises(ValueError, match="Invalid fitting method"):
            ModelFitter(method="nonexistent_method")
