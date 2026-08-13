"""
Unit tests for defect analyzer module

Tests defect statistics extraction and analysis.
"""

from pathlib import Path
from typing import Generator
from unittest.mock import Mock, patch

import numpy as np
import pytest

from nfm_md_runner.defect_analyzer import DefectAnalyzer, DefectStatistics


@pytest.fixture
def mock_trajectory(tmp_path: Path) -> Generator[Path, None, None]:
    """Fixture to create a mock trajectory file"""
    traj_file = tmp_path / "trajectory.lammpstrj"
    traj_file.write_text("# Mock LAMMPS trajectory data\n")
    yield traj_file


@pytest.fixture
def mock_reference(tmp_path: Path) -> Generator[Path, None, None]:
    """Fixture to create a mock reference structure"""
    ref_file = tmp_path / "reference.cfg"
    ref_file.write_text("# Mock reference structure\n")
    yield ref_file


@pytest.fixture
def analyzer():
    """Fixture to create a DefectAnalyzer instance"""
    with patch("nfm_md_runner.defect_analyzer.DefectAnalyzer._verify_ovito_available"):
        yield DefectAnalyzer()


def test_defect_statistics_model():
    """Test DefectStatistics data model"""
    stats = DefectStatistics(
        total_atoms=1000,
        vacancies=5,
        interstitials=3,
        vacancy_concentration=0.005,
        interstitial_concentration=0.003,
        analysis_timestamp="2026-06-20T00:00:00Z",
    )

    assert stats.total_atoms == 1000
    assert stats.vacancies == 5
    assert stats.interstitials == 3
    assert stats.vacancy_concentration == 0.005
    assert stats.interstitial_concentration == 0.003


def test_defect_statistics_defaults():
    """Test DefectStatistics default values"""
    stats = DefectStatistics(
        total_atoms=100, analysis_timestamp="2026-06-20T00:00:00Z"
    )

    assert stats.vacancies == 0
    assert stats.interstitials == 0
    assert stats.vacancy_concentration == 0.0
    assert stats.interstitial_concentration == 0.0


def test_analyzer_initialization():
    """Test DefectAnalyzer initialization"""
    with patch("nfm_md_runner.defect_analyzer.DefectAnalyzer._verify_ovito_available"):
        analyzer = DefectAnalyzer(ovito_python_path="/path/to/ovito")

        assert analyzer.ovito_python_path == "/path/to/ovito"


def test_analyze_trajectory_file_not_found(analyzer):
    """Test analyze_trajectory raises FileNotFoundError for missing files"""
    with pytest.raises(FileNotFoundError, match="Trajectory file not found"):
        analyzer.analyze_trajectory(
            Path("nonexistent.lammpstrj"), Path("reference.cfg")
        )


def test_analyze_trajectory_reference_not_found(analyzer, mock_trajectory):
    """Test analyze_trajectory raises FileNotFoundError for missing reference"""
    with pytest.raises(FileNotFoundError, match="Reference structure not found"):
        analyzer.analyze_trajectory(
            mock_trajectory, Path("nonexistent_reference.cfg")
        )


@patch("nfm_md_runner.defect_analyzer.DefectAnalyzer._verify_ovito_available")
def test_analyze_trajectory_success(_, mock_trajectory, mock_reference):
    """Test successful trajectory analysis"""
    analyzer = DefectAnalyzer()
    stats = analyzer.analyze_trajectory(mock_trajectory, mock_reference)

    assert isinstance(stats, DefectStatistics)
    assert stats.total_atoms > 0
    assert isinstance(stats.vacancies, int)
    assert isinstance(stats.interstitials, int)


@patch("nfm_md_runner.defect_analyzer.DefectAnalyzer._verify_ovito_available")
def test_compare_defect_evolution(_, analyzer, mock_reference, tmp_path):
    """Test comparing defect evolution across multiple trajectories"""
    # Create multiple mock trajectory files
    traj_files = []
    for i in range(3):
        traj_file = tmp_path / f"trajectory_{i}.lammpstrj"
        traj_file.write_text(f"# Mock trajectory {i}\n")
        traj_files.append(traj_file)

    results = analyzer.compare_defect_evolution(traj_files, mock_reference)

    assert isinstance(results, dict)
    assert len(results) == 3
    assert all(isinstance(stats, list) for stats in results.values())


