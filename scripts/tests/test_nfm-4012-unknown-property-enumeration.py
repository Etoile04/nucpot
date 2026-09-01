"""Unit tests for ``scripts/nfm-4012-unknown-property-enumeration.py``.

NFM-4013 / Path (a): the harness aggregates the structured capture list
exposed via ``MappingResult.skipped_unknown_details`` /
``process_literature_sync`` into a TSV. These tests cover the pure
functions (``_normalize_property_name``, ``_normalize_category_slug``,
``_aggregate``, ``_write_tsv``) so the harness is exercised end-to-end
without needing a live PostgreSQL or a real LLM extraction run.

The DB-bound ``_drive_one`` is not exercised here — that path is
covered by the staging run (AC-1) which is a smoke test of the
harness's whole pipeline.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load the harness as a module (the file uses dashes which is not a valid
# Python identifier for ``import``).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_HARNESS_PATH = _REPO_ROOT / "scripts" / "nfm-4012-unknown-property-enumeration.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "nfm4012_harness",
        str(_HARNESS_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["nfm4012_harness"] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_normalize_property_name_lowercase_strip_collapse() -> None:
    """Strip whitespace, lower-case, collapse ``_<mult>space`` to ``_``."""
    assert HARNESS._normalize_property_name(" Cr-Doped_Diffusion_Ea  ") == ("cr-doped_diffusion_ea")
    assert HARNESS._normalize_property_name("Activation Energy") == ("activation_energy")
    assert HARNESS._normalize_property_name("already_clean") == "already_clean"
    assert HARNESS._normalize_property_name("multi   underscore___here") == (
        "multi_underscore_here"
    )


def test_normalize_property_name_none_returns_empty() -> None:
    assert HARNESS._normalize_property_name(None) == ""
    assert HARNESS._normalize_property_name("") == ""


def test_normalize_category_slug_lowercases() -> None:
    assert HARNESS._normalize_category_slug("THERMAL") == "thermal"
    assert HARNESS._normalize_category_slug("  Nuclear  ") == "nuclear"


def test_normalize_category_slug_preserves_none() -> None:
    assert HARNESS._normalize_category_slug(None) is None
    assert HARNESS._normalize_category_slug("") is None


def test_aggregate_unique_tuples_with_frequency(tmp_path: Path) -> None:
    """Bucket by ``(category_slug, raw_category, property_name)`` and sum frequency."""
    records = [
        {
            "category_slug": "thermal",
            "raw_category": "thermal",
            "property_name": "mysterious_unknown_property",
            "sample_value": "1.0",
            "source_doi": "10.1000/a",
        },
        {
            "category_slug": "thermal",
            "raw_category": "thermal",
            "property_name": "mysterious_unknown_property",
            "sample_value": "2.0",
            "source_doi": "10.1000/b",
        },
        {
            "category_slug": "thermal",
            "raw_category": "thermal",
            "property_name": "another_oddity",
            "sample_value": "3.0",
            "source_doi": "10.1000/a",
        },
    ]
    rows = HARNESS._aggregate(records)
    assert len(rows) == 2

    by_name = {r.raw_property_name: r for r in rows}
    mystery = by_name["mysterious_unknown_property"]
    assert mystery.frequency == 2
    assert mystery.sample_value == "1.0"  # first non-null value wins
    assert sorted(mystery.source_papers) == ["doi:10.1000/a", "doi:10.1000/b"]
    assert mystery.category_slug == "thermal"

    oddity = by_name["another_oddity"]
    assert oddity.frequency == 1
    assert oddity.sample_value == "3.0"


def test_aggregate_handles_none_category_and_no_doi(tmp_path: Path) -> None:
    """``category_slug=None`` and missing DOI fall back to source_file tag."""
    records = [
        {
            "category_slug": None,
            "raw_category": None,
            "property_name": "foo_bar",
            "sample_value": "1.0",
            "source_doi": None,
            "source_file": "literature/paper.pdf",
            "material_name": "UO2",
        },
    ]
    rows = HARNESS._aggregate(records)
    assert len(rows) == 1
    row = rows[0]
    assert row.category_slug is None
    assert row.raw_category == ""  # None coerced to empty string for grouping
    assert row.source_papers == ("file:literature/paper.pdf|mat:UO2",)


def test_aggregate_sorts_by_frequency_then_name() -> None:
    """Sort: frequency DESC, raw_category ASC, raw_property_name ASC."""
    records = [
        {"category_slug": "thermal", "raw_category": "thermal", "property_name": "aaa"},
        {"category_slug": "thermal", "raw_category": "thermal", "property_name": "bbb"},
        {"category_slug": "thermal", "raw_category": "thermal", "property_name": "ccc"},
        {"category_slug": "thermal", "raw_category": "thermal", "property_name": "aaa"},
    ]
    rows = HARNESS._aggregate(records)
    assert [r.raw_property_name for r in rows] == ["aaa", "bbb", "ccc"]
    assert rows[0].frequency == 2
    assert rows[1].frequency == 1
    assert rows[2].frequency == 1


def test_write_tsv_round_trip(tmp_path: Path) -> None:
    """``_write_tsv`` emits the AC-1 header and ranks rows sequentially."""
    records = [
        {
            "category_slug": "thermal",
            "raw_category": "thermal",
            "property_name": "foo_bar",
            "sample_value": "1.0",
            "source_doi": "10.1000/x",
        },
        {
            "category_slug": None,
            "raw_category": None,
            "property_name": "baz",
            "sample_value": None,
            "source_doi": None,
            "source_file": "literature/baz.pdf",
        },
    ]
    rows = HARNESS._aggregate(records)
    out = tmp_path / "out.tsv"
    HARNESS._write_tsv(rows, out)
    with out.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        assert header == list(HARNESS.TSV_COLUMNS)
        data = list(reader)
    assert len(data) == 2
    assert [row[0] for row in data] == ["1", "2"]  # rank
    # category_slug=None should render as empty string in the TSV.
    none_row = next(r for r in data if r[3] == "")
    assert none_row[1] == "baz"  # raw_property_name
    assert none_row[6] == ""  # sample_value None → ""


def test_aggregate_empty_input() -> None:
    """Empty input returns empty rows (AC-1 still passes when no drops)."""
    assert HARNESS._aggregate([]) == []


def test_default_datasource_id_is_owen2023_9320cb50() -> None:
    """The default sample must include Owen2023 9320cb50 (NFM-4012 § 3)."""
    assert "9320cb50-eb65-4178-8d2e-c56aeb848b21" in HARNESS.DEFAULT_DATASOURCE_IDS


def test_tsv_columns_match_ac1_header() -> None:
    """TSV column order matches the AC-1 v2 spec (catalog_gap? appended)."""
    assert HARNESS.TSV_COLUMNS == (
        "rank",
        "raw_property_name",
        "normalized_property_name",
        "category_slug",
        "raw_category",
        "frequency",
        "sample_value",
        "source_papers",
        "catalog_gap?",
    )


def test_aggregate_classifies_catalog_gap_when_known_names_supplied() -> None:
    """AC-1 v2: rows where the normalized_property_name is in property_types are
    catalog_gap='FALSE' (LLM-side mismatch); otherwise 'TRUE' (catalog gap)."""
    records = [
        {"category_slug": "physical", "raw_category": "physical", "property_name": "Density"},
        {"category_slug": "thermal", "raw_category": "thermal", "property_name": "MysteryProp"},
    ]
    rows = HARNESS._aggregate(records, known_property_names={"density"})
    by_name = {r.normalized_property_name: r for r in rows}
    assert by_name["density"].catalog_gap == "FALSE"
    assert by_name["mysteryprop"].catalog_gap == "TRUE"


def test_aggregate_catalog_gap_unknown_when_no_catalog_supplied() -> None:
    """Without a catalog, catalog_gap defaults to 'UNKNOWN' (not destructive)."""
    records = [
        {"category_slug": "physical", "raw_category": "physical", "property_name": "Density"},
    ]
    rows = HARNESS._aggregate(records)
    assert rows[0].catalog_gap == "UNKNOWN"


def test_write_tsv_emits_catalog_gap_column(tmp_path: Path) -> None:
    """AC-1 v2 condition (c): the TSV file carries the catalog_gap? column."""
    records = [
        {
            "category_slug": "physical",
            "raw_category": "physical",
            "property_name": "solubility_limit",
            "sample_value": "7800.0",
            "source_doi": None,
            "source_file": "literature/x.pdf",
            "material_name": "UO2",
        },
        {
            "category_slug": None,
            "raw_category": None,
            "property_name": "bulk_modulus",
            "sample_value": "207.5",
            "source_doi": None,
            "source_file": "literature/y.pdf",
            "material_name": "UO2",
        },
    ]
    rows = HARNESS._aggregate(records, known_property_names={"bulk_modulus"})
    out = tmp_path / "out.tsv"
    HARNESS._write_tsv(rows, out)
    with out.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        assert "catalog_gap?" in header
        data = list(reader)
    by_name = {row[2]: row for row in data}
    assert by_name["solubility_limit"][-1] == "TRUE"
    assert by_name["bulk_modulus"][-1] == "FALSE"
