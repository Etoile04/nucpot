"""
Analysis Manager Module

Orchestrates the complete MD verification pipeline:
MD simulation → Defect analysis → Data averaging → Potential fitting

Architecture: Hexagonal — all external dependencies injected via Protocol ports.
Zero SQLite imports. Zero SSH imports.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .config import settings
from .data_averager import AveragedResult, DataAverager
from .defect_analyzer import DefectAnalyzer, DefectResult
from .model_fitter import FittingResult, ModelFitter
from .ports import (
    AnalysisResult,
    Executor,
    JobSpec,
    JobStatus,
    ResultRepository,
)

logger = logging.getLogger(__name__)


class AnalysisManager:
    """Manages the complete verification pipeline.

    Dependencies are injected via constructor:
    - ``result_repository``: persist analysis results (any storage backend)
    - ``executor``: submit and manage MD simulation jobs (local, HPC, etc.)

    Both are optional for backward compatibility.  When ``None``, the
    manager falls back to placeholder / no-op behaviour.
    """

    def __init__(
        self,
        result_repository: Optional[ResultRepository] = None,
        executor: Optional[Executor] = None,
    ) -> None:
        self.result_repository = result_repository
        self.executor = executor

        self.defect_analyzer = DefectAnalyzer(
            ovito_python_path=settings.ovito_python_path
        )
        self.data_averager = DataAverager()
        self.model_fitter = ModelFitter()

    async def run_verification_pipeline(
        self,
        potential_file: Path,
        structure_file: Path,
        simulation_params: dict[str, Any],
        fitting_params: Optional[dict[str, float]] = None,
    ) -> dict[str, Any]:
        """Run complete verification pipeline.

        Args:
            potential_file: Path to potential function file.
            structure_file: Path to atomic structure file.
            simulation_params: MD simulation parameters.
            fitting_params: Optional initial parameters for fitting.

        Returns:
            Dictionary with complete verification results.

        Raises:
            FileNotFoundError: If input files don't exist.
            ValueError: If simulation parameters are invalid.
        """
        if not potential_file.exists():
            raise FileNotFoundError(f"Potential file not found: {potential_file}")

        if not structure_file.exists():
            raise FileNotFoundError(f"Structure file not found: {structure_file}")

        # Step 1: Run MD simulation via Executor port
        trajectory_files = await self._run_md_simulation(
            potential_file, structure_file, simulation_params
        )

        # Step 2: Analyze defects
        defect_results = self._analyze_defects(trajectory_files, structure_file)

        # Step 3: Average data
        averaged_data = self._average_results(defect_results)

        # Step 4: Fit potential
        fitting_result = None
        if fitting_params:
            fitting_result = self._fit_potential(averaged_data, fitting_params)

        # Build result
        result_dict: dict[str, Any] = {
            "potential_file": str(potential_file),
            "structure_file": str(structure_file),
            "defect_results": defect_results,
            "averaged_data": averaged_data,
            "fitting_result": fitting_result,
        }

        # Persist via ResultRepository if available
        if self.result_repository is not None:
            analysis_result = AnalysisResult(
                job_id=self._current_job_id or "unknown",
                potential_file=str(potential_file),
                structure_file=str(structure_file),
            )
            await self.result_repository.save_result(analysis_result)

        return result_dict

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _run_md_simulation(
        self,
        potential_file: Path,
        structure_file: Path,
        params: dict[str, Any],
    ) -> list[Path]:
        """Run MD simulation via the Executor port.

        Falls back to a no-op placeholder when no executor is injected.
        """
        if self.executor is None:
            logger.info("No executor injected — skipping MD simulation")
            return []

        job_spec = JobSpec(
            potential_file=str(potential_file),
            structure_file=str(structure_file),
            temperature=float(params.get("temperature", 300)),
            pressure=float(params.get("pressure", 0)),
            steps=int(params.get("steps", 10000)),
        )

        job_id = await self.executor.submit(job_spec)
        self._current_job_id = job_id
        logger.info("Submitted job %s via executor", job_id)

        job_output = await self.executor.retrieve_output(job_id)
        return [Path(f) for f in job_output.output_files]

    _current_job_id: str | None = None

    def _analyze_defects(
        self, trajectory_files: list[Path], reference_structure: Path
    ) -> list[DefectResult]:
        """Analyze defects from trajectory files."""
        results: list[DefectResult] = []
        for traj_file in trajectory_files:
            try:
                stats = self.defect_analyzer.analyze_trajectory(
                    traj_file, reference_structure
                )
                results.append(stats)
            except Exception as exc:
                logger.warning("Failed to analyze %s: %s", traj_file, exc)

        return results

    def _average_results(
        self, defect_results: list[DefectResult]
    ) -> dict[str, AveragedResult]:
        """Average results from multiple simulations."""
        if not defect_results:
            return {}

        defect_stats_list = [
            {
                "vacancies": stats.vacancies,
                "interstitials": stats.interstitials,
                "vacancy_concentration": stats.vacancy_concentration,
                "interstitial_concentration": stats.interstitial_concentration,
            }
            for stats in defect_results
        ]

        return self.data_averager.average_defect_statistics(defect_stats_list)

    def _fit_potential(
        self, averaged_data: dict[str, AveragedResult], initial_params: dict[str, float]
    ) -> FittingResult:
        """Fit potential parameters."""
        return self.model_fitter.fit_potential(
            reference_data={}, initial_parameters=initial_params
        )

    def cleanup_temporary_files(self) -> None:
        """Clean up temporary files from analysis."""
        pass

    def save_results(self, results: dict[str, Any], output_path: Path) -> None:
        """Save verification results to file (legacy sync API).

        Prefer ``ResultRepository`` for new code.  This method is kept
        for backward compatibility with the CLI.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        json_results = self._serialize_results(results)

        with open(output_path, "w") as f:
            json.dump(json_results, f, indent=2, default=str)

    @staticmethod
    def _serialize_results(results: dict[str, Any]) -> dict[str, Any]:
        """Convert results to JSON-serializable format."""
        serialized: dict[str, Any] = {}

        for key, value in results.items():
            if isinstance(value, dict):
                serialized[key] = AnalysisManager._serialize_results(value)
            elif isinstance(value, list):
                serialized[key] = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
            elif hasattr(value, "model_dump"):
                serialized[key] = value.model_dump(mode="json")
            else:
                serialized[key] = value

        return serialized
