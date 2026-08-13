"""
Data Averaging Module

Performs statistical aggregation and averaging of MD simulation results
with IQR/Z-score anomaly detection.

**Interface**: DataAverager.average(results) -> AveragedResult
**Dependencies**: numpy only
**Coupling**: Zero SQLite, zero SSH.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class CascadeResult(BaseModel):
    """Result from a single cascade simulation run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    vacancies: float = Field(default=0.0, description="Number of vacancies")
    interstitials: float = Field(default=0.0, description="Number of interstitials")
    frenkel_pairs: float = Field(default=0.0, description="Number of Frenkel pairs")
    pkd_total: float = Field(default=0.0, description="Total PKD energy (eV)")


class AveragedResult(BaseModel):
    """Statistical average of multiple simulation runs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    mean: float = Field(..., description="Mean value")
    std: float = Field(..., description="Standard deviation")
    min: float = Field(..., description="Minimum value")
    max: float = Field(..., description="Maximum value")
    samples: int = Field(..., description="Number of samples")
    confidence_interval: Optional[tuple[float, float]] = Field(
        default=None, description="95% confidence interval"
    )
    outliers_removed: int = Field(default=0, description="Outliers removed by IQR filter")


# --- Protocol definitions (port interfaces per architecture constraints) ---


@runtime_checkable
class Averager(Protocol):
    """Protocol defining the averaging port interface."""

    def average(self, results: list[CascadeResult]) -> AveragedResult: ...


class DataAverager:
    """
    Aggregates and averages data from multiple MD simulation runs.

    Extracted from lammps-automation with zero coupling to storage/transport.
    """

    def average(self, results: list[CascadeResult]) -> AveragedResult:
        """
        Average cascade results across multiple runs (issue-spec interface).

        Args:
            results: List of CascadeResult from individual simulation runs.

        Returns:
            AveragedResult with mean/std/CI of vacancy counts.
        """
        if not results:
            raise ValueError("Results list cannot be empty")

        vacancies = np.array([r.vacancies for r in results])
        filtered, n_outliers = self._filter_outliers(vacancies)

        return AveragedResult(
            mean=float(np.mean(filtered)),
            std=float(np.std(filtered)),
            min=float(np.min(filtered)),
            max=float(np.max(filtered)),
            samples=len(filtered),
            confidence_interval=self._calculate_confidence_interval(filtered),
            outliers_removed=n_outliers,
        )

    def average_defect_statistics(
        self, defect_stats_list: list[dict[str, float]]
    ) -> dict[str, AveragedResult]:
        """
        Calculate average defect statistics across multiple runs (legacy interface).

        Args:
            defect_stats_list: List of defect statistics dictionaries.

        Returns:
            Dictionary mapping defect types to their averaged statistics.
        """
        if not defect_stats_list:
            return {}

        keys = defect_stats_list[0].keys()
        data = np.array([[stats[key] for key in keys] for stats in defect_stats_list])

        results: dict[str, AveragedResult] = {}
        for i, key in enumerate(keys):
            column = data[:, i]
            results[key] = AveragedResult(
                mean=float(np.mean(column)),
                std=float(np.std(column)),
                min=float(np.min(column)),
                max=float(np.max(column)),
                samples=len(column),
                confidence_interval=self._calculate_confidence_interval(column),
            )

        return results

    def average_energy_data(
        self, energy_list: list[float], temperature_list: list[float]
    ) -> dict[str, np.ndarray]:
        """
        Calculate thermodynamic averages from energy data.

        Args:
            energy_list: List of energy values.
            temperature_list: Corresponding temperatures.

        Returns:
            Dictionary with averaged energy and heat capacity data.
        """
        if not energy_list or len(energy_list) != len(temperature_list):
            raise ValueError("Energy and temperature lists must have same non-zero length")

        energies = np.array(energy_list)
        temperatures = np.array(temperature_list)

        sort_indices = np.argsort(temperatures)
        temperatures = temperatures[sort_indices]
        energies = energies[sort_indices]

        return {
            "temperatures": temperatures,
            "energies": energies,
            "heat_capacity": np.gradient(energies, temperatures),
        }

    def _filter_outliers(
        self, data: np.ndarray, iqr_multiplier: float = 1.5
    ) -> tuple[np.ndarray, int]:
        """
        Remove outliers using IQR method.

        Args:
            data: Input data array.
            iqr_multiplier: IQR multiplier for outlier detection.

        Returns:
            Tuple of (filtered data, number of outliers removed).
        """
        if len(data) < 4:
            return data, 0

        q1 = np.percentile(data, 25)
        q3 = np.percentile(data, 75)
        iqr = q3 - q1

        lower = q1 - iqr_multiplier * iqr
        upper = q3 + iqr_multiplier * iqr

        mask = (data >= lower) & (data <= upper)
        filtered = data[mask]
        n_outliers = len(data) - len(filtered)

        return filtered, n_outliers

    def _calculate_confidence_interval(
        self, data: np.ndarray, confidence: float = 0.95
    ) -> tuple[float, float]:
        """Calculate confidence interval using percentiles."""
        n = len(data)
        if n < 2:
            return (0.0, 0.0)

        alpha = 1 - confidence
        lower_percentile = (alpha / 2) * 100
        upper_percentile = (1 - alpha / 2) * 100

        return (
            float(np.percentile(data, lower_percentile)),
            float(np.percentile(data, upper_percentile)),
        )

    def merge_trajectory_data(
        self, trajectory_data_list: list[dict[str, np.ndarray]]
    ) -> dict[str, np.ndarray]:
        """
        Merge data from multiple trajectory analyses.

        Args:
            trajectory_data_list: List of trajectory data dictionaries.

        Returns:
            Merged data dictionary.
        """
        if not trajectory_data_list:
            return {}

        keys = trajectory_data_list[0].keys()

        merged: dict[str, np.ndarray] = {}
        for key in keys:
            arrays = [
                traj_data[key]
                for traj_data in trajectory_data_list
                if key in traj_data
            ]

            if arrays:
                merged[key] = np.concatenate(arrays, axis=0)

        return merged
