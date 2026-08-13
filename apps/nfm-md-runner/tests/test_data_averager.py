"""
Unit tests for data averager module

Tests statistical aggregation and averaging functionality.
"""

import numpy as np
import pytest

from nfm_md_runner.data_averager import AveragedResult, CascadeResult, DataAverager


def test_averaged_result_model():
    """Test AveragedResult data model"""
    result = AveragedResult(
        mean=5.0, std=1.0, min=3.0, max=7.0, samples=100
    )

    assert result.mean == 5.0
    assert result.std == 1.0
    assert result.min == 3.0
    assert result.max == 7.0
    assert result.samples == 100


def test_averaged_result_defaults():
    """Test AveragedResult default values"""
    result = AveragedResult(
        mean=5.0, std=1.0, min=3.0, max=7.0, samples=100
    )

    assert result.confidence_interval is None


def test_average_defect_statistics_empty():
    """Test averaging with empty list returns empty dict"""
    averager = DataAverager()
    result = averager.average_defect_statistics([])

    assert result == {}


def test_average_defect_statistics_single():
    """Test averaging with single data point"""
    averager = DataAverager()
    data = [{"vacancies": 5, "interstitials": 3}]

    result = averager.average_defect_statistics(data)

    assert "vacancies" in result
    assert "interstitials" in result
    assert result["vacancies"].mean == 5.0
    assert result["interstitials"].mean == 3.0


def test_average_defect_statistics_multiple():
    """Test averaging with multiple data points"""
    averager = DataAverager()
    data = [
        {"vacancies": 5, "interstitials": 3},
        {"vacancies": 7, "interstitials": 4},
        {"vacancies": 6, "interstitials": 5},
        {"vacancies": 8, "interstitials": 2},
    ]

    result = averager.average_defect_statistics(data)

    assert result["vacancies"].mean == 6.5  # (5+7+6+8)/4
    assert result["interstitials"].mean == 3.5  # (3+4+5+2)/4
    assert result["vacancies"].samples == 4
    assert result["interstitials"].samples == 4


def test_average_defect_statistics_std_calculation():
    """Test standard deviation calculation"""
    averager = DataAverager()
    data = [
        {"value": 1.0},
        {"value": 2.0},
        {"value": 3.0},
        {"value": 4.0},
        {"value": 5.0},
    ]

    result = averager.average_defect_statistics(data)

    # Std of [1,2,3,4,5] should be sqrt(2) ≈ 1.414
    assert abs(result["value"].std - 1.414) < 0.01


def test_average_energy_data_basic():
    """Test energy data averaging"""
    averager = DataAverager()
    energies = [1.0, 2.0, 3.0, 4.0, 5.0]
    temperatures = [100, 200, 300, 400, 500]

    result = averager.average_energy_data(energies, temperatures)

    assert "temperatures" in result
    assert "energies" in result
    assert "heat_capacity" in result
    assert len(result["temperatures"]) == 5
    assert len(result["energies"]) == 5


def test_average_energy_data_mismatched_lengths():
    """Test that mismatched energy and temperature lists raise error"""
    averager = DataAverager()
    energies = [1.0, 2.0, 3.0]
    temperatures = [100, 200]

    with pytest.raises(ValueError, match="must have same non-zero length"):
        averager.average_energy_data(energies, temperatures)


def test_average_energy_data_empty():
    """Test that empty lists raise error"""
    averager = DataAverager()

    with pytest.raises(ValueError, match="must have same non-zero length"):
        averager.average_energy_data([], [])


def test_average_energy_data_sorting():
    """Test that energy data is sorted by temperature"""
    averager = DataAverager()
    energies = [5.0, 1.0, 3.0]  # Unsorted
    temperatures = [500, 100, 300]  # Corresponding temps

    result = averager.average_energy_data(energies, temperatures)

    # Should be sorted by temperature
    assert list(result["temperatures"]) == [100, 300, 500]
    assert list(result["energies"]) == [1.0, 3.0, 5.0]


def test_confidence_interval_calculation():
    """Test confidence interval calculation"""
    averager = DataAverager()
    data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    ci_lower, ci_upper = averager._calculate_confidence_interval(data)

    # For normal data, CI should be symmetric around mean
    mean = np.mean(data)
    assert abs(ci_lower - mean) < abs(ci_upper - mean)  # Lower bound below mean
    assert ci_upper > mean  # Upper bound above mean


