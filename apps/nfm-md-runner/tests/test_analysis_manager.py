"""
Unit tests for analysis manager module

Tests the complete MD verification pipeline orchestration.
"""

from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from nfm_md_runner.analysis_manager import AnalysisManager
from nfm_md_runner.defect_analyzer import DefectResult
from nfm_md_runner.data_averager import AveragedResult
from nfm_md_runner.model_fitter import FittingResult, FittingMethod


@pytest.fixture
def mock_files(tmp_path: Path) -> Generator[dict[str, Path], None, None]:
    """Create mock potential and structure files"""
    potential = tmp_path / "potential.file"
    potential.write_text("# Mock potential file\n")
    structure = tmp_path / "structure.data"
    structure.write_text("# Mock structure file\n")
    yield {"potential_file": potential, "structure_file": structure}


@pytest.fixture
def manager():
    """Create AnalysisManager with mocked dependencies"""
    with patch("nfm_md_runner.analysis_manager.settings") as mock_settings:
        mock_settings.ovito_python_path = None
        with patch("nfm_md_runner.analysis_manager.DefectAnalyzer._verify_ovito_available"):
            mgr = AnalysisManager()
            yield mgr


class TestAnalysisManagerInit:
    """Test AnalysisManager initialization"""

    def test_manager_creates_components(self, manager):
        """Test that manager initializes all sub-components"""
        assert manager.defect_analyzer is not None
        assert manager.data_averager is not None
        assert manager.model_fitter is not None

    def test_manager_initial_results_empty(self, manager):
        """Test that initial results dict has expected keys"""
        assert "defect_statistics" in manager.current_results
        assert "averaged_data" in manager.current_results
        assert "fitting_results" in manager.current_results
        assert manager.current_results["defect_statistics"] == []
        assert manager.current_results["averaged_data"] == []
        assert manager.current_results["fitting_results"] == []


class TestRunVerificationPipeline:
    """Test the main verification pipeline"""

    def test_pipeline_missing_potential(self, manager, tmp_path):
        """Test pipeline raises FileNotFoundError for missing potential"""
        structure = tmp_path / "structure.data"
        structure.write_text("dummy")

        with pytest.raises(FileNotFoundError, match="Potential file not found"):
            manager.run_verification_pipeline(
                potential_file=Path("nonexistent.pot"),
                structure_file=structure,
                simulation_params={"temperature": 300},
            )

    def test_pipeline_missing_structure(self, manager, tmp_path):
        """Test pipeline raises FileNotFoundError for missing structure"""
        potential = tmp_path / "potential.file"
        potential.write_text("dummy")

        with pytest.raises(FileNotFoundError, match="Structure file not found"):
            manager.run_verification_pipeline(
                potential_file=potential,
                structure_file=Path("nonexistent.data"),
                simulation_params={"temperature": 300},
            )

    def test_pipeline_without_fitting(self, manager, mock_files):
        """Test pipeline runs without fitting step"""
        with patch.object(manager, "_run_md_simulation", return_value=[]):
            with patch.object(manager, "_analyze_defects", return_value=[]):
                with patch.object(manager, "_average_results", return_value={}):
                    result = manager.run_verification_pipeline(
                        potential_file=mock_files["potential_file"],
                        structure_file=mock_files["structure_file"],
                        simulation_params={"temperature": 300, "steps": 1000},
                    )

        assert "timestamp" in result
        assert result["potential_file"] == str(mock_files["potential_file"])
        assert result["structure_file"] == str(mock_files["structure_file"])
        assert result["defect_results"] == []
        assert result["averaged_data"] == {}
        assert result["fitting_result"] is None

    def test_pipeline_with_fitting(self, manager, mock_files):
        """Test pipeline runs with fitting step"""
        mock_fitting_result = FittingResult(
            method=FittingMethod.ARC_DPA,
            converged=True,
            iterations=50,
        )

        with patch.object(manager, "_run_md_simulation", return_value=[]):
            with patch.object(manager, "_analyze_defects", return_value=[]):
                with patch.object(manager, "_average_results", return_value={}):
                    with patch.object(
                        manager, "_fit_potential", return_value=mock_fitting_result
                    ):
                        result = manager.run_verification_pipeline(
                            potential_file=mock_files["potential_file"],
                            structure_file=mock_files["structure_file"],
                            simulation_params={"temperature": 300},
                            fitting_params={"param1": 1.0},
                        )

        assert result["fitting_result"] is not None
        assert result["fitting_result"].converged is True


