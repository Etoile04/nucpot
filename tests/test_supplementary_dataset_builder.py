"""Tests for supplementary_dataset_builder — the orchestration pipeline.

TDD RED phase: all tests written BEFORE implementation.
Each test exercises one function/behavior of the builder module.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from nfm_db.ml.materials_project_client import SupplementaryRecord


# ---------------------------------------------------------------------------
# Helpers for creating test data
# ---------------------------------------------------------------------------


def make_heaps_csv(
    tmp_path: Path,
    rows: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Create a minimal HEAPS-format CSV and return its path."""
    if rows is None:
        rows = [
            {"System": "U97.5Mo2Nb0V0Ti0.5"},
            {"System": "U90Mo5Nb5V0Ti0"},
            {"System": "U85Mo10Nb3V0Ti2"},
            {"System": "U80Mo15Nb3V0Ti2"},
        ]

    filepath = str(tmp_path / "test_heaps.csv")
    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return filepath


def make_supplementary_record(
    element_system: str = "Mo-Nb-U",
    composition: str = '{"Mo": 2.0, "Nb": 0.5, "U": 97.5}',
    phase: str = "BCC",
    source_id: str = "SUPPL-MP-test-001",
    formation_energy: float = -3.25,
    lattice_constant_a: float = 3.52,
    lattice_distortion: float = 0.35,
    **overrides: object,
) -> SupplementaryRecord:
    """Create a SupplementaryRecord with sensible defaults."""
    defaults = {
        "element_system": element_system,
        "composition": composition,
        "phase": phase,
        "functional": "PBE",
        "formation_energy": formation_energy,
        "formation_energy_uncertainty": None,
        "cohesive_energy": None,
        "lattice_constant_a": lattice_constant_a,
        "lattice_constant_b": None,
        "lattice_constant_c": None,
        "lattice_distortion": lattice_distortion,
        "source_id": source_id,
        "cutoff_energy": 500.0,
        "kpoint_density": "MP-standard",
        "code": "VASP",
    }
    defaults.update(overrides)
    return SupplementaryRecord(**defaults)


# ===========================================================================
# Test 1: BuildResult dataclass
# ===========================================================================


class TestBuildResult:
    """Tests for the BuildResult frozen dataclass."""

    def test_build_result_is_frozen(self) -> None:
        """BuildResult must be a frozen dataclass."""
        from nfm_db.ml.supplementary_dataset_builder import BuildResult

        result = BuildResult(
            total_heaps_entries=100,
            mp_matched_count=60,
            calphad_fallback_count=20,
            total_output_records=80,
            output_files=("output.csv",),
            mp_api_key_used=True,
            build_timestamp="2026-07-19T00:00:00",
        )
        # frozen dataclass raises FrozenInstanceError on mutation
        with pytest.raises(AttributeError):
            result.total_heaps_entries = 200  # type: ignore[misc]

    def test_build_result_has_all_fields(self) -> None:
        """BuildResult must have all required fields."""
        from nfm_db.ml.supplementary_dataset_builder import BuildResult

        result = BuildResult(
            total_heaps_entries=50,
            mp_matched_count=30,
            calphad_fallback_count=10,
            total_output_records=40,
            output_files=("file1.csv", "file2.csv"),
            mp_api_key_used=False,
            build_timestamp="2026-07-19T12:00:00",
        )
        assert result.total_heaps_entries == 50
        assert result.mp_matched_count == 30
        assert result.calphad_fallback_count == 10
        assert result.total_output_records == 40
        assert result.output_files == ("file1.csv", "file2.csv")
        assert result.mp_api_key_used is False
        assert result.build_timestamp == "2026-07-19T12:00:00"


# ===========================================================================
# Test 2: deduplicate_records
# ===========================================================================


class TestDeduplicateRecords:
    """Tests for deduplicate_records function."""

    def test_removes_exact_duplicate_compositions(self) -> None:
        """Duplicate compositions should be removed."""
        from nfm_db.ml.supplementary_dataset_builder import deduplicate_records

        comp = '{"U": 70.0, "Zr": 30.0}'
        records = [
            make_supplementary_record(composition=comp, source_id="SUPPL-MP-1"),
            make_supplementary_record(composition=comp, source_id="SUPPL-MP-2"),
        ]
        deduped = deduplicate_records(records)
        assert len(deduped) == 1

    def test_prefers_mp_source_over_calphad(self) -> None:
        """When duplicates exist, MP source should be preferred over CALPHAD."""
        from nfm_db.ml.supplementary_dataset_builder import deduplicate_records

        comp = '{"U": 70.0, "Zr": 30.0}'
        records = [
            make_supplementary_record(
                composition=comp,
                source_id="SUPPL-CALPHAD-abc123",
                formation_energy=-2.5,
            ),
            make_supplementary_record(
                composition=comp,
                source_id="SUPPL-MP-mp-1234",
                formation_energy=-3.25,
            ),
        ]
        deduped = deduplicate_records(records)
        assert len(deduped) == 1
        assert deduped[0].source_id == "SUPPL-MP-mp-1234"

    def test_keeps_unique_compositions(self) -> None:
        """Unique compositions should all be preserved."""
        from nfm_db.ml.supplementary_dataset_builder import deduplicate_records

        records = [
            make_supplementary_record(composition='{"U": 70.0, "Zr": 30.0}'),
            make_supplementary_record(composition='{"U": 50.0, "Zr": 50.0}'),
            make_supplementary_record(composition='{"U": 90.0, "Mo": 10.0}'),
        ]
        deduped = deduplicate_records(records)
        assert len(deduped) == 3

    def test_empty_list_returns_empty(self) -> None:
        """Empty input returns empty output."""
        from nfm_db.ml.supplementary_dataset_builder import deduplicate_records

        assert deduplicate_records([]) == []


