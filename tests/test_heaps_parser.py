"""Tests for HEAPS CSV parser — TDD RED phase.

All tests are written BEFORE the implementation module exists.
They must fail with ImportError, then pass once heaps_parser.py is implemented.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import FrozenInstanceError

import pytest


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

HEAPS_CSV_HEADER = "System,χU,enp,VEC,eoa,Omega,UMoE"


def _make_csv_rows(system_strings: list[str]) -> str:
    """Build a minimal HEAPS CSV string from system strings."""
    rows = [HEAPS_CSV_HEADER]
    for sys_str in system_strings:
        rows.append(f"{sys_str},17.23,1.42,5.95,2.88,11.2,2.96")
    return "\n".join(rows) + "\n"


def _write_temp_csv(content: str) -> str:
    """Write content to a temp file and return the path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# parse_composition_string tests
# ---------------------------------------------------------------------------


class TestParseCompositionString:
    """Tests for parse_composition_string(system_string) -> Dict[str, float]."""

    def test_parses_simple_binary_system(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        result = parse_composition_string("U93Mo7")
        assert result == {"U": 93.0, "Mo": 7.0}

    def test_parses_ternary_system(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        result = parse_composition_string("U90Mo7Nb3")
        assert result == {"U": 90.0, "Mo": 7.0, "Nb": 3.0}

    def test_parses_system_with_decimal_values(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        result = parse_composition_string("U97.5Mo2Nb0V0Ti0.5")
        assert result["U"] == pytest.approx(97.5)
        assert result["Mo"] == pytest.approx(2.0)
        assert result["Nb"] == pytest.approx(0.0)
        assert result["V"] == pytest.approx(0.0)
        assert result["Ti"] == pytest.approx(0.5)

    def test_parses_system_with_five_elements(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        result = parse_composition_string("U77Mo10Nb5V5Ti3")
        assert result["U"] == pytest.approx(77.0)
        assert result["Mo"] == pytest.approx(10.0)
        assert result["Nb"] == pytest.approx(5.0)
        assert result["V"] == pytest.approx(5.0)
        assert result["Ti"] == pytest.approx(3.0)

    def test_does_not_mutate_input(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        original = "U93Mo7"
        parse_composition_string(original)
        assert original == "U93Mo7"

    def test_returns_fresh_dict_each_call(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        a = parse_composition_string("U90Mo10")
        b = parse_composition_string("U90Mo10")
        assert a is not b
        assert a == b

    def test_raises_on_empty_string(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        with pytest.raises(ValueError):
            parse_composition_string("")

    def test_raises_on_gibberish(self) -> None:
        from nfm_db.ml.heaps_parser import parse_composition_string

        with pytest.raises(ValueError):
            parse_composition_string("not-a-composition")


# ---------------------------------------------------------------------------
# wt_to_at_percent tests
# ---------------------------------------------------------------------------


class TestWtToAtPercent:
    """Tests for wt_to_at_percent(composition_wt) -> Dict[str, float]."""

    def test_u7mo_conversion(self) -> None:
        """Spot-check: U-7Mo wt% -> at.% conversion."""
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        result = wt_to_at_percent({"U": 93.0, "Mo": 7.0})

        # Manual calculation:
        # U: 93/238.03 = 0.39076, Mo: 7/95.95 = 0.07296
        # Total moles = 0.46372
        # U at% = 0.39076/0.46372 = 84.27%, Mo at% = 15.73%
        assert result["U"] == pytest.approx(84.27, abs=0.1)
        assert result["Mo"] == pytest.approx(15.73, abs=0.1)

    def test_u10mo5nb_conversion(self) -> None:
        """Spot-check: U-10Mo-5Nb ternary."""
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        result = wt_to_at_percent({"U": 85.0, "Mo": 10.0, "Nb": 5.0})

        # Manual:
        # U: 85/238.03 = 0.35708, Mo: 10/95.95 = 0.10422, Nb: 5/92.906 = 0.05382
        # Total = 0.51512
        # U = 69.31%, Mo = 20.23%, Nb = 10.45%
        assert result["U"] == pytest.approx(69.31, abs=0.1)
        assert result["Mo"] == pytest.approx(20.23, abs=0.1)
        assert result["Nb"] == pytest.approx(10.45, abs=0.1)

    def test_u97_5mo2nb0v0ti0_5(self) -> None:
        """Spot-check first row of HEAPS data."""
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        result = wt_to_at_percent({"U": 97.5, "Mo": 2.0, "Nb": 0.0, "V": 0.0, "Ti": 0.5})

        # Manual: U=92.90%, Mo=4.73%, Nb=0%, V=0%, Ti=2.37%
        assert result["U"] == pytest.approx(92.90, abs=0.1)
        assert result["Mo"] == pytest.approx(4.73, abs=0.1)
        assert result["Ti"] == pytest.approx(2.37, abs=0.05)
        assert result["Nb"] == pytest.approx(0.0, abs=0.01)
        assert result["V"] == pytest.approx(0.0, abs=0.01)

    def test_u77mo10nb5v5ti3(self) -> None:
        """Spot-check five-element alloy."""
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        result = wt_to_at_percent({"U": 77.0, "Mo": 10.0, "Nb": 5.0, "V": 5.0, "Ti": 3.0})

        # Manual: U=50.36%, Mo=16.22%, Nb=8.38%, V=15.28%, Ti=9.76%
        assert result["U"] == pytest.approx(50.36, abs=0.2)
        assert result["Mo"] == pytest.approx(16.22, abs=0.2)
        assert result["Nb"] == pytest.approx(8.38, abs=0.2)
        assert result["V"] == pytest.approx(15.28, abs=0.2)
        assert result["Ti"] == pytest.approx(9.76, abs=0.2)

    def test_at_percents_sum_to_100(self) -> None:
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        result = wt_to_at_percent({"U": 85.0, "Mo": 10.0, "Nb": 5.0})
        assert sum(result.values()) == pytest.approx(100.0, abs=0.1)

    def test_does_not_mutate_input(self) -> None:
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        original = {"U": 93.0, "Mo": 7.0}
        wt_to_at_percent(original)
        assert original == {"U": 93.0, "Mo": 7.0}

    def test_returns_fresh_dict_each_call(self) -> None:
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        a = wt_to_at_percent({"U": 93.0, "Mo": 7.0})
        b = wt_to_at_percent({"U": 93.0, "Mo": 7.0})
        assert a is not b

    def test_raises_on_empty_composition(self) -> None:
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        with pytest.raises(ValueError):
            wt_to_at_percent({})

    def test_raises_on_unknown_element(self) -> None:
        from nfm_db.ml.heaps_parser import wt_to_at_percent

        with pytest.raises(ValueError, match="Unknown element"):
            wt_to_at_percent({"Xx": 100.0})


# ---------------------------------------------------------------------------
# format_element_system tests
# ---------------------------------------------------------------------------


class TestFormatElementSystem:
    """Tests for format_element_system(elements) -> str."""

    def test_sorts_elements_alphabetically(self) -> None:
        from nfm_db.ml.heaps_parser import format_element_system

        result = format_element_system(["U", "Mo", "Nb"])
        assert result == "Mo-Nb-U"

    def test_five_elements_sorted(self) -> None:
        from nfm_db.ml.heaps_parser import format_element_system

        result = format_element_system(["U", "Mo", "Nb", "V", "Ti"])
        assert result == "Mo-Nb-Ti-U-V"

    def test_single_element(self) -> None:
        from nfm_db.ml.heaps_parser import format_element_system

        result = format_element_system(["U"])
        assert result == "U"

    def test_does_not_mutate_input(self) -> None:
        from nfm_db.ml.heaps_parser import format_element_system

        original = ["U", "Mo", "Nb"]
        format_element_system(original)
        assert original == ["U", "Mo", "Nb"]

    def test_empty_list_raises(self) -> None:
        from nfm_db.ml.heaps_parser import format_element_system

        with pytest.raises(ValueError):
            format_element_system([])


# ---------------------------------------------------------------------------
# HeapsRecord dataclass tests
# ---------------------------------------------------------------------------


class TestHeapsRecord:
    """Tests for HeapsRecord frozen dataclass."""

    def test_is_frozen(self) -> None:
        from nfm_db.ml.heaps_parser import HeapsRecord

        record = HeapsRecord(
            element_system="Mo-Nb-Ti-U-V",
            composition_at_percent={"U": 84.27, "Mo": 15.73},
            composition_wt_percent={"U": 93.0, "Mo": 7.0},
            phase=None,
            raw_system_string="U93Mo7",
            source_row_index=1,
        )
        with pytest.raises(FrozenInstanceError):
            record.element_system = "changed"  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        from nfm_db.ml.heaps_parser import HeapsRecord

        record = HeapsRecord(
            element_system="Mo-Nb-Ti-U-V",
            composition_at_percent={"U": 84.27, "Mo": 15.73},
            composition_wt_percent={"U": 93.0, "Mo": 7.0},
            phase="BCC",
            raw_system_string="U93Mo7",
            source_row_index=1,
        )
        assert record.element_system == "Mo-Nb-Ti-U-V"
        assert record.phase == "BCC"
        assert record.source_row_index == 1


# ---------------------------------------------------------------------------
# parse_heaps_csv tests
# ---------------------------------------------------------------------------


class TestParseHeapsCsv:
    """Tests for parse_heaps_csv(filepath) -> List[HeapsRecord]."""

    def test_parses_single_row(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        content = _make_csv_rows(["U93Mo7Nb0V0Ti0"])
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert len(records) == 1
            assert records[0].raw_system_string == "U93Mo7Nb0V0Ti0"
            assert records[0].element_system == "Mo-Nb-Ti-U-V"
            assert records[0].source_row_index == 0
            assert records[0].phase is None
        finally:
            os.unlink(path)

    def test_parses_multiple_rows(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        content = _make_csv_rows(["U93Mo7Nb0V0Ti0", "U90Mo10Nb0V0Ti0"])
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert len(records) == 2
            assert records[0].raw_system_string == "U93Mo7Nb0V0Ti0"
            assert records[1].raw_system_string == "U90Mo10Nb0V0Ti0"
        finally:
            os.unlink(path)

    def test_records_have_at_percent(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        content = _make_csv_rows(["U93Mo7Nb0V0Ti0"])
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert "U" in records[0].composition_at_percent
            assert "Mo" in records[0].composition_at_percent
            assert sum(records[0].composition_at_percent.values()) == pytest.approx(
                100.0, abs=0.1
            )
        finally:
            os.unlink(path)

    def test_records_have_wt_percent(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        content = _make_csv_rows(["U97.5Mo2Nb0V0Ti0.5"])
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert records[0].composition_wt_percent["U"] == pytest.approx(97.5)
            assert records[0].composition_wt_percent["Mo"] == pytest.approx(2.0)
            assert records[0].composition_wt_percent["Ti"] == pytest.approx(0.5)
        finally:
            os.unlink(path)

    def test_skips_malformed_row_and_logs_warning(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        lines = [
            HEAPS_CSV_HEADER,
            "U93Mo7Nb0V0Ti0,17.23,1.42,5.95,2.88,11.2,2.96",
            "INVALID_DATA,17.23,1.42,5.95,2.88,11.2,2.96",
            "U90Mo10Nb0V0Ti0,17.23,1.42,5.95,2.88,11.2,2.96",
        ]
        content = "\n".join(lines) + "\n"
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert len(records) == 2
            assert records[0].raw_system_string == "U93Mo7Nb0V0Ti0"
            assert records[1].raw_system_string == "U90Mo10Nb0V0Ti0"
        finally:
            os.unlink(path)

    def test_empty_csv_returns_empty_list(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        path = _write_temp_csv(HEAPS_CSV_HEADER + "\n")
        try:
            records = parse_heaps_csv(path)
            assert records == []
        finally:
            os.unlink(path)

    def test_returns_list_of_heaps_records(self) -> None:
        from nfm_db.ml.heaps_parser import HeapsRecord, parse_heaps_csv

        content = _make_csv_rows(["U93Mo7Nb0V0Ti0"])
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert isinstance(records, list)
            assert all(isinstance(r, HeapsRecord) for r in records)
        finally:
            os.unlink(path)

    def test_file_not_found_raises(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        with pytest.raises(FileNotFoundError):
            parse_heaps_csv("/nonexistent/path/heaps.csv")

    def test_records_are_not_mutatable(self) -> None:
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        content = _make_csv_rows(["U93Mo7Nb0V0Ti0"])
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            with pytest.raises(FrozenInstanceError):
                records[0].element_system = "mutated"  # type: ignore[misc]
        finally:
            os.unlink(path)

    def test_source_row_index_tracks_correctly(self) -> None:
        """After skipping a bad row, source_row_index should still reflect
        the original CSV row number."""
        from nfm_db.ml.heaps_parser import parse_heaps_csv

        lines = [
            HEAPS_CSV_HEADER,
            "U93Mo7Nb0V0Ti0,17.23,1.42,5.95,2.88,11.2,2.96",
            "BAD_ROW,17.23,1.42,5.95,2.88,11.2,2.96",
            "U90Mo10Nb0V0Ti0,17.23,1.42,5.95,2.88,11.2,2.96",
        ]
        content = "\n".join(lines) + "\n"
        path = _write_temp_csv(content)
        try:
            records = parse_heaps_csv(path)
            assert records[0].source_row_index == 0
            assert records[1].source_row_index == 2
        finally:
            os.unlink(path)