class TestInternalPipelineSteps:
    """Test internal pipeline step methods"""

    def test_run_md_simulation_placeholder(self, manager, mock_files):
        """Test _run_md_simulation returns empty list (placeholder)"""
        result = manager._run_md_simulation(
            mock_files["potential_file"],
            mock_files["structure_file"],
            {"temperature": 300},
        )
        assert result == []

    def test_analyze_defects_empty_trajectories(self, manager, mock_files):
        """Test _analyze_defects with empty trajectory list"""
        result = manager._analyze_defects([], mock_files["structure_file"])
        assert result == []

    def test_analyze_defects_with_trajectories(self, manager, mock_files, tmp_path):
        """Test _analyze_defects processes trajectory files"""
        traj_file = tmp_path / "traj.dump"
        traj_file.write_text("# trajectory\n")

        mock_stats = DefectResult(
            total_atoms=500,
            vacancies=2,
            interstitials=1,
            vacancy_concentration=0.004,
            interstitial_concentration=0.002,
            analysis_timestamp="2026-06-23T00:00:00Z",
        )

        with patch.object(
            manager.defect_analyzer,
            "analyze_trajectory",
            return_value=mock_stats,
        ):
            result = manager._analyze_defects(
                [traj_file], mock_files["structure_file"]
            )

        assert len(result) == 1
        assert result[0].total_atoms == 500

    def test_analyze_defects_handles_analysis_error(
        self, manager, mock_files, tmp_path
    ):
        """Test _analyze_defects gracefully handles analysis errors"""
        traj_file = tmp_path / "traj.dump"
        traj_file.write_text("# trajectory\n")

        with patch.object(
            manager.defect_analyzer,
            "analyze_trajectory",
            side_effect=RuntimeError("Analysis failed"),
        ):
            result = manager._analyze_defects(
                [traj_file], mock_files["structure_file"]
            )

        # Should return empty list (error is caught and logged)
        assert result == []

    def test_average_results_empty(self, manager):
        """Test _average_results with empty defect results"""
        result = manager._average_results([])
        assert result == {}

    def test_average_results_with_data(self, manager):
        """Test _average_results processes defect statistics"""
        stats_list = [
            DefectResult(
                total_atoms=1000,
                vacancies=5,
                interstitials=3,
                vacancy_concentration=0.005,
                interstitial_concentration=0.003,
                analysis_timestamp="2026-06-23T00:00:00Z",
            ),
            DefectResult(
                total_atoms=1000,
                vacancies=7,
                interstitials=4,
                vacancy_concentration=0.007,
                interstitial_concentration=0.004,
                analysis_timestamp="2026-06-23T00:00:00Z",
            ),
        ]

        with patch.object(
            manager.data_averager,
            "average_defect_statistics",
            return_value={
                "vacancies": AveragedResult(
                    mean=6.0, std=1.0, min=5.0, max=7.0, samples=2
                ),
                "interstitials": AveragedResult(
                    mean=3.5, std=0.5, min=3.0, max=4.0, samples=2
                ),
            },
        ):
            result = manager._average_results(stats_list)

        assert "vacancies" in result
        assert result["vacancies"].mean == 6.0

    def test_fit_potential_calls_model_fitter(self, manager):
        """Test _fit_potential delegates to model fitter"""
        mock_result = FittingResult(
            method=FittingMethod.ARC_DPA, converged=True, iterations=10
        )
        averaged_data = {
            "vacancies": AveragedResult(
                mean=6.0, std=1.0, min=5.0, max=7.0, samples=2
            )
        }
        initial_params = {"param1": 1.0}

        mock_fitter = MagicMock()
        mock_fitter.fit_potential.return_value = mock_result
        manager.model_fitter = mock_fitter

        result = manager._fit_potential(averaged_data, initial_params)

        assert result.converged is True
        mock_fitter.fit_potential.assert_called_once_with(
            reference_data={}, initial_parameters=initial_params
        )


class TestSaveResults:
    """Test result serialization and saving"""

    def test_save_results_creates_file(self, manager, tmp_path):
        """Test save_results creates output file"""
        output_path = tmp_path / "results" / "output.json"
        results = {
            "timestamp": "2026-06-23T00:00:00Z",
            "potential_file": "/path/to/potential",
            "defect_results": [],
            "averaged_data": {},
            "fitting_result": None,
        }

        manager.save_results(results, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "2026-06-23" in content

    def test_save_results_with_fitting_result(self, manager, tmp_path):
        """Test save_results serializes fitting results"""
        output_path = tmp_path / "results.json"
        fitting_result = FittingResult(
            method=FittingMethod.RPA,
            converged=True,
            iterations=100,
            final_error=0.001,
        )
        results = {
            "timestamp": "2026-06-23T00:00:00Z",
            "potential_file": "/path/to/potential",
            "defect_results": [],
            "averaged_data": {},
            "fitting_result": fitting_result,
        }

        manager.save_results(results, output_path)

        assert output_path.exists()

    def test_save_results_with_defect_stats(self, manager, tmp_path):
        """Test save_results with defect stats (model_dump handles numpy)"""
        output_path = tmp_path / "results.json"
        stats = DefectResult(
            total_atoms=1000,
            vacancies=5,
            interstitials=3,
            vacancy_concentration=0.005,
            interstitial_concentration=0.003,
            analysis_timestamp="2026-06-23T00:00:00Z",
        )
        results = {
            "timestamp": "2026-06-23T00:00:00Z",
            "potential_file": "/path/to/potential",
            "defect_results": [stats],
            "averaged_data": {},
            "fitting_result": None,
        }

        # spatial_distribution (None by default) is JSON-serializable;
        # test that the basic save path works when no numpy arrays are set
        manager.save_results(results, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "1000" in content
        assert "timestamp" in content

    def test_serialize_results_dict(self, manager):
        """Test _serialize_results with dict values"""
        results = {"key1": [1, 2, 3], "key2": {"nested": "value"}}
        serialized = manager._serialize_results(results)
        assert serialized["key1"] == [1, 2, 3]
        assert serialized["key2"]["nested"] == "value"

    def test_serialize_results_pydantic(self, manager):
        """Test _serialize_results with Pydantic model"""
        stats = DefectResult(
            total_atoms=500,
            vacancies=2,
            interstitials=1,
            vacancy_concentration=0.004,
            interstitial_concentration=0.002,
            analysis_timestamp="2026-06-23T00:00:00Z",
        )
        results = {"stats": stats}
        serialized = manager._serialize_results(results)
        assert serialized["stats"]["total_atoms"] == 500

    def test_serialize_results_string(self, manager):
        """Test _serialize_results with plain string value"""
        results = {"path": "/some/path.txt"}
        serialized = manager._serialize_results(results)
        assert serialized["path"] == "/some/path.txt"

    def test_cleanup_temporary_files(self, manager):
        """Test cleanup_temporary_files runs without error"""
        manager.cleanup_temporary_files()  # Should not raise