def test_confidence_interval_single_sample():
    """Test confidence interval with single sample"""
    averager = DataAverager()
    data = np.array([5.0])

    ci_lower, ci_upper = averager._calculate_confidence_interval(data)

    # Single sample should return (0, 0)
    assert ci_lower == 0.0
    assert ci_upper == 0.0


def test_merge_trajectory_data_empty():
    """Test merging empty trajectory data list"""
    averager = DataAverager()
    result = averager.merge_trajectory_data([])

    assert result == {}


def test_merge_trajectory_data_single():
    """Test merging single trajectory data"""
    averager = DataAverager()
    data = [
        {
            "positions": np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
            "energies": np.array([1.0, 2.0]),
        }
    ]

    result = averager.merge_trajectory_data(data)

    assert "positions" in result
    assert "energies" in result
    assert len(result["positions"]) == 2
    assert len(result["energies"]) == 2


def test_merge_trajectory_data_multiple():
    """Test merging multiple trajectory data"""
    averager = DataAverager()
    data = [
        {"energies": np.array([1.0, 2.0])},
        {"energies": np.array([3.0, 4.0])},
        {"energies": np.array([5.0, 6.0])},
    ]

    result = averager.merge_trajectory_data(data)

    assert "energies" in result
    # Should concatenate along first axis
    assert len(result["energies"]) == 6  # 2 + 2 + 2
    assert list(result["energies"]) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_merge_trajectory_data_missing_keys():
    """Test merging handles missing keys gracefully"""
    averager = DataAverager()
    data = [
        {"energies": np.array([1.0, 2.0])},
        {"positions": np.array([[1.0, 2.0, 3.0]])},  # Different key
        {"energies": np.array([3.0, 4.0])},
    ]

    result = averager.merge_trajectory_data(data)

    # Should only have energies (present in 2 of 3 trajectories)
    assert "energies" in result
    assert len(result["energies"]) == 4
    # positions not included (only in 1 trajectory)
    assert "positions" not in result


# --- Tests for issue-spec average() interface ---

def test_average_cascade_results_basic():
    """Test average() with CascadeResult objects"""
    averager = DataAverager()
    results = [
        CascadeResult(vacancies=5.0, interstitials=3.0, pkd_total=100.0),
        CascadeResult(vacancies=7.0, interstitials=4.0, pkd_total=120.0),
        CascadeResult(vacancies=6.0, interstitials=5.0, pkd_total=110.0),
    ]

    averaged = averager.average(results)

    assert averaged.mean == 6.0
    assert averaged.samples == 3
    assert averaged.outliers_removed == 0
    assert averaged.confidence_interval is not None


def test_average_cascade_results_empty_raises():
    """Test average() raises ValueError for empty list"""
    averager = DataAverager()

    with pytest.raises(ValueError, match="Results list cannot be empty"):
        averager.average([])


def test_average_cascade_results_with_outliers():
    """Test average() with outliers that get filtered by IQR"""
    averager = DataAverager()
    results = [
        CascadeResult(vacancies=5.0),
        CascadeResult(vacancies=6.0),
        CascadeResult(vacancies=5.0),
        CascadeResult(vacancies=6.0),
        CascadeResult(vacancies=5.0),
        CascadeResult(vacancies=100.0),  # Outlier
    ]

    averaged = averager.average(results)

    # Outlier should be removed
    assert averaged.outliers_removed == 1
    assert averaged.samples == 5
    assert averaged.mean == 5.4  # (5+6+5+6+5)/5


def test_filter_outliers_short_data():
    """Test _filter_outliers returns data unchanged when < 4 points"""
    averager = DataAverager()
    data = np.array([1.0, 2.0, 3.0])

    filtered, n_outliers = averager._filter_outliers(data)

    assert len(filtered) == 3
    assert n_outliers == 0


def test_filter_outliers_actual_filtering():
    """Test _filter_outliers with enough data to trigger IQR filtering"""
    averager = DataAverager()
    data = np.array([20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 100.0])

    filtered, n_outliers = averager._filter_outliers(data)

    assert n_outliers == 1
    assert len(filtered) == 6
    assert 100.0 not in filtered


def test_cascade_result_model():
    """Test CascadeResult data model"""
    cr = CascadeResult(vacancies=5.0, interstitials=3.0, frenkel_pairs=2.0)

    assert cr.vacancies == 5.0
    assert cr.interstitials == 3.0
    assert cr.frenkel_pairs == 2.0
    assert cr.pkd_total == 0.0  # default