# ===========================================================================
# Test 3: write_dft_export_csv
# ===========================================================================


class TestWriteDftExportCsv:
    """Tests for write_dft_export_csv function."""

    def test_writes_csv_with_correct_headers(self, tmp_path: Path) -> None:
        """Output CSV must have all DFT export spec S3 field names."""
        from nfm_db.ml.supplementary_dataset_builder import (
            DFT_EXPORT_CSV_FIELDS,
            write_dft_export_csv,
        )

        records = [make_supplementary_record()]
        output_path = str(tmp_path / "test_output.csv")
        result = write_dft_export_csv(records, output_path)

        assert Path(result).exists()
        with open(result, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == list(DFT_EXPORT_CSV_FIELDS)

    def test_writes_single_record_correctly(self, tmp_path: Path) -> None:
        """A single record should produce one data row with correct values."""
        from nfm_db.ml.supplementary_dataset_builder import write_dft_export_csv

        record = make_supplementary_record(
            element_system="Mo-Nb-U",
            composition='{"Mo": 2.0, "Nb": 0.5, "U": 97.5}',
            phase="BCC",
            source_id="SUPPL-MP-mp-001",
            formation_energy=-3.25,
            lattice_constant_a=3.52,
            lattice_distortion=0.35,
        )
        output_path = str(tmp_path / "test_single.csv")
        write_dft_export_csv([record], output_path)

        with open(output_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["element_system"] == "Mo-Nb-U"
            assert rows[0]["source_id"] == "SUPPL-MP-mp-001"
            assert rows[0]["formation_energy"] == "-3.25"
            assert rows[0]["lattice_constant_a"] == "3.52"

    def test_empty_records_creates_header_only(self, tmp_path: Path) -> None:
        """Empty record list should produce a CSV with only headers."""
        from nfm_db.ml.supplementary_dataset_builder import (
            DFT_EXPORT_CSV_FIELDS,
            write_dft_export_csv,
        )

        output_path = str(tmp_path / "empty.csv")
        write_dft_export_csv([], output_path)

        with open(output_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == list(DFT_EXPORT_CSV_FIELDS)
            rows = list(reader)
            assert len(rows) == 0

    def test_returns_path_object(self, tmp_path: Path) -> None:
        """Function should return a Path object."""
        from nfm_db.ml.supplementary_dataset_builder import write_dft_export_csv

        output_path = str(tmp_path / "ret.csv")
        result = write_dft_export_csv([], output_path)
        assert isinstance(result, Path)


# ===========================================================================
# Test 4: calphad_proxy
# ===========================================================================


class TestCalphadProxy:
    """Tests for calphad_proxy fallback function."""

    def test_returns_record_for_known_umo_nb_system(self) -> None:
        """CALPHAD proxy should return a record for U-Mo-Nb systems."""
        from nfm_db.ml.supplementary_dataset_builder import calphad_proxy

        composition = {"U": 80.0, "Mo": 15.0, "Nb": 5.0}
        result = calphad_proxy(composition)
        assert result is not None
        assert isinstance(result, SupplementaryRecord)
        assert result.source_id.startswith("SUPPL-CALPHAD-")

    def test_returns_record_for_known_umo_ti_system(self) -> None:
        """CALPHAD proxy should return a record for U-Mo-Ti systems."""
        from nfm_db.ml.supplementary_dataset_builder import calphad_proxy

        composition = {"U": 80.0, "Mo": 10.0, "Ti": 10.0}
        result = calphad_proxy(composition)
        assert result is not None
        assert isinstance(result, SupplementaryRecord)

    def test_returns_none_for_unknown_system(self) -> None:
        """CALPHAD proxy should return None for systems without data."""
        from nfm_db.ml.supplementary_dataset_builder import calphad_proxy

        composition = {"Fe": 50.0, "Cr": 25.0, "Ni": 25.0}
        result = calphad_proxy(composition)
        assert result is None

    def test_record_has_required_fields(self) -> None:
        """CALPHAD proxy record must have all SupplementaryRecord fields."""
        from nfm_db.ml.supplementary_dataset_builder import calphad_proxy

        composition = {"U": 80.0, "Mo": 15.0, "Nb": 5.0}
        result = calphad_proxy(composition)
        assert result is not None
        assert result.element_system
        assert result.composition
        assert result.phase
        assert result.formation_energy != 0.0
        assert result.lattice_constant_a > 0.0
        assert result.lattice_distortion >= 0.0

    def test_source_id_contains_hash(self) -> None:
        """CALPHAD source_id should end with a deterministic hash."""
        from nfm_db.ml.supplementary_dataset_builder import calphad_proxy

        composition = {"U": 80.0, "Mo": 15.0, "Nb": 5.0}
        result = calphad_proxy(composition)
        assert result is not None
        parts = result.source_id.split("-")
        assert parts[0] == "SUPPL"
        assert parts[1] == "CALPHAD"
        assert len(parts) >= 3

    def test_same_composition_same_hash(self) -> None:
        """Same composition should produce the same hash."""
        from nfm_db.ml.supplementary_dataset_builder import calphad_proxy

        composition = {"U": 70.0, "Mo": 20.0, "Nb": 10.0}
        r1 = calphad_proxy(composition)
        r2 = calphad_proxy(composition)
        assert r1 is not None
        assert r2 is not None
        assert r1.source_id == r2.source_id


# ===========================================================================
# Test 5: build_supplementary_dataset (end-to-end pipeline)
# ===========================================================================


class TestBuildSupplementaryDataset:
    """Tests for the main orchestration function."""

    def test_runs_end_to_end_no_api_key(self, tmp_path: Path) -> None:
        """Pipeline should run with CALPHAD fallback when no API key."""
        from nfm_db.ml.supplementary_dataset_builder import build_supplementary_dataset

        heaps_csv = make_heaps_csv(tmp_path)
        output_dir = str(tmp_path / "output")

        result = build_supplementary_dataset(
            heaps_csv_path=heaps_csv,
            output_dir=output_dir,
            mp_api_key=None,
        )

        assert result.total_heaps_entries > 0
        assert result.total_output_records >= 0
        assert result.mp_api_key_used is False
        assert result.build_timestamp

    def test_output_dir_created(self, tmp_path: Path) -> None:
        """Output directory should be created if it doesn't exist."""
        from nfm_db.ml.supplementary_dataset_builder import build_supplementary_dataset

        heaps_csv = make_heaps_csv(tmp_path)
        output_dir = str(tmp_path / "nested" / "output" / "dir")

        build_supplementary_dataset(
            heaps_csv_path=heaps_csv,
            output_dir=output_dir,
            mp_api_key=None,
        )

        assert os.path.isdir(output_dir)

    def test_output_csv_files_exist(self, tmp_path: Path) -> None:
        """Pipeline should create CSV files in the output directory."""
        from nfm_db.ml.supplementary_dataset_builder import build_supplementary_dataset

        heaps_csv = make_heaps_csv(tmp_path)
        output_dir = str(tmp_path / "output")

        result = build_supplementary_dataset(
            heaps_csv_path=heaps_csv,
            output_dir=output_dir,
            mp_api_key=None,
        )

        assert len(result.output_files) > 0
        for filepath in result.output_files:
            assert os.path.isfile(filepath)

    def test_output_csv_matches_dft_spec_headers(self, tmp_path: Path) -> None:
        """Output CSV headers must match DFT export spec S3 exactly."""
        from nfm_db.ml.supplementary_dataset_builder import (
            DFT_EXPORT_CSV_FIELDS,
            build_supplementary_dataset,
        )

        heaps_csv = make_heaps_csv(tmp_path)
        output_dir = str(tmp_path / "output")

        result = build_supplementary_dataset(
            heaps_csv_path=heaps_csv,
            output_dir=output_dir,
            mp_api_key=None,
        )

        for filepath in result.output_files:
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                assert reader.fieldnames == list(DFT_EXPORT_CSV_FIELDS)

    def test_file_naming_convention(self, tmp_path: Path) -> None:
        """Output files should follow naming: supplementary_dft_batch_NNN_total_date.csv."""
        from nfm_db.ml.supplementary_dataset_builder import build_supplementary_dataset

        heaps_csv = make_heaps_csv(tmp_path)
        output_dir = str(tmp_path / "output")

        result = build_supplementary_dataset(
            heaps_csv_path=heaps_csv,
            output_dir=output_dir,
            mp_api_key=None,
        )

        for filepath in result.output_files:
            filename = os.path.basename(filepath)
            assert filename.startswith("supplementary_dft_batch_")
            assert filename.endswith(".csv")

    def test_counts_are_consistent(self, tmp_path: Path) -> None:
        """Output count should not exceed mp + calphad counts."""
        from nfm_db.ml.supplementary_dataset_builder import build_supplementary_dataset

        heaps_csv = make_heaps_csv(tmp_path)
        output_dir = str(tmp_path / "output")

        result = build_supplementary_dataset(
            heaps_csv_path=heaps_csv,
            output_dir=output_dir,
            mp_api_key=None,
        )

        # After dedup, total can be <= sum of sources
        assert result.total_output_records <= (
            result.mp_matched_count + result.calphad_fallback_count
        )
