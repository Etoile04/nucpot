"""
Unit tests for CLI interface

Tests CLI commands using click.testing.CliRunner with mocked backends.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from nfm_md_runner.cli import cli, verify_config, init, run, analyze_defects


@pytest.fixture
def runner():
    """Create a Click CliRunner"""
    return CliRunner()


@pytest.fixture
def mock_files(tmp_path):
    """Create mock input files for CLI tests"""
    potential = tmp_path / "UO2.eam"
    potential.write_text("# Mock UO2 potential\n")
    structure = tmp_path / "UO2.data"
    structure.write_text("# Mock UO2 structure\n")
    trajectory = tmp_path / "trajectory.dump"
    trajectory.write_text("# Mock trajectory\n")
    return {
        "potential": potential,
        "structure": structure,
        "trajectory": trajectory,
    }


class TestCLIRoot:
    """Test root CLI commands"""

    def test_cli_version(self, runner):
        """Test --version flag"""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_cli_help(self, runner):
        """Test --help flag"""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "nfm-md-runner" in result.output

    def test_cli_verbose(self, runner):
        """Test --verbose flag prints workspace info when paired with a command"""
        with patch("nfm_md_runner.cli.settings") as mock_settings:
            mock_settings.app_version = "0.1.0"
            mock_settings.workspace_dir = Path("/tmp/workspace")
            result = runner.invoke(cli, ["--verbose", "verify-config"])
            assert result.exit_code == 0
            assert "nfm-md-runner" in result.output


class TestVerifyConfig:
    """Test verify-config command"""

    def test_verify_config_success(self, runner):
        """Test verify-config with valid environment"""
        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.settings") as mock_settings:
                mock_settings.workspace_dir = Path("/tmp/ws")
                mock_settings.output_dir = Path("/tmp/out")
                mock_settings.temp_dir = Path("/tmp/tmp")
                mock_settings.hpc_host = None
                mock_settings.ovito_enabled = False

                result = runner.invoke(cli, ["verify-config"])
                assert result.exit_code == 0
                assert "Environment configuration is valid" in result.output

    def test_verify_config_failure(self, runner):
        """Test verify-config with invalid environment"""
        with patch(
            "nfm_md_runner.cli.verify_environment",
            side_effect=ValueError("HPC config incomplete"),
        ):
            result = runner.invoke(cli, ["verify-config"])
            assert result.exit_code == 1
            assert "HPC config incomplete" in result.output

    def test_verify_config_with_hpc(self, runner):
        """Test verify-config shows HPC info when configured"""
        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.settings") as mock_settings:
                mock_settings.workspace_dir = Path("/tmp/ws")
                mock_settings.output_dir = Path("/tmp/out")
                mock_settings.temp_dir = Path("/tmp/tmp")
                mock_settings.hpc_host = "hpc.example.com"
                mock_settings.hpc_connection_string.return_value = "user@hpc.example.com"
                mock_settings.ovito_enabled = False

                result = runner.invoke(cli, ["verify-config"])
                assert result.exit_code == 0
                assert "hpc.example.com" in result.output


class TestInit:
    """Test init command"""

    def test_init_creates_workspace(self, runner, tmp_path):
        """Test init creates directory structure"""
        result = runner.invoke(cli, ["init", str(tmp_path / "new-workspace")])
        assert result.exit_code == 0
        assert "Initialized" in result.output

        workspace = tmp_path / "new-workspace"
        assert (workspace / "workspace").exists()
        assert (workspace / "output").exists()
        assert (workspace / "temp").exists()
        assert (workspace / "potentials").exists()
        assert (workspace / "structures").exists()

    def test_init_creates_env_example(self, runner, tmp_path):
        """Test init creates .env.example file"""
        result = runner.invoke(cli, ["init", str(tmp_path / "new-project")])
        assert result.exit_code == 0
        assert (tmp_path / "new-project" / ".env.example").exists()

    def test_init_creates_readme(self, runner, tmp_path):
        """Test init creates README.md file"""
        result = runner.invoke(cli, ["init", str(tmp_path / "new-project")])
        assert result.exit_code == 0
        assert (tmp_path / "new-project" / "README.md").exists()


class TestRunCommand:
    """Test run command"""

    def test_run_success(self, runner, mock_files):
        """Test run command with valid inputs"""
        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.AnalysisManager") as MockManager:
                mock_mgr = MockManager.return_value
                mock_mgr.run_verification_pipeline.return_value = {
                    "timestamp": "2026-06-23T00:00:00Z",
                    "potential_file": str(mock_files["potential"]),
                    "structure_file": str(mock_files["structure"]),
                    "defect_results": [],
                    "averaged_data": {},
                    "fitting_result": None,
                }

                result = runner.invoke(
                    cli,
                    [
                        "run",
                        str(mock_files["potential"]),
                        str(mock_files["structure"]),
                        "--temperature", "300",
                        "--steps", "1000",
                    ],
                )

                assert result.exit_code == 0
                assert "verification for potential" in result.output

    def test_run_with_output(self, runner, mock_files, tmp_path):
        """Test run command saves output to file"""
        output_file = tmp_path / "results.json"

        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.AnalysisManager") as MockManager:
                mock_mgr = MockManager.return_value
                mock_mgr.run_verification_pipeline.return_value = {
                    "timestamp": "2026-06-23T00:00:00Z",
                    "potential_file": str(mock_files["potential"]),
                    "structure_file": str(mock_files["structure"]),
                    "defect_results": [],
                    "averaged_data": {},
                    "fitting_result": None,
                }

                result = runner.invoke(
                    cli,
                    [
                        "run",
                        str(mock_files["potential"]),
                        str(mock_files["structure"]),
                        "--output", str(output_file),
                    ],
                )

                assert result.exit_code == 0
                assert "Results saved" in result.output

    def test_run_with_fitting(self, runner, mock_files):
        """Test run command with --fit flag"""
        from nfm_md_runner.model_fitter import FittingResult, FittingMethod

        mock_fitting = FittingResult(
            method=FittingMethod.ARC_DPA, converged=True, iterations=50
        )

        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.AnalysisManager") as MockManager:
                mock_mgr = MockManager.return_value
                mock_mgr.run_verification_pipeline.return_value = {
                    "timestamp": "2026-06-23T00:00:00Z",
                    "potential_file": str(mock_files["potential"]),
                    "structure_file": str(mock_files["structure"]),
                    "defect_results": [],
                    "averaged_data": {},
                    "fitting_result": mock_fitting,
                }

                result = runner.invoke(
                    cli,
                    [
                        "run",
                        str(mock_files["potential"]),
                        str(mock_files["structure"]),
                        "--fit",
                    ],
                )

                assert result.exit_code == 0

    def test_run_env_failure(self, runner, mock_files):
        """Test run command with environment verification failure"""
        with patch(
            "nfm_md_runner.cli.verify_environment",
            side_effect=ValueError("Config error"),
        ):
            result = runner.invoke(
                cli,
                [
                    "run",
                    str(mock_files["potential"]),
                    str(mock_files["structure"]),
                ],
            )

            assert result.exit_code == 1
            assert "Config error" in result.output


class TestAnalyzeDefectsCommand:
    """Test analyze-defects command"""

    def test_analyze_defects_success(self, runner, mock_files):
        """Test analyze-defects with valid inputs"""
        from nfm_md_runner.defect_analyzer import DefectStatistics

        mock_stats = DefectStatistics(
            total_atoms=1000,
            vacancies=5,
            interstitials=3,
            vacancy_concentration=0.005,
            interstitial_concentration=0.003,
            analysis_timestamp="2026-06-23T00:00:00Z",
        )

        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.settings") as mock_settings:
                mock_settings.ovito_python_path = None
                with patch("nfm_md_runner.defect_analyzer.DefectAnalyzer") as MockAnalyzer:
                    MockAnalyzer.return_value.analyze_trajectory.return_value = mock_stats

                    result = runner.invoke(
                        cli,
                        [
                            "analyze-defects",
                            str(mock_files["trajectory"]),
                            str(mock_files["structure"]),
                        ],
                    )

                    assert result.exit_code == 0
                    assert "Total atoms: 1000" in result.output

    def test_analyze_defects_with_output(self, runner, mock_files, tmp_path):
        """Test analyze-defects saves output"""
        from nfm_md_runner.defect_analyzer import DefectStatistics

        mock_stats = DefectStatistics(
            total_atoms=500,
            vacancies=2,
            interstitials=1,
            vacancy_concentration=0.004,
            interstitial_concentration=0.002,
            analysis_timestamp="2026-06-23T00:00:00Z",
        )
        output_file = tmp_path / "defects.json"

        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.settings") as mock_settings:
                mock_settings.ovito_python_path = None
                with patch("nfm_md_runner.defect_analyzer.DefectAnalyzer") as MockAnalyzer:
                    MockAnalyzer.return_value.analyze_trajectory.return_value = mock_stats

                    result = runner.invoke(
                        cli,
                        [
                            "analyze-defects",
                            str(mock_files["trajectory"]),
                            str(mock_files["structure"]),
                            "--output", str(output_file),
                        ],
                    )

                    assert result.exit_code == 0
                    assert output_file.exists()

    def test_analyze_defects_error(self, runner, mock_files):
        """Test analyze-defects handles errors"""
        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.cli.settings") as mock_settings:
                mock_settings.ovito_python_path = None
                with patch(
                    "nfm_md_runner.defect_analyzer.DefectAnalyzer",
                    side_effect=RuntimeError("OVITO not found"),
                ):
                    result = runner.invoke(
                        cli,
                        [
                            "analyze-defects",
                            str(mock_files["trajectory"]),
                            str(mock_files["structure"]),
                        ],
                    )

                    assert result.exit_code == 1


class TestFitPotentialCommand:
    """Test fit-potential command"""

    def test_fit_potential_success(self, runner, mock_files):
        """Test fit-potential command runs"""
        with patch("nfm_md_runner.cli.verify_environment", return_value=True):
            with patch("nfm_md_runner.model_fitter.ModelFitter") as MockFitter:
                mock_fitter = MockFitter.return_value
                mock_fitter.method = "arc-dpa"

                result = runner.invoke(
                    cli,
                    [
                        "fit-potential",
                        str(mock_files["potential"]),
                        "--method", "arc-dpa",
                    ],
                )

                assert result.exit_code == 0

    def test_fit_potential_error(self, runner, mock_files):
        """Test fit-potential handles errors"""
        with patch(
            "nfm_md_runner.cli.verify_environment",
            side_effect=ValueError("Bad config"),
        ):
            result = runner.invoke(
                cli,
                ["fit-potential", str(mock_files["potential"])],
            )

            assert result.exit_code == 1
            assert "Bad config" in result.output