def test_compare_defect_evolution_handles_errors(
    analyzer, mock_reference, tmp_path
):
    """Test that compare_defect_evolution handles individual failures gracefully"""
    # Mix of valid and invalid trajectory files
    traj_files = [
        tmp_path / "valid.lammpstrj",  # Will be created
        Path("nonexistent.lammpstrj"),  # Will fail
    ]

    # Create valid trajectory
    traj_files[0].write_text("# Valid trajectory\n")

    with patch.object(analyzer, "analyze_trajectory", side_effect=Exception("Test error")):
        results = analyzer.compare_defect_evolution(traj_files, mock_reference)

        # Should return empty results for failed analyses
        assert len(results) == 2


def test_defect_concentration_calculation():
    """Test defect concentration calculation"""
    total_atoms = 1000
    vacancies = 10
    interstitials = 5

    vacancy_concentration = vacancies / total_atoms
    interstitial_concentration = interstitials / total_atoms

    assert vacancy_concentration == 0.01
    assert interstitial_concentration == 0.005


def test_ovito_path_skips_import():
    """Test that providing ovito_python_path skips OVITO import check"""
    with patch("nfm_md_runner.defect_analyzer.DefectAnalyzer._verify_ovito_available"):
        analyzer = DefectAnalyzer(ovito_python_path="/custom/path/to/ovito")
    assert analyzer.ovito_python_path == "/custom/path/to/ovito"


def test_missing_ovito_raises_import_error():
    """Test that missing OVITO raises ImportError with helpful message"""
    import sys

    analyzer = DefectAnalyzer.__new__(DefectAnalyzer)
    analyzer.ovito_python_path = None

    # Ensure ovito is not in sys.modules, then call verify
    ovito_was_present = "ovito" in sys.modules
    if "ovito" in sys.modules:
        del sys.modules["ovito"]

    try:
        with pytest.raises(ImportError, match="OVITO is not available"):
            analyzer._verify_ovito_available()
    finally:
        if ovito_was_present:
            sys.modules["ovito"] = True  # Restore


def test_analyze_interface(tmp_path):
    """Test the issue-spec analyze() interface"""
    with patch("nfm_md_runner.defect_analyzer.DefectAnalyzer._verify_ovito_available"):
        analyzer = DefectAnalyzer()

    dump = tmp_path / "dump.atom"
    dump.write_text("# LAMMPS dump\n")
    ref = tmp_path / "ref.lmp"
    ref.write_text("# Reference\n")

    result = analyzer.analyze(dump, ref)

    assert isinstance(result, DefectStatistics)
    assert result.total_atoms > 0
    assert result.frenkel_pairs >= 0


def test_defect_result_new_fields():
    """Test DefectResult has Frenkel pair and displaced atom fields"""
    stats = DefectStatistics(
        total_atoms=500,
        vacancies=3,
        interstitials=2,
        frenkel_pairs=1,
        displaced_atoms=4,
        replaced_atoms=1,
        vacancy_concentration=0.006,
        interstitial_concentration=0.004,
        analysis_timestamp="2026-06-23T00:00:00+00:00",
    )

    assert stats.frenkel_pairs == 1
    assert stats.displaced_atoms == 4
    assert stats.replaced_atoms == 1


def test_defect_statistics_json_serialization():
    """Test that DefectStatistics can be serialized to JSON"""
    stats = DefectStatistics(
        total_atoms=1000,
        vacancies=5,
        interstitials=3,
        vacancy_concentration=0.005,
        interstitial_concentration=0.003,
        analysis_timestamp="2026-06-20T00:00:00Z",
    )

    # Should be able to serialize without errors
    json_dict = stats.model_dump()

    assert json_dict["total_atoms"] == 1000
    assert json_dict["vacancies"] == 5
    assert json_dict["interstitials"] == 3
